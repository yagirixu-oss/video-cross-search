"""各サイトへの並び順指定、ページ継続、キャッシュの分離を検証する。"""
import httpx
import pytest

from test_search import client_for, isolated_key
from test_pagination import page_payload, request_position


@pytest.mark.parametrize("sort,yt,nico", [
    ("default", "relevance", "-viewCounter"),
    ("newest", "date", "-startTime"),
    ("views", "viewCount", "-viewCounter"),
])
def test_provider_order_is_kept_on_every_page(sort, yt, nico):
    """初回と続きの両方に正しい順序を送り、返却順をサイト内で維持する。"""
    calls = []
    def handler(request):
        """並び順を確認して、API側で並んだ動画を返す。"""
        platform, start = request_position(request)
        calls.append((platform, start))
        assert request.url.params["order" if platform == "youtube" else "_sort"] == (yt if platform == "youtube" else nico)
        return httpx.Response(200, json=page_payload(platform, start, 6, 12))
    with client_for(handler) as client:
        first = client.get("/api/search", params={"q": "test", "sort": sort}).json()
        assert first["sort"] == sort
        second = client.get("/api/search", params={"q": "test", "sort": sort, "cursor": first["next_cursor"]}).json()
        assert second["sort"] == sort and second["next_cursor"] is None
        assert [v["id"] for v in second["videos"] if v["platform"] == "youtube"] == [f"youtube:sample{i:05d}" for i in range(6, 12)]
        assert len(calls) == 4


def test_cache_and_cursor_are_bound_to_sort():
    """順序変更は新しい検索。別順序のキャッシュやカーソルを混用しない。"""
    orders = []
    def handler(request):
        """YouTubeのみの通信を記録する。"""
        platform, start = request_position(request)
        assert platform == "youtube"
        orders.append(request.url.params["order"])
        return httpx.Response(200, json=page_payload(platform, start, 6, 12))
    with client_for(handler) as client:
        base = {"q": "test", "target": "youtube"}
        default = client.get("/api/search", params=base).json()
        latest = client.get("/api/search", params={**base, "sort": "newest"}).json()
        assert not latest["sources"]["youtube"]["cached"]
        again = client.get("/api/search", params={**base, "sort": "newest"}).json()
        assert again["sources"]["youtube"]["cached"]
        rejected = client.get("/api/search", params={**base, "sort": "newest", "cursor": default["next_cursor"]})
        assert rejected.status_code == 410
        assert orders == ["relevance", "date"]


def test_retry_keeps_selected_sort():
    """再生数順で失敗したサイトを、同じ順序・同じ位置で再試行する。"""
    calls = []
    def handler(request):
        """YouTubeの初回のみ失敗させ、再試行時の順序を確認する。"""
        platform, start = request_position(request)
        calls.append((platform, start))
        assert request.url.params['order'] == 'viewCount'
        if len(calls) == 1:
            return httpx.Response(500)
        return httpx.Response(200, json=page_payload(platform, start, 6, 12))
    with client_for(handler) as client:
        params = {"q": "test", "target": "youtube", "sort": "views"}
        first = client.get('/api/search', params=params)
        assert first.status_code == 502
        second = client.get('/api/search', params={**params, 'cursor': first.json()['retry_cursor']})
        assert second.status_code == 200
        assert calls == [('youtube', 0), ('youtube', 0)]


@pytest.mark.parametrize("sort", ["", "date", "rating", "invalid"])
def test_invalid_sort_is_rejected_without_requests(sort):
    """画面が提供しない順序は422で返す。外部パラメーターを直接通さない。"""
    def handler(request):
        """呼ばれたら検証漏れ。"""
        pytest.fail("invalid sort reached provider")
    with client_for(handler) as client:
        assert client.get('/api/search', params={'q': 'test', 'sort': sort}).status_code == 422
