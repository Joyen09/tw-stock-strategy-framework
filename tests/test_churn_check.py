"""交易品質健檢的純函式測試（不讀真實帳戶檔）。"""
from tools.churn_check import _cooldown_violations, _round_trips


def _t(date, sym, side, realized=None):
    d = {"date": date, "symbol": sym, "side": side, "shares": 10, "price": 100.0}
    if realized is not None:
        d["realized"] = realized
    return d


def test_round_trip_pairs_buy_with_sell():
    trades = [_t("2026-08-03", "2330", "BUY"), _t("2026-08-05", "2330", "SELL", -356)]
    rts = _round_trips(trades)
    assert len(rts) == 1
    sym, buy_d, sell_d, held, pnl = rts[0]
    assert (sym, buy_d, sell_d, held, pnl) == ("2330", "2026-08-03", "2026-08-05", 2, -356)


def test_multiple_rounds_pair_fifo():
    """同一檔買賣多輪要先進先出配對，不能全部湊在一起。"""
    trades = [
        _t("2026-08-01", "2330", "BUY"), _t("2026-08-10", "2330", "SELL", 100),
        _t("2026-08-20", "2330", "BUY"), _t("2026-08-25", "2330", "SELL", -50),
    ]
    rts = _round_trips(trades)
    assert [(r[1], r[2], r[3]) for r in rts] == [
        ("2026-08-01", "2026-08-10", 9), ("2026-08-20", "2026-08-25", 5)]


def test_sell_without_matching_buy_is_skipped():
    """買進發生在成交記帳上線之前 → 配不到，不能亂算成持有 0 天。"""
    assert _round_trips([_t("2026-07-31", "1301", "SELL", -1350)]) == []


def test_unsorted_input_is_handled():
    """紀錄順序若亂掉（手動編輯過）仍要依日期配對。"""
    trades = [_t("2026-08-05", "2330", "SELL", 10), _t("2026-08-03", "2330", "BUY")]
    assert _round_trips(trades)[0][3] == 2


def test_cooldown_violation_detected():
    """賣掉隔天又買回同一檔＝典型的來回洗，要抓出來。"""
    trades = [_t("2026-08-03", "2330", "SELL"), _t("2026-08-04", "2330", "BUY")]
    v = _cooldown_violations(trades, days=5)
    assert len(v) == 1 and v[0][0] == "2330" and v[0][3] == 1


def test_rebuy_after_cooldown_is_not_a_violation():
    trades = [_t("2026-08-03", "2330", "SELL"), _t("2026-08-20", "2330", "BUY")]
    assert _cooldown_violations(trades, days=5) == []


def test_different_symbol_is_not_a_violation():
    """換股不是違規——冷卻只針對剛賣掉的那一檔。"""
    trades = [_t("2026-08-03", "1301", "SELL"), _t("2026-08-04", "2886", "BUY")]
    assert _cooldown_violations(trades, days=5) == []


def test_empty_input_is_safe():
    assert _round_trips([]) == []
    assert _cooldown_violations([], days=5) == []
