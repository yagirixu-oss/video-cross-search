"""続き取得の位置・失敗時の再試行・期限切れを、外部APIに接続せず検証する。"""
import httpx
import pytest

from app.pagination import CursorCodec, NICO_MAX_OFFSET
import app.pagination as pagination_module
from test_search import client_for, youtube_payload, nico_payload, isolated_key


# ── ページごとに異なる動画を返す模擬API ─────────────────
def page_payload(platform, start, count, total):
    """実際のAPIと同じ目印・件数を持つページを作る。動画IDは通し番号にする。"""
    if platform == "youtube":
        payload = youtube_payload(count)
        for i, item in enumerate(payload["items"], start):
            item["id"]["videoId"] = f"sample{i:05d}"
        if start + count < total:
            payload["nextPageToken"] = f"page-{start + count}"
    else:
        payload = nico_payload(count)
        payload["meta"]["totalCount"] = total
        for i, item in enumerate(payload["data"], start):
            item["contentId"] = f"sm{100 + i}"
    return payload


def request_position(request):
    """外部APIへ送った続き情報を検証しやすい数値に戻す。"""
    if request.url.host == "www.googleapis.com":
        assert request.url.params["maxResults"] == "6"
        token = request.url.params.get("pageToken")
        return "youtube", int(token.removeprefix("page-")) if token else 0
    return "niconico", int(request.url.params["_offset"])


def test_three_pages_no_prefetch_no_skips_and_exhausted_source_stops():
    """初回は各1通信のみ。6件ずつ進み、終了したサイトは二度と呼ばない。"""
    calls = []
    def handler(request):
        """YouTubeは14件、ニコニコは7件の検索結果を再現する。"""
        platform, start = request_position(request)
        calls.append((platform, start))
        total = 14 if platform == "youtube" else 7
        return httpx.Response(200, json=page_payload(platform, start, min(6, total-start), total))
    with client_for(handler) as client:
        first = client.get("/api/search", params={"q": "test"}).json()
        assert len(calls) == 2 and len(first["videos"]) == 12
        second = client.get("/api/search", params={"q": "test", "cursor": first["next_cursor"]}).json()
        assert len(second["videos"]) == 7
        third = client.get("/api/search", params={"q": "test", "cursor": second["next_cursor"]}).json()
        assert len(third["videos"]) == 2
        assert third["next_cursor"] is None
        assert third["sources"]["niconico"]["requested"] is False
        ids = [v["id"] for page in (first, second, third) for v in page["videos"]]
        assert len(ids) == len(set(ids)) == 21
        assert calls == [("youtube", 0), ("niconico", 0), ("youtube", 6), ("niconico", 6), ("youtube", 12)]


def test_page_cache_is_separate_and_repeated_cursor_is_idempotent():
    """同じ続き要求を再送しても同じページを返し、初回キャッシュと混同しない。"""
    calls = []
    def handler(request):
        """各サイト18件を用意する。"""
        platform, start = request_position(request)
        calls.append((platform, start))
        return httpx.Response(200, json=page_payload(platform, start, 6, 18))
    with client_for(handler) as client:
        first = client.get("/api/search", params={"q": "test"}).json()
        params = {"q": "test", "cursor": first["next_cursor"]}
        second = client.get("/api/search", params=params).json()
        repeat = client.get("/api/search", params=params).json()
        assert repeat["videos"] == second["videos"] != first["videos"]
        assert len(calls) == 4
        assert all(s["cached"] for s in repeat["sources"].values())


def test_retry_only_failed_site_preserves_other_sites_next_position():
    """片方の2ページ目が失敗しても、再試行後に両方3ページ目へ進める。"""
    calls = []
    def handler(request):
        """YouTubeの2ページ目だけ、初回は500にする。"""
        platform, start = request_position(request)
        calls.append((platform, start))
        if (platform, start) == ("youtube", 6) and calls.count((platform, start)) == 1:
            return httpx.Response(500)
        return httpx.Response(200, json=page_payload(platform, start, 6, 18))
    with client_for(handler) as client:
        first = client.get("/api/search", params={"q": "test"}).json()
        second = client.get("/api/search", params={"q": "test", "cursor": first["next_cursor"]}).json()
        assert second["partial"] and len(second["videos"]) == 6
        retried = client.get("/api/search", params={"q": "test", "cursor": second["retry_cursor"]}).json()
        assert len(retried["videos"]) == 6
        assert retried["sources"]["niconico"]["requested"] is False
        assert calls[-1] == ("youtube", 6)
        third = client.get("/api/search", params={"q": "test", "cursor": retried["next_cursor"]}).json()
        assert calls[-2:] == [("youtube", 12), ("niconico", 12)]
        assert third["next_cursor"] is None


