"""連續多日模擬：展示「真實 Shioaji 模式」每天實際會怎麼反應。

跟 dry-run 不同，這裡用一個『持久的』紙上帳戶（會記得持倉與現金），
一天一天往前跑，讓你看到：第1天鋪倉、第2天之後續抱、何時換股。
不需要 Shioaji API。

★ 省 API：資料『只抓一次』(PrefetchProvider) 後在本機重播，
  所以即使模擬 30 天，FinMind 也只呼叫約 (檔數×5+1) 次。

用法：
  python examples/simulate_days.py                       # 離線樣本資料、15 天
  python examples/simulate_days.py finmind 2330,2317,2891,2308,2303 20
"""
import os
import sys
from typing import Optional

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.broker.paper import PaperBroker
from src.data.base import DataProvider
from src.engine.trader import LiveTrader
from src import strategies

CASH = 50000
BUDGET = 10000
MAX_POS = 5


class PrefetchProvider(DataProvider):
    """把整段資料一次抓進記憶體，之後切片重播，避免逐日重複打 API。"""

    def __init__(self, inner: DataProvider, symbols, start, end):
        self._hist = {s: inner.history(s, start, end) for s in symbols}
        self._fund = {s: inner.fundamentals(s) for s in symbols}
        self._bench = inner.benchmark(start, end)

    def history(self, symbol, start, end):
        df = self._hist.get(symbol)
        if df is None or df.empty:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        return df.loc[start:end].copy()

    def fundamentals(self, symbol):
        return self._fund.get(symbol)

    def benchmark(self, start, end) -> Optional[pd.Series]:
        if self._bench is None:
            return None
        return self._bench.loc[start:end].copy()

    def universe(self):
        return list(self._hist)


def main():
    source = sys.argv[1] if len(sys.argv) > 1 else "sample"
    if source == "finmind":
        from main import _load_dotenv
        from src.data.finmind import FinMindProvider
        _load_dotenv()
        inner = FinMindProvider()
        symbols = sys.argv[2].split(",") if len(sys.argv) > 2 else ["2330", "2317", "2891", "2308", "2303"]
        n_days = int(sys.argv[3]) if len(sys.argv) > 3 else 20
        pf_start, end = "2022-06-01", "2026-06-30"
    else:
        from src.data.sample import SampleDataProvider
        inner = SampleDataProvider()
        symbols = inner.universe()
        n_days = int(sys.argv[2]) if len(sys.argv) > 2 else 15
        pf_start, end = "2023-01-01", "2025-12-31"

    print(f"一次抓取 {len(symbols)} 檔資料中（只打這一次 API）...")
    try:
        provider = PrefetchProvider(inner, symbols, pf_start, end)
    except Exception as e:
        print(f"\n抓取失敗：{e}\n若是 FinMind 額度上限，請等約 1 小時再試。")
        return

    broker = PaperBroker(cash=CASH, fee_discount=0.28)
    trader = LiveTrader(
        provider, broker, strategies.build("lynch"),
        position_budget=BUDGET, dry_run=False,
        regime_filter=True, max_positions=MAX_POS,
    )

    hist = provider.history(symbols[0], pf_start, end)
    dates = list(hist.index[-n_days:])

    print(f"初始資金 {CASH:,}　每檔上限 {BUDGET:,}　最多 {MAX_POS} 檔　策略 lynch+regime\n")
    prices = {}
    for i, d in enumerate(dates, 1):
        ds = d.strftime("%Y-%m-%d")
        plans = trader.scan(symbols, ds)

        prices = {}
        for s in symbols:
            sub = provider.history(s, pf_start, ds)
            if not sub.empty:
                prices[s] = float(sub["close"].iloc[-1])
        equity = broker.equity(prices)
        positions = broker.positions()

        print(f"=== 第 {i} 天 {ds} ===")
        if plans:
            for p in plans:
                tag = "🟢買" if p.action == "BUY" else "🔴賣"
                print(f"    {tag} {p.symbol} {p.shares} 股 @ {p.price:.1f}")
        else:
            print("    (無動作，續抱)")
        # 用半形空格、且把長字串拆行，避免終端機換行時數字黏在一起
        print(f"    現金 {broker.cash():>8,.0f}   總資產 {equity:>8,.0f}   持倉 {len(positions)} 檔")
        held = ", ".join(f"{p.symbol}x{p.shares}" for p in positions) or "無"
        print(f"    持股 {held}\n")

    if prices:
        print(f"模擬結束：總資產 {broker.equity(prices):,.0f}（起始 {CASH:,}）")


if __name__ == "__main__":
    main()
