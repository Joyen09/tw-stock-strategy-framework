"""除權息事件處理：把「股價機械性下跌」和「真的虧錢」分開。

# 為什麼需要這個（2026-09-09 實盤事故）

緯穎 6669 在 2026-09-02 除權：股價 7,800 → 2,615（被除以 2.98 倍），
持有 1 股的人實際上會變成約 2.98 股，資產沒有減少。

但模擬帳不處理這件事，它只看到「股價腰斬三分之二」：
  - ATR 移動停損觸發 → 在 2,610 賣掉 → 實現 -3,343
  - 真實情況：持股價值其實是 +1,055
  - **純會計誤差 4,398 元**，佔該帳戶初始資金 22%

同期兆豐金 2886 也一樣：8/13 除息 1.75（48.7→46.95），8/14 就被 ATR 停損，
若沒有那 1.75 的機械性下跌就不會觸發，另外 194 股 × 1.75 = 340 元股利也沒入帳。

# 設計原則

1. **只做看得懂的**：純除息（息）算現金、純除權（權）算股數。
   混合型（權息）無法從這張表可靠拆解，用「跌幅是否 >20%」判斷主成分——
   沒有公司會一次配 20% 以上的現金殖利率，那種幅度必然是股票股利。
   判斷不了就跳過並大聲警告，寧可不動也不要亂調。
2. **冪等**：帳戶記住處理到哪一天，同一筆事件不會套用兩次。
3. **留痕**：每次調整都寫進成交紀錄，事後查得到帳是怎麼變的。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

# 單日價格調整超過這個比例，主成分必定是股票股利而非現金股利
# （沒有公司一次配 20% 以上現金殖利率）。用來判斷混合型「權息」該當股還是當息。
STOCK_DIVIDEND_THRESHOLD = 0.20


@dataclass(frozen=True)
class CorporateAction:
    """一次除權息事件。kind: 'cash'(配息) / 'stock'(配股) / 'unknown'(拆不出來)。"""
    date: str
    symbol: str
    kind: str
    before_price: float
    after_price: float
    raw_label: str = ""          # 期交所原始標示：息 / 權 / 權息
    note: str = ""               # kind='unknown' 時說明為什麼不處理

    @property
    def cash_per_share(self) -> float:
        """每股配發現金（kind='cash' 才有意義）。"""
        return max(self.before_price - self.after_price, 0.0) if self.kind == "cash" else 0.0

    @property
    def share_ratio(self) -> float:
        """除權後 1 股變成幾股（kind='stock' 才有意義，其餘為 1.0）。"""
        if self.kind != "stock" or self.after_price <= 0:
            return 1.0
        return self.before_price / self.after_price


def classify(before_price: float, after_price: float, label: str,
             symbol: str = "", date: str = "") -> CorporateAction:
    """依「除權息前後參考價」與原始標示判斷這是配息還是配股。

    label 來自 FinMind TaiwanStockDividendResult 的 stock_or_cache_dividend：
    「息」= 純現金、「權」= 純股票、「權息」= 兩者都有。
    """
    base = dict(date=date, symbol=symbol, before_price=float(before_price),
                after_price=float(after_price), raw_label=label or "")
    if before_price <= 0 or after_price <= 0:
        return CorporateAction(kind="unknown", note="參考價異常（<=0）", **base)
    drop = 1 - after_price / before_price
    if drop <= 0:
        return CorporateAction(kind="unknown", note="價格未下調，無需處理", **base)

    has_stock = "權" in (label or "")
    has_cash = "息" in (label or "")
    if has_stock and not has_cash:
        return CorporateAction(kind="stock", **base)
    if has_cash and not has_stock:
        return CorporateAction(kind="cash", **base)
    if has_stock and has_cash:
        # 混合型拆不出來：用跌幅判斷主成分（見模組 docstring）
        if drop >= STOCK_DIVIDEND_THRESHOLD:
            return CorporateAction(kind="unknown", **base,
                                   note=f"權息混合且跌幅 {drop:.1%} 過大，拆不出股/息比例，"
                                        f"不自動處理以免調錯，請人工確認")
        return CorporateAction(kind="cash", **base)
    return CorporateAction(kind="unknown", note=f"無法辨識的類型「{label}」", **base)


def fetch(api, symbol: str, start: str, end: str) -> List[CorporateAction]:
    """從 FinMind 抓某檔在期間內的除權息事件。抓不到就回空清單（不讓排程掛掉）。"""
    try:
        df = api.taiwan_stock_dividend_result(stock_id=symbol, start_date=start, end_date=end)
    except Exception as e:
        print(f"[ca] {symbol} 除權息查詢失敗（略過）：{e}")
        return []
    if df is None or getattr(df, "empty", True):
        return []
    out = []
    for _, row in df.iterrows():
        try:
            out.append(classify(
                before_price=row["before_price"],
                after_price=row["after_price"],
                label=str(row.get("stock_or_cache_dividend", "")),
                symbol=symbol,
                date=str(row["date"])[:10],
            ))
        except (KeyError, TypeError, ValueError) as e:
            print(f"[ca] {symbol} 某筆除權息資料異常（略過）：{e}")
    return sorted(out, key=lambda a: a.date)


def apply_to_position(shares: int, avg_price: float, action: CorporateAction
                      ) -> Optional[tuple]:
    """把一次除權息套用到持倉，回傳 (新股數, 新均價, 入帳現金)；不該處理就回 None。

    配股：股數乘以配股比例（取整，不足 1 股的零頭折現金，與實務一致）、均價等比例下調，
          總成本不變 —— 這樣停損線才會跟著調整，不會被機械性跌價誤觸發。
    配息：股數不變，現金入帳；均價扣掉每股股利（維持「成本基礎」與市價同基準）。
    """
    if shares <= 0 or action.kind == "unknown":
        return None

    if action.kind == "stock":
        ratio = action.share_ratio
        if ratio <= 1.0:
            return None
        total = shares * ratio
        new_shares = int(total)
        frac_cash = (total - new_shares) * action.after_price  # 不足 1 股的零頭折現金
        if new_shares <= 0:
            return None
        new_avg = shares * avg_price / new_shares  # 總成本不變
        return new_shares, new_avg, frac_cash

    if action.kind == "cash":
        per_share = action.cash_per_share
        if per_share <= 0:
            return None
        # 均價不可為負（極端情況：成本已低於累積股利），下限守在 0.01
        return shares, max(avg_price - per_share, 0.01), shares * per_share

    return None
