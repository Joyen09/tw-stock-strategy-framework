"""成交紀錄持久化與已實現損益 (回答「錢是怎麼虧的」)。"""
import json

from src.broker.base import Order, OrderSide
from src.broker.multi_paper import MultiPaperBroker
from src.broker.persistent_paper import MAX_TRADES, PersistentPaperBroker
from src.control import _format_trades, handle_broker_command


def test_trades_persist_across_processes(tmp_path):
    path = str(tmp_path / "paper.json")
    b1 = PersistentPaperBroker(path=path, cash=100_000)
    b1.place_order(Order("2330", OrderSide.BUY, 100, 500.0, "測試買進"))

    b2 = PersistentPaperBroker(path=path)  # 模擬「下一次排程」重新開 process
    assert len(b2.trades) == 1
    assert b2.trades[0]["symbol"] == "2330"
    assert b2.trades[0]["side"] == "BUY"
    assert b2.trades[0]["shares"] == 100


def test_sell_records_realized_pnl_net_of_costs(tmp_path):
    path = str(tmp_path / "paper.json")
    b = PersistentPaperBroker(path=path, cash=100_000)
    b.place_order(Order("2330", OrderSide.BUY, 100, 500.0, "買"))
    b.place_order(Order("2330", OrderSide.SELL, 100, 550.0, "賣"))

    sells = [t for t in b.trades if t["side"] == "SELL"]
    assert len(sells) == 1
    rec = sells[0]
    assert rec["avg_cost"] == 500.0
    # 毛利 5,000，扣掉賣出手續費與證交稅後應小於毛利、但仍為正
    assert 0 < rec["realized"] < 5000


def test_losing_trade_records_negative_realized(tmp_path):
    path = str(tmp_path / "paper.json")
    b = PersistentPaperBroker(path=path, cash=100_000)
    b.place_order(Order("1301", OrderSide.BUY, 100, 62.0, "買"))
    b.place_order(Order("1301", OrderSide.SELL, 100, 54.0, "ATR 停損"))
    rec = [t for t in b.trades if t["side"] == "SELL"][0]
    assert rec["realized"] < 0
    assert "停損" in rec["reason"] or rec["reason"] == ""


def test_unfilled_order_not_recorded(tmp_path):
    path = str(tmp_path / "paper.json")
    b = PersistentPaperBroker(path=path, cash=1_000)  # 資金不足
    b.place_order(Order("2330", OrderSide.BUY, 100, 500.0, "買不起"))
    assert b.trades == []


def test_trade_log_is_capped(tmp_path):
    path = str(tmp_path / "paper.json")
    b = PersistentPaperBroker(path=path, cash=10_000_000)
    for i in range(MAX_TRADES + 20):
        b.place_order(Order("2330", OrderSide.BUY, 1, 100.0 + i, "買"))
    saved = json.loads(open(path, encoding="utf-8").read())
    assert len(saved["trades"]) == MAX_TRADES  # 只留最新的，帳戶檔不會無限長大


def test_bad_start_date_is_ignored_not_crash(tmp_path):
    """手動編輯帳戶檔把 start_date 填成說明文字時，要靜靜忽略而非拿爛值去對大盤。"""
    path = tmp_path / "paper.json"
    path.write_text(json.dumps({
        "cash": 10_000, "initial_cash": 10_000, "start_date": "你的啟用日", "positions": []
    }), encoding="utf-8")
    b = PersistentPaperBroker(path=str(path))
    assert b.start_date is None


def test_valid_start_date_is_kept(tmp_path):
    path = tmp_path / "paper.json"
    path.write_text(json.dumps({
        "cash": 10_000, "initial_cash": 10_000, "start_date": "2026-07-07", "positions": []
    }), encoding="utf-8")
    assert PersistentPaperBroker(path=str(path)).start_date == "2026-07-07"


def test_realized_summary_counts_wins_and_losses(tmp_path):
    path = str(tmp_path / "paper_a.json")
    b = PersistentPaperBroker(path=path, cash=200_000)
    b.place_order(Order("2330", OrderSide.BUY, 100, 500.0, "買"))
    b.place_order(Order("2330", OrderSide.SELL, 100, 550.0, "獲利了結"))
    b.place_order(Order("1301", OrderSide.BUY, 100, 62.0, "買"))
    b.place_order(Order("1301", OrderSide.SELL, 100, 54.0, "停損"))

    multi = MultiPaperBroker([("lynch", path)])
    rows = multi.realized_summary()
    assert len(rows) == 1
    r = rows[0]
    assert r["wins"] == 1 and r["losses"] == 1
    assert r["n_buy"] == 2 and r["n_sell"] == 2
    assert r["fees_est"] > 0  # 小額部位受 20 元低消支配，成本不可忽略


def test_trades_command_via_broker_handler(tmp_path):
    path = str(tmp_path / "paper_a.json")
    b = PersistentPaperBroker(path=path, cash=100_000)
    b.place_order(Order("2330", OrderSide.BUY, 100, 500.0, "買"))
    reply = handle_broker_command("/trades", MultiPaperBroker([("lynch", path)]))
    assert "已實現損益" in reply
    assert "2330" in reply


def test_trades_message_warns_when_log_empty(tmp_path):
    """舊帳戶沒有歷史成交紀錄時要講清楚，不要讓使用者以為「沒交易過」。"""
    path = tmp_path / "paper_a.json"
    path.write_text(json.dumps({"cash": 10_000, "initial_cash": 10_000, "positions": []}),
                    encoding="utf-8")
    rows = MultiPaperBroker([("lynch", str(path))]).realized_summary()
    msg = _format_trades(rows)
    assert "沒有任何成交紀錄" in msg
