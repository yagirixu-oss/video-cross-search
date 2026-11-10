"""外部APIを置き換え、成功・部分失敗・利用制限・入力をネット接続なしで確認する。"""
import asyncio

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.search import parse_youtube, parse_niconico
import app.main as main_module
import app.search as search_module


# ── テスト用の外部API応答 ─────────────────────────────────
def youtube_payload(count=6):
    """実APIと同じ構造で、架空の動画情報を用意する。"""
    return {"items": [
        {"id": {"kind": "youtube#video", "videoId": f"sample{i:05d}"},
         "snippet": {"title": f"Video {i} &amp; title", "liveBroadcastContent": "none",
                     "thumbnails": {"high": {"url": "https://example.org/image.jpg"}}}}
        for i in range(count)
    ]}


def nico_payload(count=6):
    """ニコニコのmeta/data構造を用意する。"""
    return {"meta": {"status": 200}, "data": [
        {"contentId": f"sm{100+i}", "title": f"ニコニコ {i}", "thumbnailUrl": None}
        for i in range(count)
    ]}


@pytest.fixture(autouse=True)
def isolated_key(monkeypatch):
    """実際のキーや.envに依存しないよう、テスト専用の値を設定する。"""
    monkeypatch.setenv("YOUTUBE_API_KEY", "dummy-test-key")
    monkeypatch.setattr(main_module, "dotenv_values", lambda *a, **k: {})


def client_for(handler):
    """HTTP通信だけを差し替え、FastAPIの入口からテストできるようにする。"""
    return TestClient(create_app(search_transport=httpx.MockTransport(handler)))


# ── APIの入力と統合 ──────────────────────────────────────
def test_both_success_and_cache():
    """依頼形式、APIキーの送り先、最大6本ずつの交互表示、キャッシュを確認する。"""
    calls = []
    def handler(request):
        """サイトに応じた架空の結果を返す。"""
        calls.append(request)
        assert request.url.params["q"] == "初音ミク & test"
        assert "key" not in request.url.params
        if request.url.host == "www.googleapis.com":
            assert request.headers["x-goog-api-key"] == "dummy-test-key"
            assert request.url.params["type"] == "video"
            assert request.url.params["part"] == "snippet"
            return httpx.Response(200, json=youtube_payload(10))
        assert "x-goog-api-key" not in request.headers
        assert request.url.params["_limit"] == "6"
        assert request.url.params["_sort"] == "-viewCounter"
        assert request.headers["User-Agent"].startswith("video-cross-search")
        return httpx.Response(200, json=nico_payload())
    with client_for(handler) as client:
        first = client.get("/api/search", params={"q": "  初音ミク & test  "})
        assert first.status_code == 200
        assert first.headers["cache-control"] == "no-store"
        data = first.json()
        assert data["query"] == "初音ミク & test"
        assert data["partial"] is False
        assert len(data["videos"]) == 12
        assert [v["platform"] for v in data["videos"]] == ["youtube", "niconico"] * 6
        assert data["videos"][0]["title"] == "Video 0 & title"
        assert "dummy-test-key" not in first.text
        second = client.get("/api/search", params={"q": data["query"]}).json()
        assert len(calls) == 2
        assert all(s["cached"] for s in second["sources"].values())


@pytest.mark.parametrize("query", [None, "", "   ", "　", "a"*101, "a\nb", "\x00hello"])
def test_invalid_input_never_calls_upstream(query):
    """空欄・過長・制御文字は外部APIへ送らず422で返す。"""
    def handler(request):
        """呼ばれたら入力検証漏れなので失敗させる。"""
        pytest.fail("invalid query reached provider")
    with client_for(handler) as client:
        response = client.get("/api/search", params={} if query is None else {"q": query})
        assert response.status_code == 422


def test_missing_youtube_key_keeps_nico(monkeypatch):
    """キーがなくてもニコニコの結果は返す。YouTubeへは通信しない。"""
    monkeypatch.setenv("YOUTUBE_API_KEY", "")
    def handler(request):
        """YouTubeが呼ばれていないことも確認する。"""
        assert request.url.host == "snapshot.search.nicovideo.jp"
        return httpx.Response(200, json=nico_payload())
    with client_for(handler) as client:
        response = client.get("/api/search", params={"q": "ミク"})
        assert response.status_code == 200
        data = response.json()
        assert len(data["videos"]) == 6 and data["partial"]
        assert data["sources"]["youtube"]["code"] == "not_configured"


@pytest.mark.parametrize("mode,code", [
    ("quota", "quota_exceeded"), ("key", "configuration_error"),
    ("timeout", "timeout"), ("network", "connection_error"),
    ("malformed", "invalid_response"), ("bad_items", "invalid_response"),
    ("server", "upstream_error"),
])
def test_youtube_failure_is_partial_and_redacted(mode, code):
    """外部の異常があっても成功したサイトの結果と、秘密を含まない案内を返す。"""
    def handler(request):
        """指定した種類の障害を再現する。"""
        if request.url.host != "www.googleapis.com":
            return httpx.Response(200, json=nico_payload(2))
        if mode == "timeout":
            raise httpx.ReadTimeout("dummy-test-key", request=request)
        if mode == "network":
            raise httpx.ConnectError("dummy-test-key", request=request)
        if mode == "malformed":
            return httpx.Response(200, text="dummy-test-key")
        if mode == "bad_items":
            return httpx.Response(200, json={"items": None})
        if mode == "server":
            return httpx.Response(500, text="dummy-test-key")
        return httpx.Response(403, json={"error": {"message": "dummy-test-key",
            "errors": [{"reason": "quotaExceeded" if mode == "quota" else "keyInvalid"}]}})
    with client_for(handler) as client:
        response = client.get("/api/search", params={"q": "test"})
        assert response.status_code == 200
        data = response.json()
        assert len(data["videos"]) == 2 and data["partial"]
        assert data["sources"]["youtube"]["code"] == code
        assert "dummy-test-key" not in response.text


