"""快取包裝：把任何 DataProvider 的查詢結果記在記憶體，避免重複打 API。

compare 一次要跑多個策略、每個策略都會抓同一批股票的資料；用這個包一層，
同一檔股票的歷史/基本面只會跟 FinMind 要一次，大幅減少 API 呼叫並加速。

DiskCachingProvider：在 CachingProvider 之外再加一層磁碟快取，重新啟動程式後
不需重抓已快取的資料——對 compare / walkforward 大幅省省 FinMind 配額。
  - history / benchmark：TTL 1 天（日線每天才更新）
  - fundamentals：TTL 7 天（季報月報不常變）
快取目錄：data_cache/（已 gitignore），原子寫入（.tmp → replace）。
"""
from __future__ import annotations

import os
import pickle
import time
from pathlib import Path
from typing import Optional

import pandas as pd

from .base import DataProvider

_HOUR = 3600
_DAY = 24 * _HOUR


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


class DiskCachingProvider(DataProvider):
    """磁碟快取 + 記憶體快取雙層。重啟程式不需重抓已存好的資料。"""

    def __init__(
        self,
        inner: DataProvider,
        cache_dir: str = "data_cache",
        history_ttl: int = _DAY,
        fundamentals_ttl: int = 7 * _DAY,
    ):
        self.inner = inner
        self.dir = Path(cache_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self._h_ttl = history_ttl
        self._f_ttl = fundamentals_ttl
        self._prune()
        # 記憶體二級快取（同 process 內避免重複讀磁碟）
        self._mem_h: dict = {}
        self._mem_f: dict = {}
        self._mem_b: dict = {}

    # --- 內部工具 ---

    def _prune(self, max_age: int = 14 * _DAY) -> None:
        """清掉超過 14 天的快取檔。scan 每天 end 日期不同會產新 key，不清會無限累積。"""
        try:
            now = time.time()
            for p in self.dir.glob("*.pkl"):
                if now - p.stat().st_mtime > max_age:
                    p.unlink(missing_ok=True)
        except Exception:
            pass  # 清不掉就算了，不影響功能

    def _path(self, name: str) -> Path:
        return self.dir / f"{name}.pkl"

    def _load(self, path: Path, ttl: int):
        if not path.exists():
            return None
        if time.time() - path.stat().st_mtime > ttl:
            return None  # 過期
        try:
            with open(path, "rb") as f:
                return pickle.load(f)
        except Exception:
            return None

    def _save(self, path: Path, obj) -> None:
        tmp = path.with_suffix(".pkl.tmp")
        try:
            with open(tmp, "wb") as f:
                pickle.dump(obj, f, protocol=pickle.HIGHEST_PROTOCOL)
            os.replace(tmp, path)
        except Exception:
            tmp.unlink(missing_ok=True)

    # --- DataProvider 介面 ---

    def history(self, symbol: str, start: str, end: str) -> pd.DataFrame:
        key = (symbol, start, end)
        if key in self._mem_h:
            return self._mem_h[key]
        slug = f"h_{symbol}_{start}_{end}".replace("-", "")
        data = self._load(self._path(slug), self._h_ttl)
        if data is None:
            data = self.inner.history(symbol, start, end)
            self._save(self._path(slug), data)
        self._mem_h[key] = data
        return data

    def fundamentals(self, symbol: str):
        if symbol in self._mem_f:
            return self._mem_f[symbol]
        slug = f"f_{symbol}"
        data = self._load(self._path(slug), self._f_ttl)
        if data is None:
            data = self.inner.fundamentals(symbol)
            self._save(self._path(slug), data)
        self._mem_f[symbol] = data
        return data

    _NONE = "__NONE__"  # 磁碟哨兵：記住「這次 TAIEX 抓不到」，避免每次重跑都再等一次逾時

    def benchmark(self, start: str, end: str) -> Optional[pd.Series]:
        key = (start, end)
        if key in self._mem_b:
            return self._mem_b[key]
        slug = f"bm_{start}_{end}".replace("-", "")
        cached = self._load(self._path(slug), self._h_ttl)
        if cached is not None:
            # cached 可能是 Series，或哨兵字串代表「上次抓不到」
            data = None if isinstance(cached, str) and cached == self._NONE else cached
        else:
            data = self.inner.benchmark(start, end)
            self._save(self._path(slug), self._NONE if data is None else data)
        self._mem_b[key] = data
        return data

    def universe(self):
        return self.inner.universe()
