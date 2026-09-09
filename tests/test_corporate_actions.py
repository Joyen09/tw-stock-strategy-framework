"""除權息處理：把「股價機械性下跌」和「真的虧錢」分開（2026-09 緯穎事故的回歸測試）。"""
import json

import pytest

from src.broker.base import Order, OrderSide
from src.broker.persistent_paper import PersistentPaperBroker
from src.data.corporate_actions import CorporateAction, apply_to_position, classify


# ── 分類 ──

def test_pure_cash_dividend():
    """2886 兆豐金 2026-08-13：48.7 → 46.95，標示「息」。"""
    a = classify(48.7, 46.95, "息", "2886", "2026-08-13")
    assert a.kind == "cash"
    assert a.cash_per_share == pytest.approx(1.75)
    assert a.share_ratio == 1.0


def test_pure_stock_dividend_the_actual_wiwynn_case():
    """6669 緯穎 2026-09-02：7,800 → 2,614.99，標示「權」。1 股要變成約 2.98 股。"""
    a = classify(7800.0, 2614.99, "權", "6669", "2026-09-02")
    assert a.kind == "stock"
    assert a.share_ratio == pytest.approx(2.983, abs=0.01)
    assert a.cash_per_share == 0.0


def test_small_mixed_is_treated_as_cash():
    """2890 永豐金：標示「權息」但 40.4→38.52 幾乎全是現金（跌幅 4.7%）。"""
    a = classify(40.4, 38.52, "權息", "2890", "2026-07-23")
    assert a.kind == "cash"
    assert a.cash_per_share == pytest.approx(1.88, abs=0.01)


def test_large_mixed_is_refused_not_guessed():
    """權息混合又跌很多 → 拆不出比例，寧可不動也不要調錯。"""
    a = classify(1000.0, 500.0, "權息", "9999", "2026-01-01")
    assert a.kind == "unknown"
    assert "人工確認" in a.note


def test_bad_prices_are_refused():
    assert classify(0, 100, "息").kind == "unknown"
    assert classify(100, 0, "息").kind == "unknown"
    assert classify(100, 100, "息").kind == "unknown"      # 沒下調
    assert classify(100, 90, "???").kind == "unknown"      # 認不得的標示


# ── 套用到持倉 ──

def test_stock_dividend_keeps_total_cost():
    """配股：股數變多、均價等比例下調，總成本不變——停損線才會跟著調整。"""
    a = classify(7800.0, 2614.99, "權", "6669")
    shares, avg, cash = apply_to_position(1, 5925.0, a)
    assert shares == 2                       # 1 × 2.983 = 2.98 → 2 股 + 零頭折現
    assert cash == pytest.approx(0.983 * 2614.99, rel=0.02)
    assert shares * avg == pytest.approx(1 * 5925.0)   # 總成本守恆


def test_cash_dividend_credits_cash_and_lowers_basis():
    a = classify(48.7, 46.95, "息", "2886")
    shares, avg, cash = apply_to_position(194, 51.5, a)
    assert shares == 194                     # 股數不變
    assert avg == pytest.approx(51.5 - 1.75)
    assert cash == pytest.approx(194 * 1.75)


def test_unknown_action_changes_nothing():
    a = CorporateAction(date="2026-01-01", symbol="X", kind="unknown",
                        before_price=100, after_price=50)
    assert apply_to_position(100, 10.0, a) is None


def test_no_position_changes_nothing():
    a = classify(48.7, 46.95, "息", "2886")
    assert apply_to_position(0, 51.5, a) is None


def test_avg_price_never_goes_negative():
    """成本已低於累積股利的極端情況，均價要守在正數而不是變負的。"""
    a = classify(10.0, 1.0, "息", "X")
    shares, avg, cash = apply_to_position(100, 2.0, a)
    assert avg > 0


# ── 帳戶層（冪等、留痕）──

def _broker(tmp_path, sym="6669", shares=1, price=5925.0):
    b = PersistentPaperBroker(path=str(tmp_path / "p.json"), cash=100_000)
    b.place_order(Order(sym, OrderSide.BUY, shares, price, "建倉"))
    return b


def test_wiwynn_case_end_to_end(tmp_path):
    """完整重現事故：套用除權後不該再看起來像暴跌。"""
    b = _broker(tmp_path)
    a = classify(7800.0, 2614.99, "權", "6669", "2026-09-02")
    applied = b.apply_corporate_actions([a])

    assert len(applied) == 1
    pos = b.account.positions["6669"]
    assert pos.shares == 2
    # 均價從 5,925 降到約 2,962：市價 2,610 只比成本低一點，不會觸發 3×ATR 停損
    assert pos.avg_price == pytest.approx(2962.5, rel=0.01)
    assert pos.avg_price < 2610 * 1.2


def test_applied_twice_is_idempotent(tmp_path):
    """排程每天跑，同一筆除權不能被套用兩次。"""
    b = _broker(tmp_path)
    a = classify(7800.0, 2614.99, "權", "6669", "2026-09-02")
    b.apply_corporate_actions([a])
    after_first = (b.account.positions["6669"].shares, b.account.positions["6669"].avg_price)
    b.apply_corporate_actions([a])
    assert (b.account.positions["6669"].shares,
            b.account.positions["6669"].avg_price) == after_first


def test_idempotency_survives_restart(tmp_path):
    """跨 process 也要記得處理過了（last_ca_date 有落地）。"""
    path = str(tmp_path / "p.json")
    b1 = PersistentPaperBroker(path=path, cash=100_000)
    b1.place_order(Order("6669", OrderSide.BUY, 1, 5925.0, "建倉"))
    a = classify(7800.0, 2614.99, "權", "6669", "2026-09-02")
    b1.apply_corporate_actions([a])

    b2 = PersistentPaperBroker(path=path)          # 模擬下一次排程
    assert b2.last_ca_date == "2026-09-02"
    b2.apply_corporate_actions([a])
    assert b2.account.positions["6669"].shares == 2   # 沒有被再乘一次


def test_action_is_recorded_in_trade_log(tmp_path):
    b = _broker(tmp_path)
    b.apply_corporate_actions([classify(7800.0, 2614.99, "權", "6669", "2026-09-02")])
    ca = [t for t in b.trades if t["side"] == "CORP_ACTION"]
    assert len(ca) == 1 and ca[0]["symbol"] == "6669"
    saved = json.loads(open(b.path, encoding="utf-8").read())
    assert any(t["side"] == "CORP_ACTION" for t in saved["trades"])


def test_action_for_unheld_symbol_is_ignored(tmp_path):
    b = _broker(tmp_path, sym="2330", shares=10, price=1000.0)
    assert b.apply_corporate_actions([classify(7800.0, 2614.99, "權", "6669", "2026-09-02")]) == []


def test_cash_dividend_credits_account(tmp_path):
    b = _broker(tmp_path, sym="2886", shares=194, price=51.5)
    cash_before = b.cash()
    b.apply_corporate_actions([classify(48.7, 46.95, "息", "2886", "2026-08-13")])
    assert b.cash() == pytest.approx(cash_before + 194 * 1.75)
