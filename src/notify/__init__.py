"""通知 (notifications)：Telegram + Discord 多通道。"""
from .discord import DiscordNotifier
from .multi import MultiNotifier, build_notifier
from .telegram import TelegramNotifier as _TelegramOnly


def TelegramNotifier(*args, **kwargs):
    """向後相容工廠：無參數呼叫時回傳「多通道」notifier（Telegram+Discord，
    看環境變數哪個有設定）——main.py / control.py 既有的 `TelegramNotifier()`
    呼叫點不用改，就自動獲得 Discord 支援（Telegram bot 被凍結時 Discord 頂上）。

    明確帶參數（token/chat_id）者視為指定要純 Telegram，維持原行為。
    """
    if args or kwargs:
        return _TelegramOnly(*args, **kwargs)
    return build_notifier()


__all__ = ["TelegramNotifier", "DiscordNotifier", "MultiNotifier", "build_notifier"]
