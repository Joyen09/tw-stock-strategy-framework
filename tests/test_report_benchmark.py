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


def test_no_cash_caveat_when_market_up(monkeypatch):
    """多頭時「空頭少賠靠現金」的因果不成立，不該出現；但水位本身仍是有用資訊。"""
    monkeypatch.setattr(control, "_taiex_returns_by_start", lambda s: {"2026-01-02": 0.08})
    msg = control._format_report([_row("lynch", 0.12, "2026-01-02", cash=1000.0, mtm=10000.0)])
    assert "領先大盤" in msg
    assert "持股水位 90%" in msg
    assert "非全是選股" not in msg


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


def _pos(sym, shares, avg, last):
    return {"symbol": sym, "shares": shares, "avg": avg, "last": last,
            "priced": True, "pnl": shares * (last - avg)}


def _row2(label, initial, mtm, cash, unreal, positions, start="2026-06-20"):
    return {"label": label, "initial": initial, "cash": cash, "mtm": mtm,
            "ret": mtm / initial - 1, "unreal": unreal, "positions": positions,
            "start_date": start}


def test_realized_and_unrealized_are_split(monkeypatch):
    """帳面浮盈掩蓋已砍掉的實虧時，兩者要分開顯示——意義完全不同。"""
    monkeypatch.setattr(control, "_taiex_returns_by_start", lambda s: {})
    # 初始 17,903 → 市值 23,905，其中未實現 +6,236 → 已實現應為 -234
    row = _row2("mid100", 17903, 23905, 3072, 6236, [_pos("2006", 103, 72.4, 80.9)])
    msg = control._format_report([row])
    assert "未實現 +6,236" in msg
    assert "已實現 -234" in msg
    assert "已實現 -234｜未實現 +6,236" in msg  # 合計行也要有


def test_single_position_over_half_triggers_warning(monkeypatch):
    """一檔就佔帳戶一半以上：績效被它綁架，要另外拉一行示警。"""
    monkeypatch.setattr(control, "_taiex_returns_by_start", lambda s: {})
    row = _row2("mid100", 17903, 23905, 3072, 6236,
                [_pos("2006", 103, 72.4, 80.9), _pos("2059", 1, 7140.0, 12500.0)])
    msg = control._format_report([row])
    assert "2059 佔該帳戶 52%" in msg
    assert "單一持股主導" in msg
    assert "佔52%" in msg  # 該筆後面也標佔比


def test_normal_concentration_notes_but_does_not_warn(monkeypatch):
    """只持有 2~3 檔時三成是正常配置：標佔比即可，不該拉警告行洗版。"""
    monkeypatch.setattr(control, "_taiex_returns_by_start", lambda s: {})
    row = _row2("lynch", 29940, 27022, 2989, 1736,
                [_pos("2880", 186, 40.1, 39.0), _pos("2890", 194, 38.6, 40.5),
                 _pos("1303", 43, 170.5, 207.5)])
    msg = control._format_report([row])
    assert "佔33%" in msg          # 有標佔比
    assert "單一持股主導" not in msg  # 但沒有警告行


def test_cash_caveat_only_when_meaningfully_underinvested(monkeypatch):
    """持股水位 89% 時不能再說「少賠是因為沒滿倉」——那句話已不成立。"""
    monkeypatch.setattr(control, "_taiex_returns_by_start", lambda s: {"2026-06-20": -0.0404})
    high = _row2("a", 67803, 68136, 7619, 8446, [_pos("2330", 10, 1000.0, 1000.0)])
    msg = control._format_report([high])
    assert "持股水位 89%" in msg
    assert "非全是選股" not in msg

    monkeypatch.setattr(control, "_taiex_returns_by_start", lambda s: {"2026-06-20": -0.1636})
    low = _row2("a", 67803, 62150, 16255, -5653, [_pos("2330", 10, 1000.0, 1000.0)])
    msg2 = control._format_report([low])
    assert "非全是選股" in msg2  # 26% 現金時這句仍成立
