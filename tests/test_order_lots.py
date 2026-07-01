"""plan_order_lots 拆單邏輯測試 (純函式，不需 shioaji 套件)。

重點回歸：盤中零股單一委託上限 999 股，>999 且非整張倍數的股數必須拆成
整股(Common) + 零股(IntradayOdd)，否則會被券商拒單。
"""
from src.broker.shioaji_broker import ODD_LOT_MAX, plan_order_lots


def test_pure_odd_lot_small():
    # 純零股：2 股 -> 單一 IntradayOdd(2)，不可被放大成 1000 股 (PR#4 那個 bug)
    assert plan_order_lots(2) == [("IntradayOdd", 2)]
    assert plan_order_lots(16) == [("IntradayOdd", 16)]
    assert plan_order_lots(999) == [("IntradayOdd", 999)]


def test_pure_whole_lot():
    # 整張倍數：以「張」為單位的 Common
    assert plan_order_lots(1000) == [("Common", 1)]
    assert plan_order_lots(3000) == [("Common", 3)]


def test_mixed_lot_and_odd_gets_split():
    # 核心回歸：1666 股 = 1 張 + 666 零股，必須拆兩段，不能塞成 IntradayOdd(1666)
    assert plan_order_lots(1666) == [("Common", 1), ("IntradayOdd", 666)]
    assert plan_order_lots(2500) == [("Common", 2), ("IntradayOdd", 500)]


def test_odd_segment_never_exceeds_limit():
    # 任意股數拆出來的零股段一律 <= 999
    for shares in range(1, 10001):
        for kind, qty in plan_order_lots(shares):
            if kind == "IntradayOdd":
                assert 1 <= qty <= ODD_LOT_MAX
            else:
                assert qty >= 1


def test_reconstructs_total_shares():
    # 拆出來的各段股數加總 = 原始股數 (沒有多買 / 少買)
    for shares in [1, 2, 999, 1000, 1001, 1666, 2500, 9999]:
        total = sum(q * (1000 if kind == "Common" else 1) for kind, q in plan_order_lots(shares))
        assert total == shares


def test_zero_or_negative_no_order():
    assert plan_order_lots(0) == []
    assert plan_order_lots(-5) == []
