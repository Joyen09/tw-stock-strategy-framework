"""MonthRevenueStore 快取/增量測試（monthly-revenue-overlay-spec §2.2 / §2.5）。

驗收對應：
  - 快取命中時不打 API
  - 增量更新只抓缺少的月份（補頭/補尾/尾端新公告月），不重抓已查過的區間
  - 查過但沒資料的月份 = 缺月，不重打 API（與「沒查過」區分）
  - 單檔 API 失敗 → 沿用既有快取，不炸整批（fail-closed 交給指標層）

FakeProvider 刻意模擬「date 欄 = 公告月」的最壞語意來過濾回傳範圍，
驗證 store 的寬鬆日期界在兩種語意下都不漏月。
"""
from datetime import date

import pandas as pd
import pytest

from src.data.month_revenue import MonthRevenueStore, latest_announced_month


def P(s):
    return pd.Period(s, freq="M")


class FakeProvider:
    """月營收假資料源。data: {sym: {Period(M): revenue}}，可隨測試進度增補。

    pub_date：模擬「資料月 M 在 (M+1) 月 1 日才進 API」的公告時間線——
    設了之後，公告日晚於 pub_date 的月份抓不到（用來構造 API 落後的洞）。
    """

    def __init__(self, data, pub_date=None):
        self.data = data
        self.pub_date = pub_date
        self.calls = []  # (stock_ids, start, end)

    def get_month_revenue(self, stock_ids, start, end):
        self.calls.append((tuple(stock_ids), start, end))
        s, e = pd.Timestamp(start), pd.Timestamp(end)
        rows = []
        for sym in stock_ids:
            for m, rev in sorted(self.data.get(sym, {}).items()):
                # 模擬最壞情況：API 用「公告月（資料月+1）的 1 日」當 date 過濾
                d = (m + 1).start_time
                if self.pub_date is not None and d.date() > self.pub_date:
                    continue  # 尚未公告
                if s <= d <= e:
                    rows.append({"stock_id": sym, "rev_year": m.year,
                                 "rev_month": m.month, "revenue": int(rev),
                                 "date_raw": str(d.date())})
        cols = ["stock_id", "rev_year", "rev_month", "revenue", "date_raw"]
        return pd.DataFrame(rows, columns=cols)


def months(start, end):
    return {P(str(p)): 1_000_000 + i for i, p in
            enumerate(pd.period_range(start, end, freq="M"))}


@pytest.fixture
def store(tmp_path):
    def make(data):
        fake = FakeProvider(data)
        return fake, MonthRevenueStore(fake, cache_dir=str(tmp_path))
    return make


TODAY = date(2024, 12, 20)  # 12/20 > 10 → 照理已公告的最新資料月 = 2024-11


# ---------- 基本抓取與輸出格式 ----------

def test_first_fetch_and_series_format(store):
    fake, st = store({"2330": months("2022-01", "2024-11")})
    out = st.get(["2330"], "2024-01-01", "2024-12-31", warmup_months=26, today=TODAY)
    assert len(fake.calls) == 1
    s = out["2330"]
    assert isinstance(s.index, pd.PeriodIndex)
    # 涵蓋 warmup：2024-01 − 26 個月 = 2021-11；假資料從 2022-01 起
    assert s.index.min() == P("2022-01")
    assert s.index.max() == P("2024-11")
    assert s.dtype == float

def test_zero_revenue_is_legal_value(store):
    data = months("2024-01", "2024-06")
    data[P("2024-03")] = 0  # 營收 0 是合法值（§2.4）
    fake, st = store({"2330": data})
    out = st.get(["2330"], "2024-01-01", "2024-07-31", warmup_months=0, today=TODAY)
    assert out["2330"][P("2024-03")] == 0.0


# ---------- 快取命中：不打 API ----------

