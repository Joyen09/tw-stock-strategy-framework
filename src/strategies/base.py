"""策略基底類別。

每個名人策略都繼承 Strategy 並實作 evaluate()。設計重點：

- 策略只負責「看資料 -> 產生訊號」，不碰下單與資金控管，方便回測與實單共用。
- evaluate() 只能看到「截至當下」的資料 (prices 已由引擎切片)，避免未來函數 (look-ahead bias)。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pandas as pd

from ..models import Action, Fundamentals, Position, Signal


@dataclass
class StrategyContext:
    """傳給策略的所有資訊。"""

    symbol: str
    prices: pd.DataFrame                      # 欄位: open/high/low/close/volume，index 為日期，僅含當日(含)以前
    fundamentals: Optional[Fundamentals] = None
    benchmark: Optional[pd.Series] = None     # 大盤收盤價 (對齊 prices.index)，相對強弱用
    position: Optional[Position] = None       # 目前持倉，None 代表空手
    chips: Optional[pd.DataFrame] = None      # 籌碼: trust_net/foreign_net (三大法人買賣超)。
                                              # 法人資料盤後才公布 → 引擎只餵「前一交易日(含)以前」，天然 T+1


class Strategy:
    """所有策略的父類別。"""

    name = "base"
    #: 是否需要基本面資料，沒有就無法評估
    requires_fundamentals = False
    #: 是否需要籌碼資料 (三大法人買賣超)，沒有就無法評估
    requires_chips = False
    #: 至少需要幾根 K 棒才開始評估
    min_bars = 1

    def __init__(self, **params):
        self.params = params

    # --- 子類別覆寫 ---
    def evaluate(self, ctx: StrategyContext) -> Signal:  # pragma: no cover - 抽象
        raise NotImplementedError

    # --- 共用工具 ---
    def _ready(self, ctx: StrategyContext) -> bool:
        if len(ctx.prices) < self.min_bars:
            return False
        if self.requires_fundamentals and ctx.fundamentals is None:
            return False
        if self.requires_chips and (ctx.chips is None or ctx.chips.empty):
            return False
        return True

    def _hold(self, reason: str = "條件不足") -> Signal:
        return Signal(Action.HOLD, 0.0, reason, strategy=self.name)

    def _signal(self, action: Action, strength: float, reason: str, symbol: str) -> Signal:
        return Signal(action, max(0.0, min(1.0, strength)), reason, symbol, self.name)
