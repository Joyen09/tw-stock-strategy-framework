#!/usr/bin/env python3
"""新聞熱度事件研究：驗證「看到新聞再買」到底有沒有肉。

動機：部署新聞看板 (investment-news) 之前，先用歷史資料誠實回答一個問題——
「某檔股票新聞量暴增」這個訊號，在你隔天早上看到摘要之後才進場，
還有沒有超額報酬？還是市場早就反映完了？

方法 (事件研究法)：
1. 用 FinMind TaiwanStockNews 抓每檔股票的每日新聞則數
   ⚠️ 官方文件：「由於資料量過大，單次請求只提供一天資料」→ 必須逐日抓。
   免費層每小時 600 次請求，所以本研究只能做「先導版」：
   預設 5 檔新聞量大的代表股 × 近一年 ≈ 1,200 次請求 ≈ 2 小時。
   全量 (150 檔×2 年 = 7.5 萬次) 在免費層不可行，先導結果夠回答方向性問題。
2. 事件日 = 當日新聞則數 > 過去 window 日的 平均 + k×標準差 (且 >= min_count 則)
3. 模擬「隔天才看到摘要」的散戶時間線：
   - 事件日 t 的新聞 → 你 t+1 早上 07:30 看到 → 最快 t+1 收盤價進場
   - 報酬 = 進場後 1/5/10/20 個交易日的價格變化
4. 對照組 = 同一檔股票「所有交易日」的同 horizon 平均報酬 (基準線)
   事件後報酬 - 基準線 = 新聞訊號的「額外」貢獻 (可能是負的)
5. 同時回報事件日當天的漲跌 (你進場前市場已反映了多少)

誠實聲明：
- 新聞「則數」只衡量熱度，分不出利多利空——這是免費資料的極限
- 只查交易日的新聞 (省一半額度)，週末新聞沒算進熱度——結果略偏保守
- 樣本只有幾檔大型股，結論外推到中小型股要打折

用法 (在 VM 上、.env 有 FINMIND_TOKEN)：
    python tools/news_event_study.py                     # 預設 5 檔 × 近一年，約 2 小時
    python tools/news_event_study.py --symbols 2330,2317 --start 2025-01-01
每天的新聞則數都會快取到 data_cache/newsd_*.json：
中斷/額度用完後重跑同指令，會從斷點接續、已抓過的日子 0 請求。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

CACHE_DIR = Path("data_cache")

# 預設樣本：新聞量大、市值大到天天有人寫的代表股 (台積電/鴻海/聯發科/廣達/台達電)
DEFAULT_SYMBOLS = "2330,2317,2454,2382,2308"


class QuotaExhausted(Exception):
    """連續失敗多次，多半是 API 額度用完——進度已快取，稍後重跑即可續。"""


# ---------------- 純函式 (可測試) ----------------

def roll_to_trading_days(news_dates: pd.Series, trading_days: pd.DatetimeIndex) -> pd.Series:
    """把每則新聞的日曆日對齊到「下一個交易日」(含當日)。
    目前主流程逐日只查交易日、用不到這支；保留給未來加查週末新聞時用。"""
    counts = pd.Series(0, index=trading_days, dtype=int)
    if news_dates.empty or len(trading_days) == 0:
        return counts
    idx = trading_days.searchsorted(pd.DatetimeIndex(news_dates), side="left")
    for i in idx:
        if i < len(trading_days):
            counts.iloc[i] += 1
    return counts


def detect_events(counts: pd.Series, window: int = 60, k: float = 2.0,
                  min_count: int = 3) -> pd.Series:
    """事件日偵測：新聞則數 > rolling(mean + k*std) 且 >= min_count。
    rolling 只用「過去」資料 (shift 1)，避免拿當天資訊定義當天門檻。"""
    mean = counts.shift(1).rolling(window, min_periods=20).mean()
    std = counts.shift(1).rolling(window, min_periods=20).std()
    threshold = (mean + k * std).clip(lower=min_count)
    return (counts >= threshold) & counts.ge(min_count) & threshold.notna()


def forward_returns(close: pd.Series, event_days: pd.DatetimeIndex,
                    horizons=(1, 5, 10, 20)) -> pd.DataFrame:
    """散戶時間線報酬：事件日 t → t+1 收盤進場 → 持有 h 日的報酬。
    另附 same_day = 事件日當天漲跌 (進場前市場已反映的部分)。
    回傳每列一個事件、欄位 = same_day, h1, h5, ...；資料不足的事件略過。"""
    rows = []
    pos = {d: i for i, d in enumerate(close.index)}
    for t in event_days:
        i = pos.get(t)
        if i is None or i + 1 >= len(close):
            continue
        entry = close.iloc[i + 1]
        if entry <= 0:
            continue
        row = {"date": t,
               "same_day": close.iloc[i] / close.iloc[i - 1] - 1 if i >= 1 else None}
        for h in horizons:
            j = i + 1 + h
            row[f"h{h}"] = close.iloc[j] / entry - 1 if j < len(close) else None
        rows.append(row)
    return pd.DataFrame(rows)


def baseline_returns(close: pd.Series, horizons=(1, 5, 10, 20)) -> dict:
    """基準線：這檔股票「任何一天收盤買進」持有 h 日的平均報酬。"""
    out = {}
    for h in horizons:
        r = close.shift(-h) / close - 1
        out[f"h{h}"] = float(r.mean()) if r.notna().any() else None
    return out


# ---------------- 逐日抓新聞 (含磁碟快取與額度保護) ----------------

def fetch_daily_news_counts(api, sym: str, trading_days: pd.DatetimeIndex,
                            sleep: float) -> pd.Series:
    """逐「交易日」查該股新聞則數 (TaiwanStockNews 單次請求只回一天)。

    快取：data_cache/newsd_{sym}.json 存 {日期: 則數}，抓過的日子 0 請求，
    每 20 天存檔一次——中斷/額度用完都不掉進度。
    連續失敗 6 次視為額度用完，丟 QuotaExhausted (外層收尾、提示續跑)。
    """
    cp = CACHE_DIR / f"newsd_{sym}.json"
    data: dict = {}
    if cp.exists():
        try:
            data = json.loads(cp.read_text())
        except Exception:
            data = {}

    def _save():
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cp.write_text(json.dumps(data))

    todo = [d.strftime("%Y-%m-%d") for d in trading_days
            if d.strftime("%Y-%m-%d") not in data]
    fails = 0
    for n, ds in enumerate(todo, 1):
        try:
            df = api.taiwan_stock_news(stock_id=sym, start_date=ds, end_date=ds)
            data[ds] = 0 if df is None or df.empty else int(len(df))
            fails = 0
        except Exception as e:
            fails += 1
            if fails >= 6:
                _save()
                raise QuotaExhausted(f"{sym} 連續失敗 6 次 (最後錯誤: {e})")
            time.sleep(65)  # 可能撞到每小時額度上限，喘口氣再試
            continue
        if n % 20 == 0:
            _save()
            print(f"      {sym}: 已抓 {len(data)}/{len(trading_days)} 天", flush=True)
        time.sleep(sleep)
    _save()
    return pd.Series({pd.Timestamp(k): int(v) for k, v in data.items()},
                     dtype=int).reindex(trading_days, fill_value=0)


# ---------------- 主流程 ----------------

def main():
    ap = argparse.ArgumentParser(description="新聞熱度事件研究 (先導版：驗證新聞訊號有沒有超額報酬)")
    ap.add_argument("--symbols", default=DEFAULT_SYMBOLS,
                    help=f"逗號分隔股票 (預設新聞大戶 {DEFAULT_SYMBOLS})")
    ap.add_argument("--universe", default="", help="改用整個股池 (tw50/mid100)——額度警告：一年約 1.2 萬次/50 檔")
    ap.add_argument("--start", default=(date.today() - timedelta(days=365)).isoformat())
    ap.add_argument("--end", default=date.today().isoformat())
    ap.add_argument("--window", type=int, default=60, help="熱度基準的回看天數")
    ap.add_argument("--sigma", type=float, default=2.0, help="幾個標準差才算暴增")
    ap.add_argument("--min-count", type=int, default=3, help="事件日最少新聞則數")
    ap.add_argument("--sleep", type=float, default=6.0,
                    help="每次 API 呼叫間隔秒數 (免費層 600 次/時 ≈ 每 6 秒 1 次)")
    args = ap.parse_args()

    from src.data.finmind import FinMindProvider
    from src.data.cache import DiskCachingProvider

    if args.universe:
        from src.data.universe import resolve
        symbols = resolve(args.universe)
    else:
        symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]

    raw = FinMindProvider()
    prices = DiskCachingProvider(raw)
    horizons = (1, 5, 10, 20)

    # 先抓價格 (便宜、多半有快取)，算出「還缺幾天新聞」→ 給出誠實的耗時預估
    px_map = {}
    todo_total = 0
    for sym in symbols:
        px = prices.history(sym, args.start, args.end)
        if px is None or px.empty or len(px) < args.window:
            print(f"  {sym}: 價格資料不足，跳過")
            continue
        px_map[sym] = px
        cp = CACHE_DIR / f"newsd_{sym}.json"
        cached = set()
        if cp.exists():
            try:
                cached = set(json.loads(cp.read_text()))
            except Exception:
                pass
        todo_total += sum(1 for d in px.index
                          if d.strftime("%Y-%m-%d") not in cached)

    est_hr = todo_total * args.sleep / 3600
    print(f"新聞熱度事件研究 (先導版)：{len(px_map)} 檔｜{args.start} ~ {args.end}")
    print(f"TaiwanStockNews 單次請求只回一天 → 還需 {todo_total:,} 次請求，"
          f"約 {est_hr:.1f} 小時 (中斷可續跑，Ctrl+C 不掉進度)")
    print("散戶時間線：事件日隔天收盤才進場 (你 07:30 看到摘要之後)\n")

    all_events = []
    base_acc = {f"h{h}": [] for h in horizons}
    news_total = 0
    aborted = False

    for i, (sym, px) in enumerate(px_map.items(), 1):
        print(f"  [{i}/{len(px_map)}] {sym} 抓新聞中...", flush=True)
        try:
            counts = fetch_daily_news_counts(raw.api, sym, px.index, args.sleep)
        except QuotaExhausted as e:
            print(f"\n⏸ 額度疑似用完：{e}")
            print("   進度已存 data_cache/，等額度重置 (每小時) 後重跑同指令即可續。")
            aborted = True
            break
        news_total += int(counts.sum())
        events = detect_events(counts, args.window, args.sigma, args.min_count)
        ev_days = events[events].index
        base = baseline_returns(px["close"], horizons)
        for h in horizons:
            if base[f"h{h}"] is not None:
                base_acc[f"h{h}"].append(base[f"h{h}"])
        print(f"      {sym}: 新聞 {int(counts.sum())} 則 → 事件 {len(ev_days)} 天", flush=True)
        if len(ev_days) == 0:
            continue
        fr = forward_returns(px["close"], ev_days, horizons)
        if not fr.empty:
            fr["symbol"] = sym
            all_events.append(fr)

    if news_total == 0 and not aborted:
        print("\n❌ 一則新聞都抓不到 (逐日查也是)。請單獨測一天看 API 回什麼：")
        print("   python -c \"from FinMind.data import DataLoader; import os;"
              " d=DataLoader(); d.login_by_token(api_token=os.getenv('FINMIND_TOKEN'));"
              " print(d.taiwan_stock_news(stock_id='2330', start_date='2026-07-20',"
              " end_date='2026-07-20'))\"")
        return 1

    if not all_events:
        if not aborted:
            print(f"\n抓到 {news_total} 則新聞但沒有任何事件日 (門檻太嚴或期間太短)。"
                  f"可調 --sigma 1.5 或 --min-count 2 再試。")
        return 0

    ev = pd.concat(all_events, ignore_index=True)
    n = len(ev)
    print(f"\n{'='*64}")
    if aborted:
        print("(以下為「已完成股票」的部分結果，續跑完再看最終版)")
    print(f"事件總數：{n} 個 (跨 {ev['symbol'].nunique()} 檔)")
    sd = ev["same_day"].dropna()
    if not sd.empty:
        print(f"事件日當天平均已漲跌 {sd.mean():+.2%} (你進場「之前」市場已反映的部分)\n")

    print(f"{'持有':<6}{'事件後平均':>10}{'基準線':>10}{'超額':>10}{'勝率':>8}{'樣本':>7}")
    print("-" * 52)
    verdicts = []
    for h in horizons:
        col = ev[f"h{h}"].dropna()
        base = pd.Series(base_acc[f"h{h}"]).mean() if base_acc[f"h{h}"] else 0.0
        if col.empty:
            continue
        excess = col.mean() - base
        win = (col > 0).mean()
        print(f"{h:>3} 日 {col.mean():>+9.2%}{base:>+10.2%}{excess:>+10.2%}"
              f"{win:>7.1%}{len(col):>7}")
        verdicts.append((h, excess))

    print(f"\n{'='*64}")
    print("判讀：")
    meaningful = [h for h, e in verdicts if e > 0.005]  # 超額 >0.5% 才算有肉
    if not meaningful:
        print("  🔴 事件後報酬跟「隨便挑一天買」差不多 (或更差)：")
        print("     新聞熱度在你能進場的時點已無額外資訊——符合「公開新聞已被定價」的預期。")
        print("     → 看板當『風險雷達＋理解工具』用，不要當買進訊號。")
    else:
        print(f"  🟡 持有 {meaningful} 日有 >0.5% 超額——先別興奮，這還沒過三關：")
        print("     熱度分不出利多利空、樣本只有幾檔大型股、也還沒扣手續費。")
        print("     要當訊號用，得先做成策略跑 空頭壓力測試 + walkforward 再說。")
    print("  ⚠️ 本研究只有『熱度』沒有『方向』；利多利空混在一起平均，")
    print("     結果偏保守——但這正是免費資料下你實際拿得到的訊號品質。")
    return 0


if __name__ == "__main__":
    # .env 載入 (跟 main.py 相同邏輯，讓 FINMIND_TOKEN 生效)
    _env = Path(__file__).resolve().parent.parent / ".env"
    if _env.exists():
        for line in _env.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    sys.exit(main())
