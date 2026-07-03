"""麥克連法人籌碼跟單 (策略 K)——投信/外資買超 + 多頭技術面確認的波段多單。

來源：麥克連公開著作《小散戶這樣追籌碼賺 1 億》的公開規則量化詮釋 (非本人背書)。
核心邏輯：跟著三大法人 (尤其投信) 的錢走，但技術面全數確認才進場。

籌碼標記 (用 ctx.chips: trust_net / foreign_net)：
- 紅色 (最強)：投信與外資「同日同買」 → strength 0.9
- 藍色：投信或外資連續 2 日買超 → strength 0.65

技術面過濾 (做多，全部滿足才進場)：
1. 週線：10 週均線 > 20 週均線 (週線多頭)
2. 日線：5/10/20/60 日均線多頭排列，且價在 5 日線上
3. KD：K > D
4. MACD：DIF > 0 且柱狀體為正
5. 量價：當日收漲且量 > 20 日均量 (簡化版「上漲量增」)

出場 (任一觸發)：
1. 漲幅達 +10% (漲幅滿足，先落袋)
2. 價離 5 日線正乖離 >= 10% (漲太快)
3. 收盤跌破 10 日線 (移動出場；tw50 屬中大型股用 10 日線)
4. 停損 -7% (全域紀律)

前視偏差防範：法人買賣超約 15:00–16:00 盤後公布，回測引擎只餵「評估日前一交易日
(含) 以前」的籌碼 → 訊號天然是 T+1 執行，不偷看當日籌碼。
"""
from __future__ import annotations

import pandas as pd

from .. import indicators as ind
from ..models import Action, Signal
from .base import Strategy, StrategyContext


class McLeanStrategy(Strategy):
    name = "mclean"
    requires_fundamentals = False
    requires_chips = True
    min_bars = 130  # 週線 20 週均 (~100 日) + 60 日均線緩衝

    DEFAULTS = dict(
        streak_days=2,        # 藍色標記: 連續買超天數
        profit_target=0.10,   # 漲幅滿足 +10%
        bias_exit=0.10,       # 離 5 日線正乖離 10% 出場
        exit_ma=10,           # 跌破 10 日線出場 (中大型股)
        stop_loss=0.07,       # 停損 -7%
        vol_window=20,
        chips_max_age=7,      # 籌碼最後日期距價格最後日期超過 N 天 → 資料過舊不進場
    )

    def __init__(self, **params):
        merged = {**self.DEFAULTS, **params}
        super().__init__(**merged)

    # --- 籌碼標記 ---
    def _chip_mark(self, chips: pd.DataFrame) -> tuple:
        """回傳 (顏色, strength)。紅=投信外資同買 0.9；藍=單邊連買 N 日 0.65；無=None。"""
        p = self.params
        n = p["streak_days"]
        if len(chips) < n:
            return None, 0.0
        last = chips.iloc[-1]
        if last["trust_net"] > 0 and last["foreign_net"] > 0:
            return "紅", 0.9
        tail = chips.iloc[-n:]
        if (tail["trust_net"] > 0).all() or (tail["foreign_net"] > 0).all():
            return "藍", 0.65
        return None, 0.0

    def evaluate(self, ctx: StrategyContext) -> Signal:
        if not self._ready(ctx):
            return self._hold("資料不足 (價格或籌碼)")

        p = self.params
        df = ctx.prices
        close = df["close"]
        price = close.iloc[-1]

        ma5 = ind.sma(close, 5).iloc[-1]
        ma10 = ind.sma(close, 10).iloc[-1]

        held = ctx.position is not None and ctx.position.shares > 0
        # --- 出場優先 ---
        if held:
            entry = ctx.position.avg_price
            if entry > 0 and price <= entry * (1 - p["stop_loss"]):
                return self._signal(Action.SELL, 1.0, f"停損 -{p['stop_loss']:.0%}", ctx.symbol)
            if entry > 0 and price >= entry * (1 + p["profit_target"]):
                return self._signal(Action.SELL, 1.0, f"漲幅滿足 +{p['profit_target']:.0%}，落袋", ctx.symbol)
            if ma5 == ma5 and ma5 > 0 and (price - ma5) / ma5 >= p["bias_exit"]:
                return self._signal(Action.SELL, 1.0, f"離 5 日線正乖離 >{p['bias_exit']:.0%}，漲太快先出", ctx.symbol)
            if ma10 == ma10 and price < ma10:
                return self._signal(Action.SELL, 1.0, f"跌破 {p['exit_ma']} 日線，移動出場", ctx.symbol)
            return self._hold("續抱 (法人趨勢未破壞)")

        # --- 進場：籌碼標記 + 技術面全確認 ---
        chips = ctx.chips
        # 籌碼過舊 (停牌/資料斷更) 不進場
        age = (df.index[-1] - chips.index[-1]).days
        if age > p["chips_max_age"]:
            return self._hold(f"籌碼資料過舊 ({age} 天前)")

        mark, strength = self._chip_mark(chips)
        if mark is None:
            return self._hold("無法人買超標記")

        # 1) 週線多頭：10 週均 > 20 週均
        wk = close.resample("W-FRI").last().dropna()
        if len(wk) < 20:
            return self._hold("週線資料不足")
        w10 = wk.rolling(10).mean().iloc[-1]
        w20 = wk.rolling(20).mean().iloc[-1]
        if not (w10 == w10 and w20 == w20 and w10 > w20):
            return self._hold("週線未多頭 (10週<20週)")

        # 2) 日線均線多頭排列
        ma20 = ind.sma(close, 20).iloc[-1]
        ma60 = ind.sma(close, 60).iloc[-1]
        if not (price >= ma5 >= ma10 >= ma20 >= ma60):
            return self._hold("日線均線未多頭排列")

        # 3) KD 金叉狀態
        k, d = ind.kd(df)
        if not (k.iloc[-1] == k.iloc[-1] and k.iloc[-1] > d.iloc[-1]):
            return self._hold("KD 未黃金交叉")

        # 4) MACD 多方
        dif, sig_line, hist = ind.macd(close)
        if not (dif.iloc[-1] > 0 and hist.iloc[-1] > 0):
            return self._hold("MACD 未翻多")

        # 5) 上漲量增
        vol = df["volume"]
        avg_vol = ind.sma(vol, p["vol_window"]).iloc[-1]
        up_day = len(close) >= 2 and price > close.iloc[-2]
        big_vol = avg_vol == avg_vol and vol.iloc[-1] > avg_vol
        if not (up_day and big_vol):
            return self._hold("非上漲量增")

        return self._signal(
            Action.BUY, strength,
            f"法人{mark}色標記 (投信/外資買超) + 週日線多頭 + KD/MACD 翻多 + 上漲量增",
            ctx.symbol,
        )
