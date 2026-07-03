"""共用資料模型 (shared data models)."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class Action(str, Enum):
    """交易訊號動作。"""

    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


@dataclass
class Signal:
    """單一個股、單一策略在某時間點產生的訊號。

    strength 介於 0~1，代表訊號強度（例如可用來決定下單部位大小）。
    reason 用中文描述為什麼產生這個訊號，方便人工審查。
    """

    action: Action = Action.HOLD
    strength: float = 0.0
    reason: str = ""
    symbol: Optional[str] = None
    strategy: Optional[str] = None

    @property
    def is_actionable(self) -> bool:
        return self.action in (Action.BUY, Action.SELL) and self.strength > 0


@dataclass
class Fundamentals:
    """個股基本面資料 (來源例如 FinMind / 公開資訊觀測站)。

    欄位皆為 Optional，缺值時策略應自行判斷（通常視為不符合條件）。
    """

    symbol: str
    name: str = ""
    pe: Optional[float] = None              # 本益比 Price / Earnings
    pb: Optional[float] = None              # 股價淨值比 Price / Book
    roe: Optional[float] = None             # 股東權益報酬率 (%)
    eps: Optional[float] = None             # 每股盈餘
    eps_growth: Optional[float] = None      # EPS 年成長率 (%)
    revenue_growth: Optional[float] = None  # 營收年成長率 (%)
    dividend_yield: Optional[float] = None  # 現金殖利率 (%)
    debt_ratio: Optional[float] = None      # 負債比 (%)
    current_ratio: Optional[float] = None   # 流動比率 (%)
    gross_margin: Optional[float] = None    # 毛利率 (%)
    market_cap: Optional[float] = None      # 市值 (億元)
    fcf: Optional[float] = None             # 近四季自由現金流 (營業現金流-資本支出，元)
    extra: dict = field(default_factory=dict)

    @property
    def peg(self) -> Optional[float]:
        """彼得林區愛用的 PEG = PE / 盈餘成長率。"""
        if self.pe is None or self.eps_growth is None or self.eps_growth <= 0:
            return None
        return self.pe / self.eps_growth


@dataclass
class Position:
    """持倉。"""

    symbol: str
    shares: int = 0
    avg_price: float = 0.0

    @property
    def cost(self) -> float:
        return self.shares * self.avg_price
