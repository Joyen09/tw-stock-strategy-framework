"""美股隔夜領先資料 (US overnight lead)。

概念：美股某日盤 (calendar day t) 收盤在台灣時間 t+1 凌晨 ~04:00，
台股 t+1 早上 09:00 才開盤 → 美股 t 的漲跌「領先」台股 t+1。

本模組把美股代理指數/個股的「隔夜報酬」對齊到台股的「反應日期」，
供 us_overnight 策略使用。資料用 yfinance；抓不到時優雅降級回 None。

代理對應 (台股代號 -> 美股代理)：
- 2330 台積電 -> TSM (台積電 ADR)，相關性最高
- 半導體/電子權值 -> ^SOX (費城半導體指數)
- 其他預設也用 ^SOX (本策略最適合電子/半導體股)
"""
from __future__ import annotations

from typing import Dict, Optional

import pandas as pd


class USLeadProvider:
    PROXY: Dict[str, str] = {
        "2330": "TSM",   # 台積電 ADR
        "2454": "^SOX", "2379": "^SOX", "3034": "^SOX", "3037": "^SOX",
        "2308": "^SOX", "2317": "^SOX", "2382": "^SOX", "3231": "^SOX",
        "2357": "^SOX", "2303": "^SOX", "3711": "^SOX",
    }
    DEFAULT = "^SOX"

    def __init__(self, lookback: str = "6y", series_map: Optional[Dict[str, pd.Series]] = None):
        """series_map: 可注入 {proxy: 隔夜報酬Series} 供測試/離線使用 (繞過 yfinance)。"""
        self.lookback = lookback
        self._cache: Dict[str, pd.Series] = dict(series_map or {})
        self._failed: set = set()

    def _proxy(self, symbol: str) -> str:
        return self.PROXY.get(symbol, self.DEFAULT)

    def overnight_returns(self, symbol: str) -> Optional[pd.Series]:
        """回傳該股對應美股代理的『隔夜報酬』Series，index 已平移到台股反應日。"""
        proxy = self._proxy(symbol)
        if proxy in self._cache:
            return self._cache[proxy]
        if proxy in self._failed:
            return None
        try:
            import yfinance as yf

            df = yf.download(proxy, period=self.lookback, progress=False, auto_adjust=True)
            close = df["Close"]
            if hasattr(close, "columns"):  # 多層欄位時取第一欄
                close = close.iloc[:, 0]
            close = close.dropna()
            ret = close.pct_change().dropna()
            # 平移 +1 天：美股 t 的報酬對應台股 t+1 反應 (避免未來函數)。
            ret.index = pd.to_datetime(ret.index).tz_localize(None).normalize() + pd.Timedelta(days=1)
            self._cache[proxy] = ret
            return ret
        except Exception as e:  # 網路被擋/未裝 yfinance → 優雅降級
            print(f"[us_lead] {proxy} 隔夜資料抓取失敗（{e}）；該策略將略過此檔")
            self._failed.add(proxy)
            return None

    def overnight_on(self, symbol: str, date) -> Optional[float]:
        """取得『台股某日』可參考的美股隔夜報酬 (最近一個已收盤的美股 session)。"""
        s = self.overnight_returns(symbol)
        if s is None or s.empty:
            return None
        try:
            v = s.asof(pd.Timestamp(date).normalize())  # <= date 的最後一筆，無未來函數
        except Exception:
            return None
        return float(v) if v == v else None  # 過濾 NaN
