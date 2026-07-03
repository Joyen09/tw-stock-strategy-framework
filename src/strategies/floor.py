"""地板股搶反彈 (策略 G)——跌到「個股統計極端乖離」且爆量，搶短線過度反應的反彈。

來源：權證小哥招牌短線的量化詮釋 (非本人背書)。核心概念「地板線」：
不用固定 -30% 乖離一體適用，而是取**這檔股票自己**的月線乖離歷史分布的
極端分位 (預設 P5)——牛皮股跌 8% 就是極端、飆股要跌 25% 才算。

進場 (全部滿足)：
1. 月線乖離 (close-20MA)/20MA <= 歷史乖離分布的 floor_pct 分位 (只用「昨日以前」
   的分布算分位，不含今天，避免前視)
2. 當日成交量 >= 20 日均量 × vol_mult (恐慌爆量)
3. 排除：資料不足 (< min_bars，分布不可信)

出場 (任一觸發，短進短出)：
1. 反彈回到 20 日均線 (乖離修復完成，反彈波吃完)
2. 停損 -4% (搶反彈像搶銀行，不對就跑)

備註：spec 的「排除主力持續大賣出貨中」需要券商分點資料 (付費限定)，本實作
省略——這是純價量簡化版。「T+1 殺低爆量才進、買黑不買紅」的日內擇時在日線
回測中以「訊號日收盤進場」近似，實際偏保守 (真實 SOP 買得更低)。
"""
from __future__ import annotations

import pandas as pd

from .. import indicators as ind
from ..models import Action, Signal
from .base import Strategy, StrategyContext


class FloorStrategy(Strategy):
    name = "floor"
    requires_fundamentals = False
    min_bars = 250  # 至少一年的乖離分布，分位數才可信

    DEFAULTS = dict(
        ma_window=20,     # 月線
        floor_pct=0.05,   # 地板線 = 乖離分布的第 5 百分位
        vol_window=20,
        vol_mult=2.0,     # 爆量：>= 20 日均量 2 倍
        stop_loss=0.04,   # 緊停損 4%
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

        ma = ind.sma(close, p["ma_window"])
        bias = (close - ma) / ma  # 月線乖離序列
        cur_bias = bias.iloc[-1]
        if cur_bias != cur_bias:
            return self._hold("均線未成形")

        held = ctx.position is not None and ctx.position.shares > 0
        # --- 出場優先：反彈到月線 or 停損 ---
        if held:
            entry = ctx.position.avg_price
            if entry > 0 and price <= entry * (1 - p["stop_loss"]):
                return self._signal(Action.SELL, 1.0, f"停損 -{p['stop_loss']:.0%}，搶反彈失敗快跑", ctx.symbol)
            if price >= ma.iloc[-1]:
                return self._signal(Action.SELL, 1.0, "反彈回月線，乖離修復完成獲利了結", ctx.symbol)
            return self._hold("持有中 (等反彈到月線或停損)")

        # --- 進場：跌破自家地板線 + 恐慌爆量 ---
        hist = bias.iloc[:-1].dropna()  # 只用昨日以前的分布，避免前視
        if len(hist) < self.min_bars - p["ma_window"] - 5:
            return self._hold("乖離分布樣本不足")
        floor_line = float(hist.quantile(p["floor_pct"]))
        if cur_bias > floor_line:
            return self._hold(f"未觸及地板線 (乖離 {cur_bias:.1%} > P{p['floor_pct']*100:.0f} {floor_line:.1%})")

        avg_vol = ind.sma(vol, p["vol_window"]).iloc[-1]
        if not (avg_vol == avg_vol and vol.iloc[-1] >= avg_vol * p["vol_mult"]):
            return self._hold("未爆量 (恐慌未到位，可能陰跌不止)")

        # 跌越深越極端，訊號越強
        depth = (floor_line - cur_bias) / abs(floor_line) if floor_line else 0
        strength = min(1.0, 0.6 + depth * 0.8)
        return self._signal(
            Action.BUY, strength,
            f"觸及地板線 (乖離 {cur_bias:.1%}，歷史 P{p['floor_pct']*100:.0f}) 且爆量，搶過度反應反彈",
            ctx.symbol,
        )
