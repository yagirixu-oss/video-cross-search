"""各サイトへの検索依頼・共通形式への変換・結果の統合を担当する。"""
import asyncio
import html
import re
import time
from collections import OrderedDict
from itertools import zip_longest
from typing import Literal

import httpx
from pydantic import BaseModel

from .models import Video

# ── 外部APIの接続先と、この授業制作での制限 ──────────────
YOUTUBE_ENDPOINT = "https://www.googleapis.com/youtube/v3/search"
NICONICO_ENDPOINT = "https://snapshot.search.nicovideo.jp/api/v2/snapshot/video/contents/search"
PER_SITE = 6
CACHE_SECONDS = 60
CACHE_ENTRIES = 128
REQUEST_DEADLINE = 10.0  # 接続待ちも含め、サイトごとの処理全体を打ち切る秒数。
Platform = Literal["youtube", "niconico"]


class SourceStatus(BaseModel):
    """サイトごとの結果。0件の成功と、検索できなかった失敗を区別する。"""
    status: Literal["ok", "unavailable", "error"]
    count: int = 0
    code: str | None = None
    message: str | None = None
    cached: bool = False


class SearchResponse(BaseModel):
    """画面へ返す検索結果。動画の形式は固定一覧と共通。"""
    query: str
    videos: list[Video]
    sources: dict[str, SourceStatus]
    partial: bool


class ProviderError(Exception):
    """外部の生エラーを、APIキーを含まない公開用のエラーへ置き換える。"""
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


# ── 外部データの整形 ─────────────────────────────────────
def thumbnail(value: object, platform: Platform) -> str:
    """使えるHTTP(S)画像URLだけを採用し、欠けた画像にはローカル画像を使う。"""
    if isinstance(value, str) and value.startswith(("https://", "http://")):
        return value
    return f"/static/{platform}-placeholder.svg"


def parse_youtube(payload: dict) -> list[Video]:
    """動画以外・ライブ中・配信予定を除外し、最大6本を共通形式にする。"""
    items = payload.get("items")
    if not isinstance(items, list):
        raise ProviderError("invalid_response", "YouTubeから正しい形式の結果を取得できませんでした。")
    videos = []
    seen = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        identifier, snippet = item.get("id"), item.get("snippet")
        if not isinstance(identifier, dict) or not isinstance(snippet, dict):
            continue
        video_id = identifier.get("videoId")
        if identifier.get("kind") != "youtube#video" or not isinstance(video_id, str):
            continue
        if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id) or video_id in seen:
            continue
        # noneは通常の投稿動画や終了済みの動画。ライブ・配信予定は表示しない。
        if snippet.get("liveBroadcastContent") != "none":
            continue
        title = snippet.get("title")
        if not isinstance(title, str) or not title.strip():
            continue
        images = snippet.get("thumbnails")
        images = images if isinstance(images, dict) else {}
        image = next((images[size].get("url") for size in ("high", "medium", "default")
                      if isinstance(images.get(size), dict) and images[size].get("url")), None)
        videos.append(Video(
            id=f"youtube:{video_id}", platform="youtube", title=html.unescape(title),
            thumbnail_url=thumbnail(image, "youtube"),
            video_url=f"https://www.youtube.com/watch?v={video_id}",
        ))
        seen.add(video_id)
        if len(videos) == PER_SITE:
            break
    return videos


def parse_niconico(payload: dict) -> list[Video]:
    """投稿動画の検索結果を共通形式にする。生放送APIは使用しない。"""
    meta, items = payload.get("meta"), payload.get("data")
    if not isinstance(meta, dict) or meta.get("status") != 200 or not isinstance(items, list):
        raise ProviderError("invalid_response", "ニコニコ動画から正しい形式の結果を取得できませんでした。")
    videos = []
    seen = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        video_id, title = item.get("contentId"), item.get("title")
        if not isinstance(video_id, str) or not re.fullmatch(r"(?:sm|so|nm)?[0-9]+", video_id):
            continue
        if video_id in seen or not isinstance(title, str) or not title.strip():
            continue
        videos.append(Video(
            id=f"niconico:{video_id}", platform="niconico", title=title,
            thumbnail_url=thumbnail(item.get("thumbnailUrl"), "niconico"),
            video_url=f"https://www.nicovideo.jp/watch/{video_id}",
        ))
        seen.add(video_id)
        if len(videos) == PER_SITE:
            break
    return videos


