"""Discord 控制器的指令處理層測試（不需 discord.py 套件——傳輸層之下全部可測）。"""
import json

import src.control as control
from src.broker.multi_paper import MultiPaperBroker
from src.control_discord import process_command


def _broker(tmp_path):
    a = tmp_path / "paper_a.json"
    a.write_text(json.dumps({"cash": 20000, "positions": [
        {"symbol": "2330", "shares": 10, "avg_price": 1000.0}]}))
    return MultiPaperBroker([("lynch", str(a))])


def test_ignores_plain_chat(tmp_path):
    assert process_command("早安", _broker(tmp_path)) == ""
    assert process_command("", _broker(tmp_path)) == ""


def test_bang_prefix_works_like_slash(tmp_path, monkeypatch):
    # Discord 打 / 會跳斜線指令選單，!status 要等同 /status
    monkeypatch.setattr(control, "RUNTIME_FILE", str(tmp_path / "runtime.json"))
    r_slash = process_command("/status", _broker(tmp_path))
    r_bang = process_command("!status", _broker(tmp_path))
    assert "目前設定" in r_slash
    assert r_bang == r_slash


def test_prefix_tolerates_trailing_space(tmp_path):
    # "! report" (前綴後有空白) 要跟 "!report" 一樣可用
    a = process_command("!report", _broker(tmp_path))
    b = process_command("! report", _broker(tmp_path))
    assert "模擬盤績效" in a
    assert a == b


def test_unknown_command_gives_hint(tmp_path):
    r = process_command("!reprot", _broker(tmp_path))  # 打錯字
    assert "不認得指令" in r
    assert "reprot" in r


def test_error_is_surfaced_not_silent(tmp_path):
    # broker 的 report 丟例外時，要回一行錯誤訊息而非靜默
    class _Boom:
        brokers = {"x": None}

        def reload(self):
            pass

        def report(self, fn):
            raise RuntimeError("boom")

    r = process_command("!report", _Boom())
    assert "執行出錯" in r and "boom" in r


def test_holdings_via_discord(tmp_path):
    reply = process_command("/holdings", _broker(tmp_path))
    assert "2330 10 股" in reply
    assert "總資產" in reply


def test_report_via_discord_marks_to_market(tmp_path):
    reply = process_command("!report", _broker(tmp_path))
    assert "模擬盤績效" in reply


def test_pause_resume_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(control, "RUNTIME_FILE", str(tmp_path / "runtime.json"))
    b = _broker(tmp_path)
    assert "暫停" in process_command("/pause", b)
    assert control.load_runtime()["paused"] is True
    assert "恢復" in process_command("!resume", b)
    assert control.load_runtime()["paused"] is False


def test_sell_routes_through_broker(tmp_path):
    b = _broker(tmp_path)
    reply = process_command("/sell 2330", b)
    assert "2330 10 股" in reply
    assert all(p.shares == 0 for p in b.brokers["lynch"].positions())
