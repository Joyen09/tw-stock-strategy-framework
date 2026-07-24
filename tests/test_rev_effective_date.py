"""effective_date 規則測試（monthly-revenue-overlay-spec §2.3 / §7）。

涵蓋：10 日規則、週末順延、假日順延（合成日曆）、extra_lag_days、
超出日曆（尚未生效→None）、latest_effective_month。
真實案例已用日曆核對：
  - 2024-02-10（資料月 2024-01 的截止日）是週六
  - 2025-08-10（資料月 2025-07）是週日
  - 2025-10-10（資料月 2025-09）是週五但為國慶日（假日順延案例）
"""
import pandas as pd
import pytest

from src.rev_dates import (
    WEEKEND_ONLY,
    announce_deadline,
    defer_to_trading_day,
    effective_date,
    latest_effective_month,
)


def biz_calendar(start: str, end: str, holidays=()) -> pd.DatetimeIndex:
    """合成交易日曆：週一~週五扣掉指定假日。"""
    days = pd.bdate_range(start, end)
    hol = pd.DatetimeIndex([pd.Timestamp(h) for h in holidays])
    return days.difference(hol)


# ---------- announce_deadline：次月 10 日 ----------

def test_deadline_is_10th_of_next_month():
    assert announce_deadline(2024, 3) == pd.Timestamp("2024-04-10")

def test_deadline_year_rollover():
    # 12 月營收 → 隔年 1 月 10 日
    assert announce_deadline(2023, 12) == pd.Timestamp("2024-01-10")

def test_deadline_custom_day():
    assert announce_deadline(2024, 3, announce_day=15) == pd.Timestamp("2024-04-15")

def test_deadline_bad_month_raises():
    with pytest.raises(ValueError):
        announce_deadline(2024, 13)


# ---------- 週末 / 假日順延 ----------

def test_weekday_deadline_no_deferral():
    # 2024-04-10 是週三，交易日 → 不順延
    cal = biz_calendar("2024-01-01", "2024-12-31")
    assert effective_date(2024, 3, trading_days=cal) == pd.Timestamp("2024-04-10")

def test_weekend_deferral_saturday():
    # 資料月 2024-01 → 截止 2024-02-10（週六）→ 下一交易日 2024-02-12（週一）
    cal = biz_calendar("2024-01-01", "2024-12-31")
    assert effective_date(2024, 1, trading_days=cal) == pd.Timestamp("2024-02-12")

def test_weekend_deferral_sunday():
    # 資料月 2025-07 → 截止 2025-08-10（週日）→ 2025-08-11（週一）
    cal = biz_calendar("2025-01-01", "2025-12-31")
    assert effective_date(2025, 7, trading_days=cal) == pd.Timestamp("2025-08-11")

def test_holiday_deferral():
    # 資料月 2025-09 → 截止 2025-10-10（週五、國慶日休市）→ 2025-10-13（週一）
    cal = biz_calendar("2025-01-01", "2025-12-31", holidays=["2025-10-10"])
    assert effective_date(2025, 9, trading_days=cal) == pd.Timestamp("2025-10-13")

def test_long_holiday_deferral():
    # 週六截止 + 週一二連假（模擬春節式長假）→ 順延到週三
    cal = biz_calendar("2024-01-01", "2024-12-31",
                       holidays=["2024-02-12", "2024-02-13"])
    assert effective_date(2024, 1, trading_days=cal) == pd.Timestamp("2024-02-14")

def test_weekend_fallback_requires_explicit_sentinel():
    # 週末退路必須「明示」opt-in（WEEKEND_ONLY），只順延週末
    assert effective_date(2024, 1, trading_days=WEEKEND_ONLY) == pd.Timestamp("2024-02-12")
    assert effective_date(2025, 7, trading_days=WEEKEND_ONLY) == pd.Timestamp("2025-08-11")
    assert effective_date(2024, 3, trading_days=WEEKEND_ONLY) == pd.Timestamp("2024-04-10")