def test_cache_hit_no_api_call(store):
    fake, st = store({"2330": months("2022-01", "2024-11")})
    st.get(["2330"], "2024-01-01", "2024-12-31", today=TODAY)
    n = len(fake.calls)
    out = st.get(["2330"], "2024-01-01", "2024-12-31", today=TODAY)
    assert len(fake.calls) == n  # 第二次同範圍同日：0 次 API
    assert out["2330"].index.max() == P("2024-11")

def test_cache_hit_subrange_no_api_call(store):
    fake, st = store({"2330": months("2022-01", "2024-11")})
    st.get(["2330"], "2024-01-01", "2024-12-31", today=TODAY)
    n = len(fake.calls)
    out = st.get(["2330"], "2024-06-01", "2024-09-30", warmup_months=0, today=TODAY)
    assert len(fake.calls) == n
    assert out["2330"].index.min() == P("2024-06")
    assert out["2330"].index.max() == P("2024-09")


# ---------- 增量：只補缺的頭 / 尾 ----------

def test_incremental_head_only(store):
    fake, st = store({"2330": months("2020-01", "2024-11")})
    st.get(["2330"], "2024-01-01", "2024-12-31", warmup_months=0, today=TODAY)
    n = len(fake.calls)
    st.get(["2330"], "2023-01-01", "2024-12-31", warmup_months=0, today=TODAY)
    assert len(fake.calls) == n + 1        # 只多 1 次（補頭）
    _, _, end = fake.calls[-1]
    assert pd.Timestamp(end) <= pd.Timestamp("2024-02-01")  # 補頭範圍不含已查過的中段

def test_incremental_tail_only(store):
    fake, st = store({"2330": months("2020-01", "2024-11")})
    st.get(["2330"], "2023-01-01", "2023-12-31", warmup_months=0, today=TODAY)
    n = len(fake.calls)
    out = st.get(["2330"], "2023-01-01", "2024-12-31", warmup_months=0, today=TODAY)
    assert len(fake.calls) == n + 1        # 只多 1 次（補尾）
    _, start, _ = fake.calls[-1]
    assert pd.Timestamp(start) >= pd.Timestamp("2023-12-01")
    assert out["2330"].index.max() == P("2024-11")


# ---------- 缺月：查過但沒資料，不重打 ----------

def test_gap_month_not_refetched(store):
    data = months("2024-01", "2024-10")
    del data[P("2024-05")]  # 缺月（漏公告）
    fake, st = store({"2330": data})
    out = st.get(["2330"], "2024-01-01", "2024-11-30", warmup_months=0, today=TODAY)
    assert P("2024-05") not in out["2330"].index
    n = len(fake.calls)
    st.get(["2330"], "2024-01-01", "2024-11-30", warmup_months=0, today=TODAY)
    assert len(fake.calls) == n  # 缺月不觸發重抓（同日）


# ---------- 尾端新公告月：每天最多重抓一次 ----------

def test_tail_refresh_when_new_month_published(store):
    fake, st = store({"2330": months("2024-01", "2024-10")})
    # 11/8（<=10 日）首抓：10 月營收尚未公告，rows 到 2024-09...
    # （fake 資料只到 10 月；11/8 時 expected=2024-09，rows 已含 → 不刷）
    st.get(["2330"], "2024-01-01", "2024-12-31", warmup_months=0,
           today=date(2024, 11, 8))
    n = len(fake.calls)
    assert max(st._load("2330")["rows"]) == "2024-10"
    # 12 月中：11 月營收已公告（fake 補上），應觸發尾端重抓一次
    fake.data["2330"][P("2024-11")] = 9_999_999
    out = st.get(["2330"], "2024-01-01", "2024-12-31", warmup_months=0, today=TODAY)
    assert len(fake.calls) == n + 1
    assert out["2330"][P("2024-11")] == 9_999_999.0
    # 同日再跑：不再打
    st.get(["2330"], "2024-01-01", "2024-12-31", warmup_months=0, today=TODAY)
    assert len(fake.calls) == n + 1

