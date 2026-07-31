"""績效報告的大盤對照：各帳戶用自己的起算日、空頭時揭露持股水位。"""
import pandas as pd
import pytest

import src.control as control


@pytest.fixture
def fake_taiex(monkeypatch):
    """合成大盤：6/22 起 47,742 一路跌到 7/30 的 39,933（重現 2026 年這段實況）。"""
    idx = pd.bdate_range("2026-06-22", "2026-07-30")
    # 線性下跌，讓不同起算日切出不同報酬
    vals = [47742 - (47742 - 39933) * i / (len(idx) - 1) for i in range(len(idx))]
    series = pd.Series(vals, index=idx)

    def _fake(starts):
        out = {}
        for sd in {s for s in starts if s}:
            sub = series[series.index >= pd.Timestamp(sd)]
            if len(sub) >= 2:
                out[sd] = float(series.iloc[-1]) / float(sub.iloc[0]) - 1
        return out

    monkeypatch.setattr(control, "_taiex_returns_by_start", _fake)
    return series


def _row(label, ret, start_date, cash=1000.0, mtm=10000.0):
    return {"label": label, "initial": mtm / (1 + ret), "cash": cash, "mtm": mtm,
            "ret": ret, "unreal": 0.0, "positions": [], "start_date": start_date}


def test_each_account_compared_against_its_own_window(fake_taiex):
    """晚開的帳戶不該被拿去比它沒參與的行情——兩帳戶的大盤基準要不同。"""
    rows = [_row("lynch", -0.1184, "2026-06-22"), _row("mid100", -0.0073, "2026-07-07")]
    msg = control._format_report(rows)
    lines = [ln for ln in msg.splitlines() if "同期大盤" in ln]
    assert len(lines) == 3  # 兩個帳戶各一行 + 合計一行
    assert "2026-06-22 起算" in msg and "2026-07-07 起算" in msg
    # 起算日晚的帳戶，其大盤基準跌幅應較小 (只涵蓋後半段)
    early = fake_taiex.iloc[-1] / fake_taiex.iloc[0] - 1
    late_first = fake_taiex[fake_taiex.index >= pd.Timestamp("2026-07-07")].iloc[0]
    late = fake_taiex.iloc[-1] / late_first - 1
    assert late > early


def test_position_ratio_disclosed_in_bear_market(fake_taiex):
    """空頭少賠有一部分只是沒滿倉，報告要講出來而不是全記在選股上。"""
    rows = [_row("lynch", -0.0834, "2026-06-22", cash=16255.0, mtm=62150.0)]
    msg = control._format_report(rows)
    assert "持股水位" in msg
    assert "非全是選股" in msg
    assert "74%" in msg  # 1 - 16255/62150


def test_no_position_ratio_note_when_market_up(monkeypatch):
    """多頭時不需要這句提醒 (少賠的邏輯不適用)。"""
    monkeypatch.setattr(control, "_taiex_returns_by_start", lambda s: {"2026-01-02": 0.08})
    msg = control._format_report([_row("lynch", 0.12, "2026-01-02", cash=1000.0, mtm=10000.0)])
    assert "領先大盤" in msg
    assert "持股水位" not in msg


def test_account_without_start_date_gets_no_benchmark_line(monkeypatch):
    monkeypatch.setattr(control, "_taiex_returns_by_start", lambda s: {})
    msg = control._format_report([_row("lynch", -0.05, None)])
    assert "同期大盤" not in msg
    assert "報酬 -5.00%" in msg


def test_benchmark_failure_returns_empty_not_raise(monkeypatch):
    """FinMind 掛掉時 _taiex_returns_by_start 要回 {}，讓報告其餘部分照常產出。"""
    import src.data.cache as cache

    def _boom(*a, **kw):
        raise RuntimeError("FinMind 掛了")
    monkeypatch.setattr(cache, "DiskCachingProvider", _boom)
    assert control._taiex_returns_by_start(["2026-06-22"]) == {}
    # 報告本身仍完整
    msg = control._format_report([_row("lynch", -0.05, "2026-06-22")])
    assert "報酬 -5.00%" in msg and "模擬盤績效" in msg


def test_no_start_dates_skips_api_entirely():
    """全部帳戶都沒起算日時不該打 API (省額度)。"""
    assert control._taiex_returns_by_start([None, None]) == {}
    assert control._taiex_returns_by_start([]) == {}
