"""實單/模擬盤執行器：抓最新資料 -> 跑策略 -> 透過 Broker 下單。

設計成「跑一次 = 掃一輪標的」，由外部排程器 (cron / APScheduler) 在盤中或收盤後觸發，
而不是在程式內 while True，方便控制與除錯。

安全預設：dry_run=True 只印出「會下什麼單」但不真的送出，確認無誤再關閉。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

import pandas as pd

from ..broker.base import Broker, Order, OrderSide
from ..data.base import DataProvider
from ..models import Action, Position
from ..strategies.base import Strategy, StrategyContext

LOT = 1000


@dataclass
class TradePlan:
    symbol: str
    action: str
    shares: int
    price: float
    reason: str
    sent: bool = False


class LiveTrader:
    def __init__(
        self,
        provider: DataProvider,
        broker: Broker,
        strategy: Strategy,
        position_budget: float = 200_000.0,
        dry_run: bool = True,
        lookback_days: int = 400,
        quote_fn=None,
        notifier=None,
        allow_odd_lot: bool = True,
    ):
        self.provider = provider
        self.broker = broker
        self.strategy = strategy
        self.position_budget = position_budget
        self.dry_run = dry_run
        self.lookback_days = lookback_days
        self.allow_odd_lot = allow_odd_lot
        # quote_fn(symbol) -> 即時價 (盤中用)，把今天這根 K 換成現價，讓突破/停損即時生效。
        self.quote_fn = quote_fn
        self.notifier = notifier

    def _apply_realtime(self, df: pd.DataFrame, symbol: str, end: str) -> pd.DataFrame:
        """盤中用即時報價更新/補上「今天」這根 K，使日線策略能即時反應。"""
        if self.quote_fn is None:
            return df
        try:
            live = self.quote_fn(symbol)
        except Exception as e:  # 取價失敗就用原始日 K，不中斷
            print(f"[realtime] {symbol} 取即時價失敗: {e}")
            return df
        if not live or live <= 0:
            return df
        today = pd.Timestamp(end).normalize()
        df = df.copy()
        if df.index[-1].normalize() == today:
            df.iloc[-1, df.columns.get_loc("close")] = live
            df.iloc[-1, df.columns.get_loc("high")] = max(df["high"].iloc[-1], live)
            df.iloc[-1, df.columns.get_loc("low")] = min(df["low"].iloc[-1], live)
        else:
            row = df.iloc[-1].copy()
            row["open"] = row["high"] = row["low"] = row["close"] = live
            row["volume"] = 0
            df = pd.concat([df, row.to_frame().T])
            df.index = list(df.index[:-1]) + [today]
        return df

    def _current_position(self, symbol: str) -> Optional[Position]:
        for p in self.broker.positions():
            if p.symbol == symbol and p.shares > 0:
                return p
        return None

    def scan(self, symbols: List[str], end: str) -> List[TradePlan]:
        """掃描標的，回傳本輪要執行的交易計畫 (並視 dry_run 決定是否真的送單)。"""
        start = (pd.Timestamp(end) - pd.Timedelta(days=self.lookback_days * 2)).strftime("%Y-%m-%d")
        bench = self.provider.benchmark(start, end)
        plans: List[TradePlan] = []

        for sym in symbols:
            df = self.provider.history(sym, start, end)
            if df.empty:
                continue
            df = self._apply_realtime(df, sym, end)
            price = float(df["close"].iloc[-1])
            pos = self._current_position(sym)
            b = bench.reindex(df.index).ffill() if bench is not None else None
            ctx = StrategyContext(
                symbol=sym,
                prices=df,
                fundamentals=self.provider.fundamentals(sym),
                benchmark=b,
                position=pos,
            )
            sig = self.strategy.evaluate(ctx)
            if not sig.is_actionable:
                continue

            if sig.action == Action.BUY and pos is None:
                budget = self.position_budget * sig.strength
                if self.allow_odd_lot:
                    shares = int(budget // price)               # 零股
                else:
                    shares = int(budget // (price * LOT)) * LOT  # 整張
                if shares <= 0:
                    continue
                plan = TradePlan(sym, "BUY", shares, price, sig.reason)
            elif sig.action == Action.SELL and pos is not None:
                plan = TradePlan(sym, "SELL", pos.shares, price, sig.reason)
            else:
                continue

            if not self.dry_run:
                side = OrderSide.BUY if plan.action == "BUY" else OrderSide.SELL
                self.broker.place_order(Order(plan.symbol, side, plan.shares, plan.price, plan.reason))
                plan.sent = True
            plans.append(plan)

        self._notify(plans, end)
        return plans

    def _notify(self, plans: List[TradePlan], end: str):
        """有訊號就推 Telegram；無訊號不推，避免洗版。"""
        if not plans or self.notifier is None or not getattr(self.notifier, "enabled", False):
            return
        mode = "✅ 已下單" if not self.dry_run else "🧪 模擬(未下單)"
        lines = [f"<b>📈 {self.strategy.name} 策略訊號</b> ({end}) {mode}"]
        for p in plans:
            emoji = "🟢買" if p.action == "BUY" else "🔴賣"
            lines.append(f"{emoji} <b>{p.symbol}</b> {p.shares}股 @ {p.price:.2f}\n　{p.reason}")
        self.notifier.send("\n".join(lines))
