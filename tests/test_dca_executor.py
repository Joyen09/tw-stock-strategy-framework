"""定期定額執行器：每月扣款一次、錢不會憑空消失、手續費算得對。

2026-10 的驗證把方向收斂到這裡：選股那層在 tw50 是負貢獻、均線濾網對
定期定額幾乎沒作用。剩下的東西規則極少，所以每一條都要鎖死——
一個「重複扣款」或「錢入帳沒落地」的 bug，在幾年的累積裡會變成大洞。
"""
import argparse
import json

import pytest

import main as cli
from src.broker import fees
from src.broker.persistent_paper import PersistentPaperBroker


def _args(path, **kw):
    base = dict(symbol="2330", amount=10_000.0, paper_file=str(path),
                fee_discount=0.28, source="sample", end="2025-11-03",
                notify=False, force=False)
    base.update(kw)
    return argparse.Namespace(**base)


# ── 股數計算：最低手續費不能漏 ──

def test_shares_fit_inside_the_budget_including_min_fee():
    """買單是全有全無，算超了會整筆不成交（HANDOFF 注意事項 22）。"""
    for amount, price in ((10_000, 346.35), (10_000, 99.9), (3_000, 45.5), (50_000, 1_345.0)):
        n = cli.dca_shares(amount, price, 0.28)
        assert fees.buy_cost(n * price, 0.28) <= amount
        # 而且要「盡量買滿」——多一股就該超出預算
        assert fees.buy_cost((n + 1) * price, 0.28) > amount


def test_shares_zero_when_one_share_is_unaffordable():
    assert cli.dca_shares(1_000, 13_100.0, 0.28) == 0
    assert cli.dca_shares(0, 100.0, 0.28) == 0


def test_shares_zero_on_bad_price():
    assert cli.dca_shares(10_000, 0.0, 0.28) == 0


# ── 每月只扣一次 ──

def test_buys_once_then_skips_the_same_month(tmp_path, capsys):
    path = tmp_path / "dca.json"
    cli.cmd_dca(_args(path, end="2025-11-03"))
    first = json.loads(path.read_text(encoding="utf-8"))
    assert first["last_dca_month"] == "2025-11"
    assert len(first["trades"]) == 1

    cli.cmd_dca(_args(path, end="2025-11-28"))     # 同月再跑
    again = json.loads(path.read_text(encoding="utf-8"))
    assert len(again["trades"]) == 1               # 沒有重複扣款
    assert "已經扣款過" in capsys.readouterr().out


def test_force_overrides_the_monthly_guard(tmp_path):
    path = tmp_path / "dca.json"
    cli.cmd_dca(_args(path, end="2025-11-03"))
    cli.cmd_dca(_args(path, end="2025-11-28", force=True))
    assert len(json.loads(path.read_text(encoding="utf-8"))["trades"]) == 2


def test_next_month_buys_again(tmp_path):
    path = tmp_path / "dca.json"
    cli.cmd_dca(_args(path, end="2025-11-03"))
    cli.cmd_dca(_args(path, end="2025-12-01"))
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["last_dca_month"] == "2025-12"
    assert len(data["trades"]) == 2


# ── 錢的帳要對 ──

def test_total_deposited_accumulates(tmp_path):
    """initial_cash 要等於「總投入」，report 的報酬率才是總損益/總投入。"""
    path = tmp_path / "dca.json"
    for d in ("2025-11-03", "2025-12-01", "2026-01-02"):
        cli.cmd_dca(_args(path, end=d))
    assert json.loads(path.read_text(encoding="utf-8"))["initial_cash"] == pytest.approx(30_000)


def test_money_is_never_lost(tmp_path):
    """每一塊投入的錢，最後都要在「持股成本 + 現金」裡找得到（只少掉手續費）。"""
    path = tmp_path / "dca.json"
    for d in ("2025-11-03", "2025-12-01", "2026-01-02"):
        cli.cmd_dca(_args(path, end=d))
    b = PersistentPaperBroker(path=str(path))
    cost = sum(p.shares * p.avg_price for p in b.positions())
    paid_fees = 3 * fees.MIN_FEE                      # 小額扣款由最低手續費主導
    assert cost + b.cash() == pytest.approx(30_000 - paid_fees, rel=0.02)


def test_unaffordable_month_still_keeps_the_deposit(tmp_path, capsys):
    """買不起 1 股時，入帳的錢必須落地保留——不落地就等於那個月的投入消失了。"""
    path = tmp_path / "dca.json"
    cli.cmd_dca(_args(path, amount=100.0, end="2025-11-03"))   # 100 元買不起台積電
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["cash"] == pytest.approx(100.0)
    assert data["initial_cash"] == pytest.approx(100.0)
    assert data["trades"] == []
    assert data["last_dca_month"] == "2025-11"                 # 這個月算處理過了
    assert "下個月累積後再買" in capsys.readouterr().out


def test_average_cost_falls_when_price_falls(tmp_path):
    """定期定額的核心機制：同樣的錢在低點買到更多股數，均價被拉低。"""
    path = tmp_path / "dca.json"
    cli.cmd_dca(_args(path, end="2025-11-03"))
    b1 = PersistentPaperBroker(path=str(path))
    avg1 = b1.account.positions["2330"].avg_price
    cli.cmd_dca(_args(path, end="2025-12-01"))     # 樣本資料這天較低
    b2 = PersistentPaperBroker(path=str(path))
    assert b2.account.positions["2330"].avg_price < avg1
    assert b2.account.positions["2330"].shares > b1.account.positions["2330"].shares
