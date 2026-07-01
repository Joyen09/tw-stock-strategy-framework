"""台股交易成本計算測試。"""
from src.broker import fees


def test_buy_cost_adds_broker_fee():
    # 100,000 買進：手續費 = 100000 * 0.1425% = 142.5 -> 總成本 100142.5
    assert fees.buy_cost(100_000) == 100_000 + 142.5


def test_buy_cost_min_fee_floor():
    # 小額買進手續費不足 20 元時，以最低 20 元計
    assert fees.buy_cost(1_000) == 1_000 + fees.MIN_FEE


def test_buy_cost_discount():
    # 2.8 折手續費
    fee = 100_000 * fees.BROKER_FEE_RATE * 0.28
    assert fees.buy_cost(100_000, fee_discount=0.28) == 100_000 + fee


def test_sell_proceeds_deducts_fee_and_tax():
    # 賣出要扣手續費 + 證交稅 0.3%
    amount = 100_000
    expected = amount - amount * fees.BROKER_FEE_RATE - amount * fees.TAX_RATE
    assert fees.sell_proceeds(amount) == expected


def test_sell_proceeds_day_trade_half_tax():
    # 當沖證交稅減半 (0.15%)
    amount = 100_000
    expected = amount - amount * fees.BROKER_FEE_RATE - amount * (fees.TAX_RATE / 2)
    assert fees.sell_proceeds(amount, day_trade=True) == expected


def test_round_trip_cost_positive():
    # 一買一賣總成本應為正 (成本會吃掉獲利)
    assert fees.round_trip_fee(100_000) > 0
