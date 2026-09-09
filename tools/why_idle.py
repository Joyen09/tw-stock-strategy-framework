#!/usr/bin/env python3
"""帳戶為什麼都沒在交易？逐一檢查五道關卡，指出真正卡在哪一關。

動機：2026-09 發現 lynch-mid100 從頭到尾 0 筆成交。查了半天才發現是
「--max-positions 2 而且已經持有 2 檔」——空位是 0，結構上就不可能買。
心跳訊息只會說「無交易訊號」，看不出是沒訊號還是根本輪不到評估訊號，
所以每次都得翻程式碼才能回答。這支把那個推理過程固定下來。

買進要通過的五道關卡（順序與 src/engine/trader.py scan() 完全一致）：
  1. 暫停      runtime.json 的 paused（Telegram /pause 忘了解除）
  2. 大盤濾網  --regime 開著且加權指數跌破年線 → 本輪禁止做多
  3. 策略訊號  策略對這檔沒有買訊號
  4. 冷卻期    剛賣出未滿 N 個交易日，不追回
  5. 空位      持倉已達 --max-positions，slots=0
另外也檢查「手上這幾檔為什麼不賣」——賣出不受上面 2/4/5 限制。

會打 FinMind API（要跑策略就得抓價），配額吃緊時建議收盤後再跑。

用法（在 VM 的 ~/stock）：
    .venv/bin/python tools/why_idle.py --strategy lynch --universe mid100 \
        --paper-file paper_lynch_mid100.json --regime --max-positions 2 --budget 10000
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import strategies                                   # noqa: E402
from src.broker.persistent_paper import PersistentPaperBroker  # noqa: E402
from src.engine.trader import LiveTrader                     # noqa: E402
from src.models import Action                                # noqa: E402
from src.strategies.base import StrategyContext              # noqa: E402


def _root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _provider(source: str):
    if source == "finmind":
        from src.data.cache import DiskCachingProvider
        from src.data.finmind import FinMindProvider
        return DiskCachingProvider(FinMindProvider())
    from src.data.sample import SampleDataProvider
    return SampleDataProvider()


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="診斷模擬帳戶為什麼沒有交易")
    p.add_argument("--strategy", default="lynch")
    p.add_argument("--universe", default="mid100")
    p.add_argument("--symbols", default="", help="逗號分隔；留空用 --universe")
    p.add_argument("--paper-file", default="paper_lynch_mid100.json")
    p.add_argument("--max-positions", type=int, default=2)
    p.add_argument("--budget", type=float, default=10000.0)
    p.add_argument("--cooldown", type=int, default=5)
    p.add_argument("--regime", action="store_true", help="與排程一致地開啟大盤濾網")
    p.add_argument("--source", default="finmind", choices=["finmind", "sample"])
    p.add_argument("--end", default="", help="診斷日期 (預設今天)")
    args = p.parse_args(argv)

    # .env 裡的 FINMIND_TOKEN 要載進來，否則配額只有匿名等級
    sys.path.insert(0, _root())
    import main as cli
    cli._load_dotenv()

    end = args.end or pd.Timestamp.today().strftime("%Y-%m-%d")
    provider = _provider(args.source)
    strat = strategies.build(args.strategy)

    if args.symbols:
        symbols, pool = args.symbols.split(","), "自訂清單"
    else:
        from src.data.universe import resolve
        symbols, pool = resolve(args.universe), args.universe

    broker = PersistentPaperBroker(path=os.path.join(_root(), args.paper_file))
    holdings = [p for p in broker.positions() if p.shares > 0]

    # 關卡 1：暫停
    from src.control import load_runtime
    rc = load_runtime()
    paused = bool(rc.get("paused"))
    # runtime.json 的動態覆寫優先，跟排程實際跑的一致
    max_pos = rc["max_positions"] if rc.get("max_positions") else args.max_positions
    budget = rc["budget"] if rc.get("budget") else args.budget

    print(f"=== {args.strategy} / {pool} @ {end} ===")
    print(f"帳戶：{args.paper_file}｜現金 {broker.cash():,.0f}｜持倉 {len(holdings)} 檔"
          f"｜成交紀錄 {len(broker.trades)} 筆")
    print(f"設定：max_positions={max_pos}　budget={budget:,.0f}　"
          f"cooldown={args.cooldown} 個交易日　regime={'開' if args.regime else '關'}")
    for h in holdings:
        print(f"　持有 {h.symbol} {h.shares} 股 @ {h.avg_price:,.2f}")
    print()

    blockers = []  # 依序記下「結構上就擋死本輪買進」的關卡

    print("① 暫停狀態")
    if paused:
        print("   ⛔ runtime.json paused=true — 只出場不買進（Telegram 送 /resume 解除）")
        blockers.append("被 /pause 暫停")
    else:
        print("   ✅ 沒有暫停")

    # 關卡 2：大盤濾網
    start = (pd.Timestamp(end) - pd.Timedelta(days=800)).strftime("%Y-%m-%d")
    print("\n② 大盤濾網（加權指數年線）")
    bull = True
    bench = provider.benchmark(start, end)
    if not args.regime:
        print("   ─ 未開啟 --regime，不擋")
    elif bench is None or len(bench) < 200:
        print("   ⚠️ 抓不到足夠的大盤資料，實盤會視為多頭（不擋）")
    else:
        last = float(bench.iloc[-1])
        ma = float(bench.rolling(200).mean().iloc[-1])
        bull = last >= ma
        mark = "✅ 在年線之上，可做多" if bull else "⛔ 跌破年線，本輪禁止做多"
        print(f"   加權指數 {last:,.0f}　年線 {ma:,.0f} → {mark}")
        if not bull:
            blockers.append("大盤跌破年線")

    # 關卡 5 先算：空位
    slots = (max_pos - len(holdings)) if max_pos else len(symbols)
    print("\n③ 部位空位")
    if max_pos and slots <= 0:
        print(f"   ⛔ 已持有 {len(holdings)} 檔 = 上限 {max_pos} 檔，空位 0 → "
              f"就算有再強的買訊號也一張都買不進")
        blockers.append(f"滿倉（{len(holdings)}/{max_pos}）")
    else:
        print(f"   ✅ 空位 {slots} 檔（持有 {len(holdings)}／上限 {max_pos or '不限'}）")

    # 關卡 3 + 4：逐檔跑策略
    print(f"\n④ 逐檔訊號（{len(symbols)} 檔，抓資料中…）")
    trader = LiveTrader(provider, broker, strat, position_budget=budget,
                        dry_run=True, regime_filter=args.regime,
                        max_positions=max_pos, cooldown_days=args.cooldown)
    buckets: dict = defaultdict(list)
    buy_ready, sell_ready = [], []
    for sym in symbols:
        try:
            df = provider.history(sym, start, end)
        except Exception as e:
            buckets["抓不到資料"].append(f"{sym}({e.__class__.__name__})")
            continue
        if df is None or df.empty:
            buckets["抓不到資料"].append(sym)
            continue
        price = float(df["close"].iloc[-1])
        pos = next((h for h in holdings if h.symbol == sym), None)
        b = bench.reindex(df.index).ffill() if bench is not None else None
        chips = provider.institutional(sym, start, end) if getattr(strat, "requires_chips", False) else None
        try:
            sig = strat.evaluate(StrategyContext(symbol=sym, prices=df,
                                                 fundamentals=provider.fundamentals(sym),
                                                 benchmark=b, position=pos, chips=chips))
        except Exception as e:
            buckets["策略評估失敗"].append(f"{sym}({e})")
            continue

        if not sig.is_actionable:
            buckets["策略沒給訊號（觀望）"].append(sym)
        elif sig.action == Action.BUY and pos is None:
            if not bull:
                buckets["有買訊號但大盤濾網擋下"].append(sym)
            elif trader._in_cooldown(sym, df):
                buckets["有買訊號但在冷卻期"].append(sym)
            elif int((budget * sig.strength) // price) <= 0:
                buckets[f"有買訊號但預算買不起 1 股（{price:,.0f} 元）"].append(sym)
            else:
                buy_ready.append((sig.strength, sym, price, sig.reason))
        elif sig.action == Action.BUY and pos is not None:
            buckets["已持有（買訊號不重複加碼）"].append(sym)
        elif sig.action == Action.SELL and pos is not None:
            sell_ready.append((sym, price, sig.reason))
        else:
            buckets["賣訊號但沒持股（無事可做）"].append(sym)

    for reason, syms in sorted(buckets.items(), key=lambda kv: -len(kv[1])):
        head = "、".join(syms[:8]) + ("…" if len(syms) > 8 else "")
        print(f"   {len(syms):>3} 檔　{reason}：{head}")

    buy_ready.sort(reverse=True)
    print(f"\n   通過所有檢查、排隊等買進的：{len(buy_ready)} 檔")
    for s, sym, price, reason in buy_ready[:10]:
        take = "→ 會買進" if (not paused and slots > 0) else "→ 但沒有空位/暫停中，買不到"
        print(f"     {sym} @ {price:,.2f}（強度 {s:.2f}）{take}　{reason}")

    print(f"\n⑤ 手上 {len(holdings)} 檔為什麼不賣（賣出不受濾網/空位限制）")
    if not holdings:
        print("   ─ 沒有持股")
    for h in holdings:
        hit = next((x for x in sell_ready if x[0] == h.symbol), None)
        if hit:
            print(f"   🔴 {h.symbol} 觸發賣出：{hit[2]}")
        else:
            print(f"   🟢 {h.symbol} 未觸發賣出條件（策略認為續抱）")

    # 結論
    print("\n=== 結論 ===")
    if blockers:
        print("這個帳戶買不進東西，主因：" + "；".join(blockers))
        if buy_ready:
            print(f"（同時有 {len(buy_ready)} 檔本來符合買進條件，被上面擋掉了）")
        else:
            print("（就算解除上面的限制，本輪也沒有符合條件的標的）")
    elif buy_ready:
        print(f"沒有結構性阻擋，本輪有 {len(buy_ready)} 檔會買進。")
    else:
        print("沒有結構性阻擋，純粹是策略對這批標的都沒有買訊號 —— "
              "不交易本身就是策略的判斷，不是故障。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
