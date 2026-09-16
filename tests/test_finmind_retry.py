"""FinMind 長跑韌性：斷線要重試、撞額度要等（2026-09-16 的回歸測試）。

事故：三關驗證一次要打幾百次 API、跑幾十分鐘，中途只要斷一次就整份工作報銷。
連續好幾次驗證都是這樣死的，每次都得從頭再來。

兩種錯誤要分開處理：
- 一般網路錯誤 → 指數退避重試，通常第二次就過
- 撞到 API 額度 → 免費版是滾動 60 分鐘 600 次，等幾分鐘額度就會回補，
  所以要「等」不是「死」

測試不會真的 sleep（monkeypatch 掉），只驗決策邏輯與等待長度。
"""
import pytest

from src.data import finmind
from src.data.finmind import FinMindProvider, _is_rate_limit


@pytest.fixture
def no_sleep(monkeypatch):
    """攔截 sleep，記下每次等多久，測試才不會真的卡幾分鐘。"""
    slept = []
    monkeypatch.setattr(finmind.time, "sleep", lambda s: slept.append(s))
    return slept


def _call(fn, **kw):
    """_call 沒有用到 self，直接以 None 當 self 呼叫，免去建構 FinMindProvider
    （那需要真的裝 FinMind 套件）。"""
    return FinMindProvider._call(None, fn, what="測試", **kw)


class _Flaky:
    """前 n 次丟指定例外，之後成功。"""

    def __init__(self, fails, exc):
        self.fails = fails
        self.exc = exc
        self.calls = 0

    def __call__(self, **kw):
        self.calls += 1
        if self.calls <= self.fails:
            raise self.exc
        return "ok"


# ── 錯誤分類 ──

def test_rate_limit_is_detected():
    assert _is_rate_limit(Exception("402 Payment Required: user request limit reached"))
    assert _is_rate_limit(Exception("429 Too Many Requests"))


def test_ordinary_network_error_is_not_mistaken_for_rate_limit():
    """一般斷線要快速重試，不能被當成額度問題傻等 5 分鐘。"""
    assert not _is_rate_limit(Exception("Connection reset by peer"))
    assert not _is_rate_limit(Exception("Read timed out"))


# ── 一般錯誤：退避重試 ──

def test_transient_error_recovers(no_sleep):
    fn = _Flaky(1, ConnectionError("Connection reset by peer"))
    assert _call(fn) == "ok"
    assert fn.calls == 2


def test_backoff_is_exponential(no_sleep):
    _call(_Flaky(2, ConnectionError("boom")))
    assert no_sleep == [finmind.RETRY_BASE_SLEEP, finmind.RETRY_BASE_SLEEP * 2]


def test_gives_up_after_retry_budget(no_sleep):
    """不能無限重試——真的壞掉時要讓上層知道。"""
    fn = _Flaky(99, ConnectionError("boom"))
    with pytest.raises(ConnectionError):
        _call(fn)
    assert fn.calls == finmind.RETRY_ATTEMPTS


# ── 額度：等而不是死 ──

def test_rate_limit_waits_and_then_succeeds(no_sleep):
    fn = _Flaky(1, RuntimeError("402: user request limit reached"))
    assert _call(fn) == "ok"
    assert no_sleep == [finmind.RATE_LIMIT_SLEEP]   # 等的是長等待，不是幾秒退避


def test_rate_limit_waits_do_not_consume_retry_budget(no_sleep):
    """撞額度等很多輪之後，一般重試次數仍該是滿的——兩種預算不可混用。

    混用的話：等了 3 輪額度就把 4 次重試用完，之後一個小斷線就整份工作報銷。
    """
    fails = finmind.RETRY_ATTEMPTS + 1
    fn = _Flaky(fails, RuntimeError("429 too many requests"))
    assert _call(fn) == "ok"
    assert fn.calls == fails + 1
    assert len(no_sleep) == fails


def test_rate_limit_eventually_gives_up(no_sleep):
    """額度也不能無限等，否則排程會永遠掛在那裡。"""
    fn = _Flaky(999, RuntimeError("402 limit"))
    with pytest.raises(RuntimeError):
        _call(fn)
    assert len(no_sleep) == finmind.RATE_LIMIT_WAITS


def test_success_never_sleeps(no_sleep):
    assert _call(lambda **kw: "ok") == "ok"
    assert no_sleep == []
