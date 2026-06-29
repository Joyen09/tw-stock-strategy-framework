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
import os
import sys

from src.data.sample import SampleDataProvider
from src.engine.backtest import Backtester
from src.engine.trader import LiveTrader
from src.broker.paper import PaperBroker
from src import strategies


def _load_dotenv():
    """手動執行時自動載入專案根目錄的 .env (不覆蓋已存在的環境變數)。

    這樣 TELEGRAM_BOT_TOKEN / FINMIND_TOKEN 等只要寫進 .env 就會生效，
    不必每次手動 export。systemd 排程則另由 EnvironmentFile 載入。
    """
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key and key not in os.environ:  # 已 export 的優先，不覆蓋
                os.environ[key] = val


def _provider(args):
    # 預設用離線樣本資料；要接真實資料時改用 FinMindProvider。
    if getattr(args, "source", "sample") == "finmind":
        from src.data.finmind import FinMindProvider
        return FinMindProvider()
    return SampleDataProvider()


def _parse_params(s: str) -> dict:
    """把 'up_threshold=0.02,trend_ma=20' 解析成 {參數: 值}，值自動轉 int/float。"""
    out: dict = {}
    for kv in (s or "").split(","):
        kv = kv.strip()
        if not kv or "=" not in kv:
            continue
        k, _, v = kv.partition("=")
        k, v = k.strip(), v.strip()
        try:
            out[k] = int(v)
        except ValueError:
            try:
                out[k] = float(v)
            except ValueError:
                out[k] = v
    return out


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
    strat = strategies.build(args.strategy, **_parse_params(args.params))
    bt = Backtester(
        provider,
        initial_cash=args.cash,
        position_pct=args.position_pct,
        fee_discount=args.fee_discount,
        allow_odd_lot=not args.whole_lot,
        cooldown_days=args.cooldown,
        regime_filter=args.regime,
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


def cmd_compare(args):
    """一次回測多個策略，按夏普值排名輸出比較表。"""
    from src.data.cache import CachingProvider

    provider = CachingProvider(_provider(args))
    symbols = args.symbols.split(",") if args.symbols else provider.universe()
    names = args.strategy.split(",") if args.strategy else list(strategies.REGISTRY)

    print(f"比較 {len(names)} 個策略 × {len(symbols)} 檔股票（{args.start} ~ {args.end}），請稍候...\n")
    rows = []
    for name in names:
        try:
            strat = strategies.build(name)
            bt = Backtester(provider, initial_cash=args.cash, fee_discount=args.fee_discount,
                            cooldown_days=args.cooldown, regime_filter=args.regime)
            r = bt.run(strat, symbols, args.start, args.end)
            rows.append((name, r.total_return, r.cagr, r.max_drawdown, r.sharpe, len(r.trades)))
        except Exception as e:
            print(f"  {name} 失敗: {e}")

    rows.sort(key=lambda x: x[4], reverse=True)  # 依夏普值由高到低
    print(f"{'排名':<4}{'策略':<14}{'總報酬':>9}{'年化':>8}{'最大回撤':>10}{'夏普':>7}{'交易數':>7}")
    print("-" * 60)
    for i, (name, tr, cagr, mdd, sharpe, n) in enumerate(rows, 1):
        medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"{i:>2}"
        print(f"{medal:<4}{name:<14}{tr:>8.2%}{cagr:>8.2%}{mdd:>10.2%}{sharpe:>7.2f}{n:>7}")
    print("\n夏普值越高代表『風險調整後報酬』越好（同樣賺，波動越小越優）。")


def cmd_scan(args):
    provider = _provider(args)
    symbols = args.symbols.split(",") if args.symbols else provider.universe()
    strat = strategies.build(args.strategy)
    # Telegram 通知：加 --notify 且環境變數有設才會啟用
    notifier = None
    if args.notify:
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
        regime_filter=args.regime,
    )
    plans = trader.scan(symbols, args.end)

    mode = "實單" if args.live else "DRY-RUN (未送單)"
    rt = " +即時報價" if quote_fn else ""
    print(f"\n=== 掃描結果 [{mode}{rt}]：{args.strategy} @ {args.end} ===")
    if not plans:
        print("本輪無交易訊號。")
    for p in plans:
        print(f"  {p.action:<4} {p.symbol} {p.shares:>6} 股 @ {p.price:>8.2f}  {p.reason}")
    if notifier and notifier.enabled and plans:
        print(f"（已推送 {len(plans)} 筆訊號到 Telegram）")


def cmd_screen(args):
    """對一籃子股票跑所有 (或指定) 策略，列出今日各策略的買進名單。"""
    from src.engine.screener import Screener, format_report
    from src.data.universe import resolve
    from src.notify import TelegramNotifier

    provider = _provider(args)
    if args.symbols:
        symbols = args.symbols.split(",")
    else:
        symbols = resolve(args.universe)

    names = args.strategy.split(",") if args.strategy else list(strategies.REGISTRY)
    strats = [strategies.build(n) for n in names]

    print(f"掃描 {len(symbols)} 檔 × {len(strats)} 策略，請稍候...")
    res = Screener(provider, strats).run(symbols, args.end)
    report = format_report(res)
    print("\n" + report)

    if args.notify:
        n = TelegramNotifier()
        if n.enabled:
            n.send(report)
            print("\n（已推送到 Telegram）")
        else:
            print("\n（未設定 Telegram，略過推播）")


