"""動画の追加情報を補完する。検索成功と追加情報の取得失敗は分けて扱う。"""
import asyncio
from datetime import datetime, timezone
from xml.etree import ElementTree

import httpx

from .models import Video

YOUTUBE_VIDEOS_ENDPOINT = "https://www.googleapis.com/youtube/v3/videos"
NICO_THUMB_ENDPOINT = "https://ext.nicovideo.jp/api/getthumbinfo/"
METADATA_WARNING = "一部の追加情報を取得できませんでした。動画は表示しています。"


# ── 外部の値を安全に共通形式へ変換 ─────────────────────
def text_or_none(value: object) -> str | None:
    """空文字・配列などを表示名として採用しない。"""
    return value.strip() if isinstance(value, str) and value.strip() else None


def count_or_none(value: object) -> int | None:
    """本当の0回と未取得を区別する。負数・小数・boolは採用しない。"""
    if type(value) is int and value >= 0:
        return value
    if isinstance(value, str) and value.isascii() and value.isdigit() and len(value) <= 20:
        return int(value)
    return None


def date_or_none(value: object) -> datetime | None:
    """タイムゾーン付きの公開日時だけを採用する。壊れた任意項目で検索を止めない。"""
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo is not None else None
    except ValueError:
        return None


# ── 【根幹】不足項目を取得しても、検索順と動画IDは変えない ──
class MetadataService:
    """YouTubeはページ内のIDを一括取得。ニコニコの名前取得は同時2接続まで。"""

    def __init__(self, client: httpx.AsyncClient, youtube_key: str):
        """HTTP接続は検索サービスと共有する。キーはGoogle宛てだけに送る。"""
        self.client = client
        self._youtube_key = youtube_key
        self._nico_slots = asyncio.Semaphore(2)

    async def enrich(self, platform: str, videos: list[Video]) -> str | None:
        """追加取得は全体で最大4秒。失敗した値はnull/ID表示にして動画を残す。"""
        if not videos:
            return None
        try:
            if platform == "youtube":
                await asyncio.wait_for(self._youtube(videos), timeout=4.0)
            else:
                await asyncio.wait_for(
                    asyncio.gather(*(self._niconico_name(video) for video in videos)),
                    timeout=4.0,
                )
        except (httpx.HTTPError, ValueError, TypeError, asyncio.TimeoutError):
            # 生レスポンス・例外本文に秘密情報が含まれる可能性があるため返さない。
            return METADATA_WARNING
        missing = any(v.author_name is None or v.view_count is None or v.published_at is None for v in videos)
        return METADATA_WARNING if missing else None

    async def _youtube(self, videos: list[Video]) -> None:
        """検索APIにない再生数をvideos.listでまとめて取得し、IDで突き合わせる。"""
        ids = [video.id.split(":", 1)[1] for video in videos]
        response = await self.client.get(
            YOUTUBE_VIDEOS_ENDPOINT,
            params={"part": "snippet,statistics", "id": ",".join(ids)},
            headers={"X-Goog-Api-Key": self._youtube_key},
            timeout=3.0,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
            raise ValueError("Invalid metadata response")
        by_id = {item["id"]: item for item in payload["items"]
                 if isinstance(item, dict) and isinstance(item.get("id"), str)}
        for video, video_id in zip(videos, ids):
            item = by_id.get(video_id)
            if item is None:
                continue
            snippet = item.get("snippet")
            stats = item.get("statistics")
            if isinstance(snippet, dict):
                video.author_name = text_or_none(snippet.get("channelTitle")) or video.author_name
                video.author_id = text_or_none(snippet.get("channelId")) or video.author_id
                video.published_at = date_or_none(snippet.get("publishedAt")) or video.published_at
            if isinstance(stats, dict):
                video.view_count = count_or_none(stats.get("viewCount"))
            video.metadata_fetched_at = datetime.now(timezone.utc)

    async def _niconico_name(self, video: Video) -> None:
        """検索APIが返す投稿者IDを補う。補助APIの失敗時は元のIDを保持する。"""
        async with self._nico_slots:
            try:
                response = await self.client.get(
                    NICO_THUMB_ENDPOINT + video.id.split(":", 1)[1], timeout=1.0,
                )
                response.raise_for_status()
                # DTD/外部実体や過大なXMLは扱わない。ログイン情報は使用しない。
                content = response.content
                if len(content) > 200_000 or b"<!DOCTYPE" in content.upper() or b"<!ENTITY" in content.upper():
                    return
                root = ElementTree.fromstring(content)
                if root.get("status") != "ok":
                    return
                if root.findtext("./thumb/video_id") != video.id.split(":", 1)[1]:
                    return
                video.author_name = text_or_none(root.findtext("./thumb/user_nickname")) or text_or_none(root.findtext("./thumb/ch_name"))
                # 固定一覧の補完にも使える。不足分だけ埋め、検索APIの数値は維持する。
                if video.published_at is None:
                    video.published_at = date_or_none(root.findtext("./thumb/first_retrieve"))
                if video.view_count is None:
                    video.view_count = count_or_none(root.findtext("./thumb/view_counter"))
                video.metadata_fetched_at = datetime.now(timezone.utc)
            except (httpx.HTTPError, ElementTree.ParseError, ValueError):
                return
