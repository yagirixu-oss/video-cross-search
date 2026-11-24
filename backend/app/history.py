"""個人の視聴履歴を取り込み、利用者別に保存する。動画の再生回数とは別の記録。"""
import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone, timedelta
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit, parse_qs

from fastapi import HTTPException, Request

MAX_IMPORT_BYTES = 10 * 1024 * 1024
MAX_EVENTS = 5000
TOKEN_PATTERN = re.compile(r"[a-f0-9]{64}")


# ── 本人のブラウザと日時・URLを確認する ─────────────────
def owner_from(request: Request) -> str:
    """【根幹】推測困難なブラウザ専用キーをハッシュ化し、別の人の履歴と分離する。"""
    token = request.headers.get("x-history-token", "")
    if not TOKEN_PATTERN.fullmatch(token):
        raise HTTPException(401, "履歴の利用をこのブラウザで開始してください。")
    return hashlib.sha256(token.encode()).hexdigest()


def history_time(value: object) -> str | None:
    """タイムゾーンのある日時をUTCへ統一する。未来の履歴は採用しない。"""
    if not isinstance(value, str):
        return None
    try:
        date = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if not date.tzinfo or date > datetime.now(timezone.utc) + timedelta(minutes=5):
            return None
        return date.astimezone(timezone.utc).isoformat()
    except ValueError:
        return None


def youtube_id(value: object) -> str | None:
    """Takeoutのリンクを検証する。外部サイトや検索履歴を動画と取り違えない。"""
    if not isinstance(value, str):
        return None
    try:
        url = urlsplit(value)
        if url.scheme != "https" or url.hostname not in {"www.youtube.com", "youtube.com", "m.youtube.com"}:
            return None
        if url.path != "/watch" or url.username or url.password or url.port:
            return None
        identifiers = parse_qs(url.query).get("v", [])
        identifier = identifiers[0] if len(identifiers) == 1 else ""
        return identifier if re.fullmatch(r"[A-Za-z0-9_-]{11}", identifier) else None
    except ValueError:
        return None


def parse_takeout(content: bytes) -> tuple[list[dict], int]:
    """Takeoutの視聴履歴JSONだけを読む。検索履歴、広告、削除済み動画は除外する。"""
    if len(content) > MAX_IMPORT_BYTES:
        raise HTTPException(413, "履歴JSONは10MB以内にしてください。ZIPは展開してから選びます。")
    try:
        raw = json.loads(content.decode("utf-8-sig"))
    except (ValueError, UnicodeError, RecursionError):
        raise HTTPException(422, "視聴履歴のJSONを選んでください。HTMLやZIPは取り込めません。") from None
    if not isinstance(raw, list) or len(raw) > 100000:
        raise HTTPException(422, "配列形式の視聴履歴JSON（10万件以内）を選んでください。")
    events = {}
    skipped = 0
    for item in raw:
        if not isinstance(item, dict):
            skipped += 1
            continue
        identifier = youtube_id(item.get("titleUrl"))
        watched_at = history_time(item.get("time"))
        title = item.get("title")
        # 広告の表示もTakeoutに混ざるため、視聴の好みとして使わない。
        details = item.get("details", [])
        is_ad = any(isinstance(d, dict) and re.search(r"広告|advert|Google Ads", str(d.get("name", "")), re.I)
                    for d in details) if isinstance(details, list) else False
        if not identifier or not watched_at or not isinstance(title, str) or not title.strip() or is_ad:
            skipped += 1
            continue
        title = re.sub(r"^Watched\s+|\s*を視聴しました$|\s*を視聴$", "", title).strip()[:500]
        author_id = ""
        author_name = ""
        subtitles = item.get("subtitles", [])
        for author in subtitles if isinstance(subtitles, list) else []:
            if not isinstance(author, dict):
                continue
            author_name = str(author.get("name", ""))[:200]
            match = re.fullmatch(r"https://(?:www\.)?youtube\.com/channel/(UC[A-Za-z0-9_-]{22})", str(author.get("url", "")))
            author_id = match[1] if match else ""
            break
        event = {"video_id": "youtube:" + identifier, "platform": "youtube", "title": title,
                 "author_id": author_id, "author_name": author_name, "watched_at": watched_at,
                 "source": "takeout", "seconds": None}
        key = (event["video_id"], watched_at)
        if key in events:
            skipped += 1
        events[key] = event
    ordered = sorted(events.values(), key=lambda item: item["watched_at"], reverse=True)
    skipped += max(0, len(ordered) - MAX_EVENTS)
    if not ordered:
        raise HTTPException(422, "有効なYouTube視聴履歴が見つかりません。検索履歴ではなく視聴履歴JSONを選んでください。")
    return ordered[:MAX_EVENTS], skipped


