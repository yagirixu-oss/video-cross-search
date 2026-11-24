"""履歴を使う検索だけを補正する。各サイトの順位や再生数と、独自の補正を混同しない。"""
import re
import secrets
import time
import unicodedata
from collections import Counter, OrderedDict
from datetime import datetime, timezone
from itertools import zip_longest

from .pagination import CursorError, CURSOR_TTL


# ── 履歴から話題・投稿者の傾向をまとめる ────────────────
def terms(text: str) -> set[str]:
    """英数字は単語、日本語は2～3文字のまとまりにする。外部AIへ履歴を送らない。"""
    normalized = unicodedata.normalize("NFKC", text).lower()
    words = set(re.findall(r"[a-z][a-z0-9]{1,24}", normalized))
    for run in re.findall(r"[一-龥ぁ-ゖァ-ヺー]+", normalized):
        for size in (2, 3):
            words.update(run[i:i + size] for i in range(len(run) - size + 1))
    return words - {"youtube", "video", "official", "shorts", "動画", "視聴", "ました", "して", "です"}


def preferences(events: list[dict], query: str) -> dict:
    """新しい履歴ほど重くし、同じ動画の連続再生による極端な偏りを抑える。"""
    topics, authors = Counter(), Counter()
    seen = set()
    now = datetime.now(timezone.utc)
    ignored = terms(query)
    for event in events[:1000]:
        if event["video_id"] in seen:
            continue
        seen.add(event["video_id"])
        age = max(0, (now - datetime.fromisoformat(event["watched_at"])).total_seconds() / 86400)
        weight = 1 / (1 + age / 90)
        for topic in terms(event["title"]) - ignored:
            topics[topic] += weight
        if event["author_id"]:
            authors[event["platform"] + ":" + event["author_id"]] += weight
    return {"topics": dict(topics.most_common(100)), "authors": dict(authors.most_common(100)), "count": len(seen)}


def rerank(videos, preference: dict):
    """【根幹】同じ検索で取得した候補内だけを補正し、サイト間の交互表示を保つ。"""
    groups, reasons = [], {}
    topic_total = max(1, sum(preference["topics"].values()))
    author_max = max([1, *preference["authors"].values()])
    for platform in ("youtube", "niconico"):
        candidates = [video for video in videos if video.platform == platform]
        scored = []
        for index, video in enumerate(candidates):
            author = preference["authors"].get(platform + ":" + (video.author_id or ""), 0) / author_max
            topic = min(1, sum(preference["topics"].get(word, 0) for word in terms(video.title)) / topic_total * 4)
            # 元の関連順を基本に、本人の履歴との一致を小さく加点する。
            # 再生数や高評価数を独自の「人気点」に変換する処理ではない。
            score = 1 / (index + 4) + 0.20 * author + 0.12 * topic
            scored.append((score, index, video))
            if author:
                reasons[video.id] = "アプリの履歴補正：履歴にある投稿者"
            elif topic >= 0.15:
                reasons[video.id] = "アプリの履歴補正：履歴の話題に近い"
        groups.append([entry[2] for entry in sorted(scored, key=lambda entry: (-entry[0], entry[1]))])
    return [video for pair in zip_longest(*groups) for video in pair if video], reasons


# ── 続きでも最初の好み・条件を固定する ─────────────────
class PersonalSearch:
    """個人の条件をURLに入れず、短期のランダムな続き番号でサーバー内に保持する。"""

    def __init__(self, search, history):
        """通常検索と保存機能を組み合わせる。個人の順位を共通キャッシュに保存しない。"""
        self.search = search
        self.history = history
        self.pages = OrderedDict()
        self.generations = {}

    def forget(self, owner: str):
        """履歴削除後は、保存していた好みの検索条件も使えなくする。"""
        self.pages = OrderedDict((key, value) for key, value in self.pages.items() if value["owner"] != owner)
        self.generations[owner] = self.generations.get(owner, 0) + 1

    def remember(self, context: dict, cursor: str | None):
        """30分間だけ有効な続き情報を発行し、メモリ使用量を256件に制限する。"""
        if cursor is None:
            return None
        key = "personal." + secrets.token_urlsafe(32)
        self.pages[key] = {**context, "cursor": cursor, "expires": time.monotonic() + CURSOR_TTL}
        while len(self.pages) > 256:
            self.pages.popitem(last=False)
        return key

    async def run(self, owner, query, cursor, target, sort, period, youtube_format="all"):
        """履歴ONの検索を実行。日時順・再生数順を指定した場合は補正しない。"""
        condition = (query, target, sort, period, youtube_format)
        if cursor:
            context = self.pages.get(cursor)
            if not context or context["owner"] != owner or context["condition"] != condition or context["expires"] <= time.monotonic():
                raise CursorError("履歴を使う検索の続きが無効です。同じ条件で検索し直してください。")
        else:
            context = {"owner": owner, "condition": condition, "cursor": None,
                       "generation": self.generations.get(owner, 0),
                       "preference": preferences(self.history.read(owner), query)}
        enabled = sort == "default" and context["preference"]["count"] > 0
        # 種類で絞った候補の中で補正する。好みの点数だけを理由に動画を捨てない。
        result = await self.search.search(query, context["cursor"], target, sort, period,
                                          page_size=18 if enabled else 6, youtube_format=youtube_format)
        # 外部APIを待つ間に削除された場合も、古い好みを続き情報へ戻さない。
        if context["generation"] != self.generations.get(owner, 0):
            raise CursorError("検索中に履歴が削除されました。検索し直してください。")
        result = result.model_copy(deep=True)
        if enabled:
            result.videos, result.preference_reasons = rerank(result.videos, context["preference"])
        result.personalized = enabled
        result.personalization_note = (
            "このアプリの履歴を使い、取得した候補内の順番を調整しています。YouTube公式の個人向け順位ではありません。"
            if enabled else "履歴の補正は標準順で使えます。今回は指定の並び順を優先しています。"
            if sort != "default" else "履歴がまだないため、標準の順番で表示しています。"
        )
        result.next_cursor = self.remember(context, result.next_cursor)
        result.retry_cursor = self.remember(context, result.retry_cursor)
        return result
