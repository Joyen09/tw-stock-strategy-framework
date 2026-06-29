"""快取包裝：把任何 DataProvider 的查詢結果記在記憶體，避免重複打 API。

compare 一次要跑多個策略、每個策略都會抓同一批股票的資料；用這個包一層，
同一檔股票的歷史/基本面只會跟 FinMind 要一次，大幅減少 API 呼叫並加速。
"""
from __future__ import annotations

from typing import Optional

import pandas as pd

from .base import DataProvider


class CachingProvider(DataProvider):
    def __init__(self, inner: DataProvider):
        self.inner = inner
        self._h: dict = {}
        self._f: dict = {}
        self._b: dict = {}

    def history(self, symbol: str, start: str, end: str) -> pd.DataFrame:
        key = (symbol, start, end)
        if key not in self._h:
            self._h[key] = self.inner.history(symbol, start, end)
        return self._h[key]

    def fundamentals(self, symbol: str):
        if symbol not in self._f:
            self._f[symbol] = self.inner.fundamentals(symbol)
        return self._f[symbol]

    def benchmark(self, start: str, end: str) -> Optional[pd.Series]:
        key = (start, end)
        if key not in self._b:
            self._b[key] = self.inner.benchmark(start, end)
        return self._b[key]

    def universe(self):
        return self.inner.universe()
