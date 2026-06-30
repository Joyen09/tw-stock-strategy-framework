"""連續多日模擬：展示「真實 Shioaji 模式」每天實際會怎麼反應。

跟 dry-run 不同，這裡用一個『持久的』紙上帳戶（會記得持倉與現金），
一天一天往前跑，讓你看到：第1天鋪倉、第2天之後續抱、何時換股。
不需要 Shioaji API。

用法：
  python examples/simulate_days.py                       # 離線樣本資料、15 天
  python examples/simulate_days.py finmind 2330,2317,2891,2308,2303,2886 12
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.broker.paper import PaperBroker
from src.engine.trader import LiveTrader
from src import strategies

CASH = 50000
BUDGET = 10000
MAX_POS = 5


def main():
    source = sys.argv[1] if len(sys.argv) > 1 else "sample"
    if source == "finmind":
        from main import _load_dotenv
        from src.data.finmind import FinMindProvider
        _load_dotenv()
        provider = FinMindProvider()
        symbols = sys.argv[2].split(",") if len(sys.argv) > 2 else ["2330", "2317", "2891", "2308", "2303", "2886"]
        n_days = int(sys.argv[3]) if len(sys.argv) > 3 else 12
        start, end = "2024-06-01", "2026-06-30"
    else:
        from src.data.sample import SampleDataProvider
        provider = SampleDataProvider()
        symbols = provider.universe()
        n_days = int(sys.argv[2]) if len(sys.argv) > 2 else 15
        start, end = "2024-01-01", "2025-12-31"

    broker = PaperBroker(cash=CASH, fee_discount=0.28)
    trader = LiveTrader(
        provider, broker, strategies.build("lynch"),
        position_budget=BUDGET, dry_run=False,
        regime_filter=True, max_positions=MAX_POS,
    )

    # 取最近 n_days 個交易日
    hist = provider.history(symbols[0], start, end)
    dates = list(hist.index[-n_days:])

    print(f"初始資金 {CASH:,}　每檔上限 {BUDGET:,}　最多 {MAX_POS} 檔　策略 lynch+regime\n")
    for i, d in enumerate(dates, 1):
        ds = d.strftime("%Y-%m-%d")
        plans = trader.scan(symbols, ds)

        # 當日portfolio狀態
        prices = {}
        for s in symbols:
            sub = provider.history(s, start, ds)
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
            print("    （無動作，續抱）")
        held = "、".join(f"{p.symbol}x{p.shares}" for p in positions) or "無"
        print(f"    現金 {broker.cash():,.0f}　持倉 {len(positions)} 檔 [{held}]　總資產 {equity:,.0f}\n")

    print(f"模擬結束：總資產 {broker.equity(prices):,.0f}（起始 {CASH:,}）")


if __name__ == "__main__":
    main()
