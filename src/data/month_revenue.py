"""月營收磁碟快取 + 增量更新（monthly-revenue-overlay-spec §2.2）。

沿用 repo 既有快取慣例（data_cache/ 目錄、每檔一個 JSON，
同 tools/news_event_study.py 的 newsd_*.json 做法），每檔一個 revm_{sym}.json：

    {
      "stock_id": "2330",
      "rows":     {"2024-01": 215785127000, ...},   # 資料月 -> 營收（元）
      "date_raw": {"2024-01": "2024-02-01", ...},   # FinMind date 欄原值，僅稽核用
      "queried_start": "2016-11",   # 已向 API 查詢過的連續月份區間
      "queried_end":   "2024-11",
      "fetched_at": "2024-12-20"
    }

核心不變量：**queried_end 永不超過「抓取當下照理已公告的最新資料月」**
（latest_announced_month）。因此區間內查過但 rows 沒有的月份只有兩種可能：
  - 真缺月（漏公告/資料缺口）→ 指標層按 §2.4 視為無效，不重打 API
  - API 短暫落後公告節奏 → 由「每天最多一次」的尾端補洞自動癒合
    （補洞從 rows 最末月起抓、不受本次請求的 start 夾擠，洞不會被凍結）
「沒查過」（區間外）才需要增量補頭/補尾。

已下市/長期無新資料的股票：尾端補洞會每天空抓一次（每檔一請求），
由上層 staleness 規則（§5.2）把該股票剔除後自然停止——保守方向，可接受。
"""
from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path
from typing import Dict, Iterable, Optional

import pandas as pd

# z-score 需要 24 個月營收 + 緩衝（spec §2.2：fetch_start = 需求起點 − 26 個月）
WARMUP_MONTHS = 26

_KEY_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")  # 合法月份鍵 YYYY-MM


def _p(ym: str) -> pd.Period:
    return pd.Period(ym, freq="M")


def _ym(p: pd.Period) -> str:
    return f"{p.year}-{p.month:02d}"


def latest_announced_month(today: date, announce_day: int = 10) -> pd.Period:
    """今天「照理已公告」的最新資料月（日曆日近似，不看交易日曆）。

    公告截止 = 次月 announce_day 日；假日順延最多再拖幾天，
    這裡用「日曆日已過 announce_day」當保守判斷——只影響快取要抓到哪個月，
    不影響下游 effective_date 的正確性（那邊一律走 rev_dates 順延規則）。
    """
    m = pd.Period(freq="M", year=today.year, month=today.month)
    return m - 1 if today.day > announce_day else m - 2


