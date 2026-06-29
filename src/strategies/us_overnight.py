"""美股隔夜領先策略 (US overnight lead / spillover)。

理念：美股 (費半 ^SOX / 台積電 ADR) 隔夜大漲，隔天台股對應電子股傾向順勢；
利用美股收盤→台股開盤之間的資訊時間差。最適合半導體/電子權值股 (2330、2454…)。

訊號：
- 進場：美股代理隔夜報酬 >= up_threshold (預設 +1%) 且台股仍在短期均線之上 (趨勢確認)
- 出場：美股隔夜報酬 <= -down_threshold，或台股跌破更短的均線 (動能消失)

⚠️ 誠實的限制 (重要)：
- 台股開盤通常『跳空』把美股資訊反映掉，所以這策略賺的是『開盤後的延續』，不是跳空本身。
- 本框架回測以『收盤價』成交 → 等於放棄開盤跳空那段，屬於『偏保守』的估計
  (沒有偷看未來：隔夜報酬在台股開盤前就已知，收盤價更晚才發生)。
- 隔夜留倉有風險：美股一晚大跌，台股開盤直接受傷。
- 需要 yfinance 抓美股資料 (pip install yfinance)；抓不到時此策略對該檔回 HOLD。
"""
from __future__ import annotations

from .. import indicators as ind
from ..models import Action, Signal
from .base import Strategy, StrategyContext


class USOvernightStrategy(Strategy):
    name = "us_overnight"
    requires_fundamentals = False
    min_bars = 20

    DEFAULTS = dict(
        up_threshold=0.01,     # 美股隔夜漲幅門檻 (+1%)
        down_threshold=0.01,   # 美股隔夜跌幅出場門檻 (-1%)
        trend_ma=20,           # 進場趨勢濾網
        exit_ma=10,            # 出場均線
    )

    def __init__(self, lead_provider=None, **params):
        merged = {**self.DEFAULTS, **params}
        super().__init__(**merged)
        if lead_provider is None:
            from ..data.us_lead import USLeadProvider
            lead_provider = USLeadProvider()
        self.lead = lead_provider

    def evaluate(self, ctx: StrategyContext) -> Signal:
        if not self._ready(ctx):
            return self._hold("資料不足")

        on = self.lead.overnight_on(ctx.symbol, ctx.prices.index[-1])
        if on is None:
            return self._hold("無美股隔夜資料")

        p = self.params
        close = ctx.prices["close"]
        price = close.iloc[-1]
        trend = ind.sma(close, p["trend_ma"]).iloc[-1]
        exit_ma = ind.sma(close, p["exit_ma"]).iloc[-1]
        held = ctx.position is not None and ctx.position.shares > 0

        # 出場：美股隔夜轉弱，或台股動能消失 (跌破短均線)
        if held:
            if on <= -p["down_threshold"]:
                return self._signal(Action.SELL, 1.0, f"美股隔夜 {on:+.2%} 轉弱，出場", ctx.symbol)
            if exit_ma == exit_ma and price < exit_ma:
                return self._signal(Action.SELL, 1.0, f"跌破 {p['exit_ma']} 日均線，動能消失出場", ctx.symbol)

        # 進場：美股隔夜夠強 + 台股仍在趨勢之上
        if on >= p["up_threshold"] and trend == trend and price >= trend:
            strength = min(1.0, 0.5 + on * 20)  # 隔夜漲越多訊號越強
            return self._signal(
                Action.BUY, strength,
                f"美股隔夜領先 {on:+.2%}，順勢做多 (lead-lag)",
                ctx.symbol,
            )

        return self._hold(f"美股隔夜 {on:+.2%}，未達進場門檻")
