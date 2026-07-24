#!/usr/bin/env python3
"""月營收資料層探測（monthly-revenue-overlay-spec §2.1 Phase 0 的第一個動作）。

⚠️ Claude 沙箱的網路政策擋掉 FinMind 主機（api.finmindtrade.com 403），
   這支要在 VM（.env 有 FINMIND_TOKEN）或放行網域後的環境執行。

做四件事，一次跑完就能勾掉 Phase 0 驗收清單的線上項目：

1. 【欄位語意】拉 3 檔股票的 TaiwanStockMonthRevenue，完整印出回傳欄位，
   自動比對 `date` 欄位相對 revenue_year/revenue_month 是「資料所屬月份」
   還是「公告月份」，並檢查有無「實際公告日」之類的額外欄位。
   原始回應存進 tests/fixtures/rev_api_sample_raw.json 當紀錄。
2. 【effective_date 人工抽查】用真實交易日曆（取自 2330 價格序列）算出
   每檔最近 3 個資料月的 effective_date，標出跨週末/假日順延的案例。
3. 【全市場模式】測 API 是否支援「不帶 data_id 按日期抓全市場」——
   若支援，大 universe 的增量更新可用「每月 1 次請求」取代「每檔 1 次」。
4. 【額度】查 user_info 目前用量，並按預設 universe 規模估算是否吃得下。

用法：
    python tools/rev_probe.py                       # 預設 2330,2368,2609
    python tools/rev_probe.py --symbols 2330,1795
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

FIXTURE_OUT = Path("tests/fixtures/rev_api_sample_raw.json")

# 大型 2330 台積電 / 中型 2368 金像電 / 傳產航運 2609 陽明 —— 涵蓋不同板塊
DEFAULT_SYMBOLS = "2330,2368,2609"


def probe_semantics(api, symbols: list[str], start: str) -> dict:
    """拉原始月營收、印全部欄位、判定 date 語意。回傳 {sym: 原始 records}。"""
    known = {"date", "stock_id", "country", "revenue", "revenue_month", "revenue_year"}
    captures: dict = {}
    verdicts = []
    for sym in symbols:
        df = api.taiwan_stock_month_revenue(stock_id=sym, start_date=start)
        print(f"\n===== {sym} TaiwanStockMonthRevenue 原始回傳 =====")
        if df is None or df.empty:
            print("  (空)")
            continue
        print(f"欄位: {list(df.columns)}")
        print(f"dtypes:\n{df.dtypes}")
        print(df.tail(8).to_string(index=False))
        captures[sym] = json.loads(df.tail(15).to_json(orient="records"))

        extra = [c for c in df.columns if c not in known]
        if extra:
            print(f"  ⚠ 有規格假設之外的欄位：{extra} ——若其中有實際公告日，"
                  f"effective_date 應改用它（spec §2.3），請回報。")

        # date 欄相對資料月 (revenue_year, revenue_month) 的偏移量（月）
        # （逐筆建 Period：pandas 3.0 已移除 PeriodIndex(year=, month=) 建構式）
        d = pd.to_datetime(df["date"])
        data_m = pd.PeriodIndex([
            pd.Period(freq="M", year=int(y), month=int(m))
            for y, m in zip(df["revenue_year"], df["revenue_month"])
        ])
        offs = pd.Series(
            d.dt.to_period("M").astype("int64").to_numpy()
            - data_m.astype("int64").to_numpy()
        ).value_counts()
        print(f"  date 相對資料月的偏移(月): {dict(offs)}")
        verdicts.append((sym, dict(offs)))

    print("\n----- 欄位語意判定 -----")
    print("（SDK 原始碼已確認：date = 資料月+1（公告月），且 SDK 會把 start/end 先 +1 月再打 API。")
    print("  框架一律以 revenue_year/month 為資料月依據，date 只留稽核——此處為線上最終確認。）")
    for sym, offs in verdicts:
        if set(offs) == {1}:
            print(f"  {sym}: date = 資料月的次月（公告月）→ 與 SDK 文件一致 ✅")
        elif set(offs) == {0}:
            print(f"  {sym}: date = 資料所屬月份 → 與 SDK 文件矛盾，請回報！"
                  f"（框架不受影響，仍以 revenue_year/month 為準）")
        else:
            print(f"  {sym}: 偏移不一致 {offs} → 請人工檢視上方原始列！")
    return captures


def spotcheck_effective_dates(provider, symbols: list[str]) -> None:
    """真實交易日曆下，最近 3 個資料月的 effective_date（人工抽查用，§2.5）。"""
    from src.rev_dates import announce_deadline, effective_date, latest_effective_month

    today = date.today()
    px = provider.history("2330", "2019-01-01", today.isoformat())
    cal = px.index
    print(f"\n===== effective_date 人工抽查（交易日曆: 2330 價格序列，"
          f"{cal[0].date()} ~ {cal[-1].date()}，共 {len(cal)} 個交易日）=====")
    latest = latest_effective_month(pd.Timestamp(today), trading_days=cal)
    if latest is None:
        print("  ⚠ 價格日曆不足（抓不到近期交易日）→ 無法抽查，請先確認 2330 價格資料。")
        return
    months = [latest - i for i in range(3)]
    print(f"{'資料月':<10}{'次月10日':<14}{'週幾':<6}{'effective_date':<16}{'順延'}")
    for m in months:
        dl = announce_deadline(m.year, m.month)
        eff = effective_date(m.year, m.month, trading_days=cal)
        moved = "—" if eff is not None and eff == dl else \
            f"順延 {'' if eff is None else (eff - dl).days}天（週末/假日）"
        eff_s = "尚未生效" if eff is None else str(eff.date())
        print(f"{str(m):<10}{str(dl.date()):<14}{dl.strftime('%a'):<6}{eff_s:<16}{moved}")
    print("請人工核對：10 日落在週六/週日/國定假日時，effective_date 應為下一交易日。")


def probe_market_mode(token: str, start: str, end: str) -> None:
    """測「不帶 data_id 按日期抓全市場」是否可用（spec §2.2 抓取策略）。"""
    import requests

    print("\n===== 全市場模式測試（不帶 data_id）=====")
    try:
        r = requests.get(
            "https://api.finmindtrade.com/api/v4/data",
            params={"dataset": "TaiwanStockMonthRevenue",
                    "start_date": start, "end_date": end, "token": token},
            timeout=60,
        )
        j = r.json()
        rows = j.get("data") or []
        n_stock = len({x.get("stock_id") for x in rows})
        print(f"  HTTP {r.status_code} / status {j.get('status')} / msg {j.get('msg')!r}")
        print(f"  回 {len(rows)} 列、{n_stock} 檔")
        if r.status_code == 200 and n_stock > 10:
            print("  ✅ 支援全市場按日期抓 → 大 universe 增量更新可改用每月 1 次請求。")
        else:
            print("  ❌ 不支援（或受限）→ 沿用逐檔抓。")
    except Exception as e:
        print(f"  測試失敗: {e}")


def probe_quota(token: str) -> None:
    import requests

    print("\n===== FinMind 額度 =====")
    try:
        r = requests.get("https://api.finmindtrade.com/api/v4/user_info",
                         params={"token": token}, timeout=30)
        j = r.json()
        limit = j.get("api_request_limit")
        used = j.get("user_count")
        print(f"  目前用量 {used} / 上限 {limit}（滾動 60 分鐘窗）")
    except Exception as e:
        print(f"  查詢失敗: {e}")
        limit = 600
    print("  估算（免費層 600 次/滾動時窗）：")
    print("   - 月營收逐檔抓：1 檔 1 次（全期間一次回完）→ universe 300~800 檔")
    print("     首抓 ≈ 300~800 次（1~2 個時窗），之後增量每月同量、或全市場模式每月 1 次。")
    print("   - rev-universe 產生（Phase 2）：需全市場日線算 20 日均成交金額，")
    print("     逐檔 1 次 × ~1800 檔上市櫃 ≈ 3 個時窗（有磁碟快取、可中斷續跑）。")


def main():
    ap = argparse.ArgumentParser(description="月營收資料層探測（Phase 0）")
    ap.add_argument("--symbols", default=DEFAULT_SYMBOLS)
    ap.add_argument("--start", default="2025-06-01", help="欄位語意抽樣起點")
    args = ap.parse_args()
    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]

    from src.data.finmind import FinMindProvider
    provider = FinMindProvider()
    token = os.getenv("FINMIND_TOKEN", "")

    captures = probe_semantics(provider.api, symbols, args.start)
    if captures:
        FIXTURE_OUT.parent.mkdir(parents=True, exist_ok=True)
        FIXTURE_OUT.write_text(
            json.dumps({"note": "tools/rev_probe.py 抓的 TaiwanStockMonthRevenue 原始樣本，"
                                "欄位語意驗證紀錄（spec §2.1）",
                        "fetched_at": date.today().isoformat(),
                        "data": captures}, ensure_ascii=False, indent=1),
            encoding="utf-8")
        print(f"\n原始樣本已存 {FIXTURE_OUT}")

    spotcheck_effective_dates(provider, symbols)
    probe_market_mode(token, "2026-05-01", "2026-06-30")
    probe_quota(token)
    return 0


if __name__ == "__main__":
    _env = Path(__file__).resolve().parent.parent / ".env"
    if _env.exists():
        for line in _env.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    sys.exit(main())
