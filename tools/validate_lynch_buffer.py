#!/usr/bin/env python3
"""驗證 lynch 的「季線緩衝」該不該採用——三關驗證，標準先寫死。

# 背景
lynch 買在季線之上、跌破季線就賣，兩個門檻緊貼著，股價在季線附近震盪時會
一直買→賣→買→賣。2026-08 實盤出現「台積電抱 2 天被砍 -4.3%」的案例。
exit_buffer 讓賣出門檻降到季線下方 N%，形成遲滯區間。

# 為什麼要先寫死標準
不先定義「贏」，看到結果才挑對自己有利的數字，等於用回測騙自己。
本檔的 CRITERIA 一經寫定不可修改；不符合就是不採用，不是「標準太嚴」。

# 三關（沿用既有紀律）
1. 多頭回測：近年多頭期，緩衝版要「不比現行差」
2. 空頭壓測：含 2022 空頭，回撤不可惡化
3. Walkforward：訓練期選股 → 測試期（沒看過的未來）驗證，防背答案

用法（在 VM 上，.env 有 FINMIND_TOKEN）：
    python tools/validate_lynch_buffer.py                      # tw50，預設緩衝組合
    python tools/validate_lynch_buffer.py --universe mid100    # mid100 要另外驗
    python tools/validate_lynch_buffer.py --buffers 0,0.03     # 只比兩組

⚠️ 額度：逐檔回測要抓 50~100 檔的價格+財報。DiskCachingProvider 會快取，
第一次跑最久（可能撞每小時 600 次上限，中斷後重跑會從快取接續）。
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ─────────────────────────────────────────────────────────────
# 通過標準（PRE-REGISTERED，寫定後不可修改）
# ─────────────────────────────────────────────────────────────
CRITERIA = {
    # 關卡 1：多頭期不可比現行版本差
    "bull_sharpe_min_ratio": 1.00,   # 緩衝版夏普 >= 現行 × 1.00
    "bull_return_min_ratio": 1.00,   # 緩衝版總報酬 >= 現行 × 1.00
    # 關卡 2：空頭期回撤不可惡化（允許 1 個百分點的雜訊）
    "bear_dd_tolerance": 0.01,
    # 關卡 3：Walkforward 測試期（沒看過的未來）的絕對門檻
    "wf_min_sharpe": 0.5,
    "wf_min_return": 0.0,
    # 機制檢查：緩衝的作用就是減少來回洗，交易數沒下降代表它根本沒起作用
    "require_fewer_trades": True,
}

# 期間設定（與過去三關驗證一致）
BULL = ("2024-01-01", "2025-12-31")
BEAR = ("2022-01-01", "2022-12-31")   # 台股 2022 空頭
WF_TRAIN = ("2023-01-01", "2024-06-30")
WF_TEST = ("2024-07-01", "2025-12-31")


def _bt(provider, buffer_, symbols, start, end, cash, fee_discount, cooldown, regime):
    from src.engine.backtest import Backtester
    from src.strategies.lynch import LynchStrategy

    strat = LynchStrategy(exit_buffer=buffer_)
    bt = Backtester(provider, initial_cash=cash, fee_discount=fee_discount,
                    cooldown_days=cooldown, regime_filter=regime)
    return bt.run(strat, symbols, start, end)


def _rank_by_sharpe(provider, buffer_, symbols, start, end, cash, fee_discount, cooldown, regime):
    """逐檔回測挑夏普最高的，供 walkforward 的訓練期選股用。"""
    rows = []
    for sym in symbols:
        try:
            r = _bt(provider, buffer_, [sym], start, end, cash, fee_discount, cooldown, regime)
            if len(r.trades) == 0:
                continue
            rows.append((sym, r.sharpe))
        except Exception as e:
            print(f"    {sym} 失敗: {e}", flush=True)
    rows.sort(key=lambda x: x[1], reverse=True)
    return [s for s, _ in rows]


def main():
    ap = argparse.ArgumentParser(description="lynch 季線緩衝的三關驗證（標準先寫死）")
    ap.add_argument("--universe", default="tw50", help="tw50 / mid100 / top15")
    ap.add_argument("--buffers", default="0,0.02,0.03,0.05",
                    help="逗號分隔的緩衝比例；0 = 現行版本（基準）")
    ap.add_argument("--top", type=int, default=5, help="walkforward 訓練期選前幾檔")
    ap.add_argument("--cash", type=float, default=1_000_000)
    ap.add_argument("--fee-discount", type=float, default=0.28)
    ap.add_argument("--cooldown", type=int, default=5)
    ap.add_argument("--no-regime", action="store_true", help="關閉大盤濾網（預設開啟，與實盤同）")
    args = ap.parse_args()

    from src.data.cache import DiskCachingProvider
    from src.data.finmind import FinMindProvider
    from src.data.universe import resolve

    regime = not args.no_regime
    provider = DiskCachingProvider(FinMindProvider())
    symbols = resolve(args.universe)
    buffers = [float(b) for b in args.buffers.split(",")]
    if 0.0 not in buffers:
        buffers.insert(0, 0.0)  # 一定要有基準才比得出來

    common = dict(cash=args.cash, fee_discount=args.fee_discount,
                  cooldown=args.cooldown, regime=regime)

    print(f"lynch 季線緩衝驗證｜股池 {args.universe}（{len(symbols)} 檔）｜"
          f"大盤濾網 {'開' if regime else '關'}")
    print(f"緩衝組合：{buffers}")
    print("標準已於程式碼寫死（CRITERIA），不因結果調整\n")

    results = {}
    for buf in buffers:
        tag = "現行" if buf == 0 else f"緩衝{buf:.0%}"
        print(f"─── {tag} ───", flush=True)

        bull = _bt(provider, buf, symbols, *BULL, **common)
        print(f"  關1 多頭 {BULL[0]}~{BULL[1]}：報酬 {bull.total_return:+.2%}｜"
              f"夏普 {bull.sharpe:.2f}｜回撤 {bull.max_drawdown:.2%}｜{len(bull.trades)} 筆", flush=True)

        bear = _bt(provider, buf, symbols, *BEAR, **common)
        print(f"  關2 空頭 {BEAR[0]}~{BEAR[1]}：報酬 {bear.total_return:+.2%}｜"
              f"夏普 {bear.sharpe:.2f}｜回撤 {bear.max_drawdown:.2%}｜{len(bear.trades)} 筆", flush=True)

        print("  關3 walkforward 訓練期選股中...", flush=True)
        chosen = _rank_by_sharpe(provider, buf, symbols, *WF_TRAIN, **common)[: args.top]
        if chosen:
            wf = _bt(provider, buf, chosen, *WF_TEST, **common)
            print(f"  關3 測試期 {WF_TEST[0]}~{WF_TEST[1]}（選 {','.join(chosen)}）："
                  f"報酬 {wf.total_return:+.2%}｜夏普 {wf.sharpe:.2f}｜"
                  f"回撤 {wf.max_drawdown:.2%}｜{len(wf.trades)} 筆", flush=True)
        else:
            wf = None
            print("  關3 訓練期選不出股票", flush=True)
        print(flush=True)
        results[buf] = {"bull": bull, "bear": bear, "wf": wf}

    # ── 對照基準判定 ──
    base = results[0.0]
    print("=" * 72)
    print(f"{'版本':<10}{'多頭夏普':>10}{'多頭報酬':>10}{'空頭回撤':>10}"
          f"{'WF夏普':>9}{'WF報酬':>10}{'多頭筆數':>9}")
    print("-" * 72)
    for buf in buffers:
        r = results[buf]
        tag = "現行(基準)" if buf == 0 else f"緩衝{buf:.0%}"
        wf = r["wf"]
        print(f"{tag:<10}{r['bull'].sharpe:>10.2f}{r['bull'].total_return:>10.2%}"
              f"{r['bear'].max_drawdown:>10.2%}"
              f"{(wf.sharpe if wf else float('nan')):>9.2f}"
              f"{(wf.total_return if wf else float('nan')):>10.2%}"
              f"{len(r['bull'].trades):>9}")

    print("\n" + "=" * 72)
    print("逐項判定（標準見 CRITERIA，事前寫死）：\n")
    winners = []
    for buf in buffers:
        if buf == 0:
            continue
        r = results[buf]
        checks = []
        ok = True

        c = r["bull"].sharpe >= base["bull"].sharpe * CRITERIA["bull_sharpe_min_ratio"]
        checks.append(("關1 多頭夏普不比現行差", c,
                       f"{r['bull'].sharpe:.2f} vs {base['bull'].sharpe:.2f}"))
        ok &= c

        c = r["bull"].total_return >= base["bull"].total_return * CRITERIA["bull_return_min_ratio"]
        checks.append(("關1 多頭報酬不比現行差", c,
                       f"{r['bull'].total_return:+.2%} vs {base['bull'].total_return:+.2%}"))
        ok &= c

        # 回撤是負值，「不惡化」= 不比基準更負（容忍 1pp 雜訊）
        c = r["bear"].max_drawdown >= base["bear"].max_drawdown - CRITERIA["bear_dd_tolerance"]
        checks.append(("關2 空頭回撤不惡化", c,
                       f"{r['bear'].max_drawdown:.2%} vs {base['bear'].max_drawdown:.2%}"))
        ok &= c

        wf = r["wf"]
        c = wf is not None and wf.sharpe >= CRITERIA["wf_min_sharpe"]
        checks.append((f"關3 WF 夏普 >= {CRITERIA['wf_min_sharpe']}", c,
                       f"{wf.sharpe:.2f}" if wf else "無資料"))
        ok &= c

        c = wf is not None and wf.total_return > CRITERIA["wf_min_return"]
        checks.append(("關3 WF 測試期正報酬", c,
                       f"{wf.total_return:+.2%}" if wf else "無資料"))
        ok &= c

        if CRITERIA["require_fewer_trades"]:
            c = len(r["bull"].trades) < len(base["bull"].trades)
            checks.append(("機制檢查：交易數下降", c,
                           f"{len(r['bull'].trades)} vs {len(base['bull'].trades)}"))
            ok &= c

        print(f"【緩衝 {buf:.0%}】{'✅ 全數通過' if ok else '❌ 未通過'}")
        for name, passed, detail in checks:
            print(f"    {'✓' if passed else '✗'} {name}：{detail}")
        print()
        if ok:
            winners.append((buf, r["bull"].sharpe))

    print("=" * 72)
    if not winners:
        print("🔴 沒有任何緩衝值通過 → 維持現行設定（exit_buffer=0）。")
        print("   結論照實記錄：台積電那種 2 天洗掉的案例確實存在，")
        print("   但用緩衝去修在歷史資料上並沒有變好，代表那是個案不是系統性缺陷。")
    else:
        winners.sort(key=lambda x: x[1], reverse=True)
        best = winners[0][0]
        print(f"🟢 通過的緩衝值：{[f'{b:.0%}' for b, _ in winners]}｜夏普最高者：{best:.0%}")
        print(f"\n採用方式（兩個帳戶要分開驗證、分開決定）：")
        print(f"  1. 這份結果只代表 {args.universe}；另一個股池要另外跑一次")
        print(f"  2. 改 deploy/*.service 的 ExecStart 加上："
              f" --params exit_buffer={best}")
        print(f"  3. 先觀察一段時間再考慮改 LynchStrategy.DEFAULTS")
    print("\n⚠️ 回測共同限制：mid100 有生存者偏差、成交假設用收盤價、")
    print("   樣本期間有限。通過不等於未來有效，只代表「沒有明顯變差」。")
    return 0


if __name__ == "__main__":
    _env = Path(__file__).resolve().parent.parent / ".env"
    if _env.exists():
        for line in _env.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    sys.exit(main())