# ── 検索サービス ─────────────────────────────────────────
class SearchService:
    """同時検索・短期キャッシュ・サイトごとの待機を、サーバー1個の中で管理する。"""
    def __init__(self, client: httpx.AsyncClient, youtube_key: str):
        self.client = client
        self._youtube_key = youtube_key  # レスポンス・ログ・URLには含めない。
        self._locks = {name: asyncio.Lock() for name in ("youtube", "niconico")}
        self._cache = OrderedDict()
        self._cooldown = {"youtube": 0.0, "niconico": 0.0}
        self._nico_ready_at = 0.0

    async def search(self, query: str) -> SearchResponse:
        """【根幹】両サイトを並行して検索し、各サイト内の順序を保って交互に並べる。"""
        youtube, niconico = await asyncio.gather(
            self._run("youtube", query), self._run("niconico", query),
        )
        videos = [video for pair in zip_longest(youtube[0], niconico[0])
                  for video in pair if video is not None]
        sources = {"youtube": youtube[1], "niconico": niconico[1]}
        return SearchResponse(
            query=query, videos=videos, sources=sources,
            partial=any(source.status != "ok" for source in sources.values()),
        )

    async def _run(self, platform: Platform, query: str) -> tuple[list[Video], SourceStatus]:
        """サイトごとの失敗を閉じ込め、もう片方の成功を取り消さない。"""
        if platform == "youtube" and not self._youtube_key:
            return [], SourceStatus(status="unavailable", code="not_configured",
                message="YouTube検索はまだ設定されていません。ニコニコ動画のみ検索しています。")
        try:
            return await asyncio.wait_for(self._search_locked(platform, query), REQUEST_DEADLINE)
        except (asyncio.TimeoutError, httpx.TimeoutException):
            error = ProviderError("timeout", "検索先の応答が時間内に返りませんでした。少し待って再試行してください。")
        except httpx.RequestError:
            error = ProviderError("connection_error", "検索先に接続できませんでした。通信状態を確認してください。")
        except ProviderError as exc:
            error = exc
        # 外部の本文や例外文字列は返さない。秘密情報を画面へ流さないため。
        return [], SourceStatus(status="error", code=error.code, message=error.message)

    async def _search_locked(self, platform: Platform, query: str) -> tuple[list[Video], SourceStatus]:
        """同じサイトへの接続を直列化し、重複検索・混雑時の連続アクセスを防ぐ。"""
        async with self._locks[platform]:
            cache_key = (platform, query)
            now = time.monotonic()
            cached = self._cache.get(cache_key)
            if cached and now - cached[0] < CACHE_SECONDS:
                return cached[1], SourceStatus(status="ok", count=len(cached[1]), cached=True)
            if now < self._cooldown[platform]:
                raise ProviderError("cooldown", "検索先が混雑または利用制限中です。しばらく待って再試行してください。")
            if platform == "niconico":
                # 公式ガイドに従い、前回の応答時間以上の間隔を空ける。
                await asyncio.sleep(max(0, self._nico_ready_at - time.monotonic()))
            started = time.monotonic()
            try:
                payload = await self._fetch(platform, query)
            finally:
                if platform == "niconico":
                    elapsed = time.monotonic() - started
                    self._nico_ready_at = time.monotonic() + max(1.0, elapsed)
            videos = parse_youtube(payload) if platform == "youtube" else parse_niconico(payload)
            self._cache[cache_key] = (time.monotonic(), videos)
            self._cache.move_to_end(cache_key)
            while len(self._cache) > CACHE_ENTRIES:
                self._cache.popitem(last=False)
            return videos, SourceStatus(status="ok", count=len(videos))

    async def _fetch(self, platform: Platform, query: str) -> dict:
        """外部APIへGETを1回送る。自動リトライで利用枠を消費しない。"""
        if platform == "youtube":
            # APIキーはURLのクエリに載せず、GoogleのAPIキーヘッダーで送る。
            # ライブ等を除外しても6本を選びやすいよう候補を25件取得する。
            response = await self.client.get(YOUTUBE_ENDPOINT, params={
                "part": "snippet", "type": "video", "q": query, "maxResults": 25,
                "order": "relevance", "relevanceLanguage": "ja", "regionCode": "JP",
                "safeSearch": "moderate",
            }, headers={"X-Goog-Api-Key": self._youtube_key})
        else:
            response = await self.client.get(NICONICO_ENDPOINT, params={
                "q": query, "targets": "title,description,tags",
                "fields": "contentId,title,thumbnailUrl",
                "_sort": "-viewCounter", "_limit": PER_SITE,
                "_context": "video-cross-search-classroom",
            })
        if response.status_code == 503 and platform == "niconico":
            # ニコニコ公式の指定：503の後は5分以上待つ。
            self._cooldown[platform] = time.monotonic() + 300
            raise ProviderError("busy", "ニコニコ動画が混雑しています。5分後に再試行してください。")
        if response.status_code == 429:
            # Retry-After秒があれば尊重する。未指定は最低60秒待つ。
            retry_after = response.headers.get("Retry-After", "")
            seconds = int(retry_after) if retry_after.isdigit() else 60
            self._cooldown[platform] = time.monotonic() + max(60, seconds)
            raise ProviderError("rate_limited", "検索先の利用制限に達しました。時間を空けて再試行してください。")
        if response.status_code in (400, 401, 403) and platform == "youtube":
            reason = ""
            try:
                reason = response.json()["error"]["errors"][0]["reason"]
            except (ValueError, KeyError, IndexError, TypeError):
                pass
            if reason in ("quotaExceeded", "dailyLimitExceeded"):
                raise ProviderError("quota_exceeded", "YouTube検索の利用枠に達しました。利用枠の回復を待ってください。")
            raise ProviderError("configuration_error", "YouTubeに接続できません。APIキー・APIの有効化・キーの制限設定を確認してください。")
        if response.status_code == 400:
            raise ProviderError("query_rejected", "検索先がキーワードを受け付けませんでした。別の言葉で検索してください。")
        if not response.is_success:
            raise ProviderError("upstream_error", "検索先でエラーが発生しました。時間を空けて再試行してください。")
        try:
            payload = response.json()
        except ValueError:
            raise ProviderError("invalid_response", "検索先から正しい形式の結果を取得できませんでした。") from None
        if not isinstance(payload, dict):
            raise ProviderError("invalid_response", "検索先から正しい形式の結果を取得できませんでした。")
        return payload
