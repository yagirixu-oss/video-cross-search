"""外部接続なしで、フロントとの約束と起動時の検証を確認する。"""
import json

import pytest
from fastapi.testclient import TestClient

from app.main import BACKEND_DIR, Video, create_app


# ── 一覧・画像・API仕様 ───────────────────────────────────
def test_catalog_and_assets():
    """実動画12本が交互に並び、リンクのIDが一致すること。外部通信は行わない。"""
    with TestClient(create_app()) as client:
        response = client.get("/api/videos")
        assert response.status_code == 200
        videos = response.json()["videos"]
        assert len(videos) == 12
        assert [v["platform"] for v in videos] == ["youtube", "niconico"] * 6
        assert len({v["id"] for v in videos}) == 12
        for video in videos:
            assert set(video) == {"id", "platform", "title", "thumbnail_url", "video_url",
                                  "published_at", "view_count", "author_name", "author_id", "metadata_fetched_at"}
            assert video["video_url"].startswith("https://www.")
            assert video["thumbnail_url"].startswith("https://")
            Video.model_validate(video)
        # 画面・ローカル代替画像は外部サイトの稼働状態に依存せず確認する。
        for path in ["/", "/ui/app.js", "/ui/styles.css", "/favicon.ico", "/static/youtube-placeholder.svg"]:
            assert client.get(path).status_code == 200
        assert client.get("/health").json() == {"status": "ok"}
        assert "/api/videos" in client.get("/openapi.json").json()["paths"]
        assert client.get("/docs").status_code == 200
        assert client.post("/api/videos").status_code == 405


# ── フロント接続設定 ─────────────────────────────────────
def test_cors(monkeypatch):
    """指定した画面のOriginだけに、ブラウザ用の許可ヘッダーを返すこと。"""
    monkeypatch.setenv("FRONTEND_ORIGINS", "http://localhost:5500")
    with TestClient(create_app()) as client:
        allowed = client.get("/api/videos", headers={"Origin": "http://localhost:5500"})
        assert allowed.headers["access-control-allow-origin"] == "http://localhost:5500"
        denied = client.get("/api/videos", headers={"Origin": "https://example.org"})
        assert "access-control-allow-origin" not in denied.headers
        preflight = client.options("/api/videos", headers={
            "Origin": "http://localhost:5500", "Access-Control-Request-Method": "GET"})
        assert preflight.status_code == 200


# ── 壊れた固定データの検知 ────────────────────────────────
@pytest.mark.parametrize("problem", ["duplicate", "platform", "invalid_json", "missing"])
def test_bad_data_fails_startup(tmp_path, problem):
    """データの問題を成功レスポンスで隠さず、起動エラーにすること。"""
    path = tmp_path / "videos.json"
    data = json.loads((BACKEND_DIR / "data" / "videos.json").read_text(encoding="utf-8"))
    if problem == "duplicate":
        data["videos"][1]["id"] = data["videos"][0]["id"]
    elif problem == "platform":
        data["videos"][0]["platform"] = "unknown"
    if problem != "missing":
        path.write_text("{" if problem == "invalid_json" else json.dumps(data), encoding="utf-8")
    with pytest.raises((ValueError, FileNotFoundError)):
        with TestClient(create_app(path)):
            pass


# ── 実動画リンクの取り違え防止 ──────────────────────────
@pytest.mark.parametrize("changes", [
    {"video_url": "https://example.com/watch?v=aqz-KE-bpKQ"},
    {"platform": "niconico"},
    {"id": "youtube:wrong-id"},
    {"video_url": "http://www.youtube.com/watch?v=aqz-KE-bpKQ"},
    {"video_url": "https://www.youtube.com/watch?v=aqz-KE-bpKQ&v=another"},
])
def test_mismatched_video_link_is_rejected(changes):
    """リンク先・投稿元・IDが不一致なら、誤ったカードを表示する前に拒否する。"""
    video = json.loads((BACKEND_DIR / "data/videos.json").read_text(encoding="utf-8"))["videos"][0]
    video.update(changes)
    with pytest.raises(ValueError):
        Video.model_validate(video)


def test_placeholder_catalog_still_works():
    """ネットワークが使えないときの表示確認用データも引き続き使えること。"""
    with TestClient(create_app(BACKEND_DIR / "data/videos.sample.json")) as client:
        videos = client.get("/api/videos").json()["videos"]
        assert len(videos) == 12
        assert all(v["video_url"] is None for v in videos)