# ── SQLite：履歴を再起動後も残す ───────────────────────
class HistoryStore:
    """ブラウザごとに最大5000件。SQLには値を直接連結せず、プレースホルダーを使う。"""

    def __init__(self, path: Path):
        """保存先だけを保持する。通常検索では履歴DBを作らない。"""
        self.path = path

    @contextmanager
    def connect(self):
        """DBを用意し、辞書のように列名で読める接続を作る。"""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("""CREATE TABLE IF NOT EXISTS history (
            owner TEXT NOT NULL, video_id TEXT NOT NULL, platform TEXT NOT NULL,
            title TEXT NOT NULL, author_id TEXT NOT NULL, author_name TEXT NOT NULL,
            watched_at TEXT NOT NULL, source TEXT NOT NULL, seconds REAL,
            stored_at TEXT NOT NULL, PRIMARY KEY(owner, video_id, watched_at))""")
        connection.execute("CREATE INDEX IF NOT EXISTS history_owner_time ON history(owner, watched_at DESC)")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def add(self, owner: str, events: list[dict]) -> dict:
        """【根幹】同じファイルを再取込しても、動画ID＋視聴日時が同じ履歴は増やさない。"""
        with self.connect() as connection:
            return self._insert(connection, owner, events)

    # ── カードクリックの重複抑制 ──────────────────────
    def add_open(self, owner: str, event: dict) -> dict:
        """複数タブから同時に押しても、同じ利用者・動画の直近60秒は1件にする。"""
        with self.connect() as connection:
            # 【根幹】確認と保存の間に別の処理が割り込まないよう、書込を予約する。
            connection.execute("BEGIN IMMEDIATE")
            now = datetime.now(timezone.utc)
            cutoff = (now - timedelta(seconds=60)).isoformat()
            recent = connection.execute("""SELECT 1 FROM history
                WHERE owner=? AND video_id=? AND source='click' AND stored_at>=? LIMIT 1""",
                (owner, event["video_id"], cutoff)).fetchone()
            if recent:
                return {"added": 0, "duplicates": 1, "limit": MAX_EVENTS}
            record = {**event, "source": "click", "seconds": None, "watched_at": now.isoformat()}
            return self._insert(connection, owner, [record])

    def _insert(self, connection, owner: str, events: list[dict]) -> dict:
        """共通の保存処理。呼出元のトランザクション内で追加し、最新5000件を残す。"""
        now = datetime.now(timezone.utc).isoformat()
        before = connection.total_changes
        connection.executemany("""INSERT OR IGNORE INTO history
            (owner, video_id, platform, title, author_id, author_name, watched_at, source, seconds, stored_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", [
            (owner, e["video_id"], e["platform"], e["title"][:500], e.get("author_id") or "",
             e.get("author_name") or "", e["watched_at"], e["source"], e.get("seconds"), now)
            for e in events
        ])
        inserted = connection.total_changes - before
        connection.execute("""DELETE FROM history WHERE owner=? AND rowid NOT IN
            (SELECT rowid FROM history WHERE owner=? ORDER BY watched_at DESC LIMIT ?)""",
            (owner, owner, MAX_EVENTS))
        return {"added": inserted, "duplicates": len(events) - inserted, "limit": MAX_EVENTS}

    def read(self, owner: str, limit: int = MAX_EVENTS) -> list[dict]:
        """指定した本人の履歴だけを返す。アプリ取得情報は30日で削除する。"""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        with self.connect() as connection:
            connection.execute("DELETE FROM history WHERE owner=? AND source!='takeout' AND stored_at<?", (owner, cutoff))
            rows = connection.execute("""SELECT video_id, platform, title, author_id, author_name,
                watched_at, source, seconds FROM history WHERE owner=? ORDER BY watched_at DESC LIMIT ?""",
                (owner, limit)).fetchall()
        return [dict(row) for row in rows]

    def clear(self, owner: str) -> None:
        """アプリ内の本人の履歴を削除する。YouTube本体の履歴には触れない。"""
        with self.connect() as connection:
            connection.execute("DELETE FROM history WHERE owner=?", (owner,))
