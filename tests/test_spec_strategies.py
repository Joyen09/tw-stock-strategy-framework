"""spec 綠燈組策略測試：trust (投信認養 A) / floor (地板股 G) / raiho (雷浩斯 P)。"""
import pandas as pd

from src.models import Action, Fundamentals, Position
from src.strategies import build
from src.strategies.base import StrategyContext


def _df(closes, vols=None):
    n = len(closes)
    vols = vols or [2_000_000] * n
    idx = pd.date_range(end="2026-06-30", periods=n, freq="B")
    return pd.DataFrame(
        {"open": closes, "high": [c * 1.01 for c in closes],
         "low": [c * 0.99 for c in closes], "close": closes, "volume": vols},
        index=idx,
    )


# ---------- trust (投信認養) ----------

def _trust_chips(idx, days=3, trust=200_000, vol_ok=True):
    """近 days 天投信買超；trust 預設 20 萬股，搭配 200 萬股成交量 = 投量比 10%。"""
    d = idx[-days:]
    return pd.DataFrame({"trust_net": [trust] * days, "foreign_net": [0] * days}, index=d)


def test_trust_buys_on_streak_low_position():
    # 價格盤整在低檔 (相對 60 日區間下半部)，投信連買 3 天、投量比 10%
    closes = [100.0] * 30 + [95.0] * 30  # 後段壓低 → 現價在區間下緣
    df = _df(closes)
    chips = _trust_chips(df.index)
    ctx = StrategyContext(symbol="X", prices=df, chips=chips, position=None)
    sig = build("trust").evaluate(ctx)
    assert sig.action == Action.BUY
    assert "投信連買" in sig.reason


def test_trust_no_buy_at_range_top():
    closes = [95.0] * 30 + [100.0] * 29 + [104.0]  # 現價在區間頂端
    df = _df(closes)
    ctx = StrategyContext(symbol="X", prices=df, chips=_trust_chips(df.index), position=None)
    sig = build("trust").evaluate(ctx)
    assert sig.action == Action.HOLD
    assert "不追高" in sig.reason


def test_trust_no_buy_without_streak():
    closes = [100.0] * 30 + [95.0] * 30
    df = _df(closes)
    chips = _trust_chips(df.index)
    chips.iloc[-2, chips.columns.get_loc("trust_net")] = -50_000  # 中斷連買
    ctx = StrategyContext(symbol="X", prices=df, chips=chips, position=None)
    assert build("trust").evaluate(ctx).action == Action.HOLD


def test_trust_sells_when_trust_turns_seller():
    closes = [100.0] * 60
    df = _df(closes)
    chips = pd.DataFrame({"trust_net": [-100_000, -200_000, -100_000], "foreign_net": [0] * 3},
                         index=df.index[-3:])
    ctx = StrategyContext(symbol="X", prices=df, chips=chips,
                          position=Position(symbol="X", shares=100, avg_price=100.0))
    sig = build("trust").evaluate(ctx)
    assert sig.action == Action.SELL
    assert "轉賣超" in sig.reason


def test_trust_stop_loss():
    closes = [100.0] * 59 + [92.0]
    df = _df(closes)
    ctx = StrategyContext(symbol="X", prices=df, chips=_trust_chips(df.index),
                          position=Position(symbol="X", shares=100, avg_price=100.0))
    sig = build("trust").evaluate(ctx)
    assert sig.action == Action.SELL
    assert "停損" in sig.reason


# ---------- floor (地板股) ----------

def _floor_df(crash_pct=0.85, big_vol=True):
    """一年平穩 (乖離小) 後急殺：最後一根跌到 20MA 的 crash_pct 倍 + 爆量。"""
    closes = [100 + (i % 10) * 0.3 for i in range(299)]  # 平穩震盪 → 乖離分布窄
    closes.append(closes[-1] * crash_pct)                 # 急殺一根
    vols = [1_000_000] * 299 + [3_000_000 if big_vol else 1_000_000]
    return _df(closes, vols)


def test_floor_buys_on_crash_with_volume():
    df = _floor_df()
    ctx = StrategyContext(symbol="X", prices=df, position=None)
    sig = build("floor").evaluate(ctx)
    assert sig.action == Action.BUY
    assert "地板線" in sig.reason


