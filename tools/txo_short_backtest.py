#!/usr/bin/env python3
"""賣價外月選擇權（勒式）回測——判定看尾部風險，不是平均報酬。

# 為什麼標準長得跟前面的工具不一樣

賣選擇權的報酬分布是**左偏到極端**：大多數月份小賺權利金，偶爾一個月
把前面幾年賺的全部還回去，而且會附帶保證金追繳。所以：

  **「平均報酬漂亮」是這類策略的預設狀態，不是訊號。**

一個會在 2024-08 或 2022-10 那種日子爆倉的策略，回測的平均報酬一定好看——
因為爆倉那天之後的交易在回測裡照常繼續，現實中你的帳戶已經沒了。
所以本檔的判定主角是：**最壞單月、最大單日評價虧損、保證金夠不夠。**

# 資料限制（來自 tools/txo_data_survey.py 的實測，不是假設）

- 主時段叫 `position`（不是 regular_trading）。日盤與盤後混算會讓損益翻倍
- 只做月選（contract_date 是純 6 位數字）；週選 W1/W2/W4/W5 另有到期日曆
- 只用 volume>0 且 close>0 的合約建倉（實測每天約 513 個可交易履約價）
- **每日評價用 settlement_price**（實測只有 4.5% 是 0）而不是 close：
  冷門履約價 close=0，拿它評價會把空頭部位當成一文不值、憑空生出獲利
- 🔴 **沒有 bid/ask。** 價外合約的買賣價差常達權利金的 10~30%，
  用收盤價假設賣得掉會系統性高估賣方收益。所以 `SPREAD_PCT` 事前寫死，
  **不可以看到結果再調鬆**

# 標的價與結算價的近似（必須誠實標示）

用加權指數收盤價當標的與到期結算價。真實的 TXO 最後結算價是到期日
開盤集合競價的平均，與收盤價會有差距；期貨與現貨的基差也被忽略。
→ 這個近似對「價外多遠」和「到期賠多少」都有影響，所以結果只能當
**量級**參考，不能當精算。

# 通過標準（PRE-REGISTERED，跑之前就寫死）

A. 樣本至少 `MIN_MONTHS` 個月，且必須涵蓋一次大跌（否則是沒測到）
B. 扣掉價差與費用後總損益 > 0
C. **任何一天都不得保證金不足**（追繳=實質爆倉，一次就淘汰）
D. 最壞單月虧損 <= 權益的 `MAX_MONTH_LOSS`
四項不同時成立 → 不採用。C 和 D 是這份標準的重點，B 只是門票。

⚠️ 沒有提供保證金參數（A值/B值）時，本檔**拒絕給出通過與否的判定**，
只印損益與最壞情形。因為 C/D 兩項沒有保證金就算不出來，而那正是
這個策略唯一真正會殺死人的地方。寧可不判，也不要給一個漂亮的半套結論。

用法：
    # 先去期交所查「選擇權風險保證金 A值/B值」填進去（會隨市況調整）
    # 2026-10 期交所公告值（會調整，用前務必自己對一次）
    .venv/bin/python tools/txo_short_backtest.py \
        --margin-a 187000 --margin-b 94000 --maint-a 143000 --maint-b 72000
    .venv/bin/python tools/txo_short_backtest.py          # 不給保證金 → 只看損益，不判定
"""
from __future__ import annotations

import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ──────────────────────────────────────────────────────────
# 通過標準（PRE-REGISTERED，寫定後不可修改）
# ──────────────────────────────────────────────────────────
CRITERIA = {
    "spread_pct": 0.15,        # 建倉時扣掉權利金的 15% 當價差成本（實測價差 10~30%，取中間偏保守）
    "min_months": 24,          # 樣本月數下限
    "max_month_loss": 0.20,    # 最壞單月虧損不得超過權益的 20%
    "require_no_margin_call": True,   # 任何一天保證金不足 → 直接淘汰
    "min_total_pnl": 0.0,
}

SESSION = "position"       # 主時段（tools/txo_data_survey.py 實測）
MULTIPLIER = 50            # TXO 契約乘數：1 點 = 50 元
MONTHLY_RE = re.compile(r"^\d{6}$")
TAX_RATE = 0.001           # 選擇權交易稅：權利金 × 0.1%
FEE_PER_LOT = 25.0         # 每口手續費（估；券商不同）


def _third_wednesday(ym: str):
    """月選到期日 = 該月第三個星期三。"""
    import pandas as pd
    first = pd.Timestamp(f"{ym[:4]}-{ym[4:6]}-01")
    # weekday(): Mon=0 ... Wed=2
    offset = (2 - first.weekday()) % 7
    return first + pd.Timedelta(days=offset + 14)


