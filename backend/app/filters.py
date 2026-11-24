"""投稿期間を両サイトで共通の日時範囲に変換する。"""
from datetime import datetime, timedelta, timezone
from typing import Literal

SearchPeriod = Literal["all", "week", "month", "year"]
PERIOD_DAYS = {"week": 7, "month": 30, "year": 365}


def publication_window(period: SearchPeriod) -> tuple[str | None, str | None]:
    """【根幹】初回だけ期間を計算する。続きでは署名済みの同じ日時を使う。"""
    if period == "all":
        return None, None
    # 同じ分の検索はキャッシュを共有できる。暦月ではなく経過日数で数える。
    end = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    start = end - timedelta(days=PERIOD_DAYS[period])
    return start.isoformat(), end.isoformat()
