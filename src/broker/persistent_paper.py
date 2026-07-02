"""可持久化的紙上模擬券商 (persistent paper trading)。

PaperBroker 是純記憶體的，每次程式結束就忘光——不適合「每 5 分鐘跑一次、跨好幾週」的
排程模擬盤。這個子類把現金與持倉存進一個 JSON 檔，每次啟動先讀回、每次下單後存檔，
於是就能：

- 接真實盤中即時價 (由 scan 的 --realtime 提供 quote_fn，撮合價用當下現價) + 假錢
- 持倉數字乾淨可信 (自己記帳，不依賴券商回報)
- 跨排程的多次執行連續累積 (今天買的、明天記得)

用途：取代「用永豐模擬盤空跑」——永豐模擬盤的持倉/成交回報實測不可靠 (會自己成長、
每次查不同)，本地記帳才是可信的長期驗證方式。假錢，不碰真錢也不碰券商帳戶。
"""
from __future__ import annotations

import json
import os
from typing import List

from ..models import Position
from .base import Order
from .paper import PaperBroker


class PersistentPaperBroker(PaperBroker):
    def __init__(self, path: str, cash: float = 50_000.0, fee_discount: float = 1.0):
        super().__init__(cash=cash, fee_discount=fee_discount)
        self.path = path
        self._load()  # 檔案存在就覆蓋掉初始 cash/持倉 (延續上次狀態)

    def _load(self) -> None:
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

    def _save(self) -> None:
        data = {
            "cash": self.account.cash,
            "positions": [
                {"symbol": p.symbol, "shares": p.shares, "avg_price": p.avg_price}
                for p in self.account.positions.values()
                if p.shares > 0
            ],
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
        result = super().place_order(order)
        self._save()  # 每次下單後落地，跨執行不遺失
        return result
