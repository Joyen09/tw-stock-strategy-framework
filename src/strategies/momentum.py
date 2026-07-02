"""短線動能突破 (Momentum breakout) — 「快層」波段策略。

lynch/buffett/graham 是慢層 (基本面、抱數月)；這支是快層代表：純技術、短窗突破、緊停損，
反應比 livermore(60日) / oneil(52週) 這種中期突破更快，適合「盤中即時進出」的節奏
(live 端用兩層架構——慢層一天算一次候選、快層盤中用 Shioaji 即時價執行；回測用日 K)。

- 進場：股價突破近 N 日 (預設 20) 高點，且短均線 >= 長均線 (20>60，近期趨勢向上)、當日帶量。
- 出場 (擇一觸發)：固定停損 5% / 跌破近 M 日 (預設 10) 低點 / 跌破短均線 (動能轉弱)。
純技術 (不需基本面)，可跑任何 universe。
"""
from __future__ import annotations

from .. import indicators as ind
from ..models import Action, Signal
from .base import Strategy, StrategyContext


class MomentumStrategy(Strategy):
    name = "momentum"
    requires_fundamentals = False
    min_bars = 60

    DEFAULTS = dict(
        breakout_window=20,   # 突破近 20 日高點 (短線)
        fast_ma=20,
        slow_ma=60,
        vol_window=20,
        vol_mult=1.2,         # 帶量：當日量 >= 20 日均量 ×1.2
        exit_window=10,       # 跌破近 10 日低點出場
        stop_loss=0.05,       # 緊停損 5% (比 oneil 的 8% 更快砍)
    )

    def __init__(self, **params):
        merged = {**self.DEFAULTS, **params}
        super().__init__(**merged)

    def evaluate(self, ctx: StrategyContext) -> Signal:
        if not self._ready(ctx):
            return self._hold("資料不足")

        p = self.params
        df = ctx.prices
        close = df["close"]
        vol = df["volume"]
        price = close.iloc[-1]
        fast = ind.sma(close, p["fast_ma"]).iloc[-1]
        slow = ind.sma(close, p["slow_ma"]).iloc[-1]

        held = ctx.position is not None and ctx.position.shares > 0
        # --- 出場優先：緊停損 / 跌破近期低點 / 跌破短均線 ---
        if held:
            entry = ctx.position.avg_price
            exit_low = ind.rolling_low(close, p["exit_window"]).shift(1).iloc[-1]
            if entry > 0 and price <= entry * (1 - p["stop_loss"]):
                return self._signal(Action.SELL, 1.0, f"停損 -{p['stop_loss']:.0%}", ctx.symbol)
            if exit_low == exit_low and price < exit_low:
                return self._signal(Action.SELL, 1.0, f"跌破近 {p['exit_window']} 日低點出場", ctx.symbol)
            if fast == fast and price < fast:
                return self._signal(Action.SELL, 1.0, f"跌破 {p['fast_ma']} 日均線，動能轉弱", ctx.symbol)

        # --- 進場：帶量突破短期高點 + 均線多頭排列 ---
        prior_high = ind.rolling_high(close, p["breakout_window"]).shift(1).iloc[-1]
        avg_vol = ind.sma(vol, p["vol_window"]).iloc[-1]
        breakout = prior_high == prior_high and price >= prior_high
        up_trend = fast == fast and slow == slow and price >= fast and fast >= slow
        big_vol = avg_vol == avg_vol and vol.iloc[-1] >= avg_vol * p["vol_mult"]

        if breakout and up_trend and big_vol:
            # 突破幅度越大訊號越強
            margin = (price - prior_high) / prior_high if prior_high else 0
            strength = min(1.0, 0.6 + margin * 25)
            return self._signal(
                Action.BUY, strength,
                f"短線帶量突破近 {p['breakout_window']} 日高點且均線多頭排列",
                ctx.symbol,
            )

        return self._hold("未帶量突破或趨勢未確認")
