"""検索の続き位置を、改ざん検出付きの文字列（カーソル）として受け渡す。"""
import base64
import hashlib
import hmac
import json
import secrets
import time

PLATFORMS = ("youtube", "niconico")
CURSOR_TTL = 1800  # 最後に受け取った続き情報は30分間有効。
NICO_MAX_OFFSET = 100000


class CursorError(ValueError):
    """古い・改ざんされた・別の検索語の続き情報を、外部通信前に拒否する。"""


class CursorCodec:
    """サーバー起動ごとの署名鍵を使う。キーや動画本体はカーソルに含めない。"""

    def __init__(self):
        """署名専用のランダム鍵を生成する。YouTubeのAPIキーとは別物。"""
        self._secret = secrets.token_bytes(32)

    def encode(self, query: str, positions: dict, active: list[str], target: str = "all", sort: str = "default") -> str | None:
        """【根幹】検索語・各サイトの位置・今回呼ぶサイトをまとめて署名する。"""
        if not active:
            return None
        payload = json.dumps({
            "q": query, "positions": positions, "active": active, "target": target, "sort": sort,
            "expires": int(time.time()) + CURSOR_TTL,
        }, ensure_ascii=True, separators=(",", ":")).encode()
        body = base64.urlsafe_b64encode(payload).decode().rstrip("=")
        signature = hmac.new(self._secret, body.encode(), hashlib.sha256).hexdigest()
        return f"{body}.{signature}"

    def decode(self, cursor: str, query: str, target: str = "all", sort: str = "default") -> tuple[dict, list[str]]:
        """署名・期限・検索語・位置の型を確認し、使える続き情報だけを返す。"""
        try:
            body, signature = cursor.split(".")
            expected = hmac.new(self._secret, body.encode("ascii"), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(expected, signature):
                raise ValueError
            data = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
            positions, active = data["positions"], data["active"]
            if data["q"] != query or data.get("target") != target or data.get("sort") != sort or data["expires"] <= time.time():
                raise ValueError
            if set(positions) != set(PLATFORMS) or not active:
                raise ValueError
            if not isinstance(active, list) or any(p not in PLATFORMS for p in active):
                raise ValueError
            youtube, nico = positions["youtube"], positions["niconico"]
            if youtube is not None and (not isinstance(youtube, str) or len(youtube) > 1024):
                raise ValueError
            if nico is not None and (type(nico) is not int or not 0 <= nico <= NICO_MAX_OFFSET):
                raise ValueError
            if any(positions[p] is None for p in active):
                raise ValueError
            return positions, active
        except (ValueError, TypeError, KeyError, UnicodeError):
            raise CursorError("続きの情報が無効、または期限切れです。同じキーワード・検索対象・並び順で検索し直してください。") from None
