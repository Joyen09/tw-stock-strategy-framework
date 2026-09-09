#!/usr/bin/env python3
"""交易品質健檢：把成交紀錄攤開，看「來回洗」有沒有真的減少。

動機：2026-08-18 補上實盤 cooldown（賣出後 5 個交易日不重買，回測本來就有、
實盤漏了）。當時說「過一兩週看 /trades 的來回洗次數有沒有下降」——這支就是那個檢查。

看四件事：
1. 冷卻期違規：賣出後 N 天內又買回同一檔。修正後應為 0（修正前的舊紀錄會列出來對照）
2. 持有天數分布：太短代表被洗（lynch 是基本面策略，抱幾天就砍很不合理）
3. 每筆平倉的實現損益與勝率
4. 修正前 / 修正後的換手率對照

只讀本地帳戶檔，不打 API、不需要 token。

用法（在 VM 的 ~/stock）：
    .venv/bin/python tools/churn_check.py
    .venv/bin/python tools/churn_check.py --since 2026-08-18   # 只看修正後
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 實盤 cooldown 補上的日期——用來把紀錄切成「修正前 / 修正後」對照
COOLDOWN_FIX_DATE = "2026-08-18"

DEFAULT_ACCOUNTS = [
    ("lynch", "paper_account.json"),
    ("livermore", "paper_livermore.json"),
    ("lynch-mid100", "paper_lynch_mid100.json"),
]


def _load(path: Path) -> list:
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("trades", []) or []
    except Exception as e:
        print(f"  ⚠️ 讀 {path} 失敗：{e}")
        return []


def _round_trips(trades: list) -> list:
    """把成交配成「一買一賣」。回傳 [(symbol, 買日, 賣日, 持有日, 實現損益), ...]。

    同一檔可能買賣多輪，用先進先出配對；只配得起來的（有買有賣）才算一筆平倉。
    """
    open_buys: dict = {}
    out = []
    for t in sorted(trades, key=lambda x: x.get("date", "")):
        sym = t.get("symbol")
        if t.get("side") == "BUY":
            open_buys.setdefault(sym, []).append(t)
        elif t.get("side") == "SELL":
            queue = open_buys.get(sym) or []
            buy = queue.pop(0) if queue else None
            if buy is None:
                continue  # 買進發生在成交記帳上線之前，配不到，略過
            d1 = dt.date.fromisoformat(buy["date"])
            d2 = dt.date.fromisoformat(t["date"])
            out.append((sym, buy["date"], t["date"], (d2 - d1).days,
                        t.get("realized")))
    return out


def _cooldown_violations(trades: list, days: int) -> list:
    """賣出後 days 個日曆日內又買回同一檔（cooldown 用交易日算，這裡用日曆日近似，
    會略為寬鬆——抓到的一定是違規，抓不到的不保證沒有）。"""
    out = []
    sells = [t for t in trades if t.get("side") == "SELL"]
    buys = [t for t in trades if t.get("side") == "BUY"]
    for s in sells:
        sd = dt.date.fromisoformat(s["date"])
        for b in buys:
            if b.get("symbol") != s.get("symbol"):
                continue
            bd = dt.date.fromisoformat(b["date"])
            if 0 <= (bd - sd).days <= days:
                out.append((s["symbol"], s["date"], b["date"], (bd - sd).days))
    return out


def main():
    ap = argparse.ArgumentParser(description="交易品質健檢（來回洗、持有天數、勝率）")
    ap.add_argument("--since", default="", help="只看這天(含)之後的成交，YYYY-MM-DD")
    ap.add_argument("--cooldown", type=int, default=5, help="冷卻期天數（與 scan 的 --cooldown 一致）")
    args = ap.parse_args()

    root = Path(__file__).resolve().parent.parent
    print(f"交易品質健檢｜cooldown={args.cooldown} 天"
          + (f"｜只看 {args.since} 之後" if args.since else "") + "\n")

    all_rt, all_trades = [], []
    for label, fname in DEFAULT_ACCOUNTS:
        path = root / fname
        trades = _load(path)
        if args.since:
            trades = [t for t in trades if t.get("date", "") >= args.since]
        if not trades:
            print(f"【{label}】無成交紀錄"
                  + ("（帳戶檔不存在）" if not path.exists() else "（期間內沒有交易）"))
            print()
            continue

        rts = _round_trips(trades)
        viols = _cooldown_violations(trades, args.cooldown)
        all_rt += rts
        all_trades += trades

        n_buy = sum(1 for t in trades if t["side"] == "BUY")
        n_sell = sum(1 for t in trades if t["side"] == "SELL")
        print(f"【{label}】買 {n_buy} 次、賣 {n_sell} 次、完整平倉 {len(rts)} 筆")

        if rts:
            holds = [h for _, _, _, h, _ in rts]
            pnls = [p for *_, p in rts if p is not None]
            wins = sum(1 for p in pnls if p > 0)
            print(f"　持有天數：最短 {min(holds)} / 中位 {sorted(holds)[len(holds)//2]} / 最長 {max(holds)} 天")
            if pnls:
                print(f"　平倉損益：合計 {sum(pnls):+,.0f}｜{wins} 賺 / {len(pnls)-wins} 賠"
                      f"（勝率 {wins/len(pnls):.0%}）")
            short = [(s, b, e, h) for s, b, e, h, _ in rts if h <= 5]
            if short:
                print(f"　⚠️ 抱不到 5 天就出場 {len(short)} 筆：")
                for s, b, e, h in short:
                    print(f"　　{s} {b}→{e}（{h} 天）")

        if viols:
            print(f"　🔴 冷卻期違規 {len(viols)} 筆（賣出後 {args.cooldown} 天內又買回）：")
            for sym, sd, bd, gap in viols:
                tag = "修正前" if sd < COOLDOWN_FIX_DATE else "🚨 修正後仍發生，要查"
                print(f"　　{sym} 賣 {sd} → 買 {bd}（隔 {gap} 天）{tag}")
        else:
            print(f"　✅ 無冷卻期違規")
        print()

    # ── 修正前 / 修正後對照 ──
    if all_trades:
        before = [t for t in all_trades if t["date"] < COOLDOWN_FIX_DATE]
        after = [t for t in all_trades if t["date"] >= COOLDOWN_FIX_DATE]
        print("=" * 60)
        print(f"cooldown 修正（{COOLDOWN_FIX_DATE}）前後對照：")
        # 觀察窗口要用「日曆上真正經過的時間」，不能用「第一筆到最後一筆的間距」——
        # 只有 1 筆成交時後者會算成 1 天，換算出每週 7 筆的荒謬數字（原本就踩過這個坑）。
        log_start = min(t["date"] for t in all_trades)
        today = dt.date.today().isoformat()
        windows = {
            "修正前": (log_start, min(COOLDOWN_FIX_DATE, today)),
            "修正後": (COOLDOWN_FIX_DATE, today),
        }
        for tag, group in (("修正前", before), ("修正後", after)):
            w0, w1 = windows[tag]
            days = (dt.date.fromisoformat(w1) - dt.date.fromisoformat(w0)).days
            if days <= 0:
                print(f"  {tag}：窗口不足，跳過")
                continue
            weeks = days / 7
            print(f"  {tag}：{len(group)} 筆成交 / {days} 天（{w0} ~ {w1}）"
                  f" = 每週 {len(group)/weeks:.1f} 筆")
        print()
        print("⚠️ 兩段期間都很短、筆數個位數，週均只能看方向、不能當統計結論；")
        print("   而且行情本身會影響交易頻率（盤整多訊號、單邊少訊號），不是只有 cooldown 的功勞。")
    else:
        print("=" * 60)
        print("完全沒有成交紀錄。成交記帳是 2026-07-31 才加的，在那之前的買賣查不到。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
