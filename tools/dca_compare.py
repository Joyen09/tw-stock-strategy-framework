#!/usr/bin/env python3
"""定期定額要不要加均線濾網？——用 18 年資料決定，不要憑感覺。

# 為什麼需要這支

2026-10-06 的穩健性測試結論：均線濾網是**風險工具**（回撤 -56% → -26%，
代價年化 -0.78pp），報酬面的優勢是期間運氣。

但那個測試是用「一次買進、長期持有」當基準算的。**定期定額的數學不一樣**，
而且這裡有個直接的矛盾：

  - 定期定額的核心價值是「跌的時候同樣的錢買到更多股數」
  - 均線濾網的動作是「跌破年線就不買」

**兩個機制方向相反。** 濾網讓你避開下跌，但定期定額本來就想買在下跌。
所以「濾網對定期定額到底是幫忙還是扯後腿」是一個獨立的問題，不能
拿前一個測試的結論套用。這支就是測這件事。

# 三個版本

1. **純定期定額**：每月第一個交易日固定金額買進，不管行情
2. **定期定額 + 濾網**：跌破均線那個月不買，現金留著；回到均線上方再補買
3. **一次買進**：期初把全部錢投入（對照用，看定期定額本身的代價）

# 通過標準（PRE-REGISTERED，跑之前就寫死）

濾網版要被採用，必須**同時**滿足：
  A. 期末市值 / 總投入 >= 純定期定額 × 0.98（報酬最多只能差 2%）
  B. 最大回撤比純定期定額**明顯**更小（至少改善 5 個百分點）
兩項不同時成立 → **不採用濾網，用最單純的純定期定額。**

理由：濾網的唯一賣點是降風險。如果它既沒降多少風險、又讓報酬變差，
那它就只是多出來的複雜度與多出來的手續費。

# 成本

- 每次買進扣手續費（**含最低 20 元**——小額定期定額這一條很傷：
  每月 10,000 元的手續費是 max(10000×0.1425%×0.28, 20) = 20 元 = 0.2%，
  比費率算出來的 0.04% 貴五倍。很多人算定期定額時漏掉這個）
- 濾網版多出來的賣出成本不計：它是「不買」而不是「賣出」，不產生證交稅
- ⚠️ 沒算 ETF 內扣費用（0050 約 0.32%/年、006208 約 0.24%/年），三個版本都一樣

只打 1 次 API（抓一次長期 TAIEX，其餘本地算）。

用法：
    .venv/bin/python tools/dca_compare.py
    .venv/bin/python tools/dca_compare.py --monthly 5000 --ma 150
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
    "return_min_ratio": 0.98,   # A：濾網版期末倍數 >= 純定期定額 × 0.98
    "dd_min_improve": 0.05,     # B：最大回撤至少要改善 5 個百分點
}


def _buy_fee(amount: float, fee_discount: float) -> float:
    """台股買進手續費，**含最低 20 元**。小額定期定額最常被漏掉的成本。"""
    from src.broker import fees
    return max(amount * fees.BROKER_FEE_RATE * fee_discount, fees.MIN_FEE)


def _month_starts(index):
    """每個月的第一個交易日。"""
    import pandas as pd
    df = pd.Series(index, index=index)
    return list(df.groupby([index.year, index.month]).first())


def _simulate_dca(close, monthly: float, fee_discount: float,
                  ma_window: int = 0):
    """跑定期定額，回傳（每日市值曲線, 總投入, 買進次數, 跳過次數）。

    ma_window=0 → 純定期定額（不看行情）。
    >0 → 跌破均線那個月不買，錢留在現金，之後站上均線的扣款日照常買
         （現金會累積，所以不是「少投入」而是「晚投入」）。
    """
    import pandas as pd

    ma = close.rolling(ma_window).mean() if ma_window else None
    buy_days = set(_month_starts(close.index))

    shares = 0.0
    cash = 0.0          # 濾網版暫時沒投進去的錢
    invested = 0.0      # 累計投入（從口袋掏出來的總額）
    bought = skipped = 0
    values = []
    for d in close.index:
        px = float(close.loc[d])
        if d in buy_days:
            invested += monthly
            cash += monthly
            # 濾網：當日收盤在均線之上才買（均線算不出來時視為可買，不憑空擋）
            ok = True
            if ma is not None:
                m = float(ma.loc[d]) if ma.loc[d] == ma.loc[d] else None
                ok = (m is None) or (px >= m)
            if ok and cash > 0:
                fee = _buy_fee(cash, fee_discount)
                if cash > fee:
                    shares += (cash - fee) / px
                    cash = 0.0
                    bought += 1
            elif not ok:
                skipped += 1
        values.append(shares * px + cash)
    return pd.Series(values, index=close.index), invested, bought, skipped


def _simulate_lump(close, total: float, fee_discount: float):
    """期初一次買進全部（對照組）。"""
    px0 = float(close.iloc[0])
    fee = _buy_fee(total, fee_discount)
    shares = (total - fee) / px0
    return close * shares, total


def _irr(curve, invested_per_month: float, months: int, years: float) -> float:
    """資金加權年化報酬（每月固定投入的 IRR），用二分法解。

    定期定額不能用「期末/總投入」直接算年化——後面投入的錢只放了幾個月。
    """
    final = float(curve.iloc[-1])
    def fv(rate):
        # 每月投入在期末的終值（月利率 r/12 近似）
        r = (1 + rate) ** (1 / 12) - 1
        if abs(r) < 1e-12:
            return invested_per_month * months
        return invested_per_month * (((1 + r) ** months - 1) / r)
    lo, hi = -0.99, 1.0
    for _ in range(200):
        mid = (lo + hi) / 2
        if fv(mid) < final:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def _dd(curve) -> float:
    return float((curve / curve.cummax() - 1).min())


def _pad(text: str, width: int) -> str:
    """靠左補到指定「顯示寬度」。中文在終端機算兩格，用 len() 補會排不齊。"""
    import unicodedata
    w = sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in text)
    return text + " " * max(width - w, 0)


def main():
    ap = argparse.ArgumentParser(description="定期定額要不要加均線濾網（標準先寫死）")
    ap.add_argument("--start", default="2008-01-01")
    ap.add_argument("--end", default="")
    ap.add_argument("--monthly", type=float, default=10_000, help="每月投入金額")
    ap.add_argument("--ma", type=int, default=150, help="濾網均線長度（0=不測濾網）")
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
    if close is None or len(close) < args.ma + 250:
        print("❌ 抓不到足夠的大盤資料。TAIEX 逾時會被快取成哨兵，"
              "先 `rm -f data_cache/bm_*.pkl` 再重試。")
        return 1
    close = close.dropna()
    years = (close.index[-1] - close.index[0]).days / 365.25
    print(f"拿到 {len(close)} 根，{close.index[0].date()} ~ {close.index[-1].date()}"
          f"（{years:.1f} 年）\n")

    plain, inv_p, n_p, _ = _simulate_dca(close, args.monthly, args.fee_discount, 0)
    filt, inv_f, n_f, skip_f = _simulate_dca(close, args.monthly, args.fee_discount, args.ma)
    lump, inv_l = _simulate_lump(close, inv_p, args.fee_discount)

    months = n_p + 0  # 純定期定額每個月都買，次數=扣款月數
    rows = [
        ("純定期定額", plain, inv_p, False, f"{n_p} 次買進"),
        (f"定期定額+{args.ma}MA", filt, inv_f, False, f"{n_f} 次買進、跳過 {skip_f} 次"),
        ("期初一次買進", lump, inv_l, True, "1 次買進（對照組）"),
    ]

    print("=" * 72)
    print(f"每月投入 {args.monthly:,.0f} 元，共 {months} 個月（總投入 {inv_p:,.0f}）")
    print(f"{_pad('版本', 20)}{'期末市值':>13}{'倍數':>7}{'年化':>8}{'回撤':>9}")
    print("-" * 72)
    notes = []
    for tag, curve, inv, is_lump, note in rows:
        final = float(curve.iloc[-1])
        mult = final / inv if inv else float("nan")
        # 一次買進的錢是期初全額投入 → 用時間加權；定期定額要用資金加權 IRR
        cagr = ((final / inv) ** (1 / years) - 1) if is_lump else _irr(curve, args.monthly, months, years)
        print(f"{_pad(tag, 20)}{final:>13,.0f}{mult:>7.2f}{cagr:>8.2%}{_dd(curve):>9.1%}")
        notes.append(f"  {tag}：{note}")
    # 說明另外列，不要接在表格後面——中文是雙寬字元，接上去會超過終端寬度，
    # 折行時把前面的欄位蓋掉（2026-10 實測輸出變成「定跳過 60 次日濾網」）。
    print()
    for n in notes:
        print(n)

    # ── 判定 ──
    mult_p = float(plain.iloc[-1]) / inv_p
    mult_f = float(filt.iloc[-1]) / inv_f
    dd_p, dd_f = _dd(plain), _dd(filt)
    improve = dd_f - dd_p          # 正值 = 回撤比較淺

    print("\n" + "=" * 84)
    print("判定（標準見 CRITERIA，跑之前就寫死）：\n")
    a = mult_f >= mult_p * CRITERIA["return_min_ratio"]
    b = improve >= CRITERIA["dd_min_improve"]
    print(f"  {'✓' if a else '✗'} A 報酬不比純定期定額差太多"
          f"（>= ×{CRITERIA['return_min_ratio']}）：倍數 {mult_f:.2f} vs {mult_p:.2f}")
    print(f"  {'✓' if b else '✗'} B 最大回撤至少改善 {CRITERIA['dd_min_improve']:.0%}："
          f"{dd_f:.1%} vs {dd_p:.1%}（改善 {improve:+.1%}）")

    print("\n" + "=" * 84)
    if a and b:
        print(f"🟢 兩項都過 → **採用「定期定額 + {args.ma} 日濾網」**。")
        print("   它用很小的報酬代價換到明顯更淺的回撤。")
    else:
        print("🔴 沒有同時滿足 → **不採用濾網，用最單純的純定期定額。**")
        print("   這是好消息：規則愈少愈不會壞、愈不會被自己的手改掉。")
        print("   機制上也說得通——定期定額本來就想買在下跌，濾網卻叫你跌時別買，")
        print("   兩個機制方向相反，疊在一起互相抵銷。")
    print("\n（本檔只做驗證，不改任何設定、不下任何單。）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