def test_floor_no_buy_without_volume():
    df = _floor_df(big_vol=False)
    ctx = StrategyContext(symbol="X", prices=df, position=None)
    sig = build("floor").evaluate(ctx)
    assert sig.action == Action.HOLD
    assert "未爆量" in sig.reason


def test_floor_no_buy_in_normal_market():
    df = _floor_df(crash_pct=0.99)  # 小跌，沒到地板
    ctx = StrategyContext(symbol="X", prices=df, position=None)
    assert build("floor").evaluate(ctx).action == Action.HOLD


def test_floor_takes_profit_at_ma():
    closes = [100.0] * 299 + [101.0]  # 現價回到 20MA 之上
    df = _df(closes)
    ctx = StrategyContext(symbol="X", prices=df,
                          position=Position(symbol="X", shares=100, avg_price=95.0))
    sig = build("floor").evaluate(ctx)
    assert sig.action == Action.SELL
    assert "回月線" in sig.reason


def test_floor_stop_loss():
    df = _floor_df()
    price = df["close"].iloc[-1]
    ctx = StrategyContext(symbol="X", prices=df,
                          position=Position(symbol="X", shares=100, avg_price=price / 0.95))
    sig = build("floor").evaluate(ctx)
    assert sig.action == Action.SELL
    assert "停損" in sig.reason


# ---------- raiho (雷浩斯矩陣) ----------

def _fund(roe=20.0, fcf=1e9, pe=12.0):
    return Fundamentals(symbol="X", roe=roe, fcf=fcf, pe=pe)


def test_raiho_buys_a_grade_cheap_on_dip():
    closes = [100.0] * 60 + [88.0] * 60  # 回檔到區間下緣
    ctx = StrategyContext(symbol="X", prices=_df(closes), fundamentals=_fund(), position=None)
    sig = build("raiho").evaluate(ctx)
    assert sig.action == Action.BUY
    assert "A 級" in sig.reason


def test_raiho_rejects_low_roe():
    closes = [100.0] * 60 + [88.0] * 60
    ctx = StrategyContext(symbol="X", prices=_df(closes),
                          fundamentals=_fund(roe=12.0), position=None)
    assert build("raiho").evaluate(ctx).action == Action.HOLD


def test_raiho_rejects_negative_fcf():
    closes = [100.0] * 60 + [88.0] * 60
    ctx = StrategyContext(symbol="X", prices=_df(closes),
                          fundamentals=_fund(fcf=-1e8), position=None)
    assert build("raiho").evaluate(ctx).action == Action.HOLD


def test_raiho_rejects_expensive():
    closes = [100.0] * 60 + [88.0] * 60
    ctx = StrategyContext(symbol="X", prices=_df(closes),
                          fundamentals=_fund(pe=30.0), position=None)
    sig = build("raiho").evaluate(ctx)
    assert sig.action == Action.HOLD
    assert "不夠便宜" in sig.reason


def test_raiho_holds_through_price_swings():
    # 持有中股價跌 15% (< 20% 保險絲)、仍是 A 級 → 不因波動出場
    closes = [100.0] * 119 + [85.0]
    ctx = StrategyContext(symbol="X", prices=_df(closes), fundamentals=_fund(),
                          position=Position(symbol="X", shares=100, avg_price=100.0))
    sig = build("raiho").evaluate(ctx)
    assert sig.action == Action.HOLD
    assert "不因股價波動出場" in sig.reason


def test_raiho_sells_on_downgrade():
    closes = [100.0] * 120
    ctx = StrategyContext(symbol="X", prices=_df(closes),
                          fundamentals=_fund(roe=8.0),  # 降到 C 級
                          position=Position(symbol="X", shares=100, avg_price=100.0))
    sig = build("raiho").evaluate(ctx)
    assert sig.action == Action.SELL
    assert "降級" in sig.reason


def test_raiho_disaster_stop():
    closes = [100.0] * 119 + [75.0]  # -25% > 20% 保險絲
    ctx = StrategyContext(symbol="X", prices=_df(closes), fundamentals=_fund(),
                          position=Position(symbol="X", shares=100, avg_price=100.0))
    sig = build("raiho").evaluate(ctx)
    assert sig.action == Action.SELL
    assert "保險絲" in sig.reason
