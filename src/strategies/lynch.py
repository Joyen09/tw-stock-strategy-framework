"""彼得‧林區 (Peter Lynch) — 成長合理價 (GARP) / PEG。

核心理念："以合理價格買進成長股"，最愛指標是 PEG = 本益比 / 盈餘成長率。

選股條件：
1. PEG <= 1 最理想 (<= 1.2 可接受)            -> 成長相對便宜
2. 盈餘成長率介於 15% ~ 50% (太高難持續)       -> 穩健成長的 "tenbagger" 候選
3. 負債比不高 (<= 60%)
4. 營收同步成長 (> 0)

林區重視「趨勢仍在」，所以加一個技術濾網：股價在季線 (60MA) 之上。
賣出：成長動能消失 (PEG 失效或營收轉負) 或跌破季線。

exit_buffer（季線緩衝）：
買在季線之上、跌破季線就賣，兩個門檻緊貼著 —— 股價在季線附近震盪時會一直
買→賣→買→賣。2026-08 實盤就出現「台積電抱 2 天被砍」(-4.3%) 的案例。
緩衝讓賣出門檻降到季線下方 N%，形成緩衝區間 (hysteresis)。
**預設 0.0 = 維持原行為**；要改預設值前必須先過三關驗證
(tools/validate_lynch_buffer.py)，不可憑感覺調。
"""
from __future__ import annotations

from .. import indicators as ind
from ..models import Action, Signal
from .base import Strategy, StrategyContext


class LynchStrategy(Strategy):
    name = "lynch"
    requires_fundamentals = True
    min_bars = 60

    DEFAULTS = dict(
        max_peg=1.2,
        min_growth=15.0,
        max_growth=50.0,
        max_debt_ratio=60.0,
        ma_window=60,
        exit_buffer=0.0,  # 季線緩衝；0.0=原行為 (見模組 docstring)
    )

    def __init__(self, **params):
        merged = {**self.DEFAULTS, **params}
        super().__init__(**merged)

    def evaluate(self, ctx: StrategyContext) -> Signal:
        if not self._ready(ctx):
            return self._hold("資料不足或缺基本面")

        f = ctx.fundamentals
        p = self.params
        peg = f.peg
        close = ctx.prices["close"]
        ma = ind.sma(close, p["ma_window"]).iloc[-1]
        price = close.iloc[-1]
        has_ma = ma == ma  # NaN 檢查：資料不足時季線算不出來
        above_ma = price >= ma if has_ma else False
        # 出場門檻比進場低 exit_buffer，中間形成緩衝區間，股價貼著季線震盪時不會來回洗。
        exit_line = ma * (1 - p["exit_buffer"]) if has_ma else float("nan")
        broke_ma = price < exit_line if has_ma else False

        checks = {
            "PEG<=1.2": peg is not None and 0 < peg <= p["max_peg"],
            "EPS成長15~50%": f.eps_growth is not None and p["min_growth"] <= f.eps_growth <= p["max_growth"],
            "營收成長>0": f.revenue_growth is not None and f.revenue_growth > 0,
            "負債比<=60%": f.debt_ratio is not None and f.debt_ratio <= p["max_debt_ratio"],
        }
        passed = sum(checks.values())
        score = passed / len(checks)
        detail = "、".join(k for k, v in checks.items() if v) or "無"
        peg_txt = f"{peg:.2f}" if peg is not None else "N/A"

        held = ctx.position is not None and ctx.position.shares > 0
        if held and (score < 0.5 or broke_ma):
            buf = p["exit_buffer"]
            line = f"季線-{buf:.0%}" if buf > 0 else "季線"
            return self._signal(
                Action.SELL, 1.0,
                f"成長動能轉弱 (PEG={peg_txt}) 或跌破{line}，出場",
                ctx.symbol,
            )

        if score >= 0.75 and above_ma:
            return self._signal(
                Action.BUY, score,
                f"成長合理價 GARP (PEG={peg_txt}, 符合: {detail})",
                ctx.symbol,
            )

        return self._hold(f"GARP 條件不足 {score:.0%} (PEG={peg_txt})")
