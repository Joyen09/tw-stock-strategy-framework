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
        # 憑證：登入/報價不需要，但「下單」即使模擬盤也需要啟用憑證 (否則 place_order 會回
        # "Please sign ... first")。所以只要有設定憑證路徑就啟用。
        ca_path = os.getenv("SHIOAJI_CA_PATH")
        if ca_path:
            self.api.activate_ca(
                ca_path=ca_path,
                ca_passwd=os.getenv("SHIOAJI_CA_PASSWD", ""),
                person_id=os.getenv("SHIOAJI_PERSON_ID", ""),
            )
        elif not self.simulation:
            print("[Shioaji] 警告：未設定 SHIOAJI_CA_PATH，實單無法下單")

    def place_order(self, order: Order) -> Order:
        contract = self.api.Contracts.Stocks[order.symbol]
        sj = self.sj
        action = sj.constant.Action.Buy if order.side == OrderSide.BUY else sj.constant.Action.Sell

        # 依股數決定整股 or 盤中零股，避免把零股誤放大成整張。
        if order.shares % 1000 == 0 and order.shares >= 1000:
            # 整股：以「張」為單位
            order_lot = sj.constant.StockOrderLot.Common
            quantity = order.shares // 1000
            price_type = sj.constant.StockPriceType.MKT if order.price is None else sj.constant.StockPriceType.LMT
            price = order.price or 0
        else:
            # 盤中零股：以「股」為單位，且只能限價 (需帶價格)
            order_lot = sj.constant.StockOrderLot.IntradayOdd
            quantity = order.shares
            price_type = sj.constant.StockPriceType.LMT
            price = order.price or self.realtime_quote(order.symbol) or 0

        sj_order = self.api.Order(
            price=price,
            quantity=quantity,
            action=action,
            price_type=price_type,
            order_type=sj.constant.OrderType.ROD,
            order_lot=order_lot,
            account=self.api.stock_account,
        )
        trade = self.api.place_order(contract, sj_order)
        order.order_id = str(getattr(trade.status, "id", "")) or None
        order.note += f" | 已送單 status={getattr(trade.status, 'status', '?')}"
        order.filled = True  # 委託已送出 (實際成交與否需另查帳戶)；視為已下單以利回報
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
        try:
            # 以「股」為單位查詢，零股/整股都能正確表示 (不再一律 ×1000)
            poss = self.api.list_positions(self.api.stock_account, unit=self.sj.constant.Unit.Share)
            for p in poss:
                result.append(Position(symbol=p.code, shares=int(p.quantity), avg_price=float(p.price)))
        except Exception:
            # 舊版/不支援 unit 參數時退回：quantity 以「張」計，換算成股
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
