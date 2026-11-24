"""履歴の取込・削除・再生確認のHTTP窓口。履歴を他サイトへ転送しない。"""
import re
import secrets
import time
from collections import OrderedDict
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from .history import owner_from, parse_takeout, MAX_IMPORT_BYTES


class ManualWatch(BaseModel):
    """外部プレイヤーの視聴は本人が明示的に記録する。クリックだけでは登録しない。"""
    model_config = ConfigDict(extra="forbid")
    video_id: str = Field(pattern=r"^niconico:(?:sm|so|nm)?[0-9]+$", max_length=80)
    title: str = Field(min_length=1, max_length=500)
    author_id: str = Field(default="", max_length=200)
    author_name: str = Field(default="", max_length=200)


class PlaybackWatch(BaseModel):
    """再生開始時に発行した許可と、実際の再生中に数えた秒数を受け取る。"""
    model_config = ConfigDict(extra="forbid")
    permit: str = Field(min_length=20, max_length=100)
    seconds: float = Field(ge=10, le=86400, allow_inf_nan=False)


class CardOpen(BaseModel):
    """カードを開く操作の入力。再生済みとの偽装を避け、種別や秒数は受け取らない。"""
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    video_id: str = Field(pattern=r"^(?:youtube:[A-Za-z0-9_-]{11}|niconico:(?:sm|so|nm)?[0-9]+)$", max_length=80)
    title: str = Field(min_length=1, max_length=500)
    author_id: str = Field(default="", max_length=200)
    author_name: str = Field(default="", max_length=200)


def history_router() -> APIRouter:
    """アプリごとに履歴ルートと短期の再生許可を作る。"""
    router = APIRouter()
    permits = OrderedDict()

    @router.get("/api/history")
    def list_history(request: Request):
        """最大100件を表示する。補正用には保存済みの履歴から最新1000件まで使う。"""
        rows = request.app.state.history.read(owner_from(request))
        return {"total": len(rows), "events": rows[:100], "limit": 5000}

    @router.post("/api/history/import")
    async def import_history(request: Request):
        """【根幹】送信内容を上限付きで読み、本人の履歴へ重複を除いて追加する。"""
        owner = owner_from(request)
        content = bytearray()
        async for chunk in request.stream():
            content.extend(chunk)
            if len(content) > MAX_IMPORT_BYTES:
                raise HTTPException(413, "履歴JSONは10MB以内にしてください。")
        events, skipped = parse_takeout(bytes(content))
        result = request.app.state.history.add(owner, events)
        return {**result, "skipped": skipped, "total": len(request.app.state.history.read(owner))}

    @router.delete("/api/history")
    def delete_history(request: Request):
        """このアプリの履歴・好みの続き情報・未保存の再生許可を削除する。"""
        owner = owner_from(request)
        request.app.state.history.clear(owner)
        request.app.state.personal_search.forget(owner)
        for key in list(permits):
            if permits[key]["owner"] == owner:
                del permits[key]
        return {"deleted": True}

    @router.post("/api/history/manual")
    def manual_watch(payload: ManualWatch, request: Request):
        """ニコニコ動画を『見た』と本人が指定した記録を保存する。"""
        owner = owner_from(request)
        # 同じ分に連打しても同じ履歴を増やさない。
        event = {**payload.model_dump(), "platform": "niconico", "source": "manual",
                 "watched_at": datetime.now(timezone.utc).replace(second=0, microsecond=0).isoformat()}
        return request.app.state.history.add(owner, [event])

    # ── カード操作：元サイトで再生されたかは確認しない ───
    @router.post("/api/history/open")
    def record_card_open(payload: CardOpen, request: Request):
        """【根幹】開いた記録として保存し、60秒以内の連打は同じ1件にまとめる。"""
        owner = owner_from(request)
        event = {**payload.model_dump(), "platform": payload.video_id.split(":", 1)[0]}
        # 外部APIへの追加通信はしない。カードに表示済みの情報だけを利用する。
        return request.app.state.history.add_open(owner, event)

    @router.get("/api/playback/youtube/{video_id}")
    async def playback_details(video_id: str, request: Request):
        """埋込可否・子供向け指定を公式APIで確認する。確認できない動画は追跡しない。"""
        if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
            raise HTTPException(422, "動画IDが不正です。")
        owner = owner_from(request) if request.headers.get("x-history-token") else None
        service = request.app.state.search_service
        if not service._youtube_key:
            raise HTTPException(503, "YouTube APIキーが未設定です。元サイトで視聴してください。")
        try:
            response = await service.client.get("https://www.googleapis.com/youtube/v3/videos",
                params={"part": "snippet,status", "id": video_id},
                headers={"X-Goog-Api-Key": service._youtube_key}, timeout=6)
            response.raise_for_status()
            items = response.json().get("items", [])
            item = next((item for item in items if isinstance(item, dict) and item.get("id") == video_id), None)
            if not item:
                raise HTTPException(404, "動画の情報を確認できません。元サイトで視聴してください。")
            status, snippet = item.get("status", {}), item.get("snippet", {})
            if not isinstance(status, dict) or not isinstance(snippet, dict):
                raise ValueError
        except (httpx.HTTPError, ValueError, TypeError, AttributeError):
            raise HTTPException(502, "再生情報を取得できませんでした。元サイトで視聴できます。") from None
        allowed = status.get("madeForKids") is False and status.get("embeddable") is True
        permit = None
        if allowed and owner:
            permit = secrets.token_urlsafe(32)
            permits[permit] = {"owner": owner, "created": time.monotonic(), "video_id": "youtube:" + video_id,
                "platform": "youtube", "source": "player", "title": str(snippet.get("title", video_id))[:500],
                "author_id": str(snippet.get("channelId", ""))[:200],
                "author_name": str(snippet.get("channelTitle", ""))[:200],
                "watched_at": datetime.now(timezone.utc).isoformat()}
            while len(permits) > 256:
                permits.popitem(last=False)
        return {"embeddable": status.get("embeddable") is True, "tracking_allowed": allowed,
                "permit": permit, "notice": None if allowed else "この動画の再生履歴は自動記録しません。"}

    @router.post("/api/history/playback")
    def record_playback(payload: PlaybackWatch, request: Request):
        """再生中の10秒経過後に1件だけ保存する。別人の許可や期限切れは拒否する。"""
        owner = owner_from(request)
        permit = permits.get(payload.permit)
        if not permit or permit["owner"] != owner or time.monotonic() - permit["created"] > 1800:
            raise HTTPException(410, "再生の記録期限が切れました。動画を開き直してください。")
        if time.monotonic() - permit["created"] < 9:
            raise HTTPException(409, "再生開始から10秒経過後に記録できます。")
        return request.app.state.history.add(owner, [{**permit, "seconds": payload.seconds}])

    return router
