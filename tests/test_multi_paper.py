"""多帳戶模擬盤聚合 (MultiPaperBroker) + Telegram listener 合併檢視測試。"""
import json

from src.broker.base import Order, OrderSide
from src.broker.multi_paper import MultiPaperBroker
from src.control import handle_broker_command


def _make_accounts(tmp_path):
    a = tmp_path / "paper_a.json"
    b = tmp_path / "paper_b.json"
    a.write_text(json.dumps({"cash": 20000, "positions": [
        {"symbol": "2330", "shares": 10, "avg_price": 1000.0}]}))
    b.write_text(json.dumps({"cash": 5000, "positions": [
        {"symbol": "2891", "shares": 100, "avg_price": 105.0}]}))
    return [("lynch", str(a)), ("livermore", str(b))]


def test_holdings_merged_with_labels_and_total(tmp_path):
    mb = MultiPaperBroker(_make_accounts(tmp_path))
    reply = handle_broker_command("/holdings", mb)
    assert "【lynch】" in reply and "【livermore】" in reply
    assert "2330 10 股" in reply and "2891 100 股" in reply
    # 總資產 = 20000 + 10*1000 + 5000 + 100*105 = 45,500
    assert "45,500" in reply


def test_sell_routes_to_owning_account(tmp_path):
    accounts = _make_accounts(tmp_path)
    mb = MultiPaperBroker(accounts)
    reply = handle_broker_command("/sell 2891", mb)
    assert "[livermore] 2891 100 股" in reply
    # lynch 的 2330 不受影響；livermore 的 2891 已出清
    assert mb.brokers["lynch"].positions()[0].shares == 10
    assert all(p.shares == 0 for p in mb.brokers["livermore"].positions())


def test_sell_all_clears_every_account(tmp_path):
    mb = MultiPaperBroker(_make_accounts(tmp_path))
    reply = handle_broker_command("/sell all", mb)
    assert "2330" in reply and "2891" in reply
    for b in mb.brokers.values():
        assert all(p.shares == 0 for p in b.positions())


def test_sell_unknown_symbol(tmp_path):
    mb = MultiPaperBroker(_make_accounts(tmp_path))
    assert "找不到" in handle_broker_command("/sell 9999", mb)


def test_missing_account_file_hidden_from_holdings(tmp_path):
    a = tmp_path / "paper_a.json"
    a.write_text(json.dumps({"cash": 30000, "positions": []}))
    mb = MultiPaperBroker([("lynch", str(a)), ("livermore", str(tmp_path / "nope.json"))])
    reply = handle_broker_command("/holdings", mb)
    assert "【lynch】" in reply
    assert "livermore" not in reply          # 還沒建檔的帳戶不顯示
    assert "30,000" in reply                 # 總資產只算已建檔帳戶

def test_reload_sees_external_scan_write(tmp_path):
    accounts = _make_accounts(tmp_path)
    mb = MultiPaperBroker(accounts)
    # 模擬排程 scan (另一個 process) 對 livermore 帳戶下單後寫檔
    other = MultiPaperBroker(accounts)
    other.brokers["livermore"].place_order(Order("2886", OrderSide.BUY, 50, 40.0, "scan 買進"))
    reply = handle_broker_command("/holdings", mb)  # handle 內會 reload
    assert "2886 50 股" in reply