def test_none_calendar_raises():
    # 忘記帶日曆必須是錯誤，不能靜默退化成週末粗估（那是系統性前視）
    with pytest.raises(ValueError):
        effective_date(2024, 1)
    with pytest.raises(ValueError):
        defer_to_trading_day(pd.Timestamp("2024-04-10"), None)
    with pytest.raises(ValueError):
        latest_effective_month(pd.Timestamp("2024-04-15"))


def test_unknown_sentinel_raises():
    with pytest.raises(ValueError):
        effective_date(2024, 1, trading_days="business_days")


def test_empty_calendar_fail_closed():
    # 空日曆 = 零日曆知識 → 一律 None（fail-closed），不得退化成週末猜測
    empty = pd.DatetimeIndex([])
    assert effective_date(2024, 1, trading_days=empty) is None
    assert defer_to_trading_day(pd.Timestamp("2024-04-10"), empty) is None
    assert latest_effective_month(pd.Timestamp("2024-04-15"), trading_days=empty) is None


# ---------- extra_lag_days ----------

def test_extra_lag_trading_days():
    # 平日截止 2024-04-10（週三）+2 交易日 → 2024-04-12（週五）
    cal = biz_calendar("2024-01-01", "2024-12-31")
    assert effective_date(2024, 3, extra_lag_days=2, trading_days=cal) \
        == pd.Timestamp("2024-04-12")

def test_extra_lag_crosses_weekend():
    # 週五 +1 交易日 → 跳過週末到週一（2024-04-12 週五 → 2024-04-15 週一）
    cal = biz_calendar("2024-01-01", "2024-12-31")
    assert defer_to_trading_day(pd.Timestamp("2024-04-12"), cal, extra_lag_days=1) \
        == pd.Timestamp("2024-04-15")

def test_extra_lag_weekend_fallback():
    # 週末退路：2024-04-11（週四）+2 → 跳過週末 → 2024-04-15（週一）
    assert defer_to_trading_day(pd.Timestamp("2024-04-11"), WEEKEND_ONLY, extra_lag_days=2) \
        == pd.Timestamp("2024-04-15")


# ---------- 超出日曆 = 尚未生效（fail-closed）----------

def test_beyond_calendar_returns_none():
    cal = biz_calendar("2024-01-01", "2024-04-09")  # 日曆停在截止日前一天
    assert effective_date(2024, 3, trading_days=cal) is None

def test_extra_lag_beyond_calendar_returns_none():
    cal = biz_calendar("2024-01-01", "2024-04-10")
    assert effective_date(2024, 3, extra_lag_days=1, trading_days=cal) is None


# ---------- latest_effective_month ----------

def test_latest_effective_month_after_announce():
    # 2024-04-15：4/10（週三）已過 → 最新生效資料月 = 2024-03
    cal = biz_calendar("2024-01-01", "2024-12-31")
    assert latest_effective_month(pd.Timestamp("2024-04-15"), trading_days=cal) \
        == pd.Period("2024-03", freq="M")

def test_latest_effective_month_before_announce():
    # 2024-04-09：3 月營收尚未生效 → 最新 = 2024-02
    cal = biz_calendar("2024-01-01", "2024-12-31")
    assert latest_effective_month(pd.Timestamp("2024-04-09"), trading_days=cal) \
        == pd.Period("2024-02", freq="M")

def test_latest_effective_month_on_announce_day():
    # 生效日當天（含）就算生效
    cal = biz_calendar("2024-01-01", "2024-12-31")
    assert latest_effective_month(pd.Timestamp("2024-04-10"), trading_days=cal) \
        == pd.Period("2024-03", freq="M")

def test_latest_effective_month_deferral_boundary():
    # 截止 2024-02-10（週六）順延到 2/12：
    # 2/11（週日）評估 → 1 月營收「還看不到」→ 最新 = 2023-12
    cal = biz_calendar("2023-01-01", "2024-12-31")
    assert latest_effective_month(pd.Timestamp("2024-02-11"), trading_days=cal) \
        == pd.Period("2023-12", freq="M")
    # 2/12（週一）評估 → 1 月營收生效
    assert latest_effective_month(pd.Timestamp("2024-02-12"), trading_days=cal) \
        == pd.Period("2024-01", freq="M")
