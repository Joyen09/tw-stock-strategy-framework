"""賣選擇權回測的核心算式：到期日、保證金、損益、評價來源。

這類策略的回測最危險的不是算錯平均報酬，是**把尾部算小**。
所以測試重點放在：到期日對不對、保證金公式對不對、沒成交的合約有沒有
被擋掉、評價用的是結算價而不是 close（用 close 會憑空生出獲利）。
"""
import importlib.util
import os
import types

import numpy as np
import pandas as pd
import pytest

_SPEC = importlib.util.spec_from_file_location(
    "txo_short_backtest",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 "tools", "txo_short_backtest.py"),
)
bt = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(bt)


# ── 到期日 ──

@pytest.mark.parametrize("ym,expect", [
    ("202609", "2026-09-16"), ("202210", "2022-10-19"),
    ("202401", "2024-01-17"), ("202502", "2025-02-19"),
    ("202605", "2026-05-20"),
])
def test_third_wednesday(ym, expect):
    """月選到期日 = 第三個星期三。算錯一天，到期損益就整個錯。"""
    assert str(bt._third_wednesday(ym).date()) == expect


# ── 保證金 ──

def test_margin_uses_b_value_when_deep_out_of_the_money():
    """價外很遠：A值 - 價外值 會變負，應該取 B 值（下限）。"""
    m = bt._margin(premium_pts=50, otm_pts=1000, a_val=28_000, b_val=14_000)
    assert m == pytest.approx(50 * bt.MULTIPLIER + 14_000)


def test_margin_uses_a_minus_otm_when_near_the_money():
    """接近價平：價外值小，應該取 A值 - 價外值（比 B 值大）。"""
    m = bt._margin(premium_pts=200, otm_pts=100, a_val=28_000, b_val=14_000)
    assert m == pytest.approx(200 * bt.MULTIPLIER + (28_000 - 100 * bt.MULTIPLIER))


def test_margin_grows_as_position_moves_against_you():
    """部位愈往價內跑，保證金要愈高——這就是追繳的來源，不可以算成固定值。"""
    far = bt._margin(100, 800, 28_000, 14_000)
    near = bt._margin(300, 50, 28_000, 14_000)
    assert near > far


# ── 建倉過濾 ──

def _chain(dates, legs, session="position", contract="202609"):
    rows = []
    for d in dates:
        for cp, k, px, vol in legs:
            rows.append(dict(date=d, contract_date=contract, strike_price=float(k),
                             call_put=cp, close=px, settlement_price=px,
                             volume=vol, open_interest=900, trading_session=session))
    return pd.DataFrame(rows)


def _index(dates, lo, hi):
    return pd.Series(np.linspace(lo, hi, len(dates)), index=dates)


def test_untraded_strikes_cannot_be_sold():
    """volume=0 / close=0 的深價外履約價不可建倉——否則等於「用 0 元賣選擇權」。"""
    dates = pd.bdate_range("2026-08-03", "2026-09-16")
    legs = [("call", 43_500, 0.0, 0), ("put", 38_500, 0.0, 0)]   # 全部沒成交
    res = bt.backtest(_chain(dates, legs), _index(dates, 41_000, 41_300),
                      0.05, 500_000, 28_000, 14_000)
    assert res.empty


def test_picks_the_nearest_otm_strikes():
    """同方向有多個價外履約價時，要選最接近價平的那個（權利金最多的那個）。"""
    dates = pd.bdate_range("2026-08-03", "2026-09-16")
    legs = [("call", 43_500, 120.0, 500), ("call", 45_000, 40.0, 500),
            ("put", 38_500, 95.0, 500), ("put", 36_000, 30.0, 500)]
    res = bt.backtest(_chain(dates, legs), _index(dates, 41_000, 41_300),
                      0.05, 500_000, 28_000, 14_000)
    assert res.iloc[0]["call履約"] == 43_500
    assert res.iloc[0]["put履約"] == 38_500


# ── 損益帳 ──

def test_pnl_deducts_spread_tax_and_fees():
    """損益 = 權利金 - 價差 - 稅費 - 賠付。少扣一項就會高估賣方收益。"""
    dates = pd.bdate_range("2026-08-03", "2026-09-16")
    legs = [("call", 43_500, 120.0, 500), ("put", 38_500, 95.0, 500)]
    res = bt.backtest(_chain(dates, legs), _index(dates, 41_000, 41_300),
                      0.05, 500_000, 28_000, 14_000)
    gross = (120.0 + 95.0) * bt.MULTIPLIER
    expect = (gross - gross * bt.CRITERIA["spread_pct"]
              - gross * bt.TAX_RATE - 2 * bt.FEE_PER_LOT)      # 到期價內=0，無賠付
    assert res.iloc[0]["權利金"] == pytest.approx(gross, abs=1)
    assert res.iloc[0]["賠付"] == 0
    assert res.iloc[0]["損益"] == pytest.approx(expect, abs=1)


