"""推定分類の誤判定境界と、検索・キャッシュ・続きの整合性を確認する。"""
import httpx
import pytest

from app.youtube_format import duration_seconds, classify_youtube
from test_search import client_for, metadata_response, isolated_key
from test_pagination import page_payload, request_position
from test_history import history_client, import_sample, ALICE


# ── 時間とハッシュタグの境界 ────────────────────────────
@pytest.mark.parametrize("value,expected", [
    ("PT59S", 59), ("PT3M", 180), ("PT3M0.5S", 180.5), ("PT1H2M3S", 3723),
    ("P1DT1S", 86401), ("P2D", 172800), (None, None), (True, None), ("PT0S", None),
    ("PT", None), ("P", None), ("P1DT", None), ("PT-1S", None), ("PTNaNS", None),
    ("PT3M trailing", None), ("P1M", None),
])
def test_duration_does_not_guess_invalid_values(value, expected):
    """日・時間・分・秒と小数秒を読み、不正値を0秒の動画に変換しない。"""
    assert duration_seconds(value) == expected


@pytest.mark.parametrize("seconds,title,description,expected", [
    (59, "演奏 #Shorts", "", "shorts"),
    (180, "演奏", "説明\n#shorts", "shorts"),
    (30, "演奏 ＃ＳＨＯＲＴＳ", "", "shorts"),
    (30, "演奏 &#35;shorts", "", "shorts"),
    (90, "演奏", "#YouTubeShorts", "shorts"),
    (90, "演奏 #ショート動画", "", "shorts"),
    (120, "短いMV", "", "unknown"),
    (120, "How Shorts work", "", "unknown"),
    (60, "演奏 #shortstory", "", "unknown"),
    (60, "演奏 #shortsnews", "", "unknown"),
    (180.5, "演奏", "", "unknown"),
    (181, "演奏", "", "unknown"),
    (182, "演奏", "", "regular"),
    (600, "ショートの作り方 #shorts", "", "unknown"),
    (None, "演奏 #shorts", "", "unknown"),
])
def test_classification_preserves_uncertainty(seconds, title, description, expected):
    """短い通常動画・語の部分一致・時間と表記の矛盾を確定扱いしない。"""
    kind, reason = classify_youtube(seconds, title, description)
    assert kind == expected and reason


# ── 実際のHTTP窓口を通した分類と絞り込み ────────────────
def mixed_details(request):
    """6本をショート候補・通常動画候補・未判定の各2本にする。ニコニコは通常応答。"""
    response = metadata_response(request)
    if request.url.path != "/youtube/v3/videos":
        return response
    assert request.url.params["part"] == "snippet,statistics,contentDetails"
    payload = response.json()
    for item in payload["items"]:
        number = int(item["id"][-5:])
        item["snippet"]["description"] = "説明 " * 300 + ("\n#shorts" if number % 3 == 0 else "")
        item["contentDetails"] = {"duration": "PT10M" if number % 3 == 1 else "PT1M"}
    return httpx.Response(200, json=payload)


def test_filters_share_raw_cache_without_losing_candidates():
    """分類変更でも元の6本を保持。省略前の説明欄を使い、詳細取得を一括で行う。"""
    calls, detail_calls = [], []

    def upstream(request):
        """外部検索にShortsと無関係なvideoDuration条件を付けないことも確認する。"""
        calls.append(request)
        assert "videoDuration" not in request.url.params
        platform, start = request_position(request)
        return httpx.Response(200, json=page_payload(platform, start, 6, 12))

    def details(request):
        """ページ内のIDを1通信でまとめて補完する。"""
        detail_calls.append(request)
        return mixed_details(request)

    with client_for(upstream, details) as client:
        base = {"q": "演奏", "target": "youtube"}
        original = client.get("/api/search", params=base).json()
        assert len(original["videos"]) == 6
        assert original["sources"]["youtube"]["format_counts"] == {"shorts": 2, "regular": 2, "unknown": 2}
        for kind in ("shorts", "regular", "unknown"):
            data = client.get("/api/search", params={**base, "youtube_format": kind}).json()
            assert data["youtube_format"] == kind
            assert len(data["videos"]) == 2 and all(v["youtube_format"] == kind for v in data["videos"])
            assert data["sources"]["youtube"]["count"] == 2
            assert data["sources"]["youtube"]["fetched_count"] == 6
            assert data["sources"]["youtube"]["cached"]
            assert data["next_cursor"]
        assert client.get("/api/search", params=base).json()["videos"] == original["videos"]
        assert len(calls) == len(detail_calls) == 1


