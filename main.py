#!/usr/bin/env python3
"""台股名人策略 — 命令列入口。

範例：
    # 列出可用策略
    python main.py list

    # 用內建樣本資料回測巴菲特策略
    python main.py backtest --strategy buffett

    # 回測李佛摩趨勢策略、指定股票與期間
    python main.py backtest --strategy livermore --symbols 2330,2454 --start 2024-01-01

    # 模擬盤掃描 (dry-run，只印出會下的單)
    python main.py scan --strategy oneil
"""
from __future__ import annotations

import argparse
import sys

from src.data.sample import SampleDataProvider
from src.engine.backtest import Backtester
from src.engine.trader import LiveTrader
from src.broker.paper import PaperBroker
from src import strategies


def _provider(args):
    # 預設用離線樣本資料；要接真實資料時改用 FinMindProvider。
    if getattr(args, "source", "sample") == "finmind":
        from src.data.finmind import FinMindProvider
        return FinMindProvider()
    return SampleDataProvider()


def cmd_list(args):
    print("可用名人策略：")
    titles = {
        "buffett": "巴菲特 — 價值投資/護城河 (高 ROE、低負債、合理估值)",
        "graham": "葛拉漢 — 安全邊際/深度價值 (低 PE、低 PB、葛拉漢數字)",
        "lynch": "彼得林區 — 成長合理價 GARP (PEG<=1.2、穩健成長)",
        "oneil": "歐尼爾 — CANSLIM/帶量突破 52 週新高 + 相對強弱",
        "livermore": "李佛摩 — 順勢突破關鍵點 + ATR 移動停損",
    }
    for key in strategies.REGISTRY:
        print(f"  {key:<10} {titles.get(key, '')}")


def cmd_backtest(args):
    provider = _provider(args)
    symbols = args.symbols.split(",") if args.symbols else provider.universe()
    strat = strategies.build(args.strategy)
    bt = Backtester(
        provider,
        initial_cash=args.cash,
        position_pct=args.position_pct,
        fee_discount=args.fee_discount,
    )
    result = bt.run(strat, symbols, args.start, args.end)

    print(f"\n=== 回測結果：{args.strategy} ===")
    print(f"標的: {', '.join(symbols)}")
    print(f"期間: {args.start} ~ {args.end}\n")
    print(result.summary())
    if args.trades and result.trades:
        print("\n--- 交易明細 ---")
        for t in result.trades:
            print(f"{t.date.date()} {t.side:<4} {t.symbol} {t.shares:>6} @ {t.price:>8.2f}  {t.reason}")


def cmd_scan(args):
    provider = _provider(args)
    symbols = args.symbols.split(",") if args.symbols else provider.universe()
    strat = strategies.build(args.strategy)
    # Telegram 通知 (環境變數有設才會啟用)
    from src.notify import TelegramNotifier
    notifier = TelegramNotifier()

    # 實單 / 盤中即時報價：用 Shioaji 當下單券商與即時價來源
    quote_fn = None
    if args.live or args.realtime:
        from src.broker.shioaji_broker import ShioajiBroker
        broker = ShioajiBroker(simulation=not args.real_account)
        quote_fn = broker.realtime_quote
    else:
        broker = PaperBroker(cash=args.cash)

    trader = LiveTrader(
        provider, broker, strat,
        position_budget=args.budget,
        dry_run=not args.live,
        quote_fn=quote_fn,
        notifier=notifier,
    )
    plans = trader.scan(symbols, args.end)

    mode = "實單" if args.live else "DRY-RUN (未送單)"
    rt = " +即時報價" if quote_fn else ""
    print(f"\n=== 掃描結果 [{mode}{rt}]：{args.strategy} @ {args.end} ===")
    if not plans:
        print("本輪無交易訊號。")
    for p in plans:
        print(f"  {p.action:<4} {p.symbol} {p.shares:>6} 股 @ {p.price:>8.2f}  {p.reason}")
    if notifier.enabled and plans:
        print(f"（已推送 {len(plans)} 筆訊號到 Telegram）")


def cmd_notify_test(args):
    from src.notify import TelegramNotifier
    n = TelegramNotifier()
    if not n.enabled:
        print("未設定 TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID，無法測試。")
        return
    ok = n.send("✅ 台股策略 bot 測試訊息，通知設定成功！")
    print("已送出測試訊息。" if ok else "送出失敗，請檢查 token / chat_id。")


def cmd_notify_chatid(args):
    from src.notify import TelegramNotifier
    n = TelegramNotifier()
    if not n.token:
        print("請先設定 TELEGRAM_BOT_TOKEN。")
        return
    print("先對你的 bot 傳一句話 (例如 hi)，再執行本指令。\n")
    chats = n.get_chat_ids()
    if not chats:
        print("查不到對話。請先在 Telegram 對 bot 發一則訊息後再試。")
    for c in chats:
        print(f"  chat_id={c['chat_id']}   ({c['name']})")


def build_parser():
    p = argparse.ArgumentParser(description="台股名人策略交易框架")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="列出可用策略").set_defaults(func=cmd_list)

    common = dict()
    bt = sub.add_parser("backtest", help="回測")
    bt.add_argument("--strategy", required=True)
    bt.add_argument("--symbols", default="", help="逗號分隔，如 2330,2454；留空用全部樣本股")
    bt.add_argument("--start", default="2024-01-01")
    bt.add_argument("--end", default="2025-12-31")
    bt.add_argument("--cash", type=float, default=1_000_000)
    bt.add_argument("--position-pct", type=float, default=0.2)
    bt.add_argument("--fee-discount", type=float, default=0.28)
    bt.add_argument("--source", choices=["sample", "finmind"], default="sample")
    bt.add_argument("--trades", action="store_true", help="印出交易明細")
    bt.set_defaults(func=cmd_backtest)

    sc = sub.add_parser("scan", help="掃描產生交易訊號 (模擬/實單)")
    sc.add_argument("--strategy", required=True)
    sc.add_argument("--symbols", default="")
    sc.add_argument("--end", default="2025-12-31", help="掃描的基準日期")
    sc.add_argument("--cash", type=float, default=1_000_000)
    sc.add_argument("--budget", type=float, default=200_000, help="單檔最大投入金額")
    sc.add_argument("--source", choices=["sample", "finmind"], default="sample")
    sc.add_argument("--live", action="store_true", help="真的送單 (預設只 dry-run)")
    sc.add_argument("--realtime", action="store_true", help="盤中用 Shioaji 即時報價更新今日 K (不下單也可)")
    sc.add_argument("--real-account", action="store_true", help="Shioaji 用實單帳戶 (預設模擬盤)")
    sc.set_defaults(func=cmd_scan)

    sub.add_parser("notify-test", help="送一則 Telegram 測試訊息").set_defaults(func=cmd_notify_test)
    sub.add_parser("notify-chatid", help="查詢自己的 Telegram chat_id").set_defaults(func=cmd_notify_chatid)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    sys.exit(main())
