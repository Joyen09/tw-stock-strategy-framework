"""mclean (麥克連法人跟單，策略 K) 測試。"""
import pandas as pd

from src.models import Action, Position
from src.strategies import build
from src.strategies.base import StrategyContext


def _prices(n=160, growth=1.006, last_vol=3000):
    """指數型上升趨勢：均線多頭、週線多頭、MACD 柱為正、KD 金叉、最後一根收漲帶量。"""
    closes = [100 * (growth ** i) for i in range(n)]
    closes[-1] *= 1.02  # 最後一根額外跳漲：等速上漲會讓 KD 的 K=D 收斂，加根突破棒使 K>D
    idx = pd.date_range(end="2026-06-30", periods=n, freq="B")  # 交易日頻率，讓週線 resample 正常
    df = pd.DataFrame({
        "open": [c * 0.995 for c in closes],
        "high": [c * 1.01 for c in closes],
        "low": [c * 0.985 for c in closes],
        "close": closes,
        "volume": [1000] * (n - 1) + [last_vol],
    }, index=idx)
    return df


def _chips(price_idx, trust=(500, 500), foreign=(1000, 1000)):
    """做兩天的籌碼資料，日期取價格倒數第 2、3 天 (模擬引擎已切掉當日)。"""
    days = price_idx[-3:-1]
    return pd.DataFrame({"trust_net": trust, "foreign_net": foreign}, index=days)


def test_red_mark_buys():
    df = _prices()
    chips = _chips(df.index)  # 投信+外資同買 → 紅色
    ctx = StrategyContext(symbol="X", prices=df, chips=chips, position=None)
    sig = build("mclean").evaluate(ctx)
    assert sig.action == Action.BUY
    assert "紅" in sig.reason
    assert sig.strength > 0.8


def test_blue_mark_weaker_than_red():
    df = _prices()
    chips = _chips(df.index, trust=(500, 500), foreign=(-100, -100))  # 只有投信連買 → 藍色
    ctx = StrategyContext(symbol="X", prices=df, chips=chips, position=None)
    sig = build("mclean").evaluate(ctx)
    assert sig.action == Action.BUY
    assert "藍" in sig.reason
    red = build("mclean").evaluate(
        StrategyContext(symbol="X", prices=df, chips=_chips(df.index), position=None))
    assert sig.strength < red.strength


def test_no_chip_mark_holds():
    df = _prices()
    chips = _chips(df.index, trust=(-500, 500), foreign=(-100, -100))  # 無紅無藍
    ctx = StrategyContext(symbol="X", prices=df, chips=chips, position=None)
    assert build("mclean").evaluate(ctx).action == Action.HOLD


def test_missing_chips_holds():
    ctx = StrategyContext(symbol="X", prices=_prices(), chips=None, position=None)
    assert build("mclean").evaluate(ctx).action == Action.HOLD


def test_stale_chips_holds():
    df = _prices()
    old_days = df.index[:2]  # 籌碼停在好幾個月前
    chips = pd.DataFrame({"trust_net": [500, 500], "foreign_net": [500, 500]}, index=old_days)
    ctx = StrategyContext(symbol="X", prices=df, chips=chips, position=None)
    sig = build("mclean").evaluate(ctx)
    assert sig.action == Action.HOLD
    assert "過舊" in sig.reason


def test_downtrend_no_buy_even_with_red_mark():
    n = 160
    closes = [200 * (0.997 ** i) for i in range(n)]  # 下降趨勢
    idx = pd.date_range(end="2026-06-30", periods=n, freq="B")
    df = pd.DataFrame({
        "open": closes, "high": [c * 1.01 for c in closes],
        "low": [c * 0.99 for c in closes], "close": closes,
        "volume": [1000] * n,
    }, index=idx)
    ctx = StrategyContext(symbol="X", prices=df, chips=_chips(df.index), position=None)
    assert build("mclean").evaluate(ctx).action == Action.HOLD


def test_profit_target_sells():
    df = _prices()
    price = df["close"].iloc[-1]
    ctx = StrategyContext(
        symbol="X", prices=df, chips=_chips(df.index),
        position=Position(symbol="X", shares=100, avg_price=price / 1.12),  # 已賺 12% > 10%
    )
    sig = build("mclean").evaluate(ctx)
    assert sig.action == Action.SELL
    assert "漲幅滿足" in sig.reason


def test_stop_loss_sells():
    df = _prices()
    price = df["close"].iloc[-1]
    ctx = StrategyContext(
        symbol="X", prices=df, chips=_chips(df.index),
        position=Position(symbol="X", shares=100, avg_price=price / 0.92),  # 虧 8% > 7%
    )
    sig = build("mclean").evaluate(ctx)
    assert sig.action == Action.SELL
    assert "停損" in sig.reason


def test_break_10ma_sells():
    df = _prices()
    # 最後一根跌破 10 日線 (壓低收盤)，但跌幅不足以觸發停損
    df.iloc[-1, df.columns.get_loc("close")] = df["close"].iloc[-11:-1].mean() * 0.97
    price = df["close"].iloc[-1]
    ctx = StrategyContext(
        symbol="X", prices=df, chips=_chips(df.index),
        position=Position(symbol="X", shares=100, avg_price=price),
    )
    sig = build("mclean").evaluate(ctx)
    assert sig.action == Action.SELL
    assert "日線" in sig.reason
