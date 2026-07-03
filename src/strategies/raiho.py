"""雷浩斯獲利能力矩陣 (策略 P)——A 級公司 (高 ROE + 正自由現金流) 回檔便宜時買進長抱。

來源：雷浩斯公開著作「獲利能力矩陣」的量化詮釋 (非本人背書)。長線價值投資，
與短線策略完全不同物種：買的是「公司賺錢能力」，不因股價波動出場。

矩陣分級：
- A 級：ROE >= 15% 且近四季自由現金流 > 0 → 核心持股池
- B1 級：ROE 10~15% 且 FCF > 0 → 觀察池 (不進場)
- 其他：排除

進場 (全部滿足)：
1. A 級 (roe >= roe_a 且 fcf > 0)
2. 便宜：PE <= pe_max (近似「本益比河流圖下緣」)
3. 回檔：價格位於近 120 日區間的下 40% (等回檔分批買，不追高)

出場 (任一觸發)：
1. 獲利能力降級：ROE < roe_exit 或 FCF <= 0 → 基本面惡化才賣
2. (可選) 災難保險絲 stop_loss，預設 20%——spec 說不因波動出場，
   但全自動系統無人盯盤，保留一道深停損防公司暴雷，設 0 可關閉。

⚠️ 回測誠實聲明：本框架的基本面是「當下快照」，回測全期間用同一份 ROE/FCF/PE
(有前視偏差)，出場的「降級」條件在回測中永遠不會觸發 → 回測結果 ≈「用今天的
獲利能力矩陣選股後買進長抱」的選股能力測試，僅供與其他策略相對比較，
絕對數字不可盡信。真正的驗證是模擬盤逐季跑。
"""
from __future__ import annotations

from .. import indicators as ind
from ..models import Action, Signal
from .base import Strategy, StrategyContext


class RaihoStrategy(Strategy):
    name = "raiho"
    requires_fundamentals = True
    min_bars = 120

    DEFAULTS = dict(
        roe_a=15.0,        # A 級 ROE 門檻 (%)
        roe_exit=10.0,     # 跌破此 ROE 視為降級出場
        pe_max=18.0,       # 便宜價近似：本益比上限
        range_window=120,
        low_pos=0.4,       # 回檔：價格在近 120 日區間下 40%
        stop_loss=0.20,    # 災難保險絲 (spec 無此條，0=關閉)
    )

    def __init__(self, **params):
        merged = {**self.DEFAULTS, **params}
        super().__init__(**merged)

    def _grade(self, f) -> str:
        """獲利能力矩陣分級。缺值一律不給 A (寧可錯過)。"""
        if f.roe is None or f.fcf is None:
            return "?"
        if f.roe >= self.params["roe_a"] and f.fcf > 0:
            return "A"
        if f.roe >= self.params["roe_exit"] and f.fcf > 0:
            return "B1"
        return "C"

    def evaluate(self, ctx: StrategyContext) -> Signal:
        if not self._ready(ctx):
            return self._hold("資料不足 (價格或基本面)")

        p = self.params
        f = ctx.fundamentals
        close = ctx.prices["close"]
        price = close.iloc[-1]
        grade = self._grade(f)

        held = ctx.position is not None and ctx.position.shares > 0
        if held:
            entry = ctx.position.avg_price
            if p["stop_loss"] > 0 and entry > 0 and price <= entry * (1 - p["stop_loss"]):
                return self._signal(Action.SELL, 1.0,
                                    f"災難保險絲 -{p['stop_loss']:.0%} (防公司暴雷)", ctx.symbol)
            if grade == "C":
                return self._signal(Action.SELL, 1.0,
                                    "獲利能力矩陣降級 (ROE 或自由現金流惡化)，基本面出場", ctx.symbol)
            return self._hold(f"續抱 ({grade} 級，不因股價波動出場)")

        # --- 進場：A 級 + 便宜 + 回檔 ---
        if grade != "A":
            return self._hold(f"非 A 級 ({grade})")
        if f.pe is None or f.pe > p["pe_max"]:
            return self._hold(f"不夠便宜 (PE {f.pe} > {p['pe_max']})")

        w = p["range_window"]
        lo = close.iloc[-w:].min()
        hi = close.iloc[-w:].max()
        if hi > lo:
            rel = (price - lo) / (hi - lo)
            if rel > p["low_pos"]:
                return self._hold(f"未回檔到位 (區間位置 {rel:.0%} > {p['low_pos']:.0%})")

        # ROE 越高越強
        strength = min(1.0, 0.6 + (float(f.roe) - p["roe_a"]) / 50)
        return self._signal(
            Action.BUY, strength,
            f"A 級公司 (ROE {f.roe:.1f}%、FCF>0) 回檔便宜價 (PE {f.pe:.1f})，長線買進",
            ctx.symbol,
        )
