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
from .pagination import CursorCodec, PLATFORMS, NICO_MAX_OFFSET

# ── 外部APIの接続先と、この授業制作での制限 ──────────────
YOUTUBE_ENDPOINT = "https://www.googleapis.com/youtube/v3/search"
NICONICO_ENDPOINT = "https://snapshot.search.nicovideo.jp/api/v2/snapshot/video/contents/search"
PER_SITE = 6
CACHE_SECONDS = 60
CACHE_ENTRIES = 128
REQUEST_DEADLINE = 10.0  # 接続待ちも含め、サイトごとの処理全体を打ち切る秒数。
Platform = Literal["youtube", "niconico"]
SearchTarget = Literal["all", "youtube", "niconico"]


class SourceStatus(BaseModel):
    """サイトごとの結果。0件の成功と、検索できなかった失敗を区別する。"""
    status: Literal["ok", "unavailable", "error", "skipped"]
    count: int = 0
    code: str | None = None
    message: str | None = None
    cached: bool = False
    selected: bool = True  # 検索対象外と、対象内だが終了済みのサイトを区別する。
    requested: bool = True  # 再試行で対象外だったサイトはFalse。
    has_more: bool = False
    end_reason: str | None = None


class SearchResponse(BaseModel):
    """画面へ返す検索結果。動画の形式は固定一覧と共通。"""
    query: str
    target: SearchTarget = "all"
    videos: list[Video]
    sources: dict[str, SourceStatus]
    partial: bool
    next_cursor: str | None = None
    retry_cursor: str | None = None


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
        self._cursors = CursorCodec()

    async def search(self, query: str, cursor: str | None = None, target: SearchTarget = "all") -> SearchResponse:
        """【根幹】続き位置から検索し、成功したサイトだけ次の位置へ進める。"""
        selected = list(PLATFORMS) if target == "all" else [target]
        positions, active = (
            self._cursors.decode(cursor, query, target) if cursor is not None
            else ({"youtube": "" if "youtube" in selected else None,
                   "niconico": 0 if "niconico" in selected else None}, selected)
        )
        results = await asyncio.gather(*(
            self._run(platform, query, positions[platform], platform in active, platform in selected)
            for platform in PLATFORMS
        ))
        # 失敗した位置は進めない。再試行で未取得の動画を飛ばさないため。
        sources = {}
        failed = []
        for platform, (videos, source, next_position) in zip(PLATFORMS, results):
            sources[platform] = source
            positions[platform] = next_position
            if source.status == "error":
                failed.append(platform)
        videos = [video for pair in zip_longest(results[0][0], results[1][0])
                  for video in pair if video is not None]
        remaining = [p for p in PLATFORMS if positions[p] is not None]
        return SearchResponse(
            query=query, target=target, videos=videos, sources=sources,
            partial=any(source.selected and source.status != "ok" for source in sources.values()),
            next_cursor=self._cursors.encode(query, positions, remaining, target),
            # 全体の続き位置を保存しつつ、失敗したサイトだけを再試行できる。
            retry_cursor=self._cursors.encode(query, positions, failed, target),
        )

    async def _run(self, platform: Platform, query: str, position, requested: bool, selected: bool = True):
        """終了したサイトは呼ばず、失敗は現在位置を保持したまま返す。"""
        # 【根幹】対象外はキー確認やキャッシュ参照より前に終了し、外部通信しない。
        if not selected:
            return [], SourceStatus(status="skipped", selected=False, requested=False), None
        if platform == "youtube" and not self._youtube_key:
            return [], SourceStatus(status="unavailable", code="not_configured",
                requested=requested,
                message="YouTube検索はまだ設定されていません。APIキーを設定してサーバーを再起動してください。"), None
        if not requested or position is None:
            return [], SourceStatus(status="ok", requested=False, has_more=position is not None), position
        try:
            return await asyncio.wait_for(self._search_locked(platform, query, position), REQUEST_DEADLINE)
        except (asyncio.TimeoutError, httpx.TimeoutException):
            error = ProviderError("timeout", "検索先の応答が時間内に返りませんでした。少し待って再試行してください。")
        except httpx.RequestError:
            error = ProviderError("connection_error", "検索先に接続できませんでした。通信状態を確認してください。")
        except ProviderError as exc:
            error = exc
        # 外部の本文や例外文字列は返さない。秘密情報を画面へ流さないため。
        return [], SourceStatus(status="error", code=error.code, message=error.message, has_more=True), position

    async def _search_locked(self, platform: Platform, query: str, position):
        """同じサイトへの接続を直列化し、重複検索・混雑時の連続アクセスを防ぐ。"""
        async with self._locks[platform]:
            cache_key = (platform, query, position)
            now = time.monotonic()
            cached = self._cache.get(cache_key)
            if cached and now - cached[0] < CACHE_SECONDS:
                return cached[1], SourceStatus(status="ok", count=len(cached[1]), cached=True,
                    has_more=cached[2] is not None, end_reason=cached[3]), cached[2]
            if now < self._cooldown[platform]:
                raise ProviderError("cooldown", "検索先が混雑または利用制限中です。しばらく待って再試行してください。")
            if platform == "niconico":
                # 公式ガイドに従い、前回の応答時間以上の間隔を空ける。
                await asyncio.sleep(max(0, self._nico_ready_at - time.monotonic()))
            started = time.monotonic()
            try:
                payload = await self._fetch(platform, query, position)
            finally:
                if platform == "niconico":
                    elapsed = time.monotonic() - started
                    self._nico_ready_at = time.monotonic() + max(1.0, elapsed)
            videos = parse_youtube(payload) if platform == "youtube" else parse_niconico(payload)
            next_position, end_reason = self._next_position(platform, payload, position)
            self._cache[cache_key] = (time.monotonic(), videos, next_position, end_reason)
            self._cache.move_to_end(cache_key)
            while len(self._cache) > CACHE_ENTRIES:
                self._cache.popitem(last=False)
            return videos, SourceStatus(status="ok", count=len(videos),
                has_more=next_position is not None, end_reason=end_reason), next_position

    def _next_position(self, platform: Platform, payload: dict, position):
        """表示後の件数ではなく、APIが返した元のページに基づき次の位置を求める。"""
        if platform == "youtube":
            token = payload.get("nextPageToken")
            if token is None or token == "":
                return None, "exhausted"
            if not isinstance(token, str) or len(token) > 1024 or token == position:
                raise ProviderError("invalid_response", "YouTubeの続き情報を取得できませんでした。")
            # フィルターで0本になっても、nextPageTokenがあれば次のページがある。
            return token, None
        raw_count = len(payload["data"])
        next_offset = position + raw_count
        total = payload["meta"].get("totalCount")
        if type(total) is not int or total < 0:
            raise ProviderError("invalid_response", "ニコニコ動画の件数を取得できませんでした。")
        if raw_count == 0 or next_offset >= total:
            return None, "exhausted"
        if next_offset > NICO_MAX_OFFSET:
            return None, "offset_limit"
        return next_offset, None

    async def _fetch(self, platform: Platform, query: str, position) -> dict:
        """外部APIへGETを1回送る。自動リトライで利用枠を消費しない。"""
        if platform == "youtube":
            # APIキーはURLのクエリに載せず、GoogleのAPIキーヘッダーで送る。
            # 1ページ6件だけを取得し、先のページはボタンが押されるまで取得しない。
            response = await self.client.get(YOUTUBE_ENDPOINT, params={
                "part": "snippet", "type": "video", "q": query, "maxResults": PER_SITE,
                "order": "relevance", "relevanceLanguage": "ja", "regionCode": "JP",
                "safeSearch": "moderate",
                **({"pageToken": position} if position else {}),
            }, headers={"X-Goog-Api-Key": self._youtube_key})
        else:
            response = await self.client.get(NICONICO_ENDPOINT, params={
                "q": query, "targets": "title,description,tags",
                "fields": "contentId,title,thumbnailUrl",
                "_sort": "-viewCounter", "_limit": PER_SITE, "_offset": position,
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