def test_total_failure_is_502_not_empty_success():
    """両サイトの故障を「検索結果0件」と誤表示させない。"""
    with client_for(lambda request: httpx.Response(500)) as client:
        response = client.get("/api/search", params={"q": "test"})
        assert response.status_code == 502
        assert response.json()["videos"] == []
        assert all(s["status"] == "error" for s in response.json()["sources"].values())


def test_zero_matches_is_success():
    """正常な空一覧は失敗扱いにしない。"""
    def handler(request):
        """どちらにも該当する動画がない状況を再現する。"""
        return httpx.Response(200, json=youtube_payload(0) if request.url.host == "www.googleapis.com" else nico_payload(0))
    with client_for(handler) as client:
        response = client.get("/api/search", params={"q": "nothing"})
        assert response.status_code == 200
        assert response.json()["videos"] == []
        assert response.json()["partial"] is False


def test_uneven_results_keep_remaining_videos():
    """片方が少なくても、もう片方の余りを末尾に表示する。"""
    def handler(request):
        """YouTube1本・ニコニコ3本を返す。"""
        return httpx.Response(200, json=youtube_payload(1) if request.url.host == "www.googleapis.com" else nico_payload(3))
    with client_for(handler) as client:
        data = client.get("/api/search", params={"q": "test"}).json()
        assert [v["platform"] for v in data["videos"]] == ["youtube", "niconico", "niconico", "niconico"]


def test_nico_busy_cooldown_avoids_repeated_requests():
    """503の後は別キーワードであっても5分の待機を守る。"""
    nico_calls = []
    def handler(request):
        """ニコニコだけを混雑状態にする。"""
        if request.url.host == "www.googleapis.com":
            return httpx.Response(200, json=youtube_payload(1))
        nico_calls.append(request)
        return httpx.Response(503)
    with client_for(handler) as client:
        first = client.get("/api/search", params={"q": "first"}).json()
        second = client.get("/api/search", params={"q": "second"}).json()
        assert first["sources"]["niconico"]["code"] == "busy"
        assert second["sources"]["niconico"]["code"] == "cooldown"
        assert len(nico_calls) == 1


def test_providers_run_concurrently():
    """片方の応答がもう片方の開始を待っても、同時検索なら完了できる。"""
    started = set()
    async def handler(request):
        """両方が開始するまで短く待ち、直列実装なら失敗するようにする。"""
        started.add(request.url.host)
        for _ in range(100):
            if len(started) == 2:
                break
            await asyncio.sleep(0.001)
        assert len(started) == 2
        return httpx.Response(200, json=youtube_payload() if request.url.host == "www.googleapis.com" else nico_payload())
    with client_for(handler) as client:
        assert client.get("/api/search", params={"q": "test"}).status_code == 200


# ── 変換処理の境界条件 ───────────────────────────────────
def test_youtube_filters_live_upcoming_duplicates_and_invalid():
    """ライブ・予定・チャンネル・重複・壊れた項目を除外する。"""
    payload = youtube_payload()
    payload["items"][0]["snippet"]["liveBroadcastContent"] = "live"
    payload["items"][1]["snippet"]["liveBroadcastContent"] = "upcoming"
    payload["items"][2]["id"]["kind"] = "youtube#channel"
    payload["items"][3]["snippet"]["title"] = None
    payload["items"].append(payload["items"][4])
    payload["items"].append(None)
    result = parse_youtube(payload)
    assert len(result) == 2
    assert [v.id for v in result] == ["youtube:sample00004", "youtube:sample00005"]


def test_nico_filters_invalid_items_and_falls_back_image():
    """ID不正・重複・タイトル欠落を除外し、欠けた画像に代替パスを設定する。"""
    payload = nico_payload(3)
    payload["data"][1]["contentId"] = "../../"
    payload["data"][2]["title"] = None
    payload["data"].append(payload["data"][0])
    videos = parse_niconico(payload)
    assert len(videos) == 1
    assert videos[0].thumbnail_url == "/static/niconico-placeholder.svg"


def test_provider_deadline_is_bounded(monkeypatch):
    """HTTPクライアント以外で待ち続けても、サイト全体の時間制限で戻る。"""
    monkeypatch.setattr(search_module, "REQUEST_DEADLINE", 0.02)
    async def handler(request):
        """YouTubeだけ応答を遅らせる。"""
        if request.url.host == "www.googleapis.com":
            await asyncio.sleep(1)
        return httpx.Response(200, json=nico_payload(1))
    with client_for(handler) as client:
        data = client.get("/api/search", params={"q": "test"}).json()
        assert data["sources"]["youtube"]["code"] == "timeout"
        assert len(data["videos"]) == 1
