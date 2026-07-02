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

# 盤中零股 (IntradayOdd) 單一委託上限：1~999 股。>=1000 股必須走整股 (Common)。
ODD_LOT_MAX = 999
LOT = 1000


def plan_order_lots(shares: int) -> List[tuple]:
    """把總股數拆成 Shioaji 可接受的委託單段，回傳 [(lot_kind, quantity), ...]。

    - lot_kind == "Common"：整股，quantity 以「張」計 (1 張 = 1000 股)。
    - lot_kind == "IntradayOdd"：盤中零股，quantity 以「股」計 (1~999)。

    例：
      2      -> [("IntradayOdd", 2)]          # 純零股
      1000   -> [("Common", 1)]               # 純整張
      1666   -> [("Common", 1), ("IntradayOdd", 666)]   # 拆成 1 張 + 666 零股

    ⚠️ 修正前的舊邏輯會把 1666 股當成單一 IntradayOdd(1666) 送出，但盤中零股單一委託
    上限 999 股，券商會拒單。低價股 + 較大 budget 時 (股數 > 999 且非整張倍數) 就會踩到。
    """
    if shares <= 0:
        return []
    segments: List[tuple] = []
    lots = shares // LOT
    odd = shares % LOT
    if lots > 0:
        segments.append(("Common", lots))
    if odd > 0:
        segments.append(("IntradayOdd", odd))
    return segments


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

        # 依股數拆成整股 (Common, 以張計) + 零股 (IntradayOdd, 1~999 股) 兩段，避免把
        # 零股誤放大成整張、也避免 >999 股塞進單一零股委託被拒單。
        segments = plan_order_lots(order.shares)
        if not segments:
            order.note += " | 股數<=0，未送單"
            return order

        odd_price = None  # 零股需限價；懶取即時價，避免整股 MKT 時多打一次報價 API
        ids: List[str] = []
        statuses: List[str] = []
        for lot_kind, quantity in segments:
            if lot_kind == "Common":
                order_lot = sj.constant.StockOrderLot.Common
                price_type = sj.constant.StockPriceType.MKT if order.price is None else sj.constant.StockPriceType.LMT
                price = order.price or 0
            else:
                # 盤中零股：只能限價 (需帶價格)
                order_lot = sj.constant.StockOrderLot.IntradayOdd
                price_type = sj.constant.StockPriceType.LMT
                if order.price is None and odd_price is None:
                    odd_price = self.realtime_quote(order.symbol) or 0
                price = order.price if order.price is not None else odd_price

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
            oid = str(getattr(trade.status, "id", ""))
            if oid:
                ids.append(oid)
            statuses.append(str(getattr(trade.status, "status", "?")))

        order.order_id = ",".join(ids) or None
        seg_desc = "+".join(f"{k}×{q}" for k, q in segments)
        order.note += f" | 已送單[{seg_desc}] status={'/'.join(statuses)}"
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
        except Exception as e:
            # 舊版/不支援 unit 參數時退回：quantity 以「張」計，換算成股。
            # ⚠️ 這條路徑會 ×1000，若誤觸會把零股放大成整張，故印警告方便診斷。
            print(f"[Shioaji] ⚠️ list_positions(unit=Share) 失敗，退回 ×1000 換算路徑: {e}")
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
