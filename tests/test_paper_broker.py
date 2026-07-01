"""PaperBroker 模擬撮合測試。"""
import pytest

from src.broker import fees
from src.broker.base import Order, OrderSide
from src.broker.paper import PaperBroker


def _buy(broker, symbol, shares, price):
    return broker.place_order(Order(symbol, OrderSide.BUY, shares, price))


def test_buy_deducts_cash_with_fee_and_records_position():
    b = PaperBroker(cash=100_000)
    _buy(b, "2330", 10, 600.0)  # 零股 10 股 @600 = 6000 + 手續費
    assert b.cash() == pytest.approx(100_000 - fees.buy_cost(6_000))
    pos = b.positions()[0]
    assert pos.symbol == "2330"
    assert pos.shares == 10
    assert pos.avg_price == pytest.approx(600.0)


def test_buy_insufficient_funds_not_filled():
    b = PaperBroker(cash=5_000)
    order = _buy(b, "2330", 10, 600.0)  # 需要 6000+，不夠
    assert order.filled is False
    assert b.positions() == []
    assert b.cash() == 5_000  # 現金不動


def test_average_price_on_multiple_buys():
    b = PaperBroker(cash=1_000_000)
    _buy(b, "2330", 10, 600.0)
    _buy(b, "2330", 10, 800.0)
    pos = b.positions()[0]
    assert pos.shares == 20
    assert pos.avg_price == pytest.approx(700.0)  # (10*600 + 10*800) / 20


def test_partial_sell_keeps_remaining_position():
    b = PaperBroker(cash=1_000_000)
    _buy(b, "2330", 20, 600.0)
    b.place_order(Order("2330", OrderSide.SELL, 5, 700.0))
    pos = b.positions()[0]
    assert pos.shares == 15
    assert pos.avg_price == pytest.approx(600.0)  # 部分賣出不改均價


def test_sell_more_than_held_caps_at_holding():
    b = PaperBroker(cash=1_000_000)
    _buy(b, "2330", 5, 600.0)
    order = b.place_order(Order("2330", OrderSide.SELL, 50, 700.0))
    assert order.shares == 5          # 實際只賣掉持有的 5 股
    assert b.positions() == []        # 全部出清


def test_sell_without_position_noop():
    b = PaperBroker(cash=1_000_000)
    order = b.place_order(Order("2330", OrderSide.SELL, 5, 700.0))
    assert "無持倉可賣" in order.note
    assert b.cash() == 1_000_000


def test_place_order_requires_price():
    b = PaperBroker(cash=1_000_000)
    with pytest.raises(ValueError):
        b.place_order(Order("2330", OrderSide.BUY, 10, None))
