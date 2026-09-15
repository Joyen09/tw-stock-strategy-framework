#!/usr/bin/env python3
"""驗證 lynch 該不該加「固定停利」——三關驗證，標準先寫死。

# 背景

2026-09 的觀察：交易最少的帳戶（lynch-mid100，0 筆成交）報酬最好 +34%，
交易最多的 livermore 虧最多。很自然會想「那乾脆設定賺 N% 就走」。

但這件事在理論上是反過來的：GARP／成長股的報酬結構靠**少數幾檔大贏家**
撐起全部，固定停利會把那幾檔的上檔先砍掉，下檔虧損卻不受限制——
林區自己的說法是「拔掉花、澆灌雜草」。停利通常會讓**勝率上升**
（每筆都小賺出場，感覺很好）但**期望值下降**。

「感覺很好」正是它危險的地方，所以不能憑感覺決定，要用資料。

# 為什麼要先寫死標準

不先定義「贏」，看到結果才挑對自己有利的數字，等於用回測騙自己。
本檔的 CRITERIA 一經寫定不可修改；不符合就是不採用，不是「標準太嚴」。

# 空過防護（沿用 2026-08 的教訓）

停利門檻設太高（例如 +100%）根本不會觸發，結果會跟基準一模一樣，
於是「不比基準差」這條就自動通過了——那是**沒測到**，不是通過。
所以加了機制檢查：停利出場必須至少發生 MIN_TP_EXITS 次，否則判未通過。

# 三關

1. 多頭回測：停利版要「不比現行差」（這關最難過，停利就是砍上檔）
2. 空頭壓測：2021-07 起跨進 2022 空頭，回撤不可惡化；0 筆交易視為沒測到
3. Walkforward：訓練期選股 → 測試期（沒看過的未來）驗證，防背答案

用法（在 VM 上，.env 有 FINMIND_TOKEN）：
    .venv/bin/python tools/validate_take_profit.py                    # tw50
    .venv/bin/python tools/validate_take_profit.py --universe mid100
    .venv/bin/python tools/validate_take_profit.py --levels 0,0.10,0.20

⚠️ 額度：逐檔回測要抓 50~100 檔的價格+財報。DiskCachingProvider 會快取，
第一次跑最久（可能撞每小時 600 次上限，中斷後重跑會從快取接續）。
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ──────────────────────────────────────────────────────────
# 通過標準（PRE-REGISTERED，寫定後不可修改）
# ──────────────────────────────────────────────────────────
CRITERIA = {
    # 關卡 1：多頭期不可比現行版本差（停利砍上檔，這關本來就該難過）
    "bull_sharpe_min_ratio": 1.00,
    "bull_return_min_ratio": 1.00,
    # 關卡 2：空頭期回撤不可惡化（容忍 1 個百分點的雜訊）
    "bear_dd_tolerance": 0.01,
    # 關卡 3：Walkforward 測試期（沒看過的未來）的絕對門檻
    "wf_min_sharpe": 0.5,
    "wf_min_return": 0.0,
    # 機制檢查：停利必須真的觸發過，否則這次比較根本沒測到任何東西
    "min_tp_exits": 3,
}

# 期間設定（與 validate_lynch_buffer.py 一致，理由見該檔）
BULL = ("2024-01-01", "2025-12-31")
BEAR = ("2021-07-01", "2022-12-31")
WF_TRAIN = ("2023-01-01", "2024-06-30")
WF_TEST = ("2024-07-01", "2025-12-31")

TP_MARK = "停利出場"  # lynch 停利訊號的理由標記，用來數觸發次數


def _bt(provider, tp, symbols, start, end, cash, fee_discount, cooldown, regime):
    from src.engine.backtest import Backtester
    from src.strategies.lynch import LynchStrategy

    strat = LynchStrategy(take_profit=tp)
    bt = Backtester(provider, initial_cash=cash, fee_discount=fee_discount,
                    cooldown_days=cooldown, regime_filter=regime)
    return bt.run(strat, symbols, start, end)


def _round_trips(trades):
    """把成交紀錄配成一買一賣（FIFO），回傳 [(報酬率, 持有天數, 是否停利出場)]。

    用來看停利到底做了什麼：勝率、持有天數、最大單筆獲利有沒有被砍掉。
    """
    open_lots: dict = {}
    out = []
    for t in sorted(trades, key=lambda x: x.date):
        if t.side == "BUY":
            open_lots.setdefault(t.symbol, []).append((t.date, t.price, t.shares))
            continue
        lots = open_lots.get(t.symbol) or []
        remaining = t.shares
        while remaining > 0 and lots:
            d0, p0, n0 = lots[0]
            n = min(n0, remaining)
            if p0 > 0:
                out.append((t.price / p0 - 1, (t.date - d0).days, TP_MARK in (t.reason or "")))
            remaining -= n
            if n0 - n <= 0:
                lots.pop(0)
            else:
                lots[0] = (d0, p0, n0 - n)
    return out


def _behaviour(trades) -> dict:
    """停利實際造成的行為改變（勝率會漲、最大獲利會被砍，這兩個要一起看）。"""
    rt = _round_trips(trades)
    if not rt:
        return {"n": 0, "win_rate": float("nan"), "avg_hold": float("nan"),
                "best": float("nan"), "avg_win": float("nan"),
                "avg_loss": float("nan"), "tp_exits": 0}
    wins = [r for r, _, _ in rt if r > 0]
    losses = [r for r, _, _ in rt if r <= 0]
    return {
        "n": len(rt),
        "win_rate": len(wins) / len(rt),
        "avg_hold": sum(d for _, d, _ in rt) / len(rt),
        "best": max(r for r, _, _ in rt),
        "avg_win": (sum(wins) / len(wins)) if wins else float("nan"),
        "avg_loss": (sum(losses) / len(losses)) if losses else float("nan"),
        "tp_exits": sum(1 for _, _, is_tp in rt if is_tp),
    }


def _rank_by_sharpe(provider, tp, symbols, start, end, cash, fee_discount, cooldown, regime):
    """逐檔回測挑夏普最高的，供 walkforward 的訓練期選股用。"""
    rows = []
    for sym in symbols:
        try:
            r = _bt(provider, tp, [sym], start, end, cash, fee_discount, cooldown, regime)
            if len(r.trades) == 0:
                continue
            rows.append((sym, r.sharpe))
        except Exception as e:
            print(f"    {sym} 失敗: {e}", flush=True)
    rows.sort(key=lambda x: x[1], reverse=True)
    return [s for s, _ in rows]


def main():
    ap = argparse.ArgumentParser(description="lynch 固定停利的三關驗證（標準先寫死）")
    ap.add_argument("--universe", default="tw50", help="tw50 / mid100 / top15")
    ap.add_argument("--levels", default="0,0.10,0.15,0.20,0.30",
                    help="逗號分隔的停利門檻；0 = 不停利（基準）")
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
    levels = [float(x) for x in args.levels.split(",")]
    if 0.0 not in levels:
        levels.insert(0, 0.0)  # 一定要有基準才比得出來

    common = dict(cash=args.cash, fee_discount=args.fee_discount,
                  cooldown=args.cooldown, regime=regime)

    print(f"lynch 固定停利驗證｜股池 {args.universe}（{len(symbols)} 檔）｜"
          f"大盤濾網 {'開' if regime else '關'}")
    print(f"停利門檻：{[f'{x:.0%}' for x in levels]}")
    print("標準已於程式碼寫死（CRITERIA），不因結果調整\n")

    results = {}
    for tp in levels:
        tag = "不停利" if tp == 0 else f"停利{tp:.0%}"
        print(f"─── {tag} ───", flush=True)

        bull = _bt(provider, tp, symbols, *BULL, **common)
        bh = _behaviour(bull.trades)
        print(f"  關1 多頭 {BULL[0]}~{BULL[1]}：報酬 {bull.total_return:+.2%}｜"
              f"夏普 {bull.sharpe:.2f}｜回撤 {bull.max_drawdown:.2%}｜{len(bull.trades)} 筆", flush=True)
        print(f"       行為：勝率 {bh['win_rate']:.0%}｜平均抱 {bh['avg_hold']:.0f} 天｜"
              f"最大單筆 {bh['best']:+.1%}｜停利出場 {bh['tp_exits']} 次", flush=True)

        bear = _bt(provider, tp, symbols, *BEAR, **common)
        print(f"  關2 空頭 {BEAR[0]}~{BEAR[1]}：報酬 {bear.total_return:+.2%}｜"
              f"夏普 {bear.sharpe:.2f}｜回撤 {bear.max_drawdown:.2%}｜{len(bear.trades)} 筆", flush=True)

        print("  關3 walkforward 訓練期選股中...", flush=True)
        chosen = _rank_by_sharpe(provider, tp, symbols, *WF_TRAIN, **common)[: args.top]
        if chosen:
            wf = _bt(provider, tp, chosen, *WF_TEST, **common)
            print(f"  關3 測試期 {WF_TEST[0]}~{WF_TEST[1]}（選 {','.join(chosen)}）："
                  f"報酬 {wf.total_return:+.2%}｜夏普 {wf.sharpe:.2f}｜"
                  f"回撤 {wf.max_drawdown:.2%}｜{len(wf.trades)} 筆", flush=True)
        else:
            wf = None
            print("  關3 訓練期選不出股票", flush=True)
        print(flush=True)
        results[tp] = {"bull": bull, "bear": bear, "wf": wf, "bh": bh}

    # ── 對照基準判定 ──
    base = results[0.0]
    print("=" * 78)
    print(f"{'版本':<10}{'多頭夏普':>10}{'多頭報酬':>10}{'空頭回撤':>10}"
          f"{'WF夏普':>9}{'WF報酬':>10}{'勝率':>8}{'最大單筆':>10}")
    print("-" * 78)
    for tp in levels:
        r = results[tp]
        tag = "不停利(基準)" if tp == 0 else f"停利{tp:.0%}"
        wf, bh = r["wf"], r["bh"]
        print(f"{tag:<10}{r['bull'].sharpe:>10.2f}{r['bull'].total_return:>10.2%}"
              f"{r['bear'].max_drawdown:>10.2%}"
              f"{(wf.sharpe if wf else float('nan')):>9.2f}"
              f"{(wf.total_return if wf else float('nan')):>10.2%}"
              f"{bh['win_rate']:>8.0%}{bh['best']:>10.1%}")
    print("\n註：勝率上升但總報酬下降，就是停利典型的樣子——每筆都小賺很舒服，")
    print("　　但撐起整體報酬的那幾檔大贏家被提前砍掉了。看『最大單筆』那欄最清楚。")

    print("\n" + "=" * 78)
    print("逐項判定（標準見 CRITERIA，事前寫死）：\n")
    winners = []
    for tp in levels:
        if tp == 0:
            continue
        r = results[tp]
        checks = []
        ok = True

        # 機制檢查放最前面：沒觸發過就等於沒測到，後面每一關都不具意義。
        tp_exits = r["bh"]["tp_exits"]
        c = tp_exits >= CRITERIA["min_tp_exits"]
        checks.append((f"機制檢查：停利實際觸發 >= {CRITERIA['min_tp_exits']} 次", c,
                       f"{tp_exits} 次" + ("" if c else " → 門檻太高沒作用，這次比較沒測到任何東西")))
        ok &= c

        c = r["bull"].sharpe >= base["bull"].sharpe * CRITERIA["bull_sharpe_min_ratio"]
        checks.append(("關1 多頭夏普不比基準差", c,
                       f"{r['bull'].sharpe:.2f} vs {base['bull'].sharpe:.2f}"))
        ok &= c

        c = r["bull"].total_return >= base["bull"].total_return * CRITERIA["bull_return_min_ratio"]
        checks.append(("關1 多頭報酬不比基準差", c,
                       f"{r['bull'].total_return:+.2%} vs {base['bull'].total_return:+.2%}"))
        ok &= c

        # 回撤是負值，「不惡化」= 不比基準更負。空頭期 0 筆交易 = 沒測到，不是通過。
        bear_traded = len(r["bear"].trades) > 0 and len(base["bear"].trades) > 0
        if not bear_traded:
            checks.append(("關2 空頭回撤不惡化", False,
                           f"空頭期 0 筆交易 → 這一關沒測到（停利版 {len(r['bear'].trades)} 筆 / "
                           f"基準 {len(base['bear'].trades)} 筆），不予採信"))
            ok = False
        else:
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

        print(f"【停利 {tp:.0%}】{'✅ 全數通過' if ok else '❌ 未通過'}")
        for name, passed, detail in checks:
            print(f"    {'✓' if passed else '✗'} {name}：{detail}")
        print()
        if ok:
            winners.append((tp, r["bull"].sharpe))

    print("=" * 78)
    if not winners:
        print("🔴 沒有任何停利門檻通過 → 維持現行設定（take_profit=0，不停利）。")
        print("   這個結果要照實記錄並接受：『賺 N% 就走』在直覺上很吸引人，")
        print("   但在歷史資料上它砍掉的大贏家比它避開的回檔還多。")
        print("   真正該處理的是『虧損那一側』（出場規則、部位大小），不是獲利那一側。")
    else:
        winners.sort(key=lambda x: x[1], reverse=True)
        best = winners[0][0]
        print(f"🟢 通過的停利門檻：{[f'{t:.0%}' for t, _ in winners]}｜夏普最高者：{best:.0%}")
        print("\n採用前還要注意：")
        print(f"  1. 這份結果只代表 {args.universe}；另一個股池要另外跑一次")
        print("  2. 停利賣出後過了 cooldown 又可能追回同一檔（更高價），實盤要盯這件事")
        print(f"  3. 改 deploy/*.service 的 ExecStart 加上 --params take_profit={best}")
    print("\n（本檔只做驗證，不會改任何預設值、不會下任何單。）")


if __name__ == "__main__":
    sys.exit(main() or 0)
