"""機械式逢跌加碼的模擬邏輯：階梯只觸發一次、現金拖累要算到、錢不會蒸發。

這支工具的結論會決定衛星層做不做，而它最容易出錯的地方不是「加碼賺多少」，
是**現金拖累有沒有被算進去**。少算拖累，結論就會偏向「加碼很棒」。
"""
import importlib.util
import os

import pandas as pd
import pytest

_SPEC = importlib.util.spec_from_file_location(
    "dip_add_compare",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 "tools", "dip_add_compare.py"),
)
dip = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(dip)

from src.broker import fees  # noqa: E402


def _ser(values, start="2020-01-01"):
    return pd.Series(values, index=pd.bdate_range(start, periods=len(values)), dtype=float)


def _flat(n=300, px=100.0):
    return _ser([px] * n)


def _crash_then_recover(n=300):
    """前 100 天 100 元，接著腰斬到 50，最後回到 100（製造一次完整的下跌-創新高）。"""
    return _ser([100.0] * 100 + [50.0] * 100 + [100.0] * (n - 200))


# ── 階梯觸發 ──

def test_tier_fires_once_per_drawdown_episode():
    """同一輪下跌裡，每一層只能觸發一次，不然會每天狂投。"""
    r = dip.simulate(_ser([100.0] * 60 + [80.0] * 200), 10_000, 0.5,
                     [(0.10, 1.0)], 0.28)
    assert r["triggers"] == 1


def test_tier_can_fire_again_after_a_new_high():
    """創新高後階梯重置，下一輪下跌要能再觸發（不然只有第一次有用）。"""
    s = _ser([100.0] * 60 + [80.0] * 60 + [100.0] * 20 + [80.0] * 60)
    r = dip.simulate(s, 10_000, 0.5, [(0.10, 1.0)], 0.28)
    assert r["triggers"] >= 2


def test_no_trigger_when_market_never_falls():
    r = dip.simulate(_flat(), 10_000, 0.3, [(0.10, 1.0)], 0.28)
    assert r["triggers"] == 0


def test_deepest_tier_is_used_first_on_a_big_single_drop():
    """一天最多觸發一層，而且該是最深的那層：跌 40% 不能只當成跌 10% 處理。

    驗證方式：讓 -40% 發生在**最後一根**，之後沒有新的扣款日把淺層重新啟用，
    所以整段只可能觸發一次。配上「淺層只投 5%、深層投 100%」的設定：
    深層優先 → 池子被清空；淺層優先 → 池子還剩九成以上。
    """
    s = _ser([100.0] * 60 + [60.0])            # -40% 落在最後一根
    deep_first = dip.simulate(s, 10_000, 0.5,
                              [(0.10, 0.05), (0.20, 0.05), (0.30, 1.0)], 0.28)
    only_shallow = dip.simulate(s, 10_000, 0.5, [(0.10, 0.05)], 0.28)

    assert deep_first["triggers"] == 1          # 只有一次機會，沒有新扣款日
    assert only_shallow["triggers"] == 1
    # 深層（投 100%）把池子倒光，淺層（投 5%）幾乎沒動 → 平均現金拖累明顯較低
    assert deep_first["drag"] < only_shallow["drag"]


def test_remaining_tiers_keep_deploying_new_reserve_during_a_long_crash():
    """刻意的設計：崩盤持續好幾個月時，每月新存進池子的錢要繼續進場買便宜，
    不該躺著等大盤創新高。所以較淺的層會在之後有錢時才動用。"""
    s = _ser([100.0] * 60 + [60.0] * 150)      # -40% 且持續數月（會有新扣款日）
    tiers = [(0.10, 0.2), (0.20, 0.3), (0.30, 1.0)]
    r = dip.simulate(s, 10_000, 0.5, tiers, 0.28)
    assert r["triggers"] == len(tiers)         # 三層都用到了，但分散在不同月份