def test_tail_no_refresh_when_up_to_date(store):
    fake, st = store({"2330": months("2024-01", "2024-11")})
    st.get(["2330"], "2024-01-01", "2024-12-31", warmup_months=0, today=TODAY)
    n = len(fake.calls)
    # 隔天再跑：rows 已含 expected（2024-11）→ 不打 API
    st.get(["2330"], "2024-01-01", "2024-12-31", warmup_months=0,
           today=date(2024, 12, 21))
    assert len(fake.calls) == n


# ---------- 失敗處理 ----------

class ExplodingProvider(FakeProvider):
    def __init__(self, data, explode_after):
        super().__init__(data)
        self.explode_after = explode_after

    def get_month_revenue(self, stock_ids, start, end):
        if len(self.calls) >= self.explode_after:
            self.calls.append((tuple(stock_ids), start, end))
            raise RuntimeError("API 額度用完")
        return super().get_month_revenue(stock_ids, start, end)


def test_api_failure_falls_back_to_cache(tmp_path):
    data = {"2330": months("2024-01", "2024-11")}
    fake = ExplodingProvider(data, explode_after=1)
    st = MonthRevenueStore(fake, cache_dir=str(tmp_path))
    st.get(["2330"], "2024-01-01", "2024-10-31", warmup_months=0, today=TODAY)
    # 擴大範圍 → 補尾會炸 → 沿用既有快取
    out = st.get(["2330"], "2024-01-01", "2024-12-31", warmup_months=0, today=TODAY)
    assert "2330" in out
    assert out["2330"].index.max() >= P("2024-09")

def test_api_failure_without_cache_skips_symbol(tmp_path):
    fake = ExplodingProvider({"2330": months("2024-01", "2024-11")}, explode_after=0)
    st = MonthRevenueStore(fake, cache_dir=str(tmp_path))
    out = st.get(["2330"], "2024-01-01", "2024-12-31", warmup_months=0, today=TODAY)
    assert out == {}  # 無資料 → 下游 fail-closed


# ---------- 多檔與工具函數 ----------

def test_multiple_symbols(store):
    fake, st = store({"2330": months("2024-01", "2024-11"),
                      "2368": months("2024-03", "2024-11")})
    out = st.get(["2330", "2368"], "2024-01-01", "2024-12-31",
                 warmup_months=0, today=TODAY)
    assert set(out) == {"2330", "2368"}
    assert out["2368"].index.min() == P("2024-03")

def test_queried_end_never_exceeds_announced_horizon(store):
    # 核心不變量：queried_end 不記「照理還沒公告」的未來月
    fake, st = store({"2330": months("2024-01", "2024-11")})
    st.get(["2330"], "2024-01-01", "2025-06-30", warmup_months=0, today=TODAY)
    d = st._load("2330")
    assert d["queried_end"] == "2024-11"  # 12/20 時最新照理公告 = 2024-11


# ---------- 審查修正的回歸測試：洞的癒合 ----------

def test_narrow_start_request_heals_pre_start_hole(tmp_path):
    """尾端補抓不受本次請求 start 夾擠：舊區間的洞（API 落後造成）要被癒合，
    不得凍結成永久假缺月（對抗式審查確認過的 bug 場景）。"""
    data = {"2330": months("2024-01", "2024-12")}
    fake = FakeProvider(data, pub_date=date(2024, 10, 20))  # 只公告到 2024-09
    st = MonthRevenueStore(fake, cache_dir=str(tmp_path))
    st.get(["2330"], "2024-01-01", "2024-12-31", warmup_months=0,
           today=date(2024, 10, 20))
    assert max(st._load("2330")["rows"]) == "2024-09"
    # 兩個多月後、只要求 11~12 月的窄區間；此時 10/11/12 月都已公告
    fake.pub_date = None
    out = st.get(["2330"], "2024-11-01", "2024-12-31", warmup_months=0,
                 today=date(2025, 1, 15))
    rows = st._load("2330")["rows"]
    assert "2024-10" in rows          # 在請求 start 之前的洞也要補回，不能凍結
    assert "2024-11" in rows and "2024-12" in rows
    assert len(fake.calls) == 2       # 增量仍只多 1 次請求
    assert out["2330"].index.min() == P("2024-11")  # 回傳仍按請求範圍


