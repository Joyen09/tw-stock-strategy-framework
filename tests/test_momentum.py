"""momentum 短線動能策略測試。"""
import pandas as pd

from src.models import Action, Position
from src.strategies import build
from src.strategies.base import StrategyContext


def _df(closes, vols):
    n = len(closes)
    idx = pd.date_range(end="2026-06-30", periods=n, freq="D")
    return pd.DataFrame(
        {"open": closes, "high": closes, "low": closes, "close": closes, "volume": vols},
        index=idx,
    )


def test_buy_on_volume_breakout_uptrend():
    # 穩定上升 (均線多頭、價在均線上)，最後一根帶量創近 20 日新高 -> 買進
    closes = [100 + i * 0.5 for i in range(60)]
    vols = [1000] * 59 + [2000]
    ctx = StrategyContext(symbol="X", prices=_df(closes, vols), position=None)
    sig = build("momentum").evaluate(ctx)
    assert sig.action == Action.BUY
    assert sig.strength > 0


def test_no_buy_without_volume():
    # 一樣創高但沒帶量 -> 不進場
    closes = [100 + i * 0.5 for i in range(60)]
    vols = [1000] * 60
    ctx = StrategyContext(symbol="X", prices=_df(closes, vols), position=None)
    sig = build("momentum").evaluate(ctx)
    assert sig.action == Action.HOLD


def test_stop_loss_sells():
    # 持有中、現價跌破進場價 5% -> 停損賣出
    closes = [100 + i * 0.5 for i in range(59)] + [100.0]
    vols = [1000] * 60
    ctx = StrategyContext(
        symbol="X", prices=_df(closes, vols),
        position=Position(symbol="X", shares=100, avg_price=110.0),  # 100 <= 110*0.95
    )
    sig = build("momentum").evaluate(ctx)
    assert sig.action == Action.SELL
    assert "停損" in sig.reason


def test_insufficient_bars_holds():
    closes = [100.0] * 30  # < min_bars(60)
    ctx = StrategyContext(symbol="X", prices=_df(closes, [1000] * 30), position=None)
    sig = build("momentum").evaluate(ctx)
    assert sig.action == Action.HOLD
