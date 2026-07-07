"""通知模組測試：Discord webhook 格式轉換/分段、多通道聚合、向後相容。"""
import src.notify as notify
from src.notify.discord import DiscordNotifier
from src.notify.multi import MultiNotifier


class _RecordingDiscord(DiscordNotifier):
    """攔截 _post，不真的打網路。"""

    def __init__(self, webhook_url="https://discord.com/api/webhooks/x"):
        super().__init__(webhook_url=webhook_url)
        self.posts = []

    def _post(self, content):
        self.posts.append(content)
        return True


def test_discord_disabled_without_url(monkeypatch):
    monkeypatch.delenv("DISCORD_WEBHOOK_URL", raising=False)
    d = DiscordNotifier()
    assert not d.enabled
    assert d.send("hi") is False


def test_discord_converts_telegram_html_to_markdown():
    d = _RecordingDiscord()
    ok = d.send("<b>📈 lynch 策略訊號</b> PEG&lt;=1.2 且 &gt;0")
    assert ok
    assert d.posts == ["**📈 lynch 策略訊號** PEG<=1.2 且 >0"]


def test_discord_chunks_long_messages():
    d = _RecordingDiscord()
    d.send("x" * 4000)
    assert len(d.posts) == 3  # 1900+1900+200
    assert "".join(d.posts) == "x" * 4000


class _FakeChannel:
    def __init__(self, enabled, ok=True):
        self.enabled = enabled
        self.ok = ok
        self.sent = []

    def send(self, text):
        self.sent.append(text)
        return self.ok


def test_multi_sends_to_all_enabled_channels():
    a, b, off = _FakeChannel(True), _FakeChannel(True), _FakeChannel(False)
    m = MultiNotifier([a, b, off])
    assert m.enabled
    assert m.send("msg") is True
    assert a.sent == ["msg"] and b.sent == ["msg"] and off.sent == []


def test_multi_true_if_any_channel_succeeds():
    m = MultiNotifier([_FakeChannel(True, ok=False), _FakeChannel(True, ok=True)])
    assert m.send("msg") is True


def test_multi_disabled_when_no_channels():
    m = MultiNotifier([_FakeChannel(False)])
    assert not m.enabled


def test_backward_compat_factory(monkeypatch):
    # 無參數 → 多通道（Discord 有設定時 enabled）
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/x")
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    n = notify.TelegramNotifier()
    assert isinstance(n, MultiNotifier)
    assert n.enabled  # Telegram 沒設但 Discord 有 → 仍可通知

    # 帶參數 → 純 Telegram（原行為）
    t = notify.TelegramNotifier(token="t", chat_id="c")
    assert not isinstance(t, MultiNotifier)
    assert t.enabled
