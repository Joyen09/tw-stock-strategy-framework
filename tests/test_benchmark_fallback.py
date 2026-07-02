"""大盤基準備援測試：TAIEX 抓不到時，用選股池等權平均自建代理指數。"""
import pandas as pd

from src.engine.backtest import Backtester


def _px(closes):
    idx = pd.date_range("2023-01-01", periods=len(closes), freq="D")
    return pd.DataFrame(
        {"open": closes, "high": closes, "low": closes, "close": closes, "volume": 1000},
        index=idx,
    )


def test_synthetic_benchmark_equal_weights_and_normalizes():
    # 兩檔起點價差很大 (100 vs 50)，正規化後應等權：起點=1、同幅上漲 -> 指數同幅上漲
    data = {
        "A": _px([100 + i for i in range(250)]),
        "B": _px([50 + i * 0.5 for i in range(250)]),
    }
    bm = Backtester._synthetic_benchmark(data)
    assert bm is not None
    assert round(bm.iloc[0], 6) == 1.0            # 正規化到起點=1
    assert bm.iloc[-1] > bm.iloc[0]               # 多頭趨勢反映出來
    assert bm.isna().sum() == 0


def test_synthetic_benchmark_skips_empty_frames():
    data = {
        "A": _px([100 + i for i in range(250)]),
        "EMPTY": pd.DataFrame(columns=["open", "high", "low", "close", "volume"]),
    }
    bm = Backtester._synthetic_benchmark(data)
    assert bm is not None
    assert len(bm) == 250


def test_synthetic_benchmark_none_when_no_data():
    data = {"EMPTY": pd.DataFrame(columns=["open", "high", "low", "close", "volume"])}
    assert Backtester._synthetic_benchmark(data) is None


class _NoBenchProvider:
    """模擬 TAIEX 抓不到 (benchmark 回 None) 的 provider。"""

    def __init__(self, data):
        self._data = data

    def history(self, symbol, start, end):
        return self._data.get(symbol, pd.DataFrame(columns=["open", "high", "low", "close", "volume"]))

    def fundamentals(self, symbol):
        return None

    def benchmark(self, start, end):
        return None  # TAIEX 逾時/限額

    def universe(self):
        return list(self._data)


def test_regime_falls_back_to_synthetic_when_taiex_missing():
    # regime 開啟但 TAIEX 抓不到 -> 回測仍能跑完 (用備援基準)，不會爆掉
    data = {
        "A": _px([100 + i * 0.3 for i in range(300)]),
        "B": _px([80 + i * 0.2 for i in range(300)]),
    }
    from src.strategies import build

    bt = Backtester(_NoBenchProvider(data), initial_cash=100_000, regime_filter=True, warmup=210)
    r = bt.run(build("momentum"), ["A", "B"], "2023-01-01", "2023-12-31")
    assert not r.equity_curve.empty  # 有備援基準，regime 照常運作、回測完成