def test_empty_filtered_page_still_has_next_page_and_rejects_changed_kind():
    """初回0件でも次の6件へ進み、異なる種類のカーソル流用を拒否する。"""
    calls = []

    def upstream(request):
        """最初のページだけ未判定、次のページは全件ショート候補にする。"""
        platform, start = request_position(request)
        calls.append(start)
        return httpx.Response(200, json=page_payload(platform, start, 6, 12))

    def details(request):
        """ページ境界をまたいでも位置を詰め直さないことを確認する。"""
        payload = metadata_response(request).json()
        for item in payload["items"]:
            item["contentDetails"] = {"duration": "PT60S"}
            item["snippet"]["description"] = "#shorts" if int(item["id"][-5:]) >= 6 else "短いMV"
        return httpx.Response(200, json=payload)

    with client_for(upstream, details) as client:
        params = {"q": "演奏", "target": "youtube", "youtube_format": "shorts"}
        first = client.get("/api/search", params=params).json()
        assert first["videos"] == [] and first["next_cursor"] and not first["partial"]
        assert calls == [0]  # 表示件数を埋めるための先読みはしない。
        wrong = client.get("/api/search", params={**params, "youtube_format": "regular", "cursor": first["next_cursor"]})
        assert wrong.status_code == 410 and calls == [0]
        more = client.get("/api/search", params={**params, "cursor": first["next_cursor"]}).json()
        assert len(more["videos"]) == 6 and not more["next_cursor"]
        assert calls == [0, 6]


def test_filter_keeps_nico_and_retry_only_fetches_failed_site():
    """YouTubeの種類指定でニコニコを消さず、部分失敗の再試行にも種類を維持する。"""
    calls = []

    def upstream(request):
        """YouTubeのみ初回失敗を返し、2回目から分類可能な結果を返す。"""
        platform, start = request_position(request)
        calls.append((platform, start))
        if platform == "youtube" and calls.count((platform, start)) == 1:
            return httpx.Response(500)
        return httpx.Response(200, json=page_payload(platform, start, 6, 12))

    with client_for(upstream, mixed_details) as client:
        params = {"q": "演奏", "youtube_format": "shorts"}
        first = client.get("/api/search", params=params).json()
        assert len(first["videos"]) == 6 and all(v["platform"] == "niconico" for v in first["videos"])
        assert all(v["youtube_format"] is None for v in first["videos"])
        retry = client.get("/api/search", params={**params, "cursor": first["retry_cursor"]}).json()
        assert len(retry["videos"]) == 2 and all(v["youtube_format"] == "shorts" for v in retry["videos"])
        assert retry["sources"]["niconico"]["requested"] is False
        assert calls.count(("niconico", 0)) == 1


def test_metadata_failure_can_be_found_in_unknown_filter():
    """詳細取得の失敗を通常動画扱いせず、未判定に残す。"""
    def upstream(request):
        """検索だけは成功する状態を用意する。"""
        return httpx.Response(200, json=page_payload("youtube", 0, 6, 12))

    def details(request):
        """エラー本文を画面へ流さないことも確認する。"""
        return httpx.Response(500, text="dummy-test-key")

    with client_for(upstream, details) as client:
        params = {"q": "演奏", "target": "youtube", "youtube_format": "shorts"}
        response = client.get("/api/search", params=params)
        assert response.status_code == 200 and "dummy-test-key" not in response.text
        assert response.json()["videos"] == [] and response.json()["next_cursor"]
        data = client.get("/api/search", params={**params, "youtube_format": "unknown"}).json()
        assert len(data["videos"]) == 6 and all(v["youtube_format"] == "unknown" for v in data["videos"])
        assert data["sources"]["youtube"]["metadata_warning"]


def test_personal_search_keeps_kind_with_18_candidates_and_cursor(history_client):
    """履歴の順位補正と種類の条件を組み合わせ、途中での種類変更を拒否する。"""
    client, _ = history_client
    import_sample(client)
    params = {"q": "太鼓", "target": "youtube", "personalized": True, "youtube_format": "unknown"}
    first = client.get("/api/search", params=params, headers=ALICE).json()
    assert first["personalized"] and len(first["videos"]) == 18
    assert first["sources"]["youtube"]["format_counts"]["unknown"] == 18
    assert client.get("/api/search", params={**params, "youtube_format": "shorts", "cursor": first["next_cursor"]}, headers=ALICE).status_code == 410
    more = client.get("/api/search", params={**params, "cursor": first["next_cursor"]}, headers=ALICE).json()
    assert len(more["videos"]) == 18
    assert not ({v["id"] for v in first["videos"]} & {v["id"] for v in more["videos"]})


def test_invalid_kind_is_rejected_before_network():
    """想定外の種類は422にし、外部APIを呼ばない。"""
    def upstream(request):
        """入力の誤りを外部サービスに持ち出さない。"""
        pytest.fail("invalid format reached provider")

    with client_for(upstream) as client:
        assert client.get("/api/search", params={"q": "演奏", "youtube_format": "invalid"}).status_code == 422
