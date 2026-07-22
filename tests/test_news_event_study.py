"""新聞事件研究的純函式測試 (不打 API)。"""
import pandas as pd

from tools.news_event_study import (
    baseline_returns,
    detect_events,
    forward_returns,
    roll_to_trading_days,
)


def _trading_days(n=100):
    return pd.bdate_range("2024-01-01", periods=n)


def test_weekend_news_rolls_to_monday():
    days = pd.bdate_range("2024-01-01", periods=10)  # 週一起算
    # 週六 1/6、週日 1/7 的新聞 → 都算到週一 1/8
    news = pd.Series(pd.to_datetime(["2024-01-06", "2024-01-07", "2024-01-08"]))
    counts = roll_to_trading_days(news, days)
    assert counts[pd.Timestamp("2024-01-08")] == 3
    assert counts.sum() == 3


def test_detect_events_spike_only():
    days = _trading_days(100)
    counts = pd.Series(1, index=days)          # 平常每天 1 則
    counts.iloc[80] = 10                        # 第 80 天暴增
    ev = detect_events(counts, window=60, k=2.0, min_count=3)
    assert bool(ev.iloc[80]) is True
    assert ev.sum() == 1                        # 平常日不觸發


def test_detect_events_needs_history():
    days = _trading_days(10)                    # 不足 min_periods=20
    counts = pd.Series(5, index=days)
    ev = detect_events(counts, window=60)
    assert ev.sum() == 0                        # 沒有歷史基準就不判定事件


def test_forward_returns_entry_next_day():
    days = _trading_days(30)
    close = pd.Series(100.0, index=days)
    close.iloc[11] = 110.0                      # 事件隔天收盤 (進場價)
    close.iloc[12] = 121.0                      # 進場後 1 日 +10%
    fr = forward_returns(close, pd.DatetimeIndex([days[10]]), horizons=(1,))
    assert len(fr) == 1
    assert abs(fr["h1"].iloc[0] - 0.10) < 1e-9  # 以 t+1 進場價起算，不含事件日跳空


def test_forward_returns_skips_tail_events():
    days = _trading_days(10)
    close = pd.Series(100.0, index=days)
    fr = forward_returns(close, pd.DatetimeIndex([days[-1]]), horizons=(5,))
    assert fr.empty                             # 最後一天的事件沒有隔天可進場


def test_baseline_returns_flat_price_is_zero():
    close = pd.Series(50.0, index=_trading_days(40))
    base = baseline_returns(close, horizons=(1, 5))
    assert abs(base["h1"]) < 1e-12
    assert abs(base["h5"]) < 1e-12
