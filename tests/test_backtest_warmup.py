"""回測窗口必須整段都在交易（2026-09-15 發現的嚴重錯誤的回歸測試）。

舊版是 `if i < warmup: continue`——warmup 直接從回測窗口的頭 250 根扣掉，
於是「標示的期間」和「真正在交易的期間」不一樣：

  標示 2024-01-01~2025-12-31（505 根）→ 實際只交易 255 根，2024 整年是空的
  標示 2021-07-01~2022-12-31（378 根）→ 實際只交易最後 128 根（2022-07 之後），
    而那時大盤早已跌破年線、風向濾網全面禁止做多 → **空頭壓測 0 筆交易**

那個 0 筆一直被當成「這一關測不出東西」，其實是窗口被暖身吃掉了。
權益曲線也含著那 250 天不動的現金，把夏普一起壓低。

修法：暖身資料改成從 start「之前」另外抓，start 當天就開始交易。
"""
import pandas as pd
import pytest

from src.engine.backtest import Backtester
from src.models import Action, Signal
from src.strategies.base import Strategy


class SpyProvider:
    """記下每次被要求的日期區間，並回傳一段夠長的假價格。"""

    def __init__(self, first_bar="2018-01-01", last_bar="2026-12-31"):
        idx = pd.bdate_range(first_bar, last_bar)
        # 緩步上漲，讓「買了就抱著」不會觸發任何下跌類出場
        self.full = pd.DataFrame(
            {"open": 100.0, "high": 100.0, "low": 100.0,
             "close": [100.0 + i * 0.01 for i in range(len(idx))], "volume": 1000},
            index=idx,
        )
        self.history_calls = []

    def history(self, symbol, start, end):
        self.history_calls.append((start, end))
        return self.full.loc[start:end].copy()

    def fundamentals(self, symbol):
        return None

    def benchmark(self, start, end):
        return None

    def institutional(self, symbol, start, end):
        return None


class BuyAndHold(Strategy):
    """第一次看到就買，之後一直抱著——交易日期完全由引擎決定，方便驗證窗口。"""

    name = "buyhold"

    def evaluate(self, ctx):
        if ctx.position is None:
            return Signal(action=Action.BUY, strength=1.0, reason="test", symbol=ctx.symbol)
        return Signal(action=Action.HOLD, strength=0.0, reason="hold", symbol=ctx.symbol)


BT_START, BT_END = "2024-01-01", "2024-06-30"


def _run(provider, warmup=250, **kw):
    bt = Backtester(provider, initial_cash=1_000_000, warmup=warmup, **kw)
    return bt.run(BuyAndHold(), ["2330"], BT_START, BT_END)


def test_warmup_history_is_fetched_from_before_the_start():
    """暖身資料要另外抓，不能從回測期間裡扣。"""
    p = SpyProvider()
    _run(p)
    asked_start, asked_end = p.history_calls[0]
    assert pd.Timestamp(asked_start) < pd.Timestamp(BT_START)
    # 250 個交易日 ≈ 一年，多抓的量要夠算年線
    assert (pd.Timestamp(BT_START) - pd.Timestamp(asked_start)).days >= 330
    assert asked_end == BT_END


def test_trading_starts_on_the_first_day_of_the_labeled_window():
    """這就是 bug 的本體：以前第一筆交易要等 250 根之後。"""
    p = SpyProvider()
    r = _run(p)
    assert r.trades, "整段窗口都該有交易"
    assert r.trades[0].date == pd.Timestamp(BT_START)


def test_equity_curve_covers_exactly_the_labeled_window():
    """權益曲線不可含暖身期——那段是不動的現金，會把夏普一起壓低。"""
    p = SpyProvider()
    r = _run(p)
    expected = p.full.loc[BT_START:BT_END].index
    assert r.equity_curve.index[0] == expected[0]
    assert r.equity_curve.index[-1] == expected[-1]
    assert len(r.equity_curve) == len(expected)


def test_short_window_is_no_longer_vacuous():
    """6 個月的窗口遠短於 250 根暖身；舊版會 0 筆交易（空頭壓測就是死在這）。"""
    r = _run(SpyProvider())
    assert len(r.trades) > 0


def test_works_when_provider_has_no_data_before_start():
    """新上市股票（或樣本資料）沒有更早的歷史時，不可以炸掉。

    暖身不足時指標算不出來是策略自己的 min_bars/NaN 判斷要處理的事，
    引擎只負責「有多少餵多少」。
    """
    p = SpyProvider(first_bar=BT_START)
    r = _run(p)
    assert r.equity_curve.index[0] == pd.Timestamp(BT_START)
    assert r.total_return == pytest.approx(r.total_return)  # 沒有 NaN／沒有例外


def test_warmup_parameter_controls_how_much_extra_history():
    """warmup 現在的意思是「往前多抓幾根」，數字要真的有影響。"""
    small, big = SpyProvider(), SpyProvider()
    _run(small, warmup=60)
    _run(big, warmup=250)
    back_small = pd.Timestamp(BT_START) - pd.Timestamp(small.history_calls[0][0])
    back_big = pd.Timestamp(BT_START) - pd.Timestamp(big.history_calls[0][0])
    assert back_big > back_small
