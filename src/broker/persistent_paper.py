"""可持久化的紙上模擬券商 (persistent paper trading)。

PaperBroker 是純記憶體的，每次程式結束就忘光——不適合「每 5 分鐘跑一次、跨好幾週」的
排程模擬盤。這個子類把現金與持倉存進一個 JSON 檔，每次啟動先讀回、每次下單後存檔，
於是就能：

- 接真實盤中即時價 (由 scan 的 --realtime 提供 quote_fn，撮合價用當下現價) + 假錢
- 持倉數字乾淨可信 (自己記帳，不依賴券商回報)
- 跨排程的多次執行連續累積 (今天買的、明天記得)
- 成交紀錄跨執行保留，事後可以回答「錢是怎麼虧的」

用途：取代「用永豐模擬盤空跑」——永豐模擬盤的持倉/成交回報實測不可靠 (會自己成長、
每次查不同)，本地記帳才是可信的長期驗證方式。假錢，不碰真錢也不碰券商帳戶。
"""
from __future__ import annotations

import json
import os
from typing import List

from ..models import Position
from . import fees
from .base import Order, OrderSide
from .paper import PaperBroker

MAX_TRADES = 500  # 成交紀錄保留上限 (超過丟最舊的)，避免帳戶檔無限長大


def _valid_date(v):
    """只接受 YYYY-MM-DD；手動編輯帳戶檔時填錯 (例如填成說明文字) 就當作沒設定。

    start_date 錯誤不能讓 report 整個爆掉，但也不能拿爛值去對大盤——靜靜忽略最安全。
    """
    if not isinstance(v, str):
        return None
    try:
        import datetime as _dt
        _dt.date.fromisoformat(v)
        return v
    except ValueError:
        print(f"[paper] ⚠️ start_date 格式錯誤 ({v!r})，需 YYYY-MM-DD；本次忽略、不做大盤對照")
        return None


class PersistentPaperBroker(PaperBroker):
    def __init__(self, path: str, cash: float = 50_000.0, fee_discount: float = 1.0):
        super().__init__(cash=cash, fee_discount=fee_discount)
        self.path = path
        is_new = not os.path.exists(path)
        self._load()  # 檔案存在就覆蓋掉初始 cash/持倉 (延續上次狀態)
        if is_new:
            # 全新帳戶：記下建立日=起算日 (report 的大盤對照要對齊這個窗口)。
            # 只設在記憶體、不立即建檔——保留「首單前不建檔、不列入 /holdings」的行為；
            # start_date 會在第一次下單 (_save) 時一起落地。
            import datetime as _dt
            self.start_date = _dt.date.today().isoformat()

    def _load(self) -> None:
        # 初始資金：算報酬率要用。新帳戶=建構子 cash；舊檔沒存過就回填估計值。
        self.initial_cash = self.account.cash
        self.start_date = None  # 帳戶起算日 (算「同期大盤報酬」對照用)；舊檔沒存就下次存檔補今天
        self.trades = []  # 成交紀錄 (跨執行持久化)；沒有它就無法回答「錢是怎麼虧的」
        if not os.path.exists(self.path):
            return  # 首次執行：用建構子的初始 cash、空持倉
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            print(f"[paper] ⚠️ 讀取 {self.path} 失敗，沿用初始資金: {e}")
            return
        self.account.cash = float(data.get("cash", self.account.cash))
        self.account.positions = {}
        for p in data.get("positions", []):
            sym = p["symbol"]
            self.account.positions[sym] = Position(
                symbol=sym, shares=int(p["shares"]), avg_price=float(p["avg_price"])
            )
        if "initial_cash" in data:
            self.initial_cash = float(data["initial_cash"])
        else:
            # 舊檔沒存過初始資金：用「現金 + 持倉成本」回填 (≈ 初始 - 已付手續費，誤差 <0.5%)，
            # 之後 _save 會把它寫進檔案固定下來。
            cost = sum(p.shares * p.avg_price for p in self.account.positions.values())
            self.initial_cash = self.account.cash + cost
        # start_date 只在「全新帳戶建立時」由 __init__ 設定 (=真正起算日)；
        # 舊帳戶檔沒存過就維持 None，report 不硬湊大盤對照 (窗口對不齊會誤導)。
        self.start_date = _valid_date(data.get("start_date"))
        self.trades = list(data.get("trades", []))

    def _save(self) -> None:
        data = {
            "initial_cash": getattr(self, "initial_cash", self.account.cash),
            "start_date": getattr(self, "start_date", None),
            "cash": self.account.cash,
            "positions": [
                {"symbol": p.symbol, "shares": p.shares, "avg_price": p.avg_price}
                for p in self.account.positions.values()
                if p.shares > 0
            ],
            "trades": getattr(self, "trades", [])[-MAX_TRADES:],
        }
        # 原子寫入：先寫暫存檔再 rename，避免排程當中被中斷寫壞檔案。
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)

    def reload(self) -> None:
        """重讀磁碟狀態。給常駐的 Telegram 監聽器用：排程 scan 是另一個 process，
        會改到同一個檔，監聽器在回應 /holdings /sell 前先 reload 才不會拿到舊資料。"""
        self._load()

    def place_order(self, order: Order) -> Order:
        # 賣出後 avg_price 會被歸零，實現損益要在成交「之前」先記下成本。
        pos = self.account.positions.get(order.symbol)
        avg_before = pos.avg_price if pos else 0.0
        result = super().place_order(order)
        if result.filled:
            self._record_trade(result, avg_before)
        self._save()  # 每次下單後落地，跨執行不遺失
        return result

    def _record_trade(self, order: Order, avg_before: float) -> None:
        """把成交寫進持久化紀錄。賣出時一併算實現損益 (已扣賣出手續費與證交稅)。

        沒有這份紀錄，帳戶只看得到「現在剩多少」，回答不了「錢是怎麼虧的」——
        報酬率難看時無法區分「停損停在最低點」「手續費吃掉」「單純市場在跌」。
        """
        import datetime as _dt

        rec = {
            "date": _dt.date.today().isoformat(),
            "symbol": order.symbol,
            "side": "BUY" if order.side == OrderSide.BUY else "SELL",
            "shares": int(order.shares),
            "price": float(order.fill_price or order.price),
            "reason": (order.note or "").strip(),
        }
        if order.side == OrderSide.SELL and avg_before > 0:
            amount = order.shares * rec["price"]
            proceeds = fees.sell_proceeds(amount, self.fee_discount)
            # 成本基礎用建倉均價 (不含買進手續費)，故實現損益略為樂觀約一筆買進手續費。
            rec["realized"] = round(proceeds - order.shares * avg_before, 2)
            rec["avg_cost"] = round(avg_before, 3)
        if not hasattr(self, "trades"):
            self.trades = []
        self.trades.append(rec)