def test_a_big_adverse_move_produces_a_big_loss():
    """尾部要看得見：指數衝破賣出的 call 履約價，損益必須大虧。
    這是這個策略真正的風險，回測若算不出來就等於沒測。"""
    dates = pd.bdate_range("2026-08-03", "2026-09-16")
    legs = [("call", 43_500, 120.0, 500), ("put", 38_500, 95.0, 500)]
    res = bt.backtest(_chain(dates, legs), _index(dates, 41_000, 48_000),  # +17%
                      0.05, 500_000, 28_000, 14_000)
    row = res.iloc[0]
    assert row["賠付"] > 0
    assert row["損益"] < -100_000          # 權利金一萬出頭，賠付遠大於它
    assert row["最壞評價"] < row["損益"] + 1   # 評價也要反映出來


def test_settlement_price_is_used_for_marking_not_close():
    """評價要用結算價：冷門履約價 close=0，拿它評價會把空頭部位當成一文不值，
    憑空生出獲利、把尾部藏起來。"""
    dates = pd.bdate_range("2026-08-03", "2026-09-16")
    rows = []
    for i, d in enumerate(dates):
        for cp, k in (("call", 43_500), ("put", 38_500)):
            entry_day = i == 0
            rows.append(dict(
                date=d, contract_date="202609", strike_price=float(k), call_put=cp,
                # 建倉日有真實報價；之後 close 歸零但結算價仍然很高（部位在虧）
                close=120.0 if entry_day else 0.0,
                settlement_price=120.0 if entry_day else 400.0,
                volume=500 if entry_day else 0, open_interest=900,
                trading_session="position"))
    res = bt.backtest(pd.DataFrame(rows), _index(dates, 41_000, 41_300),
                      0.05, 500_000, 28_000, 14_000)
    row = res.iloc[0]
    net_premium = row["權利金"] - row["權利金"] * bt.CRITERIA["spread_pct"] \
        - row["權利金"] * bt.TAX_RATE - 2 * bt.FEE_PER_LOT
    # 用結算價（400+400 點）評價 → 大幅虧損
    assert row["最壞評價"] == pytest.approx(net_premium - 800 * bt.MULTIPLIER, abs=5)
    # 關鍵對照：若誤用 close=0 評價，最壞評價會變成「賺滿權利金」的正數
    assert row["最壞評價"] < 0 < net_premium


# ── 標準 ──

def test_criteria_are_pre_registered_and_tail_focused():
    assert bt.CRITERIA["require_no_margin_call"] is True
    assert 0 < bt.CRITERIA["max_month_loss"] <= 0.25
    assert bt.CRITERIA["spread_pct"] >= 0.10      # 價差假設不可樂觀
    assert bt.CRITERIA["min_months"] >= 24
    assert bt.SESSION == "position"               # 實測的主時段，不是猜的
    assert bt.MULTIPLIER == 50                    # TXO 一點 50 元


def test_monthly_only_regex():
    assert bt.MONTHLY_RE.match("202609")
    assert not bt.MONTHLY_RE.match("202609F4")
    assert not bt.MONTHLY_RE.match("202210W1")


def test_load_options_filters_session_and_weeklies():
    """_load_options 必須只留主時段 + 月選；混入盤後或週選會讓部位翻倍/算錯商品。"""
    dates = pd.bdate_range("2026-09-01", periods=3)
    frames = pd.concat([
        _chain(dates, [("call", 43_500, 120.0, 500)], session="position", contract="202609"),
        _chain(dates, [("call", 43_500, 120.0, 500)], session="after_market", contract="202609"),
        _chain(dates, [("call", 43_500, 120.0, 500)], session="position", contract="202609W1"),
    ], ignore_index=True)

    def taiwan_option_daily(start_date=None, end_date=None, option_id=None):
        return frames.copy()

    api = types.SimpleNamespace(taiwan_option_daily=taiwan_option_daily)
    out = bt._load_options(api, "2026-09-01", "2026-09-05")
    assert set(out["trading_session"]) == {"position"}
    assert set(out["contract_date"].astype(str)) == {"202609"}
