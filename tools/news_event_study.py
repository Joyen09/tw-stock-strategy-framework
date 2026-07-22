#!/usr/bin/env python3
"""新聞熱度事件研究：驗證「看到新聞再買」到底有沒有肉。

動機：部署新聞看板 (investment-news) 之前，先用歷史資料誠實回答一個問題——
「某檔股票新聞量暴增」這個訊號，在你隔天早上看到摘要之後才進場，
還有沒有超額報酬？還是市場早就反映完了？

方法 (事件研究法)：
1. 用 FinMind TaiwanStockNews 抓每檔股票的每日新聞則數
2. 事件日 = 當日新聞則數 > 過去 window 日的 平均 + k×標準差 (且 >= min_count 則)
3. 模擬「隔天才看到摘要」的散戶時間線：
   - 事件日 t 的新聞 → 你 t+1 早上 07:30 看到 → 最快 t+1 收盤價進場
   - 報酬 = 進場後 1/5/10/20 個交易日的價格變化
4. 對照組 = 同一檔股票「所有交易日」的同 horizon 平均報酬 (基準線)
   事件後報酬 - 基準線 = 新聞訊號的「額外」貢獻 (可能是負的)
5. 同時回報事件日當天的漲跌 (你進場前市場已反映了多少)

誠實聲明：
- 新聞「則數」只衡量熱度，分不出利多利空——這是免費資料的極限
- mid100 成分股有生存者偏差 (今天活著的名單)，結論會偏樂觀
- 若事件後平均報酬 ≈ 基準線，代表新聞熱度無額外資訊 → 看板只當風險雷達用，別當訊號

用法 (在 VM 上、.env 有 FINMIND_TOKEN)：
    python tools/news_event_study.py                        # tw50+mid100, 近兩年
    python tools/news_event_study.py --universe tw50 --start 2024-01-01
新聞與價格都會存 data_cache/，中斷重跑從斷點接續、同日重跑幾乎不耗 API 額度。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import date
from pathlib import Path

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

CACHE_DIR = Path("data_cache")


# ---------------- 純函式 (可測試) ----------------

def roll_to_trading_days(news_dates: pd.Series, trading_days: pd.DatetimeIndex) -> pd.Series:
    """把每則新聞的日曆日對齊到「下一個交易日」(含當日)：
    週末/假日的新聞會累積到下週一，跟你實際看到摘要的節奏一致。
    回傳 index=交易日、value=新聞則數。"""
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


# ---------------- 資料抓取 (含磁碟快取) ----------------

def _news_cache_path(sym: str, start: str, end: str) -> Path:
    return CACHE_DIR / f"news_{sym}_{start}_{end}.json"


def fetch_news_dates(api, sym: str, start: str, end: str, sleep: float) -> list:
    """回傳該股票的新聞日期字串清單 (一則一筆)。快取到磁碟，重跑 0 請求。"""
    cp = _news_cache_path(sym, start, end)
    if cp.exists():
        try:
            return json.loads(cp.read_text())
        except Exception:
            pass
    df = api.taiwan_stock_news(stock_id=sym, start_date=start, end_date=end)
    time.sleep(sleep)
    dates = []
    if df is not None and not df.empty and "date" in df.columns:
        # FinMind 的 date 可能含時間，只留日曆日
        dates = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d").tolist()
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cp.write_text(json.dumps(dates))
    return dates


# ---------------- 主流程 ----------------

def main():
    ap = argparse.ArgumentParser(description="新聞熱度事件研究 (驗證新聞訊號有沒有超額報酬)")
    ap.add_argument("--universe", default="tw50,mid100", help="逗號分隔: tw50,mid100,top15")
    ap.add_argument("--start", default="2024-01-01")
    ap.add_argument("--end", default=date.today().isoformat())
    ap.add_argument("--window", type=int, default=60, help="熱度基準的回看天數")
    ap.add_argument("--sigma", type=float, default=2.0, help="幾個標準差才算暴增")
    ap.add_argument("--min-count", type=int, default=3, help="事件日最少新聞則數")
    ap.add_argument("--sleep", type=float, default=0.4, help="每次 API 呼叫間隔秒數 (護額度)")
    ap.add_argument("--max-symbols", type=int, default=0, help="只跑前 N 檔 (0=全部，先小跑驗證用)")
    args = ap.parse_args()

    from src.data.universe import resolve
    from src.data.finmind import FinMindProvider
    from src.data.cache import DiskCachingProvider

    symbols: list = []
    for u in args.universe.split(","):
        for s in resolve(u.strip()):
            if s not in symbols:
                symbols.append(s)
    if args.max_symbols:
        symbols = symbols[: args.max_symbols]

    raw = FinMindProvider()
    prices = DiskCachingProvider(raw)
    horizons = (1, 5, 10, 20)

    print(f"新聞熱度事件研究：{len(symbols)} 檔｜{args.start} ~ {args.end}｜"
          f"事件=則數>{args.window}日均+{args.sigma}σ (≥{args.min_count}則)")
    print("散戶時間線：事件日隔天收盤才進場 (你 07:30 看到摘要之後)\n")

    all_events = []          # 每檔的事件報酬 DataFrame
    base_acc = {f"h{h}": [] for h in horizons}
    news_total = 0
    no_news = 0

    for i, sym in enumerate(symbols, 1):
        try:
            px = prices.history(sym, args.start, args.end)
            if px is None or px.empty or len(px) < args.window:
                continue
            dates = fetch_news_dates(raw.api, sym, args.start, args.end, args.sleep)
            news_total += len(dates)
            if not dates:
                no_news += 1
                continue
            counts = roll_to_trading_days(
                pd.Series(pd.to_datetime(dates)), px.index)
            events = detect_events(counts, args.window, args.sigma, args.min_count)
            ev_days = events[events].index
            base = baseline_returns(px["close"], horizons)
            for h in horizons:
                if base[f"h{h}"] is not None:
                    base_acc[f"h{h}"].append(base[f"h{h}"])
            if len(ev_days) == 0:
                continue
            fr = forward_returns(px["close"], ev_days, horizons)
            if not fr.empty:
                fr["symbol"] = sym
                all_events.append(fr)
            print(f"  [{i}/{len(symbols)}] {sym}: 新聞 {len(dates)} 則 → 事件 {len(ev_days)} 天",
                  flush=True)
        except Exception as e:
            print(f"  [{i}/{len(symbols)}] {sym} 失敗: {e}", flush=True)

    if news_total == 0:
        print("\n❌ 一則新聞都抓不到。可能原因：")
        print("   1) FinMind TaiwanStockNews 需要較高等級 token (跟分點資料一樣被擋)")
        print("   2) FINMIND_TOKEN 沒設定或額度用完")
        print("   直接跑: python -c \"from FinMind.data import DataLoader; import os;"
              " d=DataLoader(); d.login_by_token(api_token=os.getenv('FINMIND_TOKEN'));"
              " print(d.taiwan_stock_news(stock_id='2330', start_date='2025-06-01',"
              " end_date='2025-06-30').head())\" 看錯誤訊息。")
        return 1

    if not all_events:
        print(f"\n抓到 {news_total} 則新聞但沒有任何事件日 (門檻太嚴或期間太短)。"
              f"可調 --sigma 1.5 或 --min-count 2 再試。")
        return 0

    ev = pd.concat(all_events, ignore_index=True)
    n = len(ev)
    print(f"\n{'='*64}")
    print(f"事件總數：{n} 個 (跨 {ev['symbol'].nunique()} 檔；{no_news} 檔完全沒新聞資料)")
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
        print("     熱度分不出利多利空、mid100 有生存者偏差、也還沒扣手續費。")
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
