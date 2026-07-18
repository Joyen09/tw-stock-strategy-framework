"""聚合多個本地持久化模擬盤帳戶，給 Telegram listener 一個「總帳」視角。

雙策略配置 (lynch 核心 + livermore 衛星) 各自記帳在獨立 JSON 檔，避免 A 策略的
出場規則去評估 B 策略買的持股；但手機上查看時要合起來看——這個類把多個
PersistentPaperBroker 包成一個：

- /holdings：所有帳戶的持倉合併顯示 (標註來源策略)、各自現金、總資產
- /sell 2330：自動找到持有 2330 的帳戶、從該帳戶賣出
- /sell all：所有帳戶全部出清

只聚合「檢視/手動賣出」；買進仍由各策略的 scan 對自己的帳戶檔進行。
"""
from __future__ import annotations

import os
from typing import Dict, List, Tuple

from ..models import Position
from .base import Order
from .persistent_paper import PersistentPaperBroker


class MultiPaperBroker:
    def __init__(self, accounts: List[Tuple[str, str]]):
        """accounts: [(標籤, 檔案路徑), ...]。檔案不存在的帳戶會保留 (首單時才建檔)。"""
        self.brokers: Dict[str, PersistentPaperBroker] = {
            label: PersistentPaperBroker(path=path) for label, path in accounts
        }

    def reload(self) -> None:
        for b in self.brokers.values():
            b.reload()

    # --- 聚合檢視 ---

    def holdings_by_account(self) -> List[Tuple[str, List[Position], float, bool]]:
        """回傳 [(標籤, 持倉, 現金, 帳戶檔已存在), ...]，供 listener 排版。"""
        out = []
        for label, b in self.brokers.items():
            ps = [p for p in b.positions() if p.shares > 0]
            out.append((label, ps, b.cash(), os.path.exists(b.path)))
        return out

    def total_equity(self) -> float:
        """總資產 (現金 + 持倉成本計價；listener 沒有即時報價，用均價估)。
        只計入已建檔的帳戶，避免把「還沒開始跑的帳戶」的初始資金灌水進來。"""
        total = 0.0
        for _, ps, cash, exists in self.holdings_by_account():
            if not exists:
                continue
            total += cash + sum(p.shares * p.avg_price for p in ps)
        return total

    def report(self, price_fn) -> List[dict]:
        """對每個已建檔帳戶做市值計算，回傳績效清單（給 /report 用）。

        price_fn(symbol) -> 最新收盤價，或 None（抓不到時退回用成本價，不灌水）。
        每筆：label / initial（初始資金）/ cash / mtm（市值總資產）/
              ret（總報酬率）/ unreal（未實現損益）/ positions（明細 dict list）。
        """
        out = []
        for label, b in self.brokers.items():
            if not os.path.exists(b.path):
                continue  # 還沒開始跑的帳戶不列
            ps = [p for p in b.positions() if p.shares > 0]
            initial = float(getattr(b, "initial_cash", b.cash()))
            cash = b.cash()
            mkt = 0.0
            details = []
            for p in ps:
                px = price_fn(p.symbol)
                priced = px is not None and px > 0
                last = px if priced else p.avg_price
                value = p.shares * last
                mkt += value
                details.append({
                    "symbol": p.symbol, "shares": p.shares, "avg": p.avg_price,
                    "last": last, "priced": priced,
                    "pnl": p.shares * (last - p.avg_price),
                })
            mtm = cash + mkt
            unreal = sum(d["pnl"] for d in details)
            ret = (mtm / initial - 1) if initial > 0 else 0.0
            out.append({
                "label": label, "initial": initial, "cash": cash, "mtm": mtm,
                "ret": ret, "unreal": unreal, "positions": details,
                "start_date": getattr(b, "start_date", None),
            })
        return out

    # --- 賣出路由 ---

    def sell(self, symbol_or_all: str, reason: str = "Telegram 手動賣出") -> List[Tuple[str, str, int]]:
        """從持有該檔的帳戶賣出。回傳 [(標籤, 股票, 股數), ...]。"""
        from .base import OrderSide

        done = []
        want_all = symbol_or_all.lower() == "all"
        for label, b in self.brokers.items():
            for p in [p for p in b.positions() if p.shares > 0]:
                if want_all or p.symbol == symbol_or_all:
                    shares = p.shares  # place_order 會把持倉歸零，先記下股數
                    b.place_order(Order(p.symbol, OrderSide.SELL, shares, p.avg_price, reason))
                    done.append((label, p.symbol, shares))
        return done