def _load_options(api, start, end):
    import inspect
    import pandas as pd

    fn = api.taiwan_option_daily
    params = inspect.signature(fn).parameters
    kw = {}
    for name in ("option_id", "data_id", "futures_id"):
        if name in params:
            kw[name] = "TXO"
            break

    frames = []
    cur = pd.Timestamp(start)
    stop = pd.Timestamp(end)
    while cur <= stop:                      # 一季一次請求，避免單次回傳過大
        seg_end = min(cur + pd.offsets.QuarterEnd(0), stop)
        print(f"  抓 {cur.date()} ~ {seg_end.date()} ...", flush=True)
        try:
            df = fn(start_date=cur.strftime("%Y-%m-%d"),
                    end_date=seg_end.strftime("%Y-%m-%d"), **kw)
        except Exception as e:
            print(f"    失敗（略過這段）：{str(e)[:90]}", flush=True)
            df = None
        if df is not None and not df.empty:
            frames.append(df)
        cur = seg_end + pd.Timedelta(days=1)
    if not frames:
        return None
    df = pd.concat(frames, ignore_index=True)
    df = df[df["trading_session"] == SESSION]
    df = df[df["contract_date"].astype(str).str.match(MONTHLY_RE)]
    for c in ("strike_price", "close", "settlement_price", "volume", "open_interest"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["date"] = pd.to_datetime(df["date"])
    df["call_put"] = df["call_put"].astype(str).str.lower()
    return df.dropna(subset=["strike_price", "close"])


def _margin(premium_pts: float, otm_pts: float, a_val: float, b_val: float) -> float:
    """單口選擇權賣方保證金 = 權利金市值 + MAX(A值 - 價外值, B值)。

    價外值 = 價外點數 × 契約乘數。A/B 值由期交所公告、會隨市況調整，
    所以本檔不內建預設值——用錯的保證金去算「會不會爆倉」比不算更危險。

    ⚠️ 期交所的 A/B 值有三個欄位，用途不同，不可混用：
      原始保證金：**建倉**當下要擺進去的錢
      維持保證金：帳戶權益低於它就**追繳**（這才是「會不會爆倉」那一關）
      結算保證金：給結算會員用的，散戶用不到
    本檔分別收 (margin_a/b)=原始 與 (maint_a/b)=維持。

    ⚠️ 勒式（同時賣 call+put）在期交所有「組合式保證金」可以減收，本檔**刻意
    不減**、直接把兩腳相加。這會高估保證金需求，讓判定偏向「不通過」——
    保守的方向。真要採用時得先向期交所/券商確認實際收法，否則別反過來用
    這個數字去論證「保證金很夠」。
    """
    otm_value = max(otm_pts, 0.0) * MULTIPLIER
    return premium_pts * MULTIPLIER + max(a_val - otm_value, b_val)


def backtest(opt, index, otm_pct: float, equity: float,
             a_val: float | None, b_val: float | None,
             maint_a: float | None = None, maint_b: float | None = None):
    """每月賣一組價外勒式，持有到到期。回傳每月明細與每日評價。"""
    import pandas as pd

    months = sorted(opt["contract_date"].astype(str).unique())
    rows, daily = [], []

    for ym in months:
        exp = _third_wednesday(ym)
        chain = opt[opt["contract_date"].astype(str) == ym]
        if chain.empty:
            continue
        # 建倉日：該契約有資料的第一天（約莫上個月到期後）
        entry = chain["date"].min()
        if entry >= exp:
            continue
        s0 = index.asof(entry)
        s_exp = index.asof(exp)
        if s0 != s0 or s_exp != s_exp:
            continue

        day0 = chain[(chain["date"] == entry) & (chain["volume"] > 0) & (chain["close"] > 0)]
        calls = day0[(day0["call_put"] == "call") & (day0["strike_price"] >= s0 * (1 + otm_pct))]
        puts = day0[(day0["call_put"] == "put") & (day0["strike_price"] <= s0 * (1 - otm_pct))]
        if calls.empty or puts.empty:
            continue
        c = calls.loc[calls["strike_price"].idxmin()]      # 最接近的價外 call
        p = puts.loc[puts["strike_price"].idxmax()]        # 最接近的價外 put

        prem_pts = float(c["close"]) + float(p["close"])
        gross = prem_pts * MULTIPLIER
        spread = gross * CRITERIA["spread_pct"]
        costs = gross * TAX_RATE + 2 * FEE_PER_LOT
        payoff = (max(s_exp - float(c["strike_price"]), 0.0)
                  + max(float(p["strike_price"]) - s_exp, 0.0)) * MULTIPLIER
        pnl = gross - spread - costs - payoff

        # 每日評價（用結算價，冷門履約價 close=0 會憑空生出獲利）
        held = chain[(chain["date"] >= entry) & (chain["date"] <= exp)]
        worst_day, peak_initial, peak_maint = 0.0, 0.0, 0.0
        for d, grp in held.groupby("date"):
            def mark(leg):
                m = grp[(grp["call_put"] == leg["call_put"])
                        & (grp["strike_price"] == leg["strike_price"])]
                if m.empty:
                    return float(leg["close"])
                sp = float(m.iloc[0]["settlement_price"])
                return sp if sp > 0 else float(m.iloc[0]["close"])
            mtm_pts = mark(c) + mark(p)
            worst_day = min(worst_day, gross - spread - costs - mtm_pts * MULTIPLIER)
            s_d = index.asof(d)
            c_otm = float(c["strike_price"]) - s_d          # call 價外點數
            p_otm = s_d - float(p["strike_price"])          # put 價外點數
            if a_val is not None and b_val is not None:
                peak_initial = max(peak_initial,
                                   _margin(mark(c), c_otm, a_val, b_val)
                                   + _margin(mark(p), p_otm, a_val, b_val))
            if maint_a is not None and maint_b is not None:
                peak_maint = max(peak_maint,
                                 _margin(mark(c), c_otm, maint_a, maint_b)
                                 + _margin(mark(p), p_otm, maint_a, maint_b))
            daily.append({"date": d, "month": ym})
        # 到期結算也是一種「評價」，而且常常就是最壞的那一個。
        # 第一版只看持有期間的每日評價，於是「最壞單日評價」會遠小於實際的
        # 最壞單月損益——那等於把尾部藏起來，而尾部正是這個策略的全部重點。
        worst_day = min(worst_day, pnl)

        rows.append({
            "月份": ym, "建倉": entry.date(), "到期": exp.date(),
            "指數": round(float(s0)), "到期指數": round(float(s_exp)),
            "call履約": int(c["strike_price"]), "put履約": int(p["strike_price"]),
            "權利金": round(gross), "賠付": round(payoff), "損益": round(pnl),
            "最壞評價": round(worst_day),
            "原始保證金": round(peak_initial), "維持保證金": round(peak_maint),
        })
    return pd.DataFrame(rows)


def main():
    ap = argparse.ArgumentParser(description="賣價外月選擇權回測（標準先寫死）")
    ap.add_argument("--start", default="2021-07-01")
    ap.add_argument("--end", default="2026-09-30")
    ap.add_argument("--otm", type=float, default=0.05, help="價外幅度（0.05 = 5%%）")
    ap.add_argument("--equity", type=float, default=500_000, help="帳戶權益（算爆倉用）")
    ap.add_argument("--margin-a", type=float, default=None,
                    help="臺指選擇權風險保證金(A)值・**原始保證金**欄（建倉要擺的錢）")
    ap.add_argument("--margin-b", type=float, default=None,
                    help="臺指選擇權風險保證金(B)值・**原始保證金**欄")
    ap.add_argument("--maint-a", type=float, default=None,
                    help="(A)值的**維持保證金**欄（跌破就追繳；不給則沿用原始值，偏嚴）")
    ap.add_argument("--maint-b", type=float, default=None,
                    help="(B)值的**維持保證金**欄")
    args = ap.parse_args()

    import pandas as pd

    import main as cli
    cli._load_dotenv()

    from src.data.cache import DiskCachingProvider
    from src.data.finmind import FinMindProvider
    provider = DiskCachingProvider(FinMindProvider())

    print(f"抓加權指數 {args.start} ~ {args.end} ...", flush=True)
    index = provider.benchmark(args.start, args.end)
    if index is None or index.empty:
        print("❌ 抓不到大盤。先 rm -f data_cache/bm_*.pkl 再試。")
        return 1
    print(f"抓選擇權日資料（一季一次請求）...", flush=True)
    inner = getattr(provider, "inner", provider)
    api = getattr(inner, "api", None)
    if api is None:
        print("❌ 拿不到 FinMind DataLoader（provider 結構變了？）")
        return 1
    opt = _load_options(api, args.start, args.end)
    if opt is None or opt.empty:
        print("❌ 抓不到選擇權資料。先跑 tools/txo_data_probe.py 確認。")
        return 1
    print(f"月選主時段 {len(opt):,} 列\n")

    # 沒給維持保證金就沿用原始值。原始 > 維持，所以追繳會判得比實際更嚴 —— 偏保守。
    maint_a = args.maint_a if args.maint_a is not None else args.margin_a
    maint_b = args.maint_b if args.maint_b is not None else args.margin_b
    res = backtest(opt, index, args.otm, args.equity,
                   args.margin_a, args.margin_b, maint_a, maint_b)
    if res.empty:
        print("❌ 建不出任何部位（可能價外幅度太遠、或資料不足）。")
        return 1

    print("=" * 100)
    print(res.to_string(index=False))

    total = res["損益"].sum()
    worst_m = res["損益"].min()
    worst_d = res["最壞評價"].min()
    win = (res["損益"] > 0).mean()
    print("\n" + "=" * 100)
    print(f"樣本 {len(res)} 個月｜總損益 {total:+,.0f}｜勝率 {win:.0%}"
          f"｜平均每月 {res['損益'].mean():+,.0f}")
    print(f"**最壞單月 {worst_m:+,.0f}（權益的 {worst_m / args.equity:+.1%}）**"
          f"｜最壞單日評價 {worst_d:+,.0f}")
    print(f"價差成本假設 {CRITERIA['spread_pct']:.0%}（事前寫死）"
          f"｜已扣交易稅 {TAX_RATE:.1%} 與每口 {FEE_PER_LOT:.0f} 元手續費")

    print("\n" + "=" * 100)
    if args.margin_a is None or args.margin_b is None:
        print("⚠️ **沒有提供 --margin-a / --margin-b，本檔拒絕給出通過與否的判定。**\n")
        print("   保證金夠不夠，是這個策略唯一真正會殺死人的地方：")
        print("   追繳發生那天帳戶就結束了，但回測會若無其事地繼續交易下去，")
        print("   於是「平均報酬」照樣漂亮。沒有保證金就算不出這一關，")
        print("   給半套判定只會讓人誤以為驗證過了。")
        print("\n   去期交所抄「臺指選擇權風險保證金(A)值／(B)值」（會隨市況調整）再跑一次。")
        print("   那張表有三欄、用途不同，別抄錯：")
        print("     原始保證金 = 建倉當下要擺進去的錢")
        print("     維持保證金 = 權益跌破它就被追繳（「會不會爆倉」看這個）")
        print("     結算保證金 = 結算會員用的，散戶用不到")
        print("   .venv/bin/python tools/txo_short_backtest.py \\")
        print("       --margin-a <A原始> --margin-b <B原始> \\")
        print("       --maint-a <A維持> --maint-b <B維持>")
        print(f"\n   先看得到的部分：最壞單月 {worst_m:+,.0f}、最壞單日評價 {worst_d:+,.0f}。")
        print(f"   光這個數字就已經是權益的 {abs(worst_d) / args.equity:.0%}——"
              f"而真實世界還要加上追繳。")
        return 0

    peak_init = res["原始保證金"].max()
    peak_maint = res["維持保證金"].max()
    checks = [
        (f"A 樣本 >= {CRITERIA['min_months']} 個月", len(res) >= CRITERIA["min_months"],
         f"{len(res)} 個月"),
        ("B 扣成本後總損益 > 0", total > CRITERIA["min_total_pnl"], f"{total:+,.0f}"),
        ("C 任何一天都沒被追繳（權益 >= 維持保證金）", peak_maint <= args.equity,
         f"維持保證金最高 {peak_maint:,.0f} / 權益 {args.equity:,.0f}"),
        ("C2 建倉時擺得出原始保證金", peak_init <= args.equity,
         f"原始保證金最高 {peak_init:,.0f} / 權益 {args.equity:,.0f}"),
        (f"D 最壞單月虧損 <= 權益的 {CRITERIA['max_month_loss']:.0%}",
         worst_m >= -args.equity * CRITERIA["max_month_loss"],
         f"{worst_m:+,.0f}（{worst_m / args.equity:+.1%}）"),
    ]
    print("逐項判定（標準見 CRITERIA，事前寫死）：\n")
    for name, ok, detail in checks:
        print(f"  {'✓' if ok else '✗'} {name}：{detail}")
    ok_all = all(o for _, o, _ in checks)
    print("\n" + "=" * 100)
    if ok_all:
        print("🟡 四項都過——但**這不等於可以上真錢**。")
        print("   回測用的是收盤價近似、忽略盤中追繳與滑價，而賣選擇權真正的風險")
        print("   發生在盤中（指數跳空、隱含波動率暴衝，保證金當場翻倍）。")
        print("   下一步：紙上空跑，而且一定要跨過一次大波動再談。")
    else:
        print("🔴 未通過 → 不採用。")
    print("\n（本檔只做回測，不下單、不改任何設定。）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
