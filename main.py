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
from datetime import date


def _today() -> str:
    return date.today().strftime("%Y-%m-%d")

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


def _symbols(args, provider):
    """決定要處理哪些股票：--symbols 優先；否則用 provider 的清單；
    FinMind 沒內建清單時退回 universe (預設 top15)。"""
    if getattr(args, "symbols", ""):
        return args.symbols.split(",")
    uni = provider.universe()
    if uni:
        return uni
    from src.data.universe import resolve
    return resolve(getattr(args, "universe", "top15"))


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
        "momentum": "短線動能 — 帶量突破 20 日高點 + 均線多頭 + 緊停損 (快層)",
        "us_overnight": "美股隔夜 — 追蹤 ^SOX / TSM ADR 隔夜漲跌 (需 yfinance)",
    }
    for key in strategies.REGISTRY:
        print(f"  {key:<10} {titles.get(key, '')}")


def cmd_backtest(args):
    provider = _provider(args)
    symbols = _symbols(args, provider)
    strat = strategies.build(args.strategy, **_parse_params(args.params))
    bt = Backtester(
        provider,
        initial_cash=args.cash,
        position_pct=args.position_pct,
        fee_discount=args.fee_discount,
        allow_odd_lot=not args.whole_lot,
        cooldown_days=args.cooldown,
        regime_filter=args.regime,
        compound=args.compound,
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
    """一次回測多個策略，按夏普值排名輸出比較表。

    每個策略算完立即把結果存進 data_cache/（依 策略+股票池+期間+參數 產 key），
    斷線/中斷後重跑會跳過已算完的策略，從斷點接著跑。
    """
    import hashlib
    import json
    from pathlib import Path

    from src.data.cache import DiskCachingProvider

    provider = DiskCachingProvider(_provider(args))
    symbols = _symbols(args, provider)
    names = args.strategy.split(",") if args.strategy else list(strategies.REGISTRY)

    def _result_path(name: str) -> Path:
        raw = "|".join([name, ",".join(symbols), args.start, args.end,
                        str(args.regime), str(args.cash), str(args.fee_discount), str(args.cooldown)])
        key = hashlib.md5(raw.encode()).hexdigest()[:12]
        return Path("data_cache") / f"btres_{name}_{key}.json"

    print(f"比較 {len(names)} 個策略 × {len(symbols)} 檔股票（{args.start} ~ {args.end}）")
    print("每個策略要逐日回測全部個股，約需數分鐘（已算完的會存檔，斷線重跑可接續）：\n")
    rows = []
    for idx, name in enumerate(names, 1):
        rp = _result_path(name)
        if rp.exists():  # 之前算過（同股票池/期間/參數）→ 直接用存檔
            try:
                d = json.loads(rp.read_text())
                rows.append((name, d["tr"], d["cagr"], d["mdd"], d["sharpe"], d["n"]))
                print(f"  ✓ [{idx}/{len(names)}] {name}（讀取上次結果）：總報酬 {d['tr']:>7.2%}｜"
                      f"夏普 {d['sharpe']:>5.2f}｜{d['n']} 筆交易", flush=True)
                continue
            except Exception:
                pass  # 存檔壞了就重算
        print(f"  ▶ [{idx}/{len(names)}] 回測 {name} ...", flush=True)
        try:
            strat = strategies.build(name)
            bt = Backtester(provider, initial_cash=args.cash, fee_discount=args.fee_discount,
                            cooldown_days=args.cooldown, regime_filter=args.regime)
            r = bt.run(strat, symbols, args.start, args.end)
            row = (name, r.total_return, r.cagr, r.max_drawdown, r.sharpe, len(r.trades))
            rows.append(row)
            rp.parent.mkdir(parents=True, exist_ok=True)
            rp.write_text(json.dumps({"tr": row[1], "cagr": row[2], "mdd": row[3],
                                      "sharpe": row[4], "n": row[5]}))
            print(f"    ✓ {name}：總報酬 {r.total_return:>7.2%}｜夏普 {r.sharpe:>5.2f}｜"
                  f"{len(r.trades)} 筆交易", flush=True)
        except Exception as e:
            print(f"    ✗ {name} 失敗: {e}", flush=True)
    print()

    rows.sort(key=lambda x: x[4], reverse=True)  # 依夏普值由高到低
    print(f"{'排名':<4}{'策略':<14}{'總報酬':>9}{'年化':>8}{'最大回撤':>10}{'夏普':>7}{'交易數':>7}")
    print("-" * 60)
    for i, (name, tr, cagr, mdd, sharpe, n) in enumerate(rows, 1):
        medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"{i:>2}"
        print(f"{medal:<4}{name:<14}{tr:>8.2%}{cagr:>8.2%}{mdd:>10.2%}{sharpe:>7.2f}{n:>7}")
    print("\n夏普值越高代表『風險調整後報酬』越好（同樣賺，波動越小越優）。")


def _rank_by_sharpe(provider, args, symbols, start, end):
    """對每檔股票各自回測 args.strategy，回傳依夏普排序的 (sym,報酬,年化,回撤,夏普,交易數)。"""
    rows = []
    for sym in symbols:
        try:
            strat = strategies.build(args.strategy)
            bt = Backtester(provider, initial_cash=args.cash, fee_discount=args.fee_discount,
                            cooldown_days=args.cooldown, regime_filter=args.regime)
            r = bt.run(strat, [sym], start, end)
            if len(r.trades) == 0:
                continue  # 沒交易代表這檔不符合此策略，略過
            rows.append((sym, r.total_return, r.cagr, r.max_drawdown, r.sharpe, len(r.trades)))
        except Exception as e:
            print(f"  {sym} 失敗: {e}")
    rows.sort(key=lambda x: x[4], reverse=True)
    return rows


def cmd_pick(args):
    """科學選股：對一籃子股票各自回測同一策略，按夏普排名，挑出最速配的前 N 檔。"""
    from src.data.cache import DiskCachingProvider

    provider = DiskCachingProvider(_provider(args))
    symbols = _symbols(args, provider)

    print(f"用『{args.strategy}』策略逐檔回測 {len(symbols)} 檔（{args.start}~{args.end}），請稍候...\n")
    rows = _rank_by_sharpe(provider, args, symbols, args.start, args.end)
    from src.data.universe import NAMES
    print(f"{'排名':<4}{'股票':<14}{'總報酬':>9}{'年化':>8}{'最大回撤':>10}{'夏普':>7}{'交易數':>7}")
    print("-" * 62)
    for i, (sym, tr, cagr, mdd, sharpe, n) in enumerate(rows, 1):
        star = "⭐" if i <= args.top else "  "
        label = f"{sym}{NAMES.get(sym, '')}"
        print(f"{star}{i:<2}{label:<14}{tr:>8.2%}{cagr:>8.2%}{mdd:>10.2%}{sharpe:>7.2f}{n:>7}")

    top = [r[0] for r in rows[: args.top]]
    print(f"\n🎯 建議分散組合（夏普最高的 {len(top)} 檔）：{','.join(top)}")
    if top:
        print(f"   直接拿去掃描： python main.py scan --strategy {args.strategy} --source finmind "
              f"--regime --symbols {','.join(top)} --notify")
    print("\n⚠️ 這是『歷史』最速配，不保證未來；空頭時靠 --regime 保護。")


def cmd_walkforward(args):
    """誠實驗證：在『訓練期』選股，到『測試期』(沒看過的未來) 驗證，避免背答案。"""
    from src.data.cache import DiskCachingProvider
    from src.data.universe import NAMES

    provider = DiskCachingProvider(_provider(args))
    symbols = _symbols(args, provider)

    # 1) 訓練期：逐檔回測、挑夏普最高的前 N 檔
    print(f"【訓練期 {args.train_start}~{args.train_end}】用 {args.strategy} 從 {len(symbols)} 檔挑前 {args.top}...\n")
    ranked = _rank_by_sharpe(provider, args, symbols, args.train_start, args.train_end)
    chosen = [r[0] for r in ranked[: args.top]]
    if not chosen:
        print("訓練期選不出股票（可能沒交易）。")
        return
    print("訓練期選出：" + "、".join(f"{s}{NAMES.get(s,'')}" for s in chosen))

    # 2) 同一組在「訓練期」與「測試期」各跑一次，比較落差
    def _run(start, end):
        strat = strategies.build(args.strategy)
        bt = Backtester(provider, initial_cash=args.cash, position_pct=args.position_pct,
                        fee_discount=args.fee_discount, cooldown_days=args.cooldown,
                        regime_filter=args.regime)
        return bt.run(strat, chosen, start, end)

    in_s = _run(args.train_start, args.train_end)
    out_s = _run(args.test_start, args.test_end)

    print(f"\n{'期間':<10}{'總報酬':>10}{'年化':>9}{'最大回撤':>10}{'夏普':>8}{'交易數':>7}")
    print("-" * 56)
    print(f"{'訓練(背答案)':<12}{in_s.total_return:>9.2%}{in_s.cagr:>9.2%}{in_s.max_drawdown:>10.2%}{in_s.sharpe:>8.2f}{len(in_s.trades):>7}")
    print(f"{'測試(沒看過)':<12}{out_s.total_return:>9.2%}{out_s.cagr:>9.2%}{out_s.max_drawdown:>10.2%}{out_s.sharpe:>8.2f}{len(out_s.trades):>7}")

    print("\n判讀：")
    if out_s.sharpe >= 0.5 and out_s.total_return > 0:
        print("  ✅ 測試期(沒看過的未來)仍正報酬、夏普>=0.5 → 這套比較可信，不只是背答案。")
    elif out_s.total_return > 0:
        print("  🟡 測試期還有賺但變弱 → 有點實力，但別期待訓練期那麼好。")
    else:
        print("  🔴 測試期由盈轉虧 → 訓練期的好成績多半是『選到剛好走運的股票』，別輕信。")
    print("  (測試期通常會比訓練期差，落差越小越穩健。)")


def cmd_scan(args):
    end = args.end or _today()  # 未指定則用今天 (實盤掃描要看最新)
    provider = _provider(args)
    symbols = _symbols(args, provider)
    strat = strategies.build(args.strategy)
    # Telegram 通知：加 --notify 且環境變數有設才會啟用
    notifier = None
    if args.notify:
        from src.notify import TelegramNotifier
        notifier = TelegramNotifier()

    # 選券商 / 即時報價來源
    quote_fn = None
    if args.paper:
        # 本地持久化模擬盤：假錢、自己記帳、跨執行累積；可搭 --realtime 用 Shioaji 即時價當撮合價。
        # (取代永豐模擬盤——其持倉/成交回報實測不可靠)
        from src.broker.persistent_paper import PersistentPaperBroker
        paper_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), args.paper_file)
        broker = PersistentPaperBroker(path=paper_path, cash=args.cash)
        dry_run = False  # --paper 會真的撮合進本地帳戶，不是 dry-run
        if args.realtime:
            from src.broker.shioaji_broker import ShioajiBroker
            quote_fn = ShioajiBroker(simulation=not args.real_account).realtime_quote
    elif args.live or args.realtime:
        from src.broker.shioaji_broker import ShioajiBroker
        broker = ShioajiBroker(simulation=not args.real_account)
        quote_fn = broker.realtime_quote
        dry_run = not args.live
    else:
        broker = PaperBroker(cash=args.cash)
        dry_run = not args.live

    # 執行期設定 (Telegram /budget /maxpos /pause 動態覆寫)
    from src.control import load_runtime
    rc = load_runtime()
    budget = rc["budget"] if rc.get("budget") else args.budget
    max_pos = rc["max_positions"] if rc.get("max_positions") else args.max_positions
    paused = bool(rc.get("paused"))

    trader = LiveTrader(
        provider, broker, strat,
        position_budget=budget,
        dry_run=dry_run,
        quote_fn=quote_fn,
        notifier=notifier,
        regime_filter=args.regime,
        max_positions=max_pos,
        paused=paused,
        max_order_value=args.max_order_value,
    )
    plans = trader.scan(symbols, end)
    if paused:
        print("（⏸ 目前暫停買進中，只執行賣出）")

    mode = "本地模擬盤(假錢)" if args.paper else ("實單" if args.live else "DRY-RUN (未送單)")
    rt = " +即時報價" if quote_fn else ""
    print(f"\n=== 掃描結果 [{mode}{rt}]：{args.strategy} @ {end} ===")
    if not plans:
        print("本輪無交易訊號。")
    for p in plans:
        print(f"  {p.action:<4} {p.symbol} {p.shares:>6} 股 @ {p.price:>8.2f}  {p.reason}")
    if notifier and notifier.enabled and plans:
        if getattr(trader, "last_notify_ok", False):
            print(f"（已推送 {len(plans)} 筆訊號到 Telegram）")
        else:
            print("（Telegram 推送失敗，請檢查上方錯誤訊息）")


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
    res = Screener(provider, strats).run(symbols, args.end or _today())
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
    syms = _symbols(args, provider)
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