def test_a_single_episode_cannot_deploy_more_times_than_tiers():
    """上限保護：一輪下跌最多觸發 len(tiers) 次，避免變成無限攤平——
    那會在長期陰跌裡把錢全部倒進去。"""
    s = _ser([100.0] * 60 + [60.0] * 600)      # 跌完躺著兩年多，從不創新高
    tiers = [(0.10, 0.2), (0.30, 1.0)]
    r = dip.simulate(s, 10_000, 0.5, tiers, 0.28)
    assert r["triggers"] == len(tiers)


# ── 現金拖累（最關鍵）──

def test_reserve_creates_measurable_cash_drag():
    """保留 30% 就該量到拖累。量不到的話結論會偏向『加碼很棒』。"""
    r = dip.simulate(_flat(), 10_000, 0.3, [(0.50, 1.0)], 0.28)   # 門檻深到不觸發
    assert r["triggers"] == 0
    assert r["drag"] > 0.1


def test_plain_dca_has_no_drag():
    r = dip.simulate(_flat(), 10_000, 0.0, [], 0.28)
    assert r["drag"] == pytest.approx(0.0, abs=1e-9)


def test_drag_costs_return_in_a_rising_market():
    """上漲市場裡，躺著的錢一定讓結果變差——這就是加碼的代價。"""
    rising = _ser([100.0 * (1.0005 ** i) for i in range(400)])
    plain = dip.simulate(rising, 10_000, 0.0, [], 0.28)
    held = dip.simulate(rising, 10_000, 0.4, [(0.50, 1.0)], 0.28)   # 永不觸發
    assert float(held["curve"].iloc[-1]) < float(plain["curve"].iloc[-1])


# ── 錢的帳 ──

def test_total_invested_is_identical_for_both_versions():
    """兩個版本必須投入一樣多的錢，否則比較無效（是『晚投入』不是『多投入』）。"""
    s = _crash_then_recover()
    a = dip.simulate(s, 10_000, 0.0, [], 0.28)
    b = dip.simulate(s, 10_000, 0.3, [(0.10, 1.0)], 0.28)
    assert a["invested"] == pytest.approx(b["invested"])
    assert a["months"] == b["months"]


def test_flat_market_only_loses_fees():
    """價格不動：期末市值 = 總投入 - 手續費。多扣或少扣都代表帳算錯。"""
    r = dip.simulate(_flat(), 10_000, 0.0, [], 0.28)
    paid = r["months"] * fees.MIN_FEE
    assert float(r["curve"].iloc[-1]) == pytest.approx(r["invested"] - paid, rel=1e-9)


def test_pool_money_is_never_lost():
    """沒投出去的錢要留在市值裡（池子也是你的錢），不能憑空消失。"""
    r = dip.simulate(_flat(), 10_000, 0.5, [(0.50, 1.0)], 0.28)
    assert float(r["curve"].iloc[-1]) > r["invested"] * 0.95


def test_buying_dips_beats_plain_dca_when_the_market_v_shapes():
    """機制健全性：剛好一個 V 型（腰斬再回來），加碼就該贏。
    連這種最有利的形狀都贏不了，就是模擬寫錯了。"""
    s = _crash_then_recover(n=360)
    plain = dip.simulate(s, 10_000, 0.0, [], 0.28)
    buy = dip.simulate(s, 10_000, 0.4, [(0.10, 0.5), (0.30, 1.0)], 0.28)
    assert buy["triggers"] > 0
    assert float(buy["curve"].iloc[-1]) > float(plain["curve"].iloc[-1])


# ── 其他 ──

def test_tiers_parse():
    assert dip._parse_tiers("10:0.5,20:1.0") == [(0.10, 0.5), (0.20, 1.0)]
    assert dip._parse_tiers("") == dip.DEFAULT_TIERS


def test_criteria_are_pre_registered_and_strict():
    """打平不算贏——加碼多了複雜度與拖累，必須嚴格更好才採用。"""
    assert dip.CRITERIA["irr_must_exceed"] is True
    assert dip.CRITERIA["min_triggers"] >= 3
