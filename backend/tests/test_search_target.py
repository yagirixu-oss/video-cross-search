"""検索対象に選ばれていないサイトへ通信しないことを、APIの入口から確認する。"""
import httpx
import pytest

from test_search import client_for, isolated_key
from test_pagination import page_payload, request_position


@pytest.mark.parametrize("target", ["all", "youtube", "niconico"])
def test_target_controls_initial_and_next_page(target):
    """初回も続きも対象サイトだけに依頼し、対象外を失敗として扱わない。"""
    calls = []
    def handler(request):
        """各サイト2ページ分のデータを返す。"""
        platform, start = request_position(request)
        calls.append((platform, start))
        return httpx.Response(200, json=page_payload(platform, start, 6, 12))
    selected = ["youtube", "niconico"] if target == "all" else [target]
    with client_for(handler) as client:
        first = client.get("/api/search", params={"q": "test", "target": target}).json()
        assert first["target"] == target and first["partial"] is False
        assert len(first["videos"]) == 6 * len(selected)
        for platform, source in first["sources"].items():
            assert source["selected"] == (platform in selected)
            if platform not in selected:
                assert source["status"] == "skipped" and not source["requested"]
        second = client.get("/api/search", params={"q": "test", "target": target, "cursor": first["next_cursor"]})
        assert second.status_code == 200
        assert second.json()["next_cursor"] is None
        assert calls == [(p, offset) for offset in (0, 6) for p in selected]


@pytest.mark.parametrize("target,status", [("niconico", 200), ("youtube", 502)])
def test_missing_key_is_only_reported_for_selected_youtube(monkeypatch, target, status):
    """ニコニコのみではYouTube未設定を表示せず、YouTubeのみでは勝手に代替検索しない。"""
    monkeypatch.setenv("YOUTUBE_API_KEY", "")
    calls = []
    def handler(request):
        """ニコニコへの依頼だけを許可する。"""
        platform, start = request_position(request)
        calls.append(platform)
        assert platform == "niconico"
        return httpx.Response(200, json=page_payload(platform, start, 0, 0))
    with client_for(handler) as client:
        response = client.get("/api/search", params={"q": "test", "target": target})
        assert response.status_code == status
        assert calls == (["niconico"] if target == "niconico" else [])
        data = response.json()
        assert data["partial"] == (target == "youtube")
        assert data["sources"]["youtube"]["code"] == ("not_configured" if target == "youtube" else None)


def test_cursor_cannot_be_used_for_different_target():
    """同じキーワードでも、検索対象が異なるカーソルを流用させない。"""
    calls = []
    def handler(request):
        """YouTubeの初回だけを返す。"""
        platform, start = request_position(request)
        calls.append(platform)
        return httpx.Response(200, json=page_payload(platform, start, 6, 12))
    with client_for(handler) as client:
        first = client.get("/api/search", params={"q": "test", "target": "youtube"}).json()
        for target in ("all", "niconico"):
            response = client.get("/api/search", params={"q": "test", "target": target, "cursor": first["next_cursor"]})
            assert response.status_code == 410
        assert calls == ["youtube"]


@pytest.mark.parametrize("target", ["", "twitch", "YouTube", "youtube,niconico"])
def test_invalid_target_is_rejected_before_external_request(target):
    """対応していない指定は422で返し、検索を実行しない。"""
    def handler(request):
        """呼ばれたら入力検証漏れとして失敗させる。"""
        pytest.fail("invalid target reached provider")
    with client_for(handler) as client:
        assert client.get("/api/search", params={"q": "test", "target": target}).status_code == 422


def test_single_target_retry_keeps_target_and_position():
    """単一サイトの失敗後も、再試行で他サイトを呼ばず同じ位置へ戻る。"""
    calls = []
    def handler(request):
        """YouTubeだけを対象に、1回目の応答を失敗させる。"""
        platform, start = request_position(request)
        calls.append((platform, start))
        if len(calls) == 1:
            return httpx.Response(500)
        return httpx.Response(200, json=page_payload(platform, start, 6, 12))
    with client_for(handler) as client:
        first = client.get("/api/search", params={"q": "test", "target": "youtube"})
        assert first.status_code == 502
        retried = client.get("/api/search", params={"q": "test", "target": "youtube", "cursor": first.json()["retry_cursor"]})
        assert retried.status_code == 200 and not retried.json()["partial"]
        assert calls == [("youtube", 0), ("youtube", 0)]