def test_retry_failure_returns_502_even_if_other_site_was_already_successful():
    """対象外サイトのokを理由に、失敗だけの再試行を200にしない。"""
    def handler(request):
        """YouTubeだけ常に失敗させる。"""
        platform, start = request_position(request)
        return httpx.Response(500) if platform == "youtube" else httpx.Response(200, json=page_payload(platform, start, 6, 12))
    with client_for(handler) as client:
        first = client.get("/api/search", params={"q": "test"}).json()
        response = client.get("/api/search", params={"q": "test", "cursor": first["retry_cursor"]})
        assert response.status_code == 502
        assert response.json()["sources"]["niconico"]["requested"] is False


def test_zero_displayable_youtube_page_still_has_a_next_page():
    """ライブ動画しかないページも、APIの次ページ目印があれば続きへ進める。"""
    def handler(request):
        """YouTubeの初回ページだけライブ扱いにする。"""
        platform, start = request_position(request)
        payload = page_payload(platform, start, 6 if platform == "youtube" else 0, 12 if platform == "youtube" else 0)
        if platform == "youtube" and start == 0:
            for item in payload["items"]:
                item["snippet"]["liveBroadcastContent"] = "live"
        return httpx.Response(200, json=payload)
    with client_for(handler) as client:
        first = client.get("/api/search", params={"q": "test"}).json()
        assert first["videos"] == [] and first["next_cursor"]
        second = client.get("/api/search", params={"q": "test", "cursor": first["next_cursor"]}).json()
        assert len(second["videos"]) == 6 and second["next_cursor"] is None


@pytest.mark.parametrize("kind", ["modified", "other_query", "expired", "restart"])
def test_invalid_cursor_does_not_call_providers(kind, monkeypatch):
    """改ざん・別検索語・期限切れ・再起動後は410。勝手に先頭へ戻らない。"""
    calls = []
    def handler(request):
        """続きのある初回応答を作る。"""
        platform, start = request_position(request)
        calls.append((platform, start))
        return httpx.Response(200, json=page_payload(platform, start, 6, 12))
    with client_for(handler) as client:
        first = client.get("/api/search", params={"q": "test"}).json()
        cursor, query = first["next_cursor"], "test"
        if kind == "modified":
            cursor += "a"
        elif kind == "other_query":
            query = "another"
        elif kind == "expired":
            monkeypatch.setattr(pagination_module, "CURSOR_TTL", -1)
            cursor = client.get("/api/search", params={"q": "test"}).json()["next_cursor"]
        else:
            client.app.state.search_service._cursors = CursorCodec()
        response = client.get("/api/search", params={"q": query, "cursor": cursor})
        assert response.status_code == 410
        assert len(calls) == 2


def test_nico_raw_count_advances_even_when_items_are_filtered():
    """壊れた項目を除外した後の表示数ではなく、元データ数でoffsetを進める。"""
    calls = []
    def handler(request):
        """ニコニコの初回6件中1件を不正にする。"""
        platform, start = request_position(request)
        calls.append((platform, start))
        payload = page_payload(platform, start, 6 if platform == "niconico" else 0, 12 if platform == "niconico" else 0)
        if platform == "niconico" and start == 0:
            payload["data"][0]["title"] = None
        return httpx.Response(200, json=payload)
    with client_for(handler) as client:
        first = client.get("/api/search", params={"q": "test"}).json()
        assert len(first["videos"]) == 5
        client.get("/api/search", params={"q": "test", "cursor": first["next_cursor"]})
        assert calls[-1] == ("niconico", 6)


def test_nico_offset_limit_is_explicit_and_not_requested_beyond_limit():
    """APIの上限位置を越えるカーソルを作らず、終了理由を画面へ返す。"""
    def handler(request):
        """上限位置からの6件を返す。"""
        platform, start = request_position(request)
        assert platform == "niconico" and start == NICO_MAX_OFFSET
        return httpx.Response(200, json=page_payload(platform, start, 6, 200000))
    with client_for(handler) as client:
        cursor = client.app.state.search_service._cursors.encode("test", {"youtube": None, "niconico": NICO_MAX_OFFSET}, ["niconico"])
        data = client.get("/api/search", params={"q": "test", "cursor": cursor}).json()
        assert data["next_cursor"] is None
        assert data["sources"]["niconico"]["end_reason"] == "offset_limit"
