"""期間の引継ぎ・追加情報の部分失敗・旧資産の再利用防止を検証する。"""
import os
import re
from datetime import datetime

import httpx
import pytest
from fastapi.testclient import TestClient

import app.main as main_module
import app.search as search_module
from app.main import create_app
from app.metadata import count_or_none, date_or_none
from app.search import parse_niconico
from test_pagination import page_payload, request_position
from test_search import client_for, metadata_response, isolated_key, nico_payload


@pytest.mark.parametrize("period,days", [("week", 7), ("month", 30), ("year", 365)])
def test_period_uses_same_window_for_both_sites_and_next_page(period, days, monkeypatch):
    """時間が経過しても続きの日時条件を動かさず、両サイトへ同じ範囲を送る。"""
    calls = []
    def handler(request):
        """検索APIが受け取った期間・続き位置を記録する。"""
        platform, start = request_position(request)
        params = request.url.params
        after = params['publishedAfter'] if platform == 'youtube' else params['filters[startTime][gte]']
        before = params['publishedBefore'] if platform == 'youtube' else params['filters[startTime][lte]']
        assert (datetime.fromisoformat(before) - datetime.fromisoformat(after)).days == days
        calls.append((platform, start, after, before))
        return httpx.Response(200, json=page_payload(platform, start, 6, 12))
    with client_for(handler) as client:
        params = {'q': 'test', 'period': period, 'sort': 'newest'}
        first = client.get('/api/search', params=params).json()
        # 続きでこの関数を再実行してしまう実装なら失敗する。
        def no_new_window(*args):
            """ページ途中の期間再計算を禁止する。"""
            pytest.fail('Pagination recalculated the date range')
        monkeypatch.setattr(search_module, 'publication_window', no_new_window)
        second = client.get('/api/search', params={**params, 'cursor': first['next_cursor']}).json()
        assert second['published_after'] == first['published_after']
        assert second['period'] == period
        assert len(calls) == 4 and len({(c[2], c[3]) for c in calls}) == 1


def test_period_cache_separation_and_reject_mismatched_cursor():
    """期間変更は新規検索。別期間の続き情報は外部通信前に拒否する。"""
    calls = []
    def handler(request):
        """YouTubeだけを検索し、期間指定なしに日時条件がないことも確認する。"""
        platform, start = request_position(request)
        assert platform == 'youtube'
        calls.append(dict(request.url.params))
        return httpx.Response(200, json=page_payload(platform, start, 6, 12))
    with client_for(handler) as client:
        base = {'q': 'test', 'target': 'youtube'}
        client.get('/api/search', params=base)
        first = client.get('/api/search', params={**base, 'period': 'week'}).json()
        again = client.get('/api/search', params={**base, 'period': 'week'}).json()
        assert again['sources']['youtube']['cached']
        assert client.get('/api/search', params={**base, 'period': 'month', 'cursor': first['next_cursor']}).status_code == 410
        assert client.get('/api/search', params={**base, 'period': 'invalid'}).status_code == 422
        assert len(calls) == 2 and 'publishedAfter' not in calls[0]


def test_retry_keeps_period_and_other_site_position():
    """片方が失敗した場合も、再試行に元の範囲を引き継ぐ。"""
    calls = []
    def handler(request):
        """YouTubeだけ初回失敗させる。"""
        platform, start = request_position(request)
        calls.append((platform, start, str(request.url.params)))
        if platform == 'youtube' and sum(c[0] == 'youtube' for c in calls) == 1:
            return httpx.Response(500)
        return httpx.Response(200, json=page_payload(platform, start, 6, 12))
    with client_for(handler) as client:
        params = {'q': 'test', 'period': 'week'}
        first = client.get('/api/search', params=params).json()
        second = client.get('/api/search', params={**params, 'cursor': first['retry_cursor']}).json()
        assert second['published_before'] == first['published_before']
        assert second['sources']['niconico']['requested'] is False
        youtube_calls = [c for c in calls if c[0] == 'youtube']
        assert youtube_calls[0] == youtube_calls[1]


