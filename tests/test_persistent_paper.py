"""PersistentPaperBroker 持久化測試。

重點：跨「不同 broker 實例」(模擬跨 process 的排程執行) 持倉/現金要能延續，
且 reload 能反映另一個 process 寫入的變更。
"""
import json
import os

import pytest

from src.broker.base import Order, OrderSide
from src.broker.persistent_paper import PersistentPaperBroker


@pytest.fixture
def path(tmp_path):
    return str(tmp_path / "paper_account.json")


def _buy(broker, symbol, shares, price):
    return broker.place_order(Order(symbol, OrderSide.BUY, shares, price))


def test_first_run_uses_initial_cash_no_file(path):
    b = PersistentPaperBroker(path=path, cash=50_000)
    assert b.cash() == 50_000
    assert b.positions() == []


def test_buy_persists_across_instances(path):
    # 第一個 process：買進
    b1 = PersistentPaperBroker(path=path, cash=50_000)
    _buy(b1, "2891", 105, 71.3)
    # 第二個 process (新實例、同一檔)：應延續持倉與現金，不是重設回 50000
    b2 = PersistentPaperBroker(path=path, cash=50_000)
    pos = b2.positions()
    assert len(pos) == 1
    assert pos[0].symbol == "2891"
    assert pos[0].shares == 105
    assert b2.cash() < 50_000  # 買進扣了現金 (含手續費)


def test_file_is_written_and_wellformed(path):
    b = PersistentPaperBroker(path=path, cash=50_000)
    _buy(b, "2886", 164, 45.7)
    assert os.path.exists(path)
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    assert "cash" in data
    syms = {p["symbol"]: p for p in data["positions"]}
    assert syms["2886"]["shares"] == 164


def test_sell_persists(path):
    b1 = PersistentPaperBroker(path=path, cash=50_000)
    _buy(b1, "1216", 100, 74.7)
    b1.place_order(Order("1216", OrderSide.SELL, 100, 75.0))
    # 新實例讀回：已全出清
    b2 = PersistentPaperBroker(path=path, cash=50_000)
    assert b2.positions() == []


def test_reload_sees_external_write(path):
    # 監聽器持有 b_listener，另一個 process (b_scan) 買進後，reload 才看得到
    b_listener = PersistentPaperBroker(path=path, cash=50_000)
    assert b_listener.positions() == []

    b_scan = PersistentPaperBroker(path=path, cash=50_000)
    _buy(b_scan, "2330", 10, 600.0)

    assert b_listener.positions() == []  # 還沒 reload，看不到
    b_listener.reload()
    assert len(b_listener.positions()) == 1  # reload 後看到 scan 寫入的部位


def test_corrupt_file_falls_back_to_initial(path):
    with open(path, "w", encoding="utf-8") as f:
        f.write("{ not valid json")
    b = PersistentPaperBroker(path=path, cash=50_000)  # 不應炸，沿用初始資金
    assert b.cash() == 50_000
    assert b.positions() == []
