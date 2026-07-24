"""月營收 effective_date 規則（monthly-revenue-overlay-spec §2.3）——全部純函數。

核心紀律（無未來函數的生死線）：
    資料月 M 的營收「屬於」M 月，但「可知」時點是次月公告日。
    所有下游計算（濾網判定、事件研究 t0）一律以本模組算出的 effective_date 為準。

規則：
    effective_date = 資料月次月的 announce_day 日（預設 10）
                     → 若非交易日，順延至下一個交易日
                     → 再往後加 extra_lag_days 個交易日（預設 0）

保守偏誤（刻意的）：多數公司實際在 5–10 日之間公告，統一用 10 日會「低估」
訊號時效——寧可低估、不可高估。若上游（FinMind）提供實際公告日欄位，
改用實際公告日呼叫 defer_to_trading_day()，順延規則不變。

交易日曆（fail-closed 設計）：
    trading_days 一律「必填」：
      - 傳實際交易日 DatetimeIndex（通常取自價格序列 index），可正確處理
        台股假日（春節、國慶…）。空日曆 = 零日曆知識 → 一律回 None（fail-closed），
        絕不靜默退化成猜測。
      - 或明示傳 WEEKEND_ONLY sentinel：只順延週末的粗估退路（無假日資訊，
        會比真實日曆「早」——有前視風險，僅限測試／人工粗估，正式流程禁用）。
      - 傳 None → 直接 raise。忘記帶日曆必須是錯誤，不能是靜默的前視。
"""
from __future__ import annotations

from typing import Optional, Union

import pandas as pd

# 明示的「只順延週末」退路（僅測試/粗估用；正式流程一律傳實際交易日曆）
WEEKEND_ONLY = "weekend_only"

Calendar = Union[pd.DatetimeIndex, str]


def announce_deadline(rev_year: int, rev_month: int, announce_day: int = 10) -> pd.Timestamp:
    """資料月 (rev_year, rev_month) 的公告截止日：次月的 announce_day 日（日曆日，未順延）。

    announce_day 超過次月天數時取次月月底（announce_day=10 永遠不會發生，防呆用）。
    """
    if not 1 <= rev_month <= 12:
        raise ValueError(f"rev_month 必須在 1..12，收到 {rev_month}")
    nxt = pd.Period(freq="M", year=rev_year, month=rev_month) + 1
    day = min(int(announce_day), nxt.days_in_month)
    return pd.Timestamp(year=nxt.year, month=nxt.month, day=day)


def defer_to_trading_day(
    d: pd.Timestamp,
    trading_days: Calendar,
    extra_lag_days: int = 0,
) -> Optional[pd.Timestamp]:
    """把日曆日 d 順延到「第一個 >= d 的交易日」，再往後加 extra_lag_days 個交易日。

    結果超出日曆末端（含空日曆）→ 回 None：在已知範圍內「尚未生效」，
    下游一律視為看不到資料——fail-closed。
    """
    d = pd.Timestamp(d).normalize()
    if trading_days is None:
        raise ValueError(
            "trading_days 必填：傳實際交易日曆（DatetimeIndex），"
            "或明示 rev_dates.WEEKEND_ONLY（只順延週末的粗估退路，僅測試用）"
        )
    if isinstance(trading_days, str):
        if trading_days != WEEKEND_ONLY:
            raise ValueError(f"未知的日曆 sentinel: {trading_days!r}")
        while d.weekday() >= 5:  # 週六=5、週日=6
            d += pd.Timedelta(days=1)
        for _ in range(int(extra_lag_days)):
            d += pd.Timedelta(days=1)
            while d.weekday() >= 5:
                d += pd.Timedelta(days=1)
        return d
    cal = pd.DatetimeIndex(trading_days).normalize()
    i = cal.searchsorted(d, side="left") + int(extra_lag_days)
    if i >= len(cal):  # 空日曆自然走到這裡（0 >= 0）→ fail-closed
        return None
    return cal[i]


def effective_date(
    rev_year: int,
    rev_month: int,
    announce_day: int = 10,
    extra_lag_days: int = 0,
    trading_days: Calendar = None,
) -> Optional[pd.Timestamp]:
    """資料月 (rev_year, rev_month) 的營收「最早可用」日（spec §2.3）。

    trading_days 必填（見模組說明）；回 None = 在已知交易日曆內尚未生效（fail-closed）。
    """
    return defer_to_trading_day(
        announce_deadline(rev_year, rev_month, announce_day),
        trading_days=trading_days,
        extra_lag_days=extra_lag_days,
    )


def latest_effective_month(
    asof: pd.Timestamp,
    announce_day: int = 10,
    extra_lag_days: int = 0,
    trading_days: Calendar = None,
) -> Optional[pd.Period]:
    """評估日 asof 當天「已生效」的最新資料月（effective_date <= asof 的最大 M）。

    用途：濾網取 latest（spec §3.2）。trading_days 必填（見模組說明）。
    從 asof 當月往前找，最多回看 3 個月（正常節奏下前 1~2 個月必已生效；
    找不到代表日曆不足/參數異常，回 None 讓上游 fail-closed）。
    """
    asof = pd.Timestamp(asof).normalize()
    p = pd.Period(asof, freq="M")
    for m in (p, p - 1, p - 2, p - 3):
        eff = effective_date(
            m.year, m.month, announce_day=announce_day,
            extra_lag_days=extra_lag_days, trading_days=trading_days,
        )
        if eff is not None and eff <= asof:
            return m
    return None
