"""YouTubeの種類を推定する。公式のShorts判定ではなく、根拠と未判定を残す。"""
import html
import math
import re
import unicodedata
from typing import Literal

YouTubeFormat = Literal["all", "shorts", "regular", "unknown"]

# ── 外部APIの再生時間を秒へ変換 ──────────────────────────
def duration_seconds(value: object) -> float | None:
    """ISO 8601のD/H/M/Sを読む。欠落・不正・0秒を短い動画として扱わない。"""
    if not isinstance(value, str) or len(value) > 100:
        return None
    match = re.fullmatch(
        r"P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?)?", value
    )
    if not match or not any(match.groups()) or value.endswith("T"):
        return None
    days, hours, minutes, seconds = (float(part or 0) for part in match.groups())
    total = days * 86400 + hours * 3600 + minutes * 60 + seconds
    return total if math.isfinite(total) and total > 0 else None


# ── 【根幹】短いだけではショートと決めつけない ────────────
def classify_youtube(seconds: float | None, title: str, description: object = "") -> tuple[str, str]:
    """時間と明示的なハッシュタグを併用し、情報不足・矛盾は未判定にする。"""
    text = title + "\n" + (description if isinstance(description, str) else "")
    text = unicodedata.normalize("NFKC", html.unescape(text)).casefold()
    # shorts単体の語や#shortstoryなどを、Shortsのハッシュタグと取り違えない。
    marked = bool(re.search(r"(?<![\w#])#(?:shorts|youtubeshorts|ショート動画|ショート)(?!\w)", text))
    if seconds is None:
        return "unknown", "再生時間を取得できないため未判定"
    if seconds <= 180:
        if marked:
            return "shorts", "3分以内で、タイトルまたは説明欄にショートのハッシュタグあり"
        return "unknown", "3分以内ですが、ショートを示すハッシュタグを確認できません"
    # APIの再生時間には約1秒の差があり得るため、3分直後の境界は断定しない。
    if seconds <= 181:
        return "unknown", "3分の境界付近のため未判定"
    if marked:
        return "unknown", "3分を超えていますが、ショートのハッシュタグもあるため未判定"
    return "regular", "3分を超え、ショートのハッシュタグを確認できません"