def cmd_fundamentals(args):
    """檢視某檔股票抓到的基本面 (除錯用)，看哪些欄位有值、哪些是 None。"""
    provider = _provider(args)
    syms = args.symbols.split(",") if args.symbols else provider.universe()
    for sym in syms:
        f = provider.fundamentals(sym)
        if f is None:
            print(f"{sym}: 無法取得基本面")
            continue
        print(f"\n=== {sym} {f.name} ===")
        fields = [
            ("本益比 PE", f.pe), ("股價淨值比 PB", f.pb), ("ROE(%)", f.roe),
            ("EPS", f.eps), ("EPS成長(%)", f.eps_growth), ("營收成長(%)", f.revenue_growth),
            ("殖利率(%)", f.dividend_yield), ("負債比(%)", f.debt_ratio),
            ("流動比(%)", f.current_ratio), ("毛利率(%)", f.gross_margin), ("PEG", f.peg),
        ]
        for label, val in fields:
            mark = "✓" if val is not None else "✗ (缺)"
            print(f"  {label:<16}: {val if val is not None else '—':<12} {mark}")
        if f.extra:
            print(f"  (備註: {f.extra})")


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


def cmd_shioaji_test(args):
    """測試 Shioaji 連線：登入(預設模擬盤)、印出餘額/持倉/即時報價。"""
    try:
        from src.broker.shioaji_broker import ShioajiBroker
    except Exception as e:
        print(f"載入失敗，請先 pip install shioaji：{e}")
        return
    mode = "實單帳戶" if args.real_account else "模擬盤"
    print(f"嘗試以【{mode}】登入 Shioaji ...")
    try:
        b = ShioajiBroker(simulation=not args.real_account)
    except Exception as e:
        print(f"❌ 登入失敗：{e}\n請確認 .env 的 SHIOAJI_API_KEY / SHIOAJI_SECRET_KEY 正確。")
        return
    print("✅ 登入成功！")
    try:
        print(f"帳戶餘額: {b.cash():,.0f}")
    except Exception as e:
        print(f"(餘額查詢略過: {e})")
    pos = b.positions()
    print(f"目前持倉: {len(pos)} 檔" + (("  " + ", ".join(f'{p.symbol}x{p.shares}' for p in pos)) if pos else ""))
    q = b.realtime_quote(args.symbol)
    print(f"{args.symbol} 即時報價: {q}")
    if hasattr(b, "logout"):
        b.logout()


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
    bt.add_argument("--whole-lot", action="store_true", help="只買整張(1000股)；預設可買零股")
    bt.add_argument("--cooldown", type=int, default=5, help="賣出後幾個交易日內不重買 (防洗盤)，0=關閉")
    bt.add_argument("--params", default="", help="覆寫策略參數，如 'up_threshold=0.02,down_threshold=0.03'")
    bt.add_argument("--regime", action="store_true", help="大盤風向濾網：加權指數跌破年線時禁止做多")
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
    sc.add_argument("--regime", action="store_true", help="大盤風向濾網：跌破年線時禁止做多 (建議開啟)")
    sc.add_argument("--notify", action="store_true", help="把交易訊號推到 Telegram")
    sc.set_defaults(func=cmd_scan)

    cp = sub.add_parser("compare", help="批次比較：所有策略跑同一批股票，按夏普排名")
    cp.add_argument("--symbols", default="", help="逗號分隔股票；留空用樣本股")
    cp.add_argument("--strategy", default="", help="逗號分隔策略；留空=全部")
    cp.add_argument("--start", default="2024-01-01")
    cp.add_argument("--end", default="2025-12-31")
    cp.add_argument("--cash", type=float, default=1_000_000)
    cp.add_argument("--fee-discount", type=float, default=0.28)
    cp.add_argument("--cooldown", type=int, default=5)
    cp.add_argument("--regime", action="store_true", help="大盤風向濾網：跌破年線時禁止做多")
    cp.add_argument("--source", choices=["sample", "finmind"], default="sample")
    cp.set_defaults(func=cmd_compare)

    sg = sub.add_parser("screen", help="選股：列出今日各策略的買進名單")
    sg.add_argument("--symbols", default="", help="逗號分隔股票；留空用 --universe")
    sg.add_argument("--universe", default="top15", help="預設股池: top15 (預設) 或 tw50")
    sg.add_argument("--strategy", default="", help="逗號分隔策略；留空=全部")
    sg.add_argument("--end", default="2025-12-31", help="掃描基準日期")
    sg.add_argument("--source", choices=["sample", "finmind"], default="finmind")
    sg.add_argument("--notify", action="store_true", help="把結果推到 Telegram")
    sg.set_defaults(func=cmd_screen)

    fd = sub.add_parser("fundamentals", help="檢視某股票抓到的基本面 (除錯用)")
    fd.add_argument("--symbols", default="", help="逗號分隔，如 2330,2454")
    fd.add_argument("--source", choices=["sample", "finmind"], default="finmind")
    fd.set_defaults(func=cmd_fundamentals)

    sub.add_parser("notify-test", help="送一則 Telegram 測試訊息").set_defaults(func=cmd_notify_test)
    sub.add_parser("notify-chatid", help="查詢自己的 Telegram chat_id").set_defaults(func=cmd_notify_chatid)

    st = sub.add_parser("shioaji-test", help="測試 Shioaji 連線 (預設模擬盤)")
    st.add_argument("--symbol", default="2330", help="測試即時報價用的股票")
    st.add_argument("--real-account", action="store_true", help="用實單帳戶登入 (預設模擬盤)")
    st.set_defaults(func=cmd_shioaji_test)
    return p


def main(argv=None):
    _load_dotenv()
    args = build_parser().parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    sys.exit(main())
