#!/usr/bin/env python3
"""選擇權資料品質盤點——寫回測之前必須先知道的四件事。

# 為什麼不能直接寫回測

2026-10-06 的探針確認資料拿得到、欄位也夠。但光是三列樣本就看到四個
會讓回測結果變成垃圾、而且**看起來不會有問題**的坑：

  1. `trading_session` 有 `after_market`（盤後）——如果日盤和盤後混在一起算，
     同一天同一個履約價會出現兩筆，部位和損益全部翻倍。
  2. `contract_date` 出現 `202609F4` 這種**週選擇權**代號，跟月選 `202609`
     混在一起。週選和月選的到期行為完全不同，不分開就是在算一個不存在的商品。
  3. 大量 `close=0.0 / volume=0 / open_interest=0` 的深價外履約價。
     「用 0 元賣出選擇權」在回測裡會變成無風險收權利金 0 元卻背著風險，
     或更糟：被當成可以無限賣的免費部位。
  4. **這份資料沒有買賣報價（bid/ask），只有 OHLC 與結算價。**
     選擇權的買賣價差極寬，用收盤價假設成交會嚴重高估賣方的收益。
     這是對「賣選擇權」策略最致命的一個偏差。

所以這支先把上面四件事量化，回測的設計才有依據。**順序顛倒的話，
我會給你一個數字漂亮但完全不能用的回測。**

# 這支不會做的事

不下單、不寫策略、不給任何「賣選擇權能不能賺」的結論。
它只回答：這份資料能支撐什麼樣的回測、哪些假設必須保守處理。

用法（在 VM 上）：
    .venv/bin/python tools/txo_data_survey.py
    .venv/bin/python tools/txo_data_survey.py --months 2024-01,2025-06,2026-09
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 月選的 contract_date 長得像 202609；其他（202609F4、202610W1…）都是週選或特殊契約
MONTHLY_RE = re.compile(r"^\d{6}$")

DEFAULT_MONTHS = ["2022-10", "2024-01", "2025-06", "2026-09"]  # 跨空頭/多頭各取一段


def _pad(text: str, width: int) -> str:
    import unicodedata
    w = sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in str(text))
    return str(text) + " " * max(width - w, 0)


def _fetch(api, start, end):
    """抓一個月的 TXO 日資料。參數名由簽章決定（見 txo_data_probe 的教訓）。"""
    import inspect
    fn = api.taiwan_option_daily
    params = inspect.signature(fn).parameters
    kw = {}
    for name in ("option_id", "data_id", "futures_id"):
        if name in params:
            kw[name] = "TXO"
            break
    return fn(start_date=start, end_date=end, **kw)


def survey_month(api, month: str):
    import pandas as pd

    start = f"{month}-01"
    end = (pd.Timestamp(start) + pd.offsets.MonthEnd(1)).strftime("%Y-%m-%d")
    try:
        df = _fetch(api, start, end)
    except Exception as e:
        print(f"  {month}：抓取失敗 {str(e)[:100]}", flush=True)
        return None
    if df is None or df.empty:
        print(f"  {month}：沒有資料", flush=True)
        return None
    return df


def main():
    ap = argparse.ArgumentParser(description="選擇權資料品質盤點（寫回測前的前置作業）")
    ap.add_argument("--months", default=",".join(DEFAULT_MONTHS),
                    help="要盤點的月份，逗號分隔 (YYYY-MM)")
    args = ap.parse_args()

    import pandas as pd

    import main as cli
    cli._load_dotenv()

    from src.data.finmind import FinMindProvider
    api = FinMindProvider().api

    months = [m.strip() for m in args.months.split(",") if m.strip()]
    print(f"盤點 {len(months)} 個月：{'、'.join(months)}（每個月 1 次 API）\n", flush=True)

    frames = []
    for m in months:
        df = survey_month(api, m)
        if df is not None:
            df = df.copy()
            df["_month"] = m
            frames.append(df)
            print(f"  {m}：{len(df):,} 列", flush=True)
    if not frames:
        print("\n❌ 一個月都抓不到，無法盤點。")
        return 1
    all_df = pd.concat(frames, ignore_index=True)

    # ── 1. 交易時段 ──
    print("\n" + "=" * 72)
    print("① trading_session（混在一起算會讓部位與損益翻倍）")
    sess = Counter(all_df.get("trading_session", pd.Series(dtype=str)).fillna("(空)"))
    for k, v in sess.most_common():
        print(f"  {_pad(k, 20)}{v:>10,} 列（{v / len(all_df):.1%}）")
    print(f"  → 回測必須固定只用一種，建議 'regular_trading'／日盤。")

    # ── 2. 月選 vs 週選 ──
    print("\n" + "=" * 72)
    print("② contract_date：月選 vs 週選（到期行為不同，不可混用）")
    cd = all_df["contract_date"].astype(str)
    monthly = cd.str.match(MONTHLY_RE)
    print(f"  月選（{'純 6 位數字，例 202609'}）：{monthly.sum():>10,} 列（{monthly.mean():.1%}）")
    print(f"  其他（週選/特殊契約）：      {(~monthly).sum():>10,} 列（{(~monthly).mean():.1%}）")
    others = Counter(cd[~monthly])
    print(f"  非月選的代號樣本：{', '.join(list(others)[:8])}")
    print(f"  → 回測先只做月選，最單純；週選要另外處理到期日曆。")

    # ── 3. 有沒有真的在交易 ──
    print("\n" + "=" * 72)
    print("③ 流動性：有多少履約價其實是「掛在那裡但沒人交易」")
    reg = all_df
    if "trading_session" in all_df.columns and sess:
        main_sess = sess.most_common(1)[0][0]
        reg = all_df[all_df["trading_session"] == main_sess]
    vol = pd.to_numeric(reg.get("volume"), errors="coerce").fillna(0)
    close = pd.to_numeric(reg.get("close"), errors="coerce").fillna(0)
    oi = pd.to_numeric(reg.get("open_interest"), errors="coerce").fillna(0)
    print(f"  以主時段 {len(reg):,} 列計算：")
    print(f"  volume = 0        ：{(vol == 0).mean():>7.1%}")
    print(f"  close  = 0        ：{(close == 0).mean():>7.1%}")
    print(f"  未平倉 = 0        ：{(oi == 0).mean():>7.1%}")
    tradable = (vol > 0) & (close > 0)
    print(f"  **真的可交易（量>0 且價>0）：{tradable.mean():.1%}**")
    if tradable.any():
        per_day = reg[tradable].groupby("date").size()
        print(f"  平均每天有 {per_day.mean():.0f} 個可交易的履約價"
              f"（最少 {per_day.min()}、最多 {per_day.max()}）")
    print("  → 回測必須過濾 volume>0 且 close>0，否則會「用 0 元賣出選擇權」。")

    # ── 4. 結算價與報價 ──
    print("\n" + "=" * 72)
    print("④ 成交價假設：這份資料有什麼、缺什麼")
    have = [c for c in ("open", "max", "min", "close", "settlement_price") if c in reg.columns]
    print(f"  有的價格欄位：{', '.join(have)}")
    if "settlement_price" in reg.columns:
        sp = pd.to_numeric(reg["settlement_price"], errors="coerce").fillna(0)
        print(f"  settlement_price = 0 的比例：{(sp == 0).mean():.1%}"
              f"（若接近 100%，這欄在這個時段不可用）")
    missing = [c for c in ("bid", "ask", "bid_price", "ask_price") if c in reg.columns]
    print(f"  買賣報價（bid/ask）：{'有 ' + ', '.join(missing) if missing else '**沒有**'}")
    print()
    print("  🔴 **這是最致命的一點**：沒有 bid/ask 就無法知道真實成交價。")
    print("     選擇權的買賣價差極寬（價外合約的價差常是權利金的 10~30%），")
    print("     用收盤價假設「賣得掉」會系統性高估賣方收益。")
    print("     → 回測必須扣一個保守的價差成本，而且那個假設要寫在標準裡、")
    print("       事前決定，不可以看到結果再調鬆。")

    print("\n" + "=" * 72)
    print("盤點結論：這份資料能支撐的回測長這樣\n")
    print("  ✅ 可以做：月選、主時段、量>0 且價>0 的合約，用收盤價成交並扣價差成本")
    print("  ⚠️ 要小心：週選另算、深價外沒流動性（想賣的那種常常正是沒人交易的）")
    print("  🔴 不能假裝有的：真實成交價、盤中追繳、滑價。這些只能用保守假設蓋過去")
    print()
    print("  下一步（順序不可顛倒）：")
    print("  1. 補 txo-options-lab 的 config/margin.toml（A/B/C 保證金現在是 0）")
    print("  2. 寫回測，標準事前寫死，**判定看最大單日虧損與追繳爆倉**，不是平均報酬")
    print("  3. 紙上空跑跨過一次大波動，再談真錢")
    print("\n（本檔只盤點資料，不下單、不寫策略、不給能不能賺的結論。）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
