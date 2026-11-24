"""実際の個人履歴を使わず、取込・本人分離・順位補正・再生記録の境界を検証する。"""
import asyncio
import json
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.history import HistoryStore, parse_takeout
from app.personalization import PersonalSearch
from app.pagination import CursorError
from app.search import SearchResponse
import app.main as main_module
import app.history_api as history_api

ALICE = {"X-History-Token": "a" * 64}
BOB = {"X-History-Token": "b" * 64}


def takeout_item(index=0, title="太鼓の達人 AC 全良", days=1):
    """公開形式に合わせた架空のTakeoutレコードを作る。実在する視聴者の情報は使わない。"""
    return {"header": "YouTube", "title": "Watched " + title,
            "titleUrl": f"https://www.youtube.com/watch?v=sample{index:05d}",
            "time": (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(),
            "subtitles": [{"name": "履歴の投稿者", "url": "https://www.youtube.com/channel/UC" + "a" * 22}]}


@pytest.fixture
def history_client(tmp_path, monkeypatch):
    """APIキー・履歴DB・外部通信をテスト専用に分離する。"""
    monkeypatch.setenv("YOUTUBE_API_KEY", "dummy-test-key")
    monkeypatch.setattr(main_module, "dotenv_values", lambda *args, **kwargs: {})
    calls = []
    def upstream(request):
        """順位は固定、履歴に合う動画は9番目に置き、補正の有無を比較できるようにする。"""
        calls.append(request)
        if request.url.path.endswith("/search"):
            size = int(request.url.params["maxResults"])
            start = int(request.url.params.get("pageToken", "0"))
            return httpx.Response(200, json={"nextPageToken": str(start + size), "items": [
                {"id": {"kind": "youtube#video", "videoId": f"sample{i:05d}"},
                 "snippet": {"title": "太鼓の達人 AC 全良" if i % 18 == 8 else "太鼓の達人 Wii U 初心者",
                             "channelId": "UC" + ("a" if i % 18 == 8 else "b") * 22,
                             "channelTitle": "投稿者", "liveBroadcastContent": "none"}}
                for i in range(start, start + size)]})
        if request.url.path.endswith("/videos"):
            playback = request.url.params["part"] == "snippet,status"
            return httpx.Response(200, json={"items": [
                {"id": identifier, "snippet": {"title": "太鼓の達人 AC 全良", "channelTitle": "投稿者",
                    "channelId": "UC" + ("a" if int(identifier[-5:]) % 18 == 8 else "b") * 22,
                    "publishedAt": "2026-01-01T00:00:00Z"}, "statistics": {"viewCount": "123"},
                    "status": {"embeddable": True, "madeForKids": False} if playback else {}}
                for identifier in request.url.params["id"].split(",")]})
        return httpx.Response(500)
    application = create_app(search_transport=httpx.MockTransport(upstream), history_path=tmp_path / "history.sqlite3")
    with TestClient(application) as client:
        yield client, calls


def import_sample(client, headers=ALICE):
    """履歴の取込窓口を通して検索補正用データを準備する。"""
    return client.post("/api/history/import", headers=headers, json=[takeout_item(100)])


# ── 取込：壊れた入力・重複・本人以外への漏出 ────────────
def test_import_deduplicates_and_isolates_users(history_client):
    """同じJSONの再送で履歴が増えず、別ブラウザの一覧・検索には混ざらない。"""
    client, _ = history_client
    item = takeout_item()
    first = client.post("/api/history/import", headers=ALICE, json=[item, item]).json()
    assert first["added"] == 1 and first["skipped"] == 1
    second = client.post("/api/history/import", headers=ALICE, json=[item]).json()
    assert second["added"] == 0 and second["duplicates"] == 1
    assert client.get("/api/history", headers=ALICE).json()["total"] == 1
    assert client.get("/api/history", headers=BOB).json()["total"] == 0
    assert client.get("/api/history").status_code == 401
    assert client.get("/api/history", headers=ALICE).headers["cache-control"] == "no-store"


@pytest.mark.parametrize("payload", [{}, [], [None], [{"titleUrl": "https://www.youtube.com/results?search_query=猫"}],
    [{**takeout_item(), "titleUrl": "https://www.youtube.com.evil.example/watch?v=sample00000"}],
    [{**takeout_item(), "time": "2099-01-01T00:00:00Z"}],
    [{**takeout_item(), "time": "2026-01-01T00:00:00"}],
    [{**takeout_item(), "details": [{"name": "Google Ads"}]}]])
def test_import_rejects_non_watch_data(history_client, payload):
    """検索履歴・広告・URL偽装・不正日時を視聴履歴として保存しない。"""
    client, calls = history_client
    assert client.post("/api/history/import", headers=ALICE, json=payload).status_code == 422
    assert client.get("/api/history", headers=ALICE).json()["total"] == 0
    assert not calls  # 取込だけでは、ユーザーの履歴をGoogleへ送らない。


def test_import_limits_and_html_are_rejected(history_client):
    """HTMLを実行せず拒否し、大きすぎる送信データは上限で止める。"""
    client, _ = history_client
    assert client.post("/api/history/import", headers=ALICE, content=b"<script>alert(1)</script>").status_code == 422
    assert client.post("/api/history/import", headers=ALICE, content=b" " * (10 * 1024 * 1024 + 1)).status_code == 413
    events, _ = parse_takeout(b"\xef\xbb\xbf" + json.dumps([takeout_item()]).encode())
    assert len(events) == 1


def test_store_persists_and_expired_app_metadata_is_removed(tmp_path):
    """再起動後も本人の取込履歴が残り、期限切れのアプリ取得情報は削除される。"""
    path = tmp_path / "persistent.sqlite3"
    events, _ = parse_takeout(json.dumps([takeout_item()]).encode())
    store = HistoryStore(path)
    store.add("alice", events)
    store.add("bob", [{**events[0], "source": "player"}])
    with store.connect() as connection:
        connection.execute("UPDATE history SET stored_at=?", ((datetime.now(timezone.utc) - timedelta(days=31)).isoformat(),))
    assert len(HistoryStore(path).read("alice")) == 1
    assert not store.read("bob")


# ── 補正：通常検索・指定順・続き・他人のキャッシュを保護 ──
def test_personal_search_changes_order_and_keeps_all_candidates(history_client):
    """9番目の好みに近い候補が上がる。取得した候補を切り捨てず、他人には影響しない。"""
    client, calls = history_client
    import_sample(client)
    params = {"q": "太鼓の達人", "target": "youtube"}
    normal = client.get("/api/search", params=params).json()
    personal = client.get("/api/search", params={**params, "personalized": True}, headers=ALICE).json()
    assert normal["videos"][0]["id"] == "youtube:sample00000"
    assert personal["videos"][0]["id"] == "youtube:sample00008"
    assert personal["personalized"] and len(personal["videos"]) == 18
    assert len({v["id"] for v in personal["videos"]}) == 18
    assert personal["next_cursor"].startswith("personal.")
    assert "履歴の投稿者" not in personal["next_cursor"]
    again = client.get("/api/search", params=params).json()
    assert [v["id"] for v in again["videos"]] == [v["id"] for v in normal["videos"]]
    assert all("x-history-token" not in request.headers for request in calls)


@pytest.mark.parametrize("sort", ["newest", "views"])
def test_explicit_sort_wins_over_preferences(history_client, sort):
    """利用者が選んだ日時順・再生数順は、履歴があっても並べ替えない。"""
    client, _ = history_client
    import_sample(client)
    data = client.get("/api/search", params={"q": "太鼓", "target": "youtube", "sort": sort,
                      "personalized": True}, headers=ALICE).json()
    assert not data["personalized"] and data["max_per_site"] == 6
    assert data["videos"][0]["id"] == "youtube:sample00000"


def test_personal_cursor_keeps_snapshot_and_rejects_other_users(history_client):
    """履歴追加でページ途中の好みを変えない。別人・別検索語・OFFへの流用は拒否する。"""
    client, _ = history_client
    import_sample(client)
    params = {"q": "太鼓", "target": "youtube", "personalized": True}
    first = client.get("/api/search", params=params, headers=ALICE).json()
    cursor = first["next_cursor"]
    assert client.get("/api/search", params={**params, "cursor": cursor}, headers=BOB).status_code == 410
    assert client.get("/api/search", params={**params, "q": "猫", "cursor": cursor}, headers=ALICE).status_code == 410
    assert client.get("/api/search", params={**params, "personalized": False, "cursor": cursor}).status_code == 410
    more = client.get("/api/search", params={**params, "cursor": cursor}, headers=ALICE).json()
    assert not ({v["id"] for v in first["videos"]} & {v["id"] for v in more["videos"]})
    assert len(more["videos"]) == 18
    client.delete("/api/history", headers=BOB)
    assert client.get("/api/history", headers=ALICE).json()["total"] == 1
    client.delete("/api/history", headers=ALICE)
    assert client.get("/api/search", params={**params, "cursor": cursor}, headers=ALICE).status_code == 410
    assert client.get("/api/history", headers=ALICE).json()["total"] == 0


def test_no_history_uses_normal_search(history_client):
    """履歴がない利用者に、補正したふりをしない。"""
    client, _ = history_client
    data = client.get("/api/search", params={"q": "太鼓", "target": "youtube", "personalized": True}, headers=BOB).json()
    assert not data["personalized"] and data["max_per_site"] == 6
    assert "履歴がまだない" in data["personalization_note"]


def test_deleting_history_cancels_inflight_personal_search():
    """外部検索を待つ間に履歴を削除しても、古い好みの続き情報が復活しない。"""
    async def scenario():
        """検索の待機位置を固定し、削除→応答の順番を再現する。"""
        started, resume = asyncio.Event(), asyncio.Event()

        class DelayedSearch:
            """外部APIの返答だけを任意のタイミングまで待たせる。"""

            async def search(self, *args, **kwargs):
                """削除が終わってから、次ページ付きの応答を返す。"""
                started.set()
                await resume.wait()
                return SearchResponse(query="太鼓", videos=[], sources={}, partial=False, next_cursor="upstream-next")

        history = SimpleNamespace(read=lambda owner: [])
        service = PersonalSearch(DelayedSearch(), history)
        pending = asyncio.create_task(service.run("alice", "太鼓", None, "youtube", "default", "all"))
        await started.wait()
        service.forget("alice")
        resume.set()
        with pytest.raises(CursorError, match="履歴が削除"):
            await pending
        assert not service.pages

    asyncio.run(scenario())


# ── 再生の確認：クリックだけ・別人・子供向けを除外 ────────
def test_playback_requires_permission_time_and_same_owner(history_client, monkeypatch):
    """10秒未満、別人の許可、削除後の保存を拒否し、再送でも履歴は1件にする。"""
    client, _ = history_client
    clock = [100.0]
    monkeypatch.setattr(history_api, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    details = client.get("/api/playback/youtube/sample00008", headers=ALICE).json()
    assert details["tracking_allowed"]
    payload = {"permit": details["permit"], "seconds": 10}
    assert client.post("/api/history/playback", headers=ALICE, json=payload).status_code == 409
    clock[0] += 12
    assert client.post("/api/history/playback", headers=BOB, json=payload).status_code == 410
    assert client.post("/api/history/playback", headers=ALICE, json={**payload, "seconds": 2}).status_code == 422
    assert client.post("/api/history/playback", headers=ALICE, json=payload).json()["added"] == 1
    assert client.post("/api/history/playback", headers=ALICE, json=payload).json()["added"] == 0
    client.delete("/api/history", headers=ALICE)
    assert client.post("/api/history/playback", headers=ALICE, json=payload).status_code == 410


@pytest.mark.parametrize("status", [{"embeddable": True, "madeForKids": True}, {"embeddable": True},
                                    {"embeddable": False, "madeForKids": False}])
def test_restricted_or_unknown_playback_never_gets_recording_permit(tmp_path, monkeypatch, status):
    """子供向け・確認不能・埋込禁止では再生記録の許可を発行しない。"""
    monkeypatch.setenv("YOUTUBE_API_KEY", "dummy-test-key")
    def upstream(request):
        """状態の異なる同じ動画を返し、取得失敗を許可と取り違えないか確認する。"""
        return httpx.Response(200, json={"items": [{"id": "sample00000", "status": status, "snippet": {"title": "test"}}]})
    with TestClient(create_app(history_path=tmp_path / "history.sqlite3", search_transport=httpx.MockTransport(upstream))) as client:
        data = client.get("/api/playback/youtube/sample00000", headers=ALICE).json()
        assert data["permit"] is None and not data["tracking_allowed"]


def test_manual_record_is_explicit_and_nico_only(history_client):
    """通常検索で履歴が増えず、手動記録はニコニコの正しいIDだけを受け付ける。"""
    client, _ = history_client
    client.get("/api/search", params={"q": "太鼓", "target": "youtube"}, headers=ALICE)
    assert client.get("/api/history", headers=ALICE).json()["total"] == 0
    assert client.post("/api/history/manual", headers=ALICE,
        json={"video_id": "youtube:sample00000", "title": "test"}).status_code == 422
    result = client.post("/api/history/manual", headers=ALICE,
        json={"video_id": "niconico:sm123", "title": "見た動画"})
    assert result.status_code == 200
    assert client.get("/api/history", headers=ALICE).json()["events"][0]["source"] == "manual"
