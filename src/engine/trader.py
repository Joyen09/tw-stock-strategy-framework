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
        regime_filter: bool = False,
        regime_ma: int = 200,
        max_positions: int = 0,
        paused: bool = False,
    ):
        self.provider = provider
        self.broker = broker
        self.strategy = strategy
        self.position_budget = position_budget
        self.dry_run = dry_run
        self.lookback_days = lookback_days
        self.allow_odd_lot = allow_odd_lot
        # 最多同時持有幾檔 (只買訊號最強的前 N 檔)；0=不限制。
        self.max_positions = max_positions
        # 暫停：只出場、不買進 (由 Telegram /pause 控制)。
        self.paused = paused
        # 大盤風向濾網：與回測一致，加權指數跌破年線時禁止做多。
        self.regime_filter = regime_filter
        self.regime_ma = regime_ma
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

        # 大盤風向：加權指數是否在年線之上 (空頭則本輪禁止做多)。
        bull_market = True
        if self.regime_filter and bench is not None and len(bench) >= self.regime_ma:
            bull_market = float(bench.iloc[-1]) >= float(bench.rolling(self.regime_ma).mean().iloc[-1])

        # 第一輪：評估所有股票，分出「賣出」與「買進候選 (含訊號強度)」
        sells: List[TradePlan] = []
        buy_cands: List[tuple] = []  # (strength, plan)
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
                if not bull_market:  # 大盤空頭，禁止做多 (與回測一致)
                    continue
                budget = self.position_budget * sig.strength
                shares = int(budget // price) if self.allow_odd_lot else int(budget // (price * LOT)) * LOT
                if shares <= 0:
                    continue
                buy_cands.append((sig.strength, TradePlan(sym, "BUY", shares, price, sig.reason)))
            elif sig.action == Action.SELL and pos is not None:
                sells.append(TradePlan(sym, "SELL", pos.shares, price, sig.reason))

        # 先執行賣出 (出場不設上限)，釋出資金與部位額度
        plans: List[TradePlan] = []
        for plan in sells:
            if self._execute(plan):  # 只記錄真的成交的
                plans.append(plan)

        # 買進：只挑訊號最強的，且不超過最大持倉檔數 (避免資金被撒太散)
        buy_cands.sort(key=lambda x: x[0], reverse=True)
        held = len([p for p in self.broker.positions() if p.shares > 0])
        if self.paused:  # 暫停中：只出場、不買進
            buy_cands = []
        slots = (self.max_positions - held) if self.max_positions else len(buy_cands)
        for _, plan in buy_cands[: max(0, slots)]:
            if self._execute(plan):  # 資金不足會回 False，不誤報
                plans.append(plan)

        self.last_notify_ok = self._notify(plans, end)
        return plans

    def _execute(self, plan: TradePlan) -> bool:
        """執行下單；回傳是否真的成交 (dry-run 視為假設成立)。"""
        if self.dry_run:
            return True
        side = OrderSide.BUY if plan.action == "BUY" else OrderSide.SELL
        order = self.broker.place_order(Order(plan.symbol, side, plan.shares, plan.price, plan.reason))
        plan.sent = bool(getattr(order, "filled", False))
        return plan.sent

    def _notify(self, plans: List[TradePlan], end: str) -> bool:
        """有訊號就推 Telegram；無訊號不推，避免洗版。回傳是否成功送出。"""
        if not plans or self.notifier is None or not getattr(self.notifier, "enabled", False):
            return False
        import html

        mode = "✅ 已下單" if not self.dry_run else "🧪 模擬(未下單)"
        lines = [f"<b>📈 {html.escape(self.strategy.name)} 策略訊號</b> ({end}) {mode}"]
        for p in plans:
            emoji = "🟢買" if p.action == "BUY" else "🔴賣"
            # 理由含 PEG<=1.2、>0 等 < > 符號，HTML 模式須跳脫，否則 Telegram 回 400。
            reason = html.escape(p.reason)
            lines.append(f"{emoji} <b>{html.escape(p.symbol)}</b> {p.shares}股 @ {p.price:.2f}\n　{reason}")
        return self.notifier.send("\n".join(lines))
