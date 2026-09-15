"""測試不准打網路（2026-09-15 的回歸測試）。

事故：在 VM 上跑 pytest 會卡在 test_all_strategies_backtest_without_error，
因為它建出的 us_overnight 策略會去 yfinance 抓 6 年的 ^SOX / TSM 資料。
在網路慢的機器上，整個 test suite 看起來就像當掉。

單元測試依賴 Yahoo 連不連得上本來就是錯的：不可重現，失敗還分不清是
程式壞了還是網路壞了。conftest.py 統一關掉，這裡確保它不會被改回去。
"""
import os

import pandas as pd

from src.data.us_lead import USLeadProvider


def test_conftest_sets_offline_flag():
    assert os.getenv("STOCKBOT_NO_NETWORK"), "conftest.py 應該要把測試鎖在離線模式"


def test_no_network_returns_none_instead_of_downloading():
    """沒有注入資料時直接回 None，不去 yfinance。"""
    p = USLeadProvider()
    assert p.overnight_returns("2330") is None
    assert p._proxy("2330") in p._failed   # 記下來，不會每次重試


def test_injected_series_still_works_offline():
    """離線旗標不可以把『注入假資料』的路徑也擋掉，否則策略測試會全失效。"""
    idx = pd.bdate_range("2025-01-01", periods=5)
    s = pd.Series([0.0, 0.01, -0.01, 0.02, 0.0], index=idx)
    p = USLeadProvider(series_map={"TSM": s})
    got = p.overnight_returns("2330")
    assert got is not None and len(got) == 5


def test_overnight_on_degrades_gracefully_when_offline():
    """策略呼叫的是 overnight_on()，離線時要安靜回 None 而不是拋例外。"""
    assert USLeadProvider().overnight_on("2454", "2025-06-30") is None