def cmd_listen(args):
    """持續監聽 Telegram 指令 (/budget /maxpos /pause /resume /status /holdings /sell)。"""
    from src.control import poll_loop
    paper_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), args.paper_file)
    try:
        poll_loop(
            simulation=not args.real_account,
            paper=args.paper,
            paper_path=paper_path,
        )
    except KeyboardInterrupt:
        print("\n已停止監聽。")


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
    bt.add_argument("--universe", default="top15", help="未指定 --symbols 時的候選池: top15 或 tw50")
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
    bt.add_argument("--compound", action="store_true", help="複利：用當前權益下單(賺的錢滾入)；預設固定金額")
    bt.set_defaults(func=cmd_backtest)

    sc = sub.add_parser("scan", help="掃描產生交易訊號 (模擬/實單)")
    sc.add_argument("--strategy", required=True)
    sc.add_argument("--symbols", default="")
    sc.add_argument("--end", default="", help="掃描的基準日期 (預設今天)")
    sc.add_argument("--cash", type=float, default=1_000_000)
    sc.add_argument("--budget", type=float, default=200_000, help="單檔最大投入金額")
    sc.add_argument("--max-order-value", type=float, default=None,
                    help="單筆買單金額上限保險絲 (預設 budget*1.5)；設 0 關閉")
    sc.add_argument("--source", choices=["sample", "finmind"], default="sample")
    sc.add_argument("--live", action="store_true", help="真的送單 (預設只 dry-run)")
    sc.add_argument("--paper-file", default="paper_account.json",
                    help="本地模擬盤帳戶檔名 (跑多策略時各給一個檔，帳戶才不會互相干擾)")
    sc.add_argument("--paper", action="store_true",
                    help="本地持久化模擬盤 (假錢、自己記帳、跨執行累積)；建議搭 --realtime 用即時價。取代永豐模擬盤")
    sc.add_argument("--realtime", action="store_true", help="盤中用 Shioaji 即時報價更新今日 K (不下單也可)")
    sc.add_argument("--real-account", action="store_true", help="Shioaji 用實單帳戶 (預設模擬盤)")
    sc.add_argument("--regime", action="store_true", help="大盤風向濾網：跌破年線時禁止做多 (建議開啟)")
    sc.add_argument("--notify", action="store_true", help="把交易訊號推到 Telegram")
    sc.add_argument("--max-positions", type=int, default=0, help="最多同時持有幾檔(只買訊號最強的前N檔)；0=不限")
    sc.add_argument("--universe", default="top15", help="未指定 --symbols 時的候選池: top15 或 tw50")
    sc.set_defaults(func=cmd_scan)

    pk = sub.add_parser("pick", help="科學選股：一個策略逐檔回測，挑夏普最高的前 N 檔")
    pk.add_argument("--strategy", required=True)
    pk.add_argument("--symbols", default="", help="逗號分隔股票；留空用 --universe")
    pk.add_argument("--universe", default="tw50", help="預設股池: tw50 (預設) 或 top15")
    pk.add_argument("--top", type=int, default=5, help="挑前幾檔 (預設 5)")
    pk.add_argument("--start", default="2023-01-01")
    pk.add_argument("--end", default="2025-12-31")
    pk.add_argument("--cash", type=float, default=1_000_000)
    pk.add_argument("--fee-discount", type=float, default=0.28)
    pk.add_argument("--cooldown", type=int, default=5)
    pk.add_argument("--regime", action="store_true", help="大盤風向濾網")
    pk.add_argument("--source", choices=["sample", "finmind"], default="finmind")
    pk.set_defaults(func=cmd_pick)

    wf = sub.add_parser("walkforward", help="誠實驗證：訓練期選股→測試期(沒看過)驗證，防背答案")
    wf.add_argument("--strategy", required=True)
    wf.add_argument("--symbols", default="", help="逗號分隔股票；留空用 --universe")
    wf.add_argument("--universe", default="tw50", help="預設股池: tw50 或 top15")
    wf.add_argument("--top", type=int, default=5)
    wf.add_argument("--train-start", default="2023-01-01")
    wf.add_argument("--train-end", default="2024-06-30")
    wf.add_argument("--test-start", default="2024-07-01")
    wf.add_argument("--test-end", default="2025-12-31")
    wf.add_argument("--cash", type=float, default=1_000_000)
    wf.add_argument("--position-pct", type=float, default=0.2)
    wf.add_argument("--fee-discount", type=float, default=0.28)
    wf.add_argument("--cooldown", type=int, default=5)
    wf.add_argument("--regime", action="store_true", help="大盤風向濾網")
    wf.add_argument("--source", choices=["sample", "finmind"], default="finmind")
    wf.set_defaults(func=cmd_walkforward)

    cp = sub.add_parser("compare", help="批次比較：所有策略跑同一批股票，按夏普排名")
    cp.add_argument("--symbols", default="", help="逗號分隔股票；留空用樣本股/候選池")
    cp.add_argument("--universe", default="top15", help="未指定 --symbols 時的候選池: top15 或 tw50")
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
    sg.add_argument("--end", default="", help="掃描基準日期 (預設今天)")
    sg.add_argument("--source", choices=["sample", "finmind"], default="finmind")
    sg.add_argument("--notify", action="store_true", help="把結果推到 Telegram")
    sg.set_defaults(func=cmd_screen)

    fd = sub.add_parser("fundamentals", help="檢視某股票抓到的基本面 (除錯用)")
    fd.add_argument("--symbols", default="", help="逗號分隔，如 2330,2454")
    fd.add_argument("--source", choices=["sample", "finmind"], default="finmind")
    fd.set_defaults(func=cmd_fundamentals)

    ls = sub.add_parser("listen", help="監聽 Telegram 指令 (/budget /pause /holdings /sell...)")
    ls.add_argument("--real-account", action="store_true", help="/holdings /sell 用實單帳戶 (預設模擬盤)")
    ls.add_argument("--paper", action="store_true",
                    help="/holdings /sell 對本地持久化模擬盤帳戶 (需與 scan --paper 搭配)")
    ls.add_argument("--paper-file", default="paper_account.json",
                    help="本地模擬盤帳戶檔名 (需與 scan --paper-file 一致)")
    ls.set_defaults(func=cmd_listen)
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
