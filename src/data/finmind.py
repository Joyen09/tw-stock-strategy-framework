"""FinMind 真實台股資料來源 (免費，需在 https://finmindtrade.com 申請 token)。

需要安裝: pip install FinMind
設定 token: 環境變數 FINMIND_TOKEN，或建構時傳入 token=。

這支只負責「把 FinMind 的資料轉成框架的標準格式」，介面與 SampleDataProvider 完全相同，
所以回測 / 實單程式碼不需更動，換 provider 即可用真實資料。
"""
from __future__ import annotations

import os
from typing import List, Optional

import pandas as pd

from ..models import Fundamentals
from .base import DataProvider

TAIEX = "TAIEX"  # 加權指數代號 (FinMind TaiwanStockTotalReturnIndex / 這裡用發行量加權)


class FinMindProvider(DataProvider):
    def __init__(self, token: Optional[str] = None):
        try:
            from FinMind.data import DataLoader  # type: ignore
        except ImportError as e:  # pragma: no cover
            raise ImportError("請先安裝 FinMind: pip install FinMind") from e
        self.api = DataLoader()
        token = token or os.getenv("FINMIND_TOKEN")
        if token:
            self.api.login_by_token(api_token=token)

    def history(self, symbol: str, start: str, end: str) -> pd.DataFrame:
        df = self.api.taiwan_stock_daily(stock_id=symbol, start_date=start, end_date=end)
        if df is None or df.empty:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        df = df.rename(
            columns={
                "max": "high",
                "min": "low",
                "Trading_Volume": "volume",
                "open": "open",
                "close": "close",
            }
        )
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date").sort_index()
        df = df[["open", "high", "low", "close", "volume"]]
        # 過濾爛資料：FinMind 偶有收盤=0 的列 (停牌/缺漏)，會讓回測以 0 元成交、產生假虧損。
        df = df[df["close"] > 0]
        return df

    # ---- 基本面欄位的多重候選名稱 (FinMind 不同表 type 命名略有差異) ----
    _REVENUE = ["Revenue"]
    _GROSS = ["GrossProfit"]
    _EPS = ["EPS"]
    _NET_INCOME = ["IncomeAfterTaxes", "ProfitAfterTax", "NetIncome"]
    _ASSETS = ["TotalAssets"]
    _LIAB = ["TotalLiabilities", "Liabilities"]
    _CUR_ASSETS = ["CurrentAssets"]
    _CUR_LIAB = ["CurrentLiabilities"]
    _EQUITY = ["TotalEquity", "Equity", "EquityAttributableToOwnersOfParent"]

    @staticmethod
    def _pivot(df: pd.DataFrame) -> Optional[pd.DataFrame]:
        """把 FinMind 長格式 (date/type/value) 轉成寬表，index=date、欄位=type。"""
        if df is None or df.empty or "type" not in df.columns:
            return None
        df = df.copy()
        df["value"] = pd.to_numeric(df["value"], errors="coerce")
        piv = df.pivot_table(index="date", columns="type", values="value", aggfunc="last")
        return piv.sort_index()

    @staticmethod
    def _col(piv: Optional[pd.DataFrame], keys) -> Optional[pd.Series]:
        if piv is None:
            return None
        for k in keys:
            if k in piv.columns:
                s = piv[k].dropna()
                if not s.empty:
                    return s
        return None

    def fundamentals(self, symbol: str) -> Optional[Fundamentals]:
        """從 FinMind 多張財報表組出完整基本面快照 (盡力而為，缺值回 None)。

        每個指標各自 try/except，任一張表抓不到不會影響其他指標。
        用 `python main.py fundamentals --symbols 2330` 可檢視抓到哪些欄位。
        """
        f = Fundamentals(symbol=symbol)
        start = "2021-01-01"  # 取近幾年以便算 YoY 成長

        # 1) 估值：PER / PBR / 殖利率
        try:
            per = self.api.taiwan_stock_per_pbr(stock_id=symbol, start_date=start)
            if per is not None and not per.empty:
                latest = per.sort_values("date").iloc[-1]
                f.pe = float(latest["PER"]) if latest.get("PER") not in (None, 0) else None
                f.pb = float(latest["PBR"]) if latest.get("PBR") not in (None, 0) else None
                dy = latest.get("dividend_yield")
                f.dividend_yield = float(dy) if dy not in (None,) else None
        except Exception as e:
            f.extra["per_error"] = str(e)

        # 2) 損益表：毛利率、EPS 成長 (季 YoY)
        income = None
        try:
            income = self._pivot(self.api.taiwan_stock_financial_statement(stock_id=symbol, start_date=start))
            rev = self._col(income, self._REVENUE)
            gross = self._col(income, self._GROSS)
            eps = self._col(income, self._EPS)
            if rev is not None and gross is not None and rev.iloc[-1]:
                f.gross_margin = round(float(gross.iloc[-1]) / float(rev.iloc[-1]) * 100, 2)
            if eps is not None:
                f.eps = round(float(eps.iloc[-1]), 2)
                if len(eps) >= 5 and eps.iloc[-5] not in (0, None):  # 同季去年同期
                    f.eps_growth = round((eps.iloc[-1] - eps.iloc[-5]) / abs(eps.iloc[-5]) * 100, 2)
        except Exception as e:
            f.extra["income_error"] = str(e)

        # 3) 資產負債表：負債比、流動比、ROE
        try:
            bs = self._pivot(self.api.taiwan_stock_balance_sheet(stock_id=symbol, start_date=start))
            assets = self._col(bs, self._ASSETS)
            liab = self._col(bs, self._LIAB)
            ca = self._col(bs, self._CUR_ASSETS)
            cl = self._col(bs, self._CUR_LIAB)
            equity = self._col(bs, self._EQUITY)
            if assets is not None and liab is not None and assets.iloc[-1]:
                f.debt_ratio = round(float(liab.iloc[-1]) / float(assets.iloc[-1]) * 100, 2)
            if ca is not None and cl is not None and cl.iloc[-1]:
                f.current_ratio = round(float(ca.iloc[-1]) / float(cl.iloc[-1]) * 100, 2)
            # ROE = 近四季淨利 / 股東權益；淨利取自損益表
            ni = self._col(income, self._NET_INCOME)
            if equity is not None and ni is not None and equity.iloc[-1]:
                ttm_ni = float(ni.iloc[-4:].sum()) if len(ni) >= 4 else float(ni.iloc[-1])
                f.roe = round(ttm_ni / float(equity.iloc[-1]) * 100, 2)
        except Exception as e:
            f.extra["balance_error"] = str(e)

        # 4) 月營收 YoY 成長
        try:
            mr = self.api.taiwan_stock_month_revenue(stock_id=symbol, start_date=start)
            if mr is not None and not mr.empty and "revenue" in mr.columns:
                rev_m = mr.sort_values("date")["revenue"].astype(float).reset_index(drop=True)
                if len(rev_m) >= 13 and rev_m.iloc[-13]:
                    f.revenue_growth = round((rev_m.iloc[-1] - rev_m.iloc[-13]) / abs(rev_m.iloc[-13]) * 100, 2)
        except Exception as e:
            f.extra["revenue_error"] = str(e)

        # 5) 現金流量表：近四季自由現金流 (營業現金流 - 資本支出)，雷浩斯獲利能力矩陣用
        try:
            cf = self._pivot(self.api.taiwan_stock_cash_flows_statement(stock_id=symbol, start_date=start))
            op = self._col(cf, ["CashProvidedByOperatingActivities",
                                "NetCashProvidedByUsedInOperatingActivities",
                                "CashFlowsFromOperatingActivities"])
            capex = self._col(cf, ["AcquisitionOfPropertyAndPlantAndEquipment",
                                   "PurchaseOfPropertyPlantAndEquipment",
                                   "PropertyAndPlantAndEquipment"])
            if op is not None:
                op_ttm = float(op.iloc[-4:].sum()) if len(op) >= 4 else float(op.iloc[-1])
                # 資本支出在現金流量表通常是負數 (流出)；取絕對值相減
                capex_ttm = 0.0
                if capex is not None:
                    capex_ttm = abs(float(capex.iloc[-4:].sum()) if len(capex) >= 4 else float(capex.iloc[-1]))
                f.fcf = round(op_ttm - capex_ttm, 0)
        except Exception as e:
            f.extra["cashflow_error"] = str(e)

        return f

    def institutional(self, symbol: str, start: str, end: str) -> Optional[pd.DataFrame]:
        """三大法人每日買賣超 (股)。欄位 trust_net=投信、foreign_net=外資 (含外資自營)。

        FinMind `taiwan_stock_institutional_investors` 免費版可用，long format:
        date / name (Foreign_Investor, Investment_Trust, ...) / buy / sell。
        注意: 法人資料約 15:00–16:00 盤後公布，訊號只能用於次一交易日 (T+1)，
        回測引擎已用「只看前一日(含)以前」切片避免前視偏差。
        """
        try:
            df = self.api.taiwan_stock_institutional_investors(
                stock_id=symbol, start_date=start, end_date=end
            )
        except Exception:
            return None
        if df is None or df.empty or "name" not in df.columns:
            return None
        df = df.copy()
        df["net"] = pd.to_numeric(df["buy"], errors="coerce") - pd.to_numeric(df["sell"], errors="coerce")
        df["date"] = pd.to_datetime(df["date"])
        piv = df.pivot_table(index="date", columns="name", values="net", aggfunc="sum")

        def _sum(cols):
            hits = [c for c in cols if c in piv.columns]
            return piv[hits].sum(axis=1) if hits else pd.Series(0.0, index=piv.index)

        out = pd.DataFrame({
            "trust_net": _sum(["Investment_Trust"]),
            "foreign_net": _sum(["Foreign_Investor", "Foreign_Dealer_Self"]),
        }).sort_index()
        return out

    def benchmark(self, start: str, end: str, timeout: float = 15.0) -> Optional[pd.Series]:
        """抓加權指數 (TAIEX) 當大盤基準。

        FinMind 的 TAIEX 請求偶爾會卡住不回應 (連線 hang，沒有內建 timeout)，
        用 daemon 執行緒 + join(timeout) 包起來：逾時就放棄回 None，讓上層改用
        備援基準 (例如選股池等權平均)，程式不會被單一請求卡死。
        """
        import threading

        box: dict = {}

        def _fetch():
            try:
                box["df"] = self.api.taiwan_stock_daily(
                    stock_id="TAIEX", start_date=start, end_date=end
                )
            except Exception as e:  # pragma: no cover
                box["err"] = e

        print(f"  抓 TAIEX 大盤基準中 (最多等 {timeout:.0f} 秒，逾時改用備援基準)...", flush=True)
        t = threading.Thread(target=_fetch, daemon=True)
        t.start()
        t.join(timeout)
        if t.is_alive():  # 逾時：放棄這條 hung 的執行緒 (daemon 不擋程式結束)
            print("  TAIEX 逾時，放棄。", flush=True)
            return None
        df = box.get("df")
        if df is None or df.empty:
            return None
        df["date"] = pd.to_datetime(df["date"])
        return df.set_index("date").sort_index()["close"]

    def universe(self) -> List[str]:  # pragma: no cover - 依需求自訂清單
        return []