class MonthRevenueStore:
    """月營收快取層。provider 只需提供 get_month_revenue(stock_ids, start, end)。"""

    def __init__(self, provider, cache_dir: str = "data_cache", announce_day: int = 10):
        self.provider = provider
        self.dir = Path(cache_dir)
        self.announce_day = announce_day

    # ---------- 磁碟 ----------

    def _path(self, sym: str) -> Path:
        return self.dir / f"revm_{sym}.json"

    def _load(self, sym: str) -> Optional[dict]:
        p = self._path(sym)
        if not p.exists():
            return None
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            if not isinstance(d.get("rows"), dict):
                return None
            _p(d["queried_start"]), _p(d["queried_end"])  # 解析失敗 → 壞檔重抓
            # 丟掉不合法的月份鍵（防上游髒資料毒化快取後每次讀取都炸）
            bad = [k for k in d["rows"] if not _KEY_RE.match(str(k))]
            for k in bad:
                d["rows"].pop(k, None)
                d.get("date_raw", {}).pop(k, None)
            return d
        except Exception:
            return None

    def _save(self, sym: str, d: dict) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self._path(sym).with_suffix(".json.tmp")
        tmp.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self._path(sym))

    # ---------- 增量邏輯 ----------

    def _fetch_span(self, sym: str, m_start: pd.Period, m_end: pd.Period) -> pd.DataFrame:
        """向 API 抓 [m_start, m_end]（含）的月營收。

        起訖用「資料月當月 1 日 ~ 隔月月底再加一個月」的寬鬆日期界：
        SDK 已確認以資料月為語意（會自行 +1 月抵銷 date 偏移），寬鬆界是保險，
        涵蓋任何一種語意；最多多抓相鄰一兩個月，rows 以 revenue_year/month
        為鍵自然去重，絕不會漏。
        """
        start = f"{m_start.year}-{m_start.month:02d}-01"
        end_p = m_end + 2
        end = f"{end_p.year}-{end_p.month:02d}-01"
        df = self.provider.get_month_revenue([sym], start, end)
        return df[df["stock_id"] == sym] if not df.empty else df

    @staticmethod
    def _last_row(d: dict) -> Optional[pd.Period]:
        rows = d["rows"]
        return max((_p(k) for k in rows), default=None)

    def _ensure(self, sym: str, m_start: pd.Period, m_end: pd.Period, today: date) -> dict:
        """確保 [m_start, m_end] 中「照理已公告」的部分都查詢過；只補缺的頭/尾。"""
        horizon = latest_announced_month(today, self.announce_day)
        target_end = min(m_end, horizon)  # queried_end 的上限：不記「還沒公告」的未來月

        d = self._load(sym)
        if d is None:
            d = {"stock_id": sym, "rows": {}, "date_raw": {}}
            if target_end < m_start:
                return d  # 整段都在照理未公告的未來 → 什麼都不抓、不落盤
            self._merge(d, self._fetch_span(sym, m_start, target_end))
            d["queried_start"], d["queried_end"] = _ym(m_start), _ym(target_end)
            d["fetched_at"] = today.isoformat()
            self._save(sym, d)
            return d

        q_start, q_end = _p(d["queried_start"]), _p(d["queried_end"])
        dirty = False

        if m_start < q_start:  # 補頭（早已全部公告，一次補齊）
            self._merge(d, self._fetch_span(sym, m_start, q_start - 1))
            q_start = m_start
            dirty = True

        last_row = self._last_row(d)
        if target_end > q_end:
            # 補尾：從「舊區間中最後有資料的下一月」起抓（而非 q_end+1），
            # 順手癒合舊尾端因 API 落後留下的洞；不受 m_start 夾擠。
            lo = q_end + 1 if last_row is None else min(q_end + 1, last_row + 1)
            lo = max(q_start, lo)
            self._merge(d, self._fetch_span(sym, lo, target_end))
            q_end = target_end
            dirty = True
        elif self._tail_hole_needs_refresh(d, q_start, min(q_end, target_end), today):
            # 區間內尾端有洞（API 落後公告節奏）→ 每天最多補抓一次
            lo = q_start if last_row is None else max(q_start, last_row)
            self._merge(d, self._fetch_span(sym, lo, min(q_end, target_end)))
            dirty = True

        if dirty:
            d["queried_start"], d["queried_end"] = _ym(q_start), _ym(q_end)
            d["fetched_at"] = today.isoformat()
            self._save(sym, d)
        return d

    def _tail_hole_needs_refresh(self, d: dict, q_start: pd.Period,
                                 hi: pd.Period, today: date) -> bool:
        """已查過的區間 [q_start, hi] 內，尾端是否有待癒合的洞。"""
        if hi < q_start:
            return False
        last_row = self._last_row(d)
        if last_row is not None and last_row >= hi:
            return False  # 尾端已是照理應公告的最新月，無洞
        # 每天最多重抓一次（fetched_at 當天抓過就不再打）
        return str(d.get("fetched_at", "")) != today.isoformat()

    @staticmethod
    def _merge(d: dict, df: pd.DataFrame) -> None:
        if df is None or df.empty:
            return
        for r in df.itertuples(index=False):
            key = f"{int(r.rev_year)}-{int(r.rev_month):02d}"
            if not _KEY_RE.match(key):
                continue  # 防呆：provider 已過濾，這裡再擋一層
            d["rows"][key] = int(r.revenue)
            d["date_raw"][key] = str(r.date_raw)

    # ---------- 對外介面 ----------

    def get(
        self,
        stock_ids: Iterable[str],
        start: str,
        end: str,
        warmup_months: int = WARMUP_MONTHS,
        today: Optional[date] = None,
    ) -> Dict[str, pd.Series]:
        """回傳 {stock_id: 月營收 Series}，index 為 PeriodIndex(M)、值為元（float）。

        涵蓋 [start − warmup_months 個月, end]（z-score 暖身需要，spec §2.2）。
        缺月不補值（index 只含有資料的月份）；單檔 API 失敗只跳過該檔並警告，
        舊快取照常回（該檔資料不足時由指標層 fail-closed）。
        today 參數只供測試注入，正式流程用今天。
        """
        today = today or date.today()
        m_start = pd.Period(pd.Timestamp(start), freq="M") - int(warmup_months)
        m_end = pd.Period(pd.Timestamp(end), freq="M")
        out: Dict[str, pd.Series] = {}
        for sym in stock_ids:
            sym = str(sym)
            try:
                d = self._ensure(sym, m_start, m_end, today)
            except Exception as e:
                print(f"  ⚠ {sym} 月營收抓取失敗（沿用既有快取）：{e}", flush=True)
                d = self._load(sym)
                if d is None:
                    continue
            rows = {k: v for k, v in d["rows"].items()
                    if m_start <= _p(k) <= m_end}
            if not rows:
                continue
            s = pd.Series(
                {pd.Period(k, freq="M"): float(v) for k, v in rows.items()},
                dtype=float,
            ).sort_index()
            s.name = sym
            out[sym] = s
        return out
