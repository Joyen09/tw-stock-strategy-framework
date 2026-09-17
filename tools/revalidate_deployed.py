#!/usr/bin/env python3
"""重驗「現在實盤在跑的三組設定」還站不站得住——修掉暖身 bug 之後的總體檢。

# 為什麼要做

當初決定部署這三個帳戶時，依據是這些數字（HANDOFF 第 2 節）：
  lynch×tw50     多頭夏普 1.10、含空頭期 1.51（回撤僅 -7.2%，號稱「防守王」）
  livermore×tw50 多頭夏普 1.67、walkforward 訓練 2.69 → 測試 1.19
  lynch×mid100   多頭夏普 1.57、walkforward 測試期 +24.8%／夏普 1.31／回撤 -7.5%

但 2026-09-15 發現：回測引擎把 250 根暖身**從回測窗口本身扣掉**，所以那些數字
全都跑在「被砍掉一半」的窗口上（詳見 HANDOFF 注意事項 23）。修正後重跑停利驗證，
順便看到兩個基準的真面目：

  tw50   基準 walkforward 夏普 0.27／報酬 +3.04%
  mid100 基準 walkforward 夏普 0.29／報酬 +4.59%、空頭回撤 -26.92%

**都低於既有工具早就寫死的 wf_min_sharpe=0.5。** 也就是說，現在正在跑的設定，
拿去過它自己的關卡會被刷掉。livermore 則根本還沒用修好的引擎量過。

這支就是把那件事做完整：三組設定、同一套事前寫死的標準、一次講清楚。

# 標準怎麼來的（重要，別自欺）

- `wf_min_sharpe` / `wf_min_return` 直接沿用 validate_lynch_buffer.py 與
  validate_take_profit.py 裡**早就寫死**的值，那是在看到上面這些數字之前定的。
- `bull_min_sharpe` / `bear_max_drawdown` 是本檔新訂的。⚠️ 誠實揭露：訂這兩個
  門檻時，我**已經看過** tw50/mid100 的部分結果。所以它們的說服力低於前兩項，
  判定表會分開標示「沿用既有門檻」與「本檔新訂」，別把兩者當成同等證據。
- 一經寫定不可修改。不符合就是不符合，不是「標準太嚴」。

# 這支不會做的事

不改任何預設值、不動 systemd、不下任何單。它只回答一個問題：
**如果今天才要決定部署，這三組設定過得了關嗎？**

用法（跑很久，一定要放背景）：
    nohup .venv/bin/python -u tools/revalidate_deployed.py > revalidate.log 2>&1 &
    tail -f revalidate.log

⚠️ 額度：三組設定 × 四個窗口，tw50+mid100 合計約 700~800 次 API。
FinMind 撞額度會自動等額度回補（見 src/data/finmind.py），不必守著。
中斷直接重跑，data_cache/ 會接續。
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
    # 沿用既有驗證工具的門檻（在看到本次結果之前就寫死的，證據力較強）
    "wf_min_sharpe": 0.5,
    "wf_min_return": 0.0,
    # 本檔新訂（訂定時已看過部分結果，證據力較弱，判定表會標示）
    "bull_min_sharpe": 0.80,      # 多頭期連 0.8 都沒有，不值得佔用資金
    "bear_max_drawdown": -0.25,   # 空頭期最大回撤不得比 -25% 更差
    # 每一關都要有足夠交易，否則是「沒測到」不是「通過」（2026-08 的教訓）
    "min_trades": 5,
}

NEW_IN_THIS_FILE = {"關1 多頭夏普", "關2 空頭回撤"}  # 標示哪些門檻是本檔新訂的

# ──────────────────────────────────────────────────────────
# 大盤對照（2026-09-17 追加，見下方說明）
# ──────────────────────────────────────────────────────────
# ⚠️ 誠實揭露：這一項是在看到「三組全部卡關3」之後才加的。
# 事後加標準通常是作弊，但這裡有個關鍵差別：**它只會讓判定更嚴，不可能救活任何一組**。
# 事後「放寬」標準去救失敗的東西才是作弊；事後「加嚴」是補上原本就該問的問題。
#
# 為什麼原本就該問：三關全部只量「策略自己的絕對績效」，從頭到尾沒有跟大盤比過。
# 多頭期夏普 1.10、報酬 +45% 看起來很好，但如果同期大盤買進持有就 +55%，
# 那這套系統做的所有事情是**淨減損**——承擔了選股風險、付了手續費與稅，
# 換來比躺著不動更差的結果。（諷刺的是 report 早就會對照大盤，回測反而沒有。）
BENCH_GATE = "關4 多頭期贏過大盤"


def _buy_hold(provider, start, end):
    """同期大盤買進持有的報酬／夏普／回撤。抓不到回 None（不讓整份工作掛掉）。"""
    try:
        s = provider.benchmark(start, end)
    except Exception as e:
        print(f"  [bench] {start}~{end} 大盤抓取失敗（略過對照）：{e}", flush=True)
        return None
    if s is None or len(s) < 2:
        return None
    r = s.pct_change().dropna()
    return {
        "ret": float(s.iloc[-1]) / float(s.iloc[0]) - 1,
        "sharpe": float(r.mean() / r.std() * (252 ** 0.5)) if float(r.std()) else 0.0,
        "dd": float((s / s.cummax() - 1).min()),
    }


# 期間設定（與其他驗證工具一致）
BULL = ("2024-01-01", "2025-12-31")
BEAR = ("2021-07-01", "2022-12-31")
WF_TRAIN = ("2023-01-01", "2024-06-30")
WF_TEST = ("2024-07-01", "2025-12-31")

# 現在實盤在跑的三組（對應 deploy/*.service）
CONFIGS = [
    ("lynch × tw50", "lynch", "tw50", "stockbot.service"),
    ("livermore × tw50", "livermore", "tw50", "stockbot-livermore.service"),
    ("lynch × mid100", "lynch", "mid100", "stockbot-lynch-mid100.service"),
]


def _bt(provider, strat_name, symbols, start, end, cash, fee_discount, cooldown, regime):
    from src import strategies
    from src.engine.backtest import Backtester

    bt = Backtester(provider, initial_cash=cash, fee_discount=fee_discount,
                    cooldown_days=cooldown, regime_filter=regime)
    return bt.run(strategies.build(strat_name), symbols, start, end)


def _rank_by_sharpe(provider, strat_name, symbols, start, end, **kw):
    """逐檔回測挑夏普最高的，供 walkforward 訓練期選股用（與既有工具同法）。"""
    rows = []
    for sym in symbols:
        try:
            r = _bt(provider, strat_name, [sym], start, end, **kw)
            if len(r.trades) == 0:
                continue
            rows.append((sym, r.sharpe))
        except Exception as e:
            print(f"    {sym} 失敗: {e}", flush=True)
    rows.sort(key=lambda x: x[1], reverse=True)
    return [s for s, _ in rows]


def _evaluate(name, strat_name, universe, service, provider, top, common):
    from src.data.universe import resolve

    symbols = resolve(universe)
    print(f"─── {name}（{service}，{len(symbols)} 檔）───", flush=True)

    bull = _bt(provider, strat_name, symbols, *BULL, **common)
    print(f"  關1 多頭 {BULL[0]}~{BULL[1]}：報酬 {bull.total_return:+.2%}｜"
          f"夏普 {bull.sharpe:.2f}｜回撤 {bull.max_drawdown:.2%}｜{len(bull.trades)} 筆", flush=True)

    bear = _bt(provider, strat_name, symbols, *BEAR, **common)
    print(f"  關2 空頭 {BEAR[0]}~{BEAR[1]}：報酬 {bear.total_return:+.2%}｜"
          f"夏普 {bear.sharpe:.2f}｜回撤 {bear.max_drawdown:.2%}｜{len(bear.trades)} 筆", flush=True)

    print("  關3 walkforward 訓練期選股中...", flush=True)
    chosen = _rank_by_sharpe(provider, strat_name, symbols, *WF_TRAIN, **common)[:top]
    wf = _bt(provider, strat_name, chosen, *WF_TEST, **common) if chosen else None
    if wf is not None:
        print(f"  關3 測試期 {WF_TEST[0]}~{WF_TEST[1]}（選 {','.join(chosen)}）："
              f"報酬 {wf.total_return:+.2%}｜夏普 {wf.sharpe:.2f}｜"
              f"回撤 {wf.max_drawdown:.2%}｜{len(wf.trades)} 筆", flush=True)
    else:
        print("  關3 訓練期選不出股票", flush=True)
    bull_bh = _buy_hold(provider, *BULL)
    bear_bh = _buy_hold(provider, *BEAR)
    if bull_bh:
        ex = bull.total_return - bull_bh["ret"]
        print(f"  關4 同期大盤買進持有：多頭 {bull_bh['ret']:+.2%}（夏普 {bull_bh['sharpe']:.2f}）"
              f" → 策略超額 {ex:+.2%}", flush=True)
    if bear_bh:
        print(f"       空頭期大盤 {bear_bh['ret']:+.2%}／回撤 {bear_bh['dd']:.2%}"
              f"（策略回撤 {bear.max_drawdown:.2%}）", flush=True)
    print(flush=True)
    return {"bull": bull, "bear": bear, "wf": wf, "bull_bh": bull_bh, "bear_bh": bear_bh}


def _judge(r) -> list:
    """回傳 [(關卡名, 通過?, 說明)]。交易數不足一律判未通過（沒測到 ≠ 通過）。"""
    out = []
    bull, bear, wf = r["bull"], r["bear"], r["wf"]

    traded = len(bull.trades) >= CRITERIA["min_trades"]
    if not traded:
        out.append(("關1 多頭夏普", False, f"只有 {len(bull.trades)} 筆交易 → 沒測到"))
    else:
        c = bull.sharpe >= CRITERIA["bull_min_sharpe"]
        out.append(("關1 多頭夏普", c, f"{bull.sharpe:.2f} (門檻 {CRITERIA['bull_min_sharpe']})"))

    if len(bear.trades) < CRITERIA["min_trades"]:
        out.append(("關2 空頭回撤", False,
                    f"只有 {len(bear.trades)} 筆交易 → 沒測到（空過比失敗更危險）"))
    else:
        c = bear.max_drawdown >= CRITERIA["bear_max_drawdown"]
        out.append(("關2 空頭回撤", c,
                    f"{bear.max_drawdown:.2%} (門檻 {CRITERIA['bear_max_drawdown']:.0%})"))

    if wf is None or len(wf.trades) < CRITERIA["min_trades"]:
        n = 0 if wf is None else len(wf.trades)
        out.append(("關3 WF 夏普", False, f"只有 {n} 筆交易 → 沒測到"))
        out.append(("關3 WF 報酬", False, "沒測到"))
    else:
        c = wf.sharpe >= CRITERIA["wf_min_sharpe"]
        out.append(("關3 WF 夏普", c, f"{wf.sharpe:.2f} (門檻 {CRITERIA['wf_min_sharpe']})"))
        c = wf.total_return > CRITERIA["wf_min_return"]
        out.append(("關3 WF 報酬", c, f"{wf.total_return:+.2%} (門檻 > 0)"))

    # 關4：多頭期有沒有贏過「什麼都不做、直接買大盤」。抓不到大盤就不判（不臆測）。
    bh = r.get("bull_bh")
    if bh is None:
        out.append((BENCH_GATE, False, "抓不到大盤資料 → 沒測到"))
    else:
        ex = bull.total_return - bh["ret"]
        out.append((BENCH_GATE, ex > 0,
                    f"策略 {bull.total_return:+.2%} vs 大盤 {bh['ret']:+.2%}"
                    f"（超額 {ex:+.2%}）"))
    return out


def main():
    ap = argparse.ArgumentParser(description="重驗實盤三組設定（標準事前寫死）")
    ap.add_argument("--only", default="", help="只驗某一組（比對 CONFIGS 名稱的子字串）")
    ap.add_argument("--top", type=int, default=5, help="walkforward 訓練期選前幾檔")
    ap.add_argument("--cash", type=float, default=1_000_000)
    ap.add_argument("--fee-discount", type=float, default=0.28)
    ap.add_argument("--cooldown", type=int, default=5)
    ap.add_argument("--no-regime", action="store_true", help="關閉大盤濾網（預設開啟，與實盤同）")
    args = ap.parse_args()

    from src.data.cache import DiskCachingProvider
    from src.data.finmind import FinMindProvider

    provider = DiskCachingProvider(FinMindProvider())
    common = dict(cash=args.cash, fee_discount=args.fee_discount,
                  cooldown=args.cooldown, regime=not args.no_regime)
    configs = [c for c in CONFIGS if args.only.lower() in c[0].lower()]

    print("實盤設定重驗（修正回測暖身 bug 後）")
    print(f"大盤濾網 {'開' if not args.no_regime else '關'}｜cooldown {args.cooldown}｜"
          f"手續費折扣 {args.fee_discount}")
    print("標準已於程式碼寫死（CRITERIA），不因結果調整\n")

    results = {}
    for name, strat_name, universe, service in configs:
        results[name] = _evaluate(name, strat_name, universe, service, provider, args.top, common)

    print("=" * 78)
    print(f"{'設定':<18}{'多頭夏普':>10}{'多頭報酬':>10}{'超額':>10}{'空頭回撤':>10}"
          f"{'WF夏普':>9}{'WF報酬':>10}")
    print("-" * 78)
    for name in results:
        r = results[name]
        wf, bh = r["wf"], r.get("bull_bh")
        ex = (r["bull"].total_return - bh["ret"]) if bh else float("nan")
        print(f"{name:<18}{r['bull'].sharpe:>10.2f}{r['bull'].total_return:>10.2%}"
              f"{ex:>10.2%}{r['bear'].max_drawdown:>10.2%}"
              f"{(wf.sharpe if wf else float('nan')):>9.2f}"
              f"{(wf.total_return if wf else float('nan')):>10.2%}")
    if any(results[n].get("bull_bh") for n in results):
        b = next(results[n]["bull_bh"] for n in results if results[n].get("bull_bh"))
        print("")
        print(f"（同期大盤買進持有：{b['ret']:+.2%}，夏普 {b['sharpe']:.2f}——"
              f"『超額』為負代表這套系統做的一切都是淨減損）")

    print("\n" + "=" * 78)
    print("逐項判定（★ = 本檔新訂的門檻，訂定時已看過部分結果，證據力較弱）：\n")
    verdicts = {}
    for name in results:
        checks = _judge(results[name])
        ok = all(c for _, c, _ in checks)
        verdicts[name] = ok
        print(f"【{name}】{'✅ 通過' if ok else '❌ 未通過'}")
        for gate, passed, detail in checks:
            star = "★" if gate in NEW_IN_THIS_FILE else " "
            print(f"   {star}{'✓' if passed else '✗'} {gate}：{detail}")
        print()

    print("=" * 78)
    failed = [n for n, ok in verdicts.items() if not ok]
    if not failed:
        print("🟢 三組設定都還站得住，維持現狀。")
    else:
        print(f"🔴 未通過：{'、'.join(failed)}")
        print("\n這**不代表要立刻關掉它們**。回測失格與實盤失格是兩件事，而且模擬盤是假錢、")
        print("繼續空跑的成本只有電力。真正該改變的是**這些數字在決策中的份量**：")
        print("  1. HANDOFF 第 2 節那些部署依據（夏普 1.5+、WF +24.8%）已失效，要改寫")
        print("  2. **上真錢的前提消失了** —— 原本的計畫是「空跑穩定後分配 5 萬」，")
        print("     但那個計畫建立在錯誤的回測上，必須重新論證才能繼續")
        print("  3. 先讓空跑帳戶繼續累積真實紀錄，那是唯一沒有被這個 bug 污染的證據")
    print("\n（本檔只做驗證，不改任何設定、不動 systemd、不下任何單。）")


if __name__ == "__main__":
    sys.exit(main() or 0)
