#!/usr/bin/env python3
"""年線濾網到底是真的，還是剛好在這段歷史上運氣好？——穩健性測試。

# 背景

2026-09-22 的全週期對照發現：**不選股、只對大盤做 200 日均線濾網**
（站上持有、跌破空手），2021-07~2025-12 拿到報酬 +68.81%、夏普 0.91、
回撤 -18.69%，風險調整後贏過三組實盤策略的全部。

這個結果好到必須懷疑。均線濾網有個眾所周知的弱點：**盤整市會被來回巴**
（站上就買、跌破就賣，來回幾次手續費與價差就吃掉報酬）。
2021~2025 剛好只有一個乾淨的大波段（2022 一路跌、2024~25 一路漲），
那是對均線濾網最友善的形狀。換一段歷史可能完全不是這樣。

# 這支在問什麼

一個真實的效果應該要「換條件也還在」。所以測三件事：

1. **換均線長度**（100/150/200/250 日）——只有 200 日有效 = 參數挑出來的，不是規律
2. **換期間**（滾動 3 年窗口，一年推一次）——只在某幾年有效 = 運氣
3. **盤整年單獨看**（2015、2018 這種上上下下的年份）——濾網最該露餡的地方

# 通過標準（PRE-REGISTERED，跑之前就寫死）

這支是**在跑出任何結果之前**寫完的，下面的標準沒有看過答案：

- A. 均線長度不敏感：4 種長度裡至少 3 種，全期回撤要優於買進持有
- B. 期間不敏感：滾動窗口裡「回撤有改善」的比例 >= 70%
- C. 報酬不能為了防守賠太多：滾動窗口裡「報酬落後買進持有不超過 5 個百分點」
     的比例 >= 50%

三項全過 = 穩健；過 2 項 = 有條件成立（只能當降波動工具，不能當賺更多工具）；
過 1 項以下 = 那 +68.81% 就是這段歷史的運氣，別當真。

# 成本與現實

- 手續費與證交稅照扣（進出場各一次）
- ⚠️ 沒算 ETF 內扣費用（0050 約 0.32%/年）與買賣價差，實際會再差一點
- ⚠️ 用加權指數當標的，實際要用 0050，兩者有追蹤誤差

只打 **1 次 API**（抓一次長期 TAIEX，其餘在本地切窗口算），不吃 FinMind 額度。

用法：
    .venv/bin/python tools/ma_filter_robustness.py
    .venv/bin/python tools/ma_filter_robustness.py --start 2005-01-01 --windows 5
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
    "ma_lengths_beating_bh_dd": 3,   # A：4 種長度裡至少幾種回撤優於買進持有
    "window_dd_improve_ratio": 0.70,  # B：滾動窗口中回撤有改善的比例
    "window_return_lag_tol": 0.05,    # C：報酬落後買進持有可容忍的幅度
    "window_return_ok_ratio": 0.50,   # C：滿足上面容忍度的窗口比例
}

MA_LENGTHS = [100, 150, 200, 250]


def _simulate(close, ma_window: int, fee_discount: float):
    """對一條指數序列跑「站上均線持有、跌破空手」，回傳每日報酬序列（已扣成本）。

    當日收盤決策、次日生效（shift 1），不用當天的收盤決定當天的部位。
    """
    from src.broker import fees

    ma = close.rolling(ma_window).mean()
    pos = (close >= ma).shift(1).fillna(False).astype(bool)   # 次日才生效
    ret = close.pct_change().fillna(0.0)
    buy_rate = fees.BROKER_FEE_RATE * fee_discount
    sell_rate = fees.BROKER_FEE_RATE * fee_discount + fees.TAX_RATE
    switch = pos.ne(pos.shift(1).fillna(False))
    cost = switch.astype(float) * pos.map(lambda x: buy_rate if x else sell_rate)
    return ret * pos.astype(float) - cost, pos, switch


def _stats(daily_ret):
    """由每日報酬算 報酬／夏普／最大回撤。"""
    if daily_ret is None or len(daily_ret) < 2:
        return None
    curve = (1 + daily_ret).cumprod()
    sd = float(daily_ret.std())
    return {
        "ret": float(curve.iloc[-1]) - 1,
        "sharpe": float(daily_ret.mean() / sd * (252 ** 0.5)) if sd else 0.0,
        "dd": float((curve / curve.cummax() - 1).min()),
    }


def _row(tag, st, bh, switches=None):
    ex = st["ret"] - bh["ret"]
    dd_gain = st["dd"] - bh["dd"]      # 正值 = 回撤比較淺（改善）
    sw = f"{switches:>6}" if switches is not None else f"{'—':>6}"
    return (f"{tag:<16}{st['ret']:>9.1%}{ex:>9.1%}{st['sharpe']:>7.2f}"
            f"{st['dd']:>9.1%}{dd_gain:>+9.1%}{sw}")


def main():
    ap = argparse.ArgumentParser(description="年線濾網穩健性測試（標準先寫死）")
    ap.add_argument("--start", default="2008-01-01", help="資料起點（含暖身）")
    ap.add_argument("--end", default="", help="資料終點（預設今天）")
    ap.add_argument("--windows", type=int, default=3, help="滾動窗口長度（年）")
    ap.add_argument("--fee-discount", type=float, default=0.28)
    ap.add_argument("--source", default="finmind", choices=["finmind", "sample"])
    args = ap.parse_args()

    import pandas as pd

    import main as cli
    cli._load_dotenv()

    end = args.end or pd.Timestamp.today().strftime("%Y-%m-%d")
    if args.source == "finmind":
        from src.data.cache import DiskCachingProvider
        from src.data.finmind import FinMindProvider
        provider = DiskCachingProvider(FinMindProvider())
    else:
        from src.data.sample import SampleDataProvider
        provider = SampleDataProvider()

    print(f"抓加權指數 {args.start} ~ {end}（只打 1 次 API）...", flush=True)
    close = provider.benchmark(args.start, end)
    if close is None or len(close) < max(MA_LENGTHS) + 250:
        print("❌ 抓不到足夠的大盤資料。TAIEX 逾時會被快取成哨兵，"
              "先 `rm -f data_cache/bm_*.pkl` 再重試。")
        return 1
    close = close.dropna()
    print(f"拿到 {len(close)} 根，{close.index[0].date()} ~ {close.index[-1].date()}\n")

    bh_daily = close.pct_change().fillna(0.0)
    hdr = f"{'':<16}{'報酬':>9}{'超額':>9}{'夏普':>7}{'回撤':>9}{'回撤改善':>9}{'進出':>6}"

    # ── 測試 1：換均線長度 ──
    print("=" * 78)
    print(f"測試 1：換均線長度（全期 {close.index[0].date()} ~ {close.index[-1].date()}）")
    print(hdr)
    print("-" * 78)
    bh_all = _stats(bh_daily)
    print(_row("買進持有", bh_all, bh_all))
    ma_pass = 0
    for m in MA_LENGTHS:
        d, _, sw = _simulate(close, m, args.fee_discount)
        st = _stats(d)
        if st["dd"] > bh_all["dd"]:
            ma_pass += 1
        print(_row(f"{m}日均線", st, bh_all, int(sw.sum())))

    # ── 測試 2：滾動窗口（用 200 日，因為那是被質疑的那一個）──
    print("\n" + "=" * 78)
    print(f"測試 2：滾動 {args.windows} 年窗口（200 日均線）——只在某幾年有效 = 運氣")
    print(hdr)
    print("-" * 78)
    d200, _, sw200 = _simulate(close, 200, args.fee_discount)
    years = sorted({d.year for d in close.index})
    dd_better = ret_ok = total = 0
    for y0 in years:
        y1 = y0 + args.windows
        if y1 - 1 > years[-1]:
            break
        a, b = f"{y0}-01-01", f"{y1 - 1}-12-31"
        seg_s, seg_b = _stats(d200.loc[a:b]), _stats(bh_daily.loc[a:b])
        if seg_s is None or seg_b is None:
            continue
        total += 1
        if seg_s["dd"] > seg_b["dd"]:
            dd_better += 1
        if seg_s["ret"] - seg_b["ret"] >= -CRITERIA["window_return_lag_tol"]:
            ret_ok += 1
        print(_row(f"{y0}~{y1 - 1}", seg_s, seg_b, int(sw200.loc[a:b].sum())))

    # ── 測試 3：盤整年單看 ──
    print("\n" + "=" * 78)
    print("測試 3：盤整／假跌破年份單獨看（均線濾網最該露餡的地方）")
    print(hdr)
    print("-" * 78)
    for y in (2011, 2015, 2016, 2018, 2019, 2021):
        a, b = f"{y}-01-01", f"{y}-12-31"
        seg_s, seg_b = _stats(d200.loc[a:b]), _stats(bh_daily.loc[a:b])
        if seg_s is None or seg_b is None:
            continue
        print(_row(f"{y} 年", seg_s, seg_b, int(sw200.loc[a:b].sum())))

    # ── 判定 ──
    print("\n" + "=" * 78)
    print("判定（標準見 CRITERIA，跑之前就寫死）：\n")
    dd_ratio = dd_better / total if total else 0.0
    ret_ratio = ret_ok / total if total else 0.0
    checks = [
        (f"A 均線長度不敏感：{CRITERIA['ma_lengths_beating_bh_dd']}/{len(MA_LENGTHS)} 種以上回撤優於買進持有",
         ma_pass >= CRITERIA["ma_lengths_beating_bh_dd"], f"{ma_pass}/{len(MA_LENGTHS)} 種"),
        (f"B 期間不敏感：回撤有改善的窗口 >= {CRITERIA['window_dd_improve_ratio']:.0%}",
         dd_ratio >= CRITERIA["window_dd_improve_ratio"], f"{dd_better}/{total} = {dd_ratio:.0%}"),
        (f"C 報酬沒賠太多：落後買進持有 <= {CRITERIA['window_return_lag_tol']:.0%} 的窗口 >= "
         f"{CRITERIA['window_return_ok_ratio']:.0%}",
         ret_ratio >= CRITERIA["window_return_ok_ratio"], f"{ret_ok}/{total} = {ret_ratio:.0%}"),
    ]
    for name, ok, detail in checks:
        print(f"  {'✓' if ok else '✗'} {name}：{detail}")
    passed = sum(1 for _, ok, _ in checks if ok)

    print("\n" + "=" * 78)
    if passed == 3:
        print("🟢 三項全過 → 這條均線是穩健的，不是這段歷史的運氣。")
        print("   它可以當一個認真的候選：規則極簡、不選股、一年動幾次。")
        print("   但仍要記得沒算 0050 內扣費用（約 0.32%/年）與追蹤誤差。")
    elif passed == 2:
        print("🟡 過 2 項 → 有條件成立：**當降波動工具可以，當賺更多工具不行。**")
        print("   也就是說它的價值是「睡得著覺」，不是「賺更多」。")
        print("   要不要為了這個付出多年的紀律，是你的偏好問題，不是數學問題。")
    else:
        print("🔴 只過 {} 項 → 那 +68.81% 主要是 2021~2025 這段形狀給的運氣。".format(passed))
        print("   均線濾網在盤整年會被來回巴，這次剛好避開了。**不要當真。**")
        print("   結論回到最樸素的那一個：定期定額買指數，不擇時也不選股。")
    print("\n（本檔只做驗證，不改任何設定、不下任何單。）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