def test_api_lag_hole_healed_next_day(tmp_path):
    """月份已過公告日但 API 落後（查過、當時沒資料）→ 次日補洞癒合，
    即使當次請求的 start 在洞之後。"""
    data = {"2330": months("2024-01", "2024-12")}
    fake = FakeProvider(data, pub_date=date(2024, 11, 30))  # 2024-11 (12/1 進 API) 還沒進
    st = MonthRevenueStore(fake, cache_dir=str(tmp_path))
    st.get(["2330"], "2024-01-01", "2024-12-31", warmup_months=0, today=TODAY)
    assert max(st._load("2330")["rows"]) == "2024-10"  # 11 月照理已公告但 API 落後
    fake.pub_date = None  # API 補上了
    st.get(["2330"], "2024-12-01", "2024-12-31", warmup_months=0,
           today=date(2024, 12, 21))
    assert "2024-11" in st._load("2330")["rows"]  # 洞癒合（start 在洞之後也一樣）


# ---------- 髒資料防線 ----------

def test_poisoned_cache_key_dropped(tmp_path):
    """快取檔裡的不合法月份鍵（如 2024-13）要被丟掉，不得讓整批崩潰。"""
    import json
    fake = FakeProvider({"2330": months("2024-01", "2024-11")})
    st = MonthRevenueStore(fake, cache_dir=str(tmp_path))
    st.get(["2330"], "2024-01-01", "2024-12-31", warmup_months=0, today=TODAY)
    p = st._path("2330")
    d = json.loads(p.read_text())
    d["rows"]["2024-13"] = 123
    p.write_text(json.dumps(d))
    out = st.get(["2330"], "2024-01-01", "2024-12-31", warmup_months=0, today=TODAY)
    assert P("2024-11") in out["2330"].index  # 正常月份照常回
    assert "2024-13" not in st._load("2330")["rows"]


def test_get_month_revenue_filters_bad_month_rows():
    """FinMindProvider.get_month_revenue 要把 rev_month 不在 1..12 的髒列擋掉。"""
    from src.data.finmind import FinMindProvider

    class FakeApi:
        def taiwan_stock_month_revenue(self, stock_id, start_date, end_date):
            return pd.DataFrame([
                {"date": "2024-02-01", "stock_id": stock_id, "country": "Taiwan",
                 "revenue": 100, "revenue_month": 1, "revenue_year": 2024},
                {"date": "2024-03-01", "stock_id": stock_id, "country": "Taiwan",
                 "revenue": 200, "revenue_month": 13, "revenue_year": 2024},  # 髒列
                {"date": "2024-03-01", "stock_id": stock_id, "country": "Taiwan",
                 "revenue": 300, "revenue_month": 2, "revenue_year": 999999},  # 髒列
            ])

    p = FinMindProvider.__new__(FinMindProvider)  # 跳過 __init__（不打網路）
    p.api = FakeApi()
    df = p.get_month_revenue(["2330"], "2024-01-01", "2024-12-31")
    assert len(df) == 1
    assert df.iloc[0]["rev_month"] == 1 and df.iloc[0]["revenue"] == 100


def test_latest_announced_month_rule():
    assert latest_announced_month(date(2024, 12, 20)) == P("2024-11")  # 已過 10 日
    assert latest_announced_month(date(2024, 12, 10)) == P("2024-10")  # 10 日當天：保守
    assert latest_announced_month(date(2024, 12, 5)) == P("2024-10")
    assert latest_announced_month(date(2024, 1, 5)) == P("2023-11")    # 跨年
