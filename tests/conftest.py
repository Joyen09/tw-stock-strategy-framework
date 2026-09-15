"""測試全域設定：整個 test suite 一律不打網路。

起因（2026-09-15）：在 VM 上跑 pytest 會卡在
`test_all_strategies_backtest_without_error`——它會建出 us_overnight 策略，
而 USLeadProvider 預設要去 yfinance 抓 6 年的 ^SOX / TSM 資料。網路慢的機器
看起來就像整個測試當掉。

單元測試依賴 Yahoo 連不連得上，本來就是錯的：結果不可重現，失敗也分不清
是程式壞了還是網路壞了。這裡統一關掉，需要外部資料的測試一律注入假資料。
"""
import os

os.environ.setdefault("STOCKBOT_NO_NETWORK", "1")
