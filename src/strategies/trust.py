"""投信認養波段多單 (策略 A)——投信持續大買佔比高 + 相對低檔 → 波段做多。

來源：權證小哥公開分享的量化詮釋 (非本人背書)。核心：投信 (基金經理人) 建倉是
以「週~月」為單位的持續買進，出現「投量比高 + 連買」時跟上，吃它的鋪倉波段。

進場 (全部滿足)：
1. 投量比 (投信買超股數 / 當日成交量) >= vol_ratio，且連續 >= ratio_days 天
2. 投信連買 >= streak_days 天
3. 股價位於近 60 日區間下半部 (相對低檔，還沒被抬高)
4. 流動性：20 日均量 >= min_volume 股

出場 (任一觸發)：
1. 投信近 streak_days 天轉為淨賣超 → 認養結束
2. 收盤跌破 20 日均線 (布林中軌)
3. 停損 -7%

備註：spec 原版投量比門檻 10% 是針對中小型股設計；tw50 大型股成交量大，投信
要佔到 10% 幾乎不可能，預設放寬為 3% (可用 --params vol_ratio=0.1 調回)。
spec 的「主力買賣超同步為正」需要券商分點資料 (FinMind 贊助會員限定)，
本實作省略該條件——這是簡化版，回測結果代表的是簡化版而非原版。
"""
from __future__ import annotations

import pandas as pd

from .. import indicators as ind
from ..models import Action, Signal
from .base import Strategy, StrategyContext


class TrustStrategy(Strategy):
    name = "trust"
    requires_fundamentals = False
    requires_chips = True
    min_bars = 60

    DEFAULTS = dict(
        vol_ratio=0.03,       # 投量比門檻 (spec 原版 0.10，針對中小型股)
        ratio_days=2,         # 投量比連續達標天數
        streak_days=3,        # 投信連買天數
        range_window=60,      # 相對低檔判斷窗口
        low_pos=0.5,          # 價格需在區間下半部 (0~1)
        exit_ma=20,           # 跌破 20MA 出場
        stop_loss=0.07,
        min_volume=1_000_000, # 20 日均量下限 (股) ≈ 1000 張
        chips_max_age=7,
    )

    def __init__(self, **params):
        merged = {**self.DEFAULTS, **params}
        super().__init__(**merged)

    def evaluate(self, ctx: StrategyContext) -> Signal:
        if not self._ready(ctx):
            return self._hold("資料不足 (價格或籌碼)")

        p = self.params
        df = ctx.prices
        close = df["close"]
        price = close.iloc[-1]
        chips = ctx.chips
        ma20 = ind.sma(close, p["exit_ma"]).iloc[-1]

        held = ctx.position is not None and ctx.position.shares > 0
        # --- 出場優先 ---
        if held:
            entry = ctx.position.avg_price
            if entry > 0 and price <= entry * (1 - p["stop_loss"]):
                return self._signal(Action.SELL, 1.0, f"停損 -{p['stop_loss']:.0%}", ctx.symbol)
            n = p["streak_days"]
            if len(chips) >= n and chips["trust_net"].iloc[-n:].sum() < 0:
                return self._signal(Action.SELL, 1.0, f"投信近 {n} 日轉賣超，認養結束", ctx.symbol)
            if ma20 == ma20 and price < ma20:
                return self._signal(Action.SELL, 1.0, f"跌破 {p['exit_ma']} 日均線出場", ctx.symbol)
            return self._hold("續抱 (投信認養未結束)")

        # --- 進場 ---
        age = (df.index[-1] - chips.index[-1]).days
        if age > p["chips_max_age"]:
            return self._hold(f"籌碼資料過舊 ({age} 天前)")

        n = p["streak_days"]
        if len(chips) < max(n, p["ratio_days"]):
            return self._hold("籌碼天數不足")

        # 1) 投信連買 N 天
        if not (chips["trust_net"].iloc[-n:] > 0).all():
            return self._hold("投信未連買")

        # 2) 投量比連續達標：用籌碼日期對齊當日成交量
        vol = df["volume"]
        ok_ratio = 0
        for d in chips.index[-p["ratio_days"]:]:
            if d in vol.index and vol.loc[d] > 0:
                if chips.loc[d, "trust_net"] / vol.loc[d] >= p["vol_ratio"]:
                    ok_ratio += 1
        if ok_ratio < p["ratio_days"]:
            return self._hold(f"投量比未連 {p['ratio_days']} 日 >= {p['vol_ratio']:.0%}")

        # 3) 相對低檔：價格在近 60 日區間下半部
        w = p["range_window"]
        lo = close.iloc[-w:].min()
        hi = close.iloc[-w:].max()
        if hi > lo:
            rel = (price - lo) / (hi - lo)
            if rel > p["low_pos"]:
                return self._hold(f"價格已在區間上半部 ({rel:.0%})，不追高")

        # 4) 流動性
        avg_vol = ind.sma(vol, 20).iloc[-1]
        if not (avg_vol == avg_vol and avg_vol >= p["min_volume"]):
            return self._hold("流動性不足")

        # 訊號強度：投量比越高越強
        last_d = chips.index[-1]
        ratio = chips["trust_net"].iloc[-1] / vol.loc[last_d] if last_d in vol.index and vol.loc[last_d] else 0
        strength = min(1.0, 0.6 + float(ratio) * 5)
        return self._signal(
            Action.BUY, strength,
            f"投信連買 {n} 日且投量比 >= {p['vol_ratio']:.0%}，相對低檔認養",
            ctx.symbol,
        )
