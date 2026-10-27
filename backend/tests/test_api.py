"""外部接続なしで、フロントとの約束と起動時の検証を確認する。"""
import json

import pytest
from fastapi.testclient import TestClient

from app.main import BACKEND_DIR, create_app


# ── 一覧・画像・API仕様 ───────────────────────────────────
def test_catalog_and_assets():
    """12本が交互に並び、仮動画に偽リンクがなく、画像を取得できること。"""
    with TestClient(create_app()) as client:
        response = client.get("/api/videos")
        assert response.status_code == 200
        videos = response.json()["videos"]
        assert len(videos) == 12
        assert [v["platform"] for v in videos] == ["youtube", "niconico"] * 6
        assert len({v["id"] for v in videos}) == 12
        for video in videos:
            assert set(video) == {"id", "platform", "title", "thumbnail_url", "video_url"}
            assert video["video_url"] is None
            image = client.get(video["thumbnail_url"])
            assert image.status_code == 200
            assert "image/svg+xml" in image.headers["content-type"]
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