def test_metadata_batches_youtube_ids_and_preserves_zero_and_order():
    """順番が異なる詳細応答をIDで結合し、本当の0回を保持する。"""
    detail_calls = []
    def handler(request):
        """検索結果を6件返す。"""
        platform, start = request_position(request)
        return httpx.Response(200, json=page_payload(platform, start, 6, 12))
    def details(request):
        """YouTube詳細を逆順にし、先頭動画の再生数を0にする。"""
        detail_calls.append(request)
        assert request.headers['x-goog-api-key'] == 'dummy-test-key'
        assert 'key' not in request.url.params
        payload = metadata_response(request).json()
        payload['items'][0]['statistics']['viewCount'] = '0'
        payload['items'].reverse()
        return httpx.Response(200, json=payload)
    with client_for(handler, details) as client:
        data = client.get('/api/search', params={'q': 'test', 'target': 'youtube'}).json()
        assert len(detail_calls) == 1
        assert len(detail_calls[0].url.params['id'].split(',')) == 6
        assert data['videos'][0]['view_count'] == 0
        assert data['videos'][0]['id'] == 'youtube:sample00000'
        assert data['videos'][0]['author_name'] == 'テスト投稿者'
        assert data['videos'][0]['published_at'].startswith('2026-11-20')


def test_metadata_failure_keeps_search_success_and_cursor():
    """追加情報だけの障害でカードや続きを失わない。秘密を含むエラーも公開しない。"""
    def handler(request):
        """各サイトの検索は成功させる。"""
        platform, start = request_position(request)
        return httpx.Response(200, json=page_payload(platform, start, 6, 12))
    def details(request):
        """追加情報だけ500を返す。"""
        assert 'x-goog-api-key' not in request.headers if request.url.host == 'ext.nicovideo.jp' else True
        return httpx.Response(500, text='dummy-test-key')
    with client_for(handler, details) as client:
        response = client.get('/api/search', params={'q': 'test'})
        data = response.json()
        assert response.status_code == 200 and len(data['videos']) == 12
        assert data['next_cursor'] and not data['retry_cursor']
        assert data['videos'][0]['view_count'] is None
        assert all(source['metadata_warning'] for source in data['sources'].values())
        assert 'dummy-test-key' not in response.text


def test_nico_metadata_and_missing_values():
    """検索APIの数値・日時・投稿者IDを変換し、壊れた任意項目は未取得にする。"""
    payload = nico_payload(2)
    payload['data'][0].update(startTime='2026-11-23T12:00:00+09:00', viewCounter=0, userId=123)
    payload['data'][1].update(startTime='invalid', viewCounter=-1, channelId=456)
    videos = parse_niconico(payload)
    assert videos[0].author_id == 'user:123' and videos[0].view_count == 0
    assert videos[0].published_at.utcoffset().total_seconds() == 32400
    assert videos[1].author_id == 'channel:456' and videos[1].published_at is None
    assert videos[1].view_count is None
    assert count_or_none(True) is None and count_or_none('1.5') is None
    assert date_or_none('2026-11-20') is None


def test_changed_assets_get_new_url_even_with_same_size_and_timestamp(tmp_path, monkeypatch):
    """今回の不具合の回帰確認。更新日時に頼らず、内容変更で資産URLを更新する。"""
    for filename in ('index.html', 'app.js', 'styles.css'):
        (tmp_path / filename).write_bytes((main_module.FRONTEND_DIR / filename).read_bytes())
    monkeypatch.setattr(main_module, 'FRONTEND_DIR', tmp_path)
    with TestClient(create_app()) as client:
        first = client.get('/')
        assert first.headers['cache-control'] == 'no-store'
        url = re.search(r'src="([^"]*app.js[^"]*)"', first.text)[1]
        script = client.get(url)
        assert script.headers['cache-control'] == 'no-store'
        path = tmp_path / 'app.js'
        original_stat = path.stat()
        path.write_text(path.read_text(encoding='utf-8').replace('use strict', 'use STRICT'), encoding='utf-8')
        os.utime(path, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
        assert path.stat().st_size == original_stat.st_size
        second = client.get('/')
        next_url = re.search(r'src="([^"]*app.js[^"]*)"', second.text)[1]
        assert url != next_url
        # 古い条件付きリクエストでも、キャッシュを再利用させず実体を返す。
        fresh = client.get(url, headers={'If-None-Match': script.headers['etag']})
        assert fresh.status_code == 200 and 'use STRICT' in fresh.text
        assert client.get('/ui/index.html').text == second.text
