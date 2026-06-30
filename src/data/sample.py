"""離線樣本資料來源。

用確定性的隨機過程 (固定種子) 生成幾檔台股的合成日 K 線與基本面，
讓使用者「不需要任何 API 金鑰、不需連網」就能跑回測、跑測試。
換成真實資料時，把 FinMindProvider 接上即可，介面相同。
"""
from __future__ import annotations

from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from ..models import Fundamentals
from .base import DataProvider

# 幾檔代表性台股 + 一組合理的基本面假資料 (僅供 demo / 回測流程驗證)。
_STOCKS = {
    "2330": dict(name="台積電", drift=0.0006, vol=0.018, start=300,
                 fund=dict(pe=18, pb=4.5, roe=28, eps=33, eps_growth=18, revenue_growth=12,
                           dividend_yield=2.0, debt_ratio=30, current_ratio=200, gross_margin=53)),
    "2317": dict(name="鴻海", drift=0.0003, vol=0.016, start=100,
                 fund=dict(pe=11, pb=1.3, roe=10, eps=10, eps_growth=8, revenue_growth=5,
                           dividend_yield=4.5, debt_ratio=58, current_ratio=160, gross_margin=6)),
    "2412": dict(name="中華電", drift=0.0001, vol=0.008, start=120,
                 fund=dict(pe=24, pb=2.4, roe=10, eps=4.7, eps_growth=2, revenue_growth=1,
                           dividend_yield=4.2, debt_ratio=25, current_ratio=90, gross_margin=37)),
    "2454": dict(name="聯發科", drift=0.0007, vol=0.024, start=600,
                 fund=dict(pe=16, pb=3.6, roe=22, eps=60, eps_growth=30, revenue_growth=20,
                           dividend_yield=3.5, debt_ratio=35, current_ratio=210, gross_margin=48)),
    "2603": dict(name="長榮", drift=0.0004, vol=0.030, start=150,
                 fund=dict(pe=8, pb=1.2, roe=15, eps=18, eps_growth=40, revenue_growth=25,
                           dividend_yield=5.0, debt_ratio=45, current_ratio=170, gross_margin=22)),
    # 以下為演示用，基本面皆符合林區條件 (PEG<=1.2、EPS成長15~50%)，但漲跌不同 → 容易觸發換股。
    "2308": dict(name="台達電", drift=0.0005, vol=0.022, start=300,
                 fund=dict(pe=20, pb=4.0, roe=22, eps=15, eps_growth=25, revenue_growth=18,
                           dividend_yield=2.5, debt_ratio=40, current_ratio=180, gross_margin=29)),
    "2891": dict(name="中信金", drift=0.0002, vol=0.014, start=30,
                 fund=dict(pe=11, pb=1.0, roe=12, eps=2.5, eps_growth=20, revenue_growth=15,
                           dividend_yield=5.5, debt_ratio=55, current_ratio=120, gross_margin=40)),
    "2882": dict(name="國泰金", drift=-0.0001, vol=0.016, start=55,
                 fund=dict(pe=10, pb=1.1, roe=11, eps=5.0, eps_growth=18, revenue_growth=12,
                           dividend_yield=5.0, debt_ratio=58, current_ratio=110, gross_margin=35)),
    "3034": dict(name="聯詠", drift=0.0006, vol=0.026, start=400,
                 fund=dict(pe=15, pb=3.0, roe=25, eps=35, eps_growth=35, revenue_growth=22,
                           dividend_yield=4.0, debt_ratio=38, current_ratio=190, gross_margin=42)),
    "1101": dict(name="台泥", drift=-0.0002, vol=0.018, start=35,
                 fund=dict(pe=13, pb=1.1, roe=9, eps=2.0, eps_growth=16, revenue_growth=10,
                           dividend_yield=4.5, debt_ratio=50, current_ratio=130, gross_margin=20)),
}


class SampleDataProvider(DataProvider):
    """合成台股資料，確定性 (seed) 以利測試重現。"""

    def __init__(self, seed: int = 42, days: int = 600):
        self.seed = seed
        self.days = days
        self._cache: Dict[str, pd.DataFrame] = {}

    def _gen(self, symbol: str) -> pd.DataFrame:
        if symbol in self._cache:
            return self._cache[symbol]
        meta = _STOCKS[symbol]
        rng = np.random.default_rng(self.seed + int(symbol))
        n = self.days
        rets = rng.normal(meta["drift"], meta["vol"], n)
        close = meta["start"] * np.exp(np.cumsum(rets))
        # 由 close 推出合理的 OHLC
        intraday = np.abs(rng.normal(0, meta["vol"] / 2, n))
        high = close * (1 + intraday)
        low = close * (1 - intraday)
        open_ = np.concatenate([[close[0]], close[:-1]])
        volume = rng.integers(5_000, 80_000, n) * 1000
        idx = pd.bdate_range(end="2025-12-31", periods=n)
        df = pd.DataFrame(
            {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
            index=idx,
        )
        self._cache[symbol] = df
        return df

    def history(self, symbol: str, start: str, end: str) -> pd.DataFrame:
        df = self._gen(symbol)
        return df.loc[start:end].copy()

    def fundamentals(self, symbol: str) -> Optional[Fundamentals]:
        meta = _STOCKS.get(symbol)
        if not meta:
            return None
        return Fundamentals(symbol=symbol, name=meta["name"], **meta["fund"])

    def benchmark(self, start: str, end: str) -> Optional[pd.Series]:
        # 用所有樣本股等權平均當作「大盤」代理。
        closes = [self._gen(s)["close"] for s in _STOCKS]
        bench = pd.concat(closes, axis=1).mean(axis=1)
        return bench.loc[start:end].copy()

    def universe(self) -> List[str]:
        return list(_STOCKS.keys())
