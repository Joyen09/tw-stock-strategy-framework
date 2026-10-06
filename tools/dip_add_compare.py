#!/usr/bin/env python3
"""機械式逢跌加碼：值不值得為它預留現金？——用 18 年資料決定。

# 這是什麼

衛星層的第一個方向。規則完全機械，**不預測任何事**：

  - 每月投入 A 元，但只拿 (1-r) 投進去，剩下 r 進「現金池」
  - 大盤從近期高點回落達門檻時，把現金池按比例投入（階梯式：跌更深投更多）
  - 大盤創新高時，階梯重置（下一輪下跌可以再觸發一次）

它的邊來自「買在低點」這個機械行為，不來自看對未來。這一點很重要——
2026-09~10 測掉的全部是「用預測去選股／擇時」，這支測的是另一類東西。

# 但它有一個真實的代價，而且很容易被忽略

**現金拖累。** 為了有錢可以加碼，你平時得讓一部分錢躺著不進市場。
台股長期上漲，躺著的錢就是在賠機會成本。所以這支的核心問題不是
「跌的時候加碼好不好」（當然好），而是：

  **為了那幾次加碼而長期預留現金，整體划不划算？**

這就是為什麼不能只看「加碼那幾筆賺多少」，要看**整體 IRR**。
報表會把平均現金拖累印出來，讓代價看得見。

# 通過標準（PRE-REGISTERED，跑之前就寫死）

加碼版要被採用，必須**同時**滿足：
  A. 資金加權年化 (IRR) **嚴格高於**純定期定額——它多了複雜度與現金拖累，
     打平不算贏，必須真的更好
  B. 最大回撤不可惡化超過 2 個百分點
  C. 加碼至少真的觸發過 3 次，否則是「沒測到」不是「通過」
     （門檻設太深就永遠不觸發，結果會跟純定期定額一模一樣而自動過關——
      這是 2026-08 空頭壓測空過的同一個陷阱）
三項不同時成立 → 不採用，維持純定期定額。

⚠️ **不要掃 --reserve 去找一個會過關的值。** 這個參數有退化極限：
reserve → 0 時加碼版就收斂成純定期定額，差異只剩雜訊，於是一定存在
某個夠小的值讓它「通過」。那是定義上的自欺，不是發現。

# 成本

- 每次買進扣手續費（含最低 20 元；小額扣款由它主導）
- 加碼是買進，不產生證交稅
- ⚠️ 沒算 ETF 內扣費用，兩個版本都一樣所以不影響比較

只打 1 次 API。

用法：
    .venv/bin/python tools/dip_add_compare.py
    .venv/bin/python tools/dip_add_compare.py --reserve 0.3 --tiers 10:0.4,20:0.5,30:1.0
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
    "irr_must_exceed": True,    # A：IRR 必須嚴格高於純定期定額（打平不算贏）
    "dd_tolerance": 0.02,       # B：最大回撤惡化容忍上限
    "min_triggers": 3,          # C：加碼至少要真的觸發幾次（防空過）
}

# 預設階梯：從近期高點回落 10% 投入池子的 1/3、20% 再投剩下一半、30% 全投
DEFAULT_TIERS = [(0.10, 1 / 3), (0.20, 0.5), (0.30, 1.0)]
PEAK_WINDOW = 250  # 「近期高點」的回看窗口（約一年）


def _parse_tiers(text: str):
    """'10:0.33,20:0.5,30:1.0' -> [(0.10, 0.33), ...]"""
    out = []
    for part in (text or "").split(","):
        part = part.strip()
        if not part:
            continue
        dd, frac = part.split(":")
        out.append((float(dd) / 100.0, float(frac)))
    return sorted(out) or DEFAULT_TIERS


def _buy_fee(amount: float, fee_discount: float) -> float:
    from src.broker import fees
    return max(amount * fees.BROKER_FEE_RATE * fee_discount, fees.MIN_FEE)


def _month_starts(index):
    import pandas as pd
    ser = pd.Series(index, index=index)
    return set(ser.groupby([index.year, index.month]).first())


def _buy(shares_held: float, cash: float, price: float, fee_discount: float):
    """把 cash 盡量換成股數，回傳 (新股數, 剩餘現金)。買不起就原樣返回。"""
    if cash <= 0 or price <= 0:
        return shares_held, cash
    fee = _buy_fee(cash, fee_discount)
    if cash <= fee:
        return shares_held, cash
    return shares_held + (cash - fee) / price, 0.0


def simulate(close, monthly: float, reserve: float, tiers, fee_discount: float):
    """跑「定期定額 + 機械式逢跌加碼」。reserve=0 且 tiers=[] 就是純定期定額。

    回傳 dict：市值曲線、總投入、加碼觸發次數、平均現金拖累。
    """
    import pandas as pd

    buy_days = _month_starts(close.index)
    peak = close.rolling(PEAK_WINDOW, min_periods=1).max()

    shares = 0.0
    pool = 0.0          # 預留等著加碼的現金
    spare = 0.0         # 買不起 1 股時暫存的零頭（不算拖累，很小）
    invested = 0.0
    triggers = 0
    fired = set()       # 本輪下跌已觸發過的階梯（創新高時清空）
    values, drags = [], []

    for d in close.index:
        px = float(close.loc[d])
        dd = px / float(peak.loc[d]) - 1

        if dd >= -0.005:            # 回到近期高點附近 → 下一輪可以重新觸發
            fired.clear()

        if d in buy_days:
            invested += monthly
            put_in = monthly * (1 - reserve)
            pool += monthly * reserve
            shares, spare = _buy(shares, spare + put_in, px, fee_discount)

        # 階梯加碼：由深到淺檢查，一天最多觸發一層，避免同日連環投入。
        #
        # 刻意的設計：**池子沒錢時不算「已觸發」**，那一層留著下次有錢再用。
        # 所以一次 -40% 的崩盤會是「當天最深那層把池子清空 → 之後每月新存進
        # 池子的錢繼續投入（用掉較淺的層）」。這是故意的：崩盤往往持續好幾個月，
        # 這時候新存的錢應該進場買便宜，而不是躺著等到大盤創新高才動。
        # 代價是一輪下跌最多只會投入 len(tiers) 次，到期就停——這是為了避免
        # 變成「無限攤平」，那會在長期陰跌裡把錢全部倒進去。
        for thr, frac in sorted(tiers, key=lambda t: -t[0]):
            if thr in fired or dd > -thr or pool <= 0:
                continue
            use = pool * frac
            before = shares
            shares, left = _buy(shares, use, px, fee_discount)
            pool = pool - use + left
            fired.add(thr)
            if shares > before:
                triggers += 1
            break

        total = shares * px + pool + spare
        values.append(total)
        drags.append((pool + spare) / total if total > 0 else 0.0)

    return {
        "curve": pd.Series(values, index=close.index),
        "invested": invested,
        "months": len(buy_days),
        "triggers": triggers,
        "drag": sum(drags) / len(drags) if drags else 0.0,
    }


def _irr(final: float, monthly: float, months: int) -> float:
    """每月固定投入的資金加權年化（二分法）。"""
    def fv(rate):
        r = (1 + rate) ** (1 / 12) - 1
        if abs(r) < 1e-12:
            return monthly * months
        return monthly * (((1 + r) ** months - 1) / r)
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
    """靠左補到「顯示寬度」。中文在終端算兩格，用 len() 補會排不齊、折行蓋字。"""
    import unicodedata
    w = sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in text)
    return text + " " * max(width - w, 0)


def main():
    ap = argparse.ArgumentParser(description="機械式逢跌加碼驗證（標準先寫死）")
    ap.add_argument("--start", default="2008-01-01")
    ap.add_argument("--end", default="")
    ap.add_argument("--monthly", type=float, default=10_000)
    ap.add_argument("--reserve", type=float, default=0.3, help="每月保留進現金池的比例")
    ap.add_argument("--tiers", default="10:0.333,20:0.5,30:1.0",
                    help="階梯 '回落%%:投入池子比例'，逗號分隔")
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
    if close is None or len(close) < 300:
        print("❌ 抓不到足夠的大盤資料。先 `rm -f data_cache/bm_*.pkl` 再重試。")
        return 1
    close = close.dropna()
    years = (close.index[-1] - close.index[0]).days / 365.25
    tiers = _parse_tiers(args.tiers)

    plain = simulate(close, args.monthly, 0.0, [], args.fee_discount)
    dip = simulate(close, args.monthly, args.reserve, tiers, args.fee_discount)

    print(f"拿到 {len(close)} 根，{close.index[0].date()} ~ {close.index[-1].date()}"
          f"（{years:.1f} 年）")
    print(f"每月 {args.monthly:,.0f} 元 × {plain['months']} 個月"
          f"（總投入 {plain['invested']:,.0f}）")
    print(f"加碼設定：保留 {args.reserve:.0%} 進池，階梯 "
          f"{'、'.join(f'跌{t:.0%}投池{f:.0%}' for t, f in tiers)}\n")

    print("=" * 72)
    print(f"{_pad('版本', 20)}{'期末市值':>13}{'年化':>8}{'回撤':>9}{'現金拖累':>10}")
    print("-" * 72)
    rows = [("純定期定額", plain), ("＋機械式逢跌加碼", dip)]
    for tag, r in rows:
        final = float(r["curve"].iloc[-1])
        print(f"{_pad(tag, 20)}{final:>13,.0f}"
              f"{_irr(final, args.monthly, r['months']):>8.2%}"
              f"{_dd(r['curve']):>9.1%}{r['drag']:>10.1%}")
    print(f"\n  加碼實際觸發 {dip['triggers']} 次"
          f"（平時有 {dip['drag']:.1%} 的錢躺在池子裡沒進市場——這是代價）")

    # ── 判定 ──
    irr_p = _irr(float(plain["curve"].iloc[-1]), args.monthly, plain["months"])
    irr_d = _irr(float(dip["curve"].iloc[-1]), args.monthly, dip["months"])
    dd_p, dd_d = _dd(plain["curve"]), _dd(dip["curve"])

    print("\n" + "=" * 72)
    print("判定（標準見 CRITERIA，跑之前就寫死）：\n")
    c = dip["triggers"] >= CRITERIA["min_triggers"]
    checks = [(f"C 加碼真的觸發 >= {CRITERIA['min_triggers']} 次", c,
               f"{dip['triggers']} 次" + ("" if c else " → 門檻太深沒觸發，這次沒測到東西"))]
    a = irr_d > irr_p
    checks.append(("A 年化嚴格高於純定期定額", a, f"{irr_d:.2%} vs {irr_p:.2%}"))
    b = dd_d >= dd_p - CRITERIA["dd_tolerance"]
    checks.append((f"B 回撤不惡化超過 {CRITERIA['dd_tolerance']:.0%}", b,
                   f"{dd_d:.1%} vs {dd_p:.1%}"))
    for name, ok, detail in checks:
        print(f"  {'✓' if ok else '✗'} {name}：{detail}")

    print("\n" + "=" * 72)
    if a and b and c:
        print("🟢 三項都過 → **採用機械式逢跌加碼當衛星層。**")
        print(f"   它用 {dip['drag']:.1%} 的現金拖累換到更高的年化，而且回撤沒惡化。")
        print("   下一步：做成 main.py 的排程指令 + 第五個紙上帳戶對照。")
    else:
        print("🔴 沒有同時滿足 → **不採用，維持純定期定額。**")
        if not a:
            print(f"   主因是現金拖累：為了那 {dip['triggers']} 次加碼，平時有 "
                  f"{dip['drag']:.1%} 的錢沒在市場裡，")
            print("   而台股長期上漲，躺著的機會成本吃掉了加碼賺到的。")
            print()
            print("   ⚠️ 不要為了讓它過關去調低 --reserve。這個參數有個退化極限：")
            print("   reserve → 0 時，加碼版本就「收斂成純定期定額」，差異只剩雜訊，")
            print("   於是一定會有某個夠小的值讓它「通過」——那是定義上的自欺，")
            print("   不是發現。真正的結論已經在這裡了：**為了加碼而長期預留現金，")
            print("   在長期上漲的市場裡划不來。**")
    print("\n（本檔只做驗證，不改任何設定、不下任何單。）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
