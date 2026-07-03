"""向量化逐日回測引擎。

流程：
1. 對每個交易日，把「截至當日」的價格切片餵給策略 (避免未來函數)。
2. 策略產生 BUY/SELL 訊號 -> 依資金控管換算張數 -> PaperBroker 以當日收盤價撮合。
3. 記錄每日總資產，最後計算績效指標。

資金控管 (簡化版)：每檔股票最多投入 position_pct 的「初始資金」，整張 (1000 股) 為單位。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

import pandas as pd

from ..broker.base import Order, OrderSide
from ..broker.paper import PaperBroker
from ..data.base import DataProvider
from ..models import Action, Position
from ..strategies.base import Strategy, StrategyContext

LOT = 1000  # 台股一張股數


@dataclass
class Trade:
    date: pd.Timestamp
    symbol: str
    side: str
    shares: int
    price: float
    reason: str


@dataclass
class BacktestResult:
    equity_curve: pd.Series
    trades: List[Trade] = field(default_factory=list)
    initial_cash: float = 0.0

    # --- 績效指標 ---
    @property
    def total_return(self) -> float:
        if self.equity_curve.empty:
            return 0.0
        return self.equity_curve.iloc[-1] / self.equity_curve.iloc[0] - 1

    @property
    def cagr(self) -> float:
        if len(self.equity_curve) < 2:
            return 0.0
        years = (self.equity_curve.index[-1] - self.equity_curve.index[0]).days / 365.25
        if years <= 0:
            return 0.0
        return (self.equity_curve.iloc[-1] / self.equity_curve.iloc[0]) ** (1 / years) - 1

    @property
    def max_drawdown(self) -> float:
        if self.equity_curve.empty:
            return 0.0
        roll_max = self.equity_curve.cummax()
        dd = self.equity_curve / roll_max - 1
        return dd.min()

    @property
    def sharpe(self) -> float:
        """年化夏普值 (假設無風險利率 0)。"""
        if len(self.equity_curve) < 2:
            return 0.0
        rets = self.equity_curve.pct_change().dropna()
        if rets.std() == 0:
            return 0.0
        return (rets.mean() / rets.std()) * (252 ** 0.5)

    def summary(self) -> str:
        return (
            f"初始資金: {self.initial_cash:,.0f}\n"
            f"期末資產: {self.equity_curve.iloc[-1]:,.0f}\n"
            f"總報酬率: {self.total_return:.2%}\n"
            f"年化報酬 (CAGR): {self.cagr:.2%}\n"
            f"最大回撤: {self.max_drawdown:.2%}\n"
            f"夏普值: {self.sharpe:.2f}\n"
            f"交易次數: {len(self.trades)}"
        )


class Backtester:
    def __init__(
        self,
        provider: DataProvider,
        initial_cash: float = 1_000_000.0,
        position_pct: float = 0.2,
        fee_discount: float = 0.28,
        warmup: int = 250,
        allow_odd_lot: bool = True,
        cooldown_days: int = 5,
        regime_filter: bool = False,
        regime_ma: int = 200,
        compound: bool = False,
    ):
        self.provider = provider
        self.initial_cash = initial_cash
        self.position_pct = position_pct
        self.fee_discount = fee_discount
        self.warmup = warmup
        # 複利：每筆下單金額用「當前帳戶權益 × 比例」(賺的錢滾入)；
        # 關閉則用「初始資金 × 比例」(固定金額，不複利)。
        self.compound = compound
        # 防洗盤：賣出後 N 個交易日內不重買同一檔，避免在均線上下來回被巴手續費。
        self.cooldown_days = cooldown_days
        # 允許零股 (1 股為單位)；台股盤中零股可交易，貴的股票小資金也買得到。
        self.allow_odd_lot = allow_odd_lot
        # 大盤風向濾網：加權指數在年線(regime_ma)之下=空頭，禁止做多 (只准出場)。
        self.regime_filter = regime_filter
        self.regime_ma = regime_ma

    def run(
        self,
        strategy: Strategy,
        symbols: List[str],
        start: str,
        end: str,
    ) -> BacktestResult:
        broker = PaperBroker(cash=self.initial_cash, fee_discount=self.fee_discount)

        data: Dict[str, pd.DataFrame] = {s: self.provider.history(s, start, end) for s in symbols}
        # 只有需要基本面的策略才抓財報，省 FinMind 呼叫次數 (技術面策略免抓)。
        funds = {}
        if getattr(strategy, "requires_fundamentals", False):
            funds = {s: self.provider.fundamentals(s) for s in symbols}
        # 籌碼 (三大法人買賣超)：只有籌碼類策略才抓。
        chips_all: Dict[str, Optional[pd.DataFrame]] = {}
        if getattr(strategy, "requires_chips", False):
            chips_all = {s: self.provider.institutional(s, start, end) for s in symbols}
        bench_full = self.provider.benchmark(start, end)
        # TAIEX 抓不到 (逾時/限額) 但要用風向濾網時，用選股池等權平均自建大盤代理，
        # regime 照常運作、且完全不需額外 API 請求 (資料已在手)。
        if self.regime_filter and bench_full is None:
            bench_full = self._synthetic_benchmark(data)
            if bench_full is not None:
                print("[regime] ⚠️ 抓不到 TAIEX，改用選股池等權平均當大盤代理")

        # 統一交易日曆 (所有股票日期聯集)。
        all_dates = sorted(set().union(*[set(df.index) for df in data.values() if not df.empty]))

        # 預算大盤年線，供風向濾網用。
        regime_ma_series = None
        if self.regime_filter and bench_full is not None:
            regime_ma_series = bench_full.rolling(self.regime_ma).mean()

        equity = []
        trades: List[Trade] = []
        cooldown_until: Dict[str, int] = {}  # 每檔賣出後，到第幾根才可再買

        for i, date in enumerate(all_dates):
            if i < self.warmup:
                equity.append((date, self._equity(broker, data, date)))
                continue

            for sym in symbols:
                df = data[sym]
                if df.empty or date not in df.index:
                    continue
                window = df.loc[:date]
                price = float(window["close"].iloc[-1])
                if price <= 0:  # 防爛資料：價格非正不交易，避免以 0 元成交
                    continue
                pos = broker.account.positions.get(sym, Position(sym))
                bench = None
                if bench_full is not None:
                    bench = bench_full.reindex(window.index).ffill()

                # 籌碼只餵「前一交易日(含)以前」：法人資料盤後才公布，避免偷看當日籌碼 (T+1 執行)。
                chips_win = None
                ch = chips_all.get(sym)
                if ch is not None and not ch.empty:
                    chips_win = ch[ch.index < date]
                ctx = StrategyContext(
                    symbol=sym,
                    prices=window,
                    fundamentals=funds.get(sym),
                    benchmark=bench,
                    position=pos if pos.shares > 0 else None,
                    chips=chips_win,
                )
                sig = strategy.evaluate(ctx)

                if sig.action == Action.BUY and pos.shares == 0:
                    if i < cooldown_until.get(sym, 0):  # 冷卻期內，跳過買進
                        continue
                    if regime_ma_series is not None:  # 大盤空頭，禁止做多
                        mkt = bench_full.asof(date)
                        mkt_ma = regime_ma_series.asof(date)
                        if mkt == mkt and mkt_ma == mkt_ma and mkt < mkt_ma:
                            continue
                    # 複利用當前權益、否則用初始資金當基準
                    base_cap = self._equity(broker, data, date) if self.compound else self.initial_cash
                    budget = base_cap * self.position_pct * sig.strength
                    if self.allow_odd_lot:
                        shares = int(budget // price)               # 零股：1 股為單位
                    else:
                        shares = int(budget // (price * LOT)) * LOT  # 整張：1000 股為單位
                    if shares > 0:
                        order = broker.place_order(Order(sym, OrderSide.BUY, shares, price, sig.reason))
                        if order.filled:
                            trades.append(Trade(date, sym, "BUY", order.shares, price, sig.reason))
                elif sig.action == Action.SELL and pos.shares > 0:
                    order = broker.place_order(Order(sym, OrderSide.SELL, pos.shares, price, sig.reason))
                    if order.filled:
                        cooldown_until[sym] = i + self.cooldown_days
                        trades.append(Trade(date, sym, "SELL", order.shares, price, sig.reason))

            equity.append((date, self._equity(broker, data, date)))

        curve = pd.Series(dict(equity)).sort_index()
        return BacktestResult(curve, trades, self.initial_cash)

    @staticmethod
    def _synthetic_benchmark(data: Dict[str, pd.DataFrame]) -> Optional[pd.Series]:
        """用選股池個股收盤價組一條等權「大盤代理」指數，當 TAIEX 的備援。

        每檔正規化到起點=1 (消除股價高低差)，再取橫向平均 -> 一條平滑的市場指數，
        足以支撐 200 日年線的多空風向判斷。純用已載入的資料，不打任何 API。
        """
        closes = [df["close"] for df in data.values() if not df.empty]
        if not closes:
            return None
        mat = pd.concat(closes, axis=1).sort_index().ffill()
        base = mat.bfill().iloc[0]           # 各欄第一個有效值
        base = base.replace(0, pd.NA)
        norm = mat.divide(base, axis=1)      # 每檔正規化到起點=1
        idx = norm.mean(axis=1, skipna=True)
        return idx.dropna()

    def _equity(self, broker: PaperBroker, data: Dict[str, pd.DataFrame], date) -> float:
        prices = {}
        for sym, df in data.items():
            sub = df.loc[:date]
            if not sub.empty:
                prices[sym] = float(sub["close"].iloc[-1])
        return broker.equity(prices)
