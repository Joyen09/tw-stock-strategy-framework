"""永豐金證券 Shioaji 真實下單接口。

需要：
1. 永豐金證券帳戶 + 開通 API + 申請憑證 (參考 https://sinotrade.github.io/)
2. pip install shioaji
3. 環境變數：
   SHIOAJI_API_KEY / SHIOAJI_SECRET_KEY (API 金鑰)
   SHIOAJI_CA_PATH (憑證檔路徑) / SHIOAJI_CA_PASSWD / SHIOAJI_PERSON_ID

⚠️ 安全提醒：
- 千萬不要把金鑰、憑證寫進程式或 commit 進 git，一律走環境變數 / .env。
- 第一次務必用「模擬帳戶」(simulation=True) 測試，確認無誤再切換實單。
"""
from __future__ import annotations

import os
from typing import List, Optional

from ..models import Position
from .base import Broker, Order, OrderSide


class ShioajiBroker(Broker):
    def __init__(self, simulation: bool = True):
        try:
            import shioaji as sj  # type: ignore
        except ImportError as e:  # pragma: no cover
            raise ImportError("請先安裝 shioaji: pip install shioaji") from e

        self.sj = sj
        self.simulation = simulation
        self.api = sj.Shioaji(simulation=simulation)
        self._login()

    def _login(self):
        api_key = os.environ["SHIOAJI_API_KEY"]
        secret_key = os.environ["SHIOAJI_SECRET_KEY"]
        self.accounts = self.api.login(api_key=api_key, secret_key=secret_key)
        # 實單需啟用憑證；模擬盤可略過。
        ca_path = os.getenv("SHIOAJI_CA_PATH")
        if not self.simulation and ca_path:
            self.api.activate_ca(
                ca_path=ca_path,
                ca_passwd=os.environ["SHIOAJI_CA_PASSWD"],
                person_id=os.environ["SHIOAJI_PERSON_ID"],
            )

    def place_order(self, order: Order) -> Order:
        contract = self.api.Contracts.Stocks[order.symbol]
        sj = self.sj
        action = sj.constant.Action.Buy if order.side == OrderSide.BUY else sj.constant.Action.Sell
        # 台股整股下單以「張」為單位，這裡把股數換成張 (1 張 = 1000 股)。
        quantity = max(1, order.shares // 1000)
        sj_order = self.api.Order(
            price=order.price or 0,
            quantity=quantity,
            action=action,
            price_type=sj.constant.StockPriceType.MKT if order.price is None else sj.constant.StockPriceType.LMT,
            order_type=sj.constant.OrderType.ROD,
            account=self.api.stock_account,
        )
        trade = self.api.place_order(contract, sj_order)
        order.order_id = str(getattr(trade.status, "id", "")) or None
        order.note += f" | 已送單 status={getattr(trade.status, 'status', '?')}"
        return order

    def realtime_quote(self, symbol: str) -> Optional[float]:
        """盤中即時成交價 (snapshot)，給 LiveTrader 當作今日 K 的現價。"""
        try:
            contract = self.api.Contracts.Stocks[symbol]
            snap = self.api.snapshots([contract])
            if snap:
                return float(snap[0].close)
        except Exception as e:  # pragma: no cover - 盤外/網路問題
            print(f"[Shioaji] {symbol} 取即時報價失敗: {e}")
        return None

    def positions(self) -> List[Position]:
        result = []
        for p in self.api.list_positions(self.api.stock_account):
            result.append(Position(symbol=p.code, shares=int(p.quantity) * 1000, avg_price=float(p.price)))
        return result

    def cash(self) -> float:
        acc = self.api.account_balance()
        return float(getattr(acc, "acc_balance", 0.0))

    def logout(self):
        try:
            self.api.logout()
        except Exception:  # pragma: no cover
            pass
