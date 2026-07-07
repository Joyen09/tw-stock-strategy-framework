"""多通道通知聚合：Telegram / Discord 哪個有設定就發哪個，都有就都發。

為什麼要聚合而不是二選一：通知通道會出事（Telegram bot 被凍結就是實例），
多通道 = 任一通道活著訊息就到得了手機；之後要加 LINE/email 也是加一個 channel。
"""
from __future__ import annotations

from typing import List

from .discord import DiscordNotifier
from .telegram import TelegramNotifier


class MultiNotifier:
    def __init__(self, channels: List):
        self._all = channels
        self.channels = [c for c in channels if getattr(c, "enabled", False)]

    @property
    def enabled(self) -> bool:
        return bool(self.channels)

    def send(self, text: str) -> bool:
        """發到所有已設定的通道；任一成功就回 True（訊息有到就算數）。"""
        ok = False
        for c in self.channels:
            ok = c.send(text) or ok
        return ok

    def get_chat_ids(self):
        """委派給 Telegram 通道（notify-chatid 指令用）。"""
        for c in self._all:
            if isinstance(c, TelegramNotifier):
                return c.get_chat_ids()
        raise RuntimeError("未設定 TELEGRAM_BOT_TOKEN")


def build_notifier() -> MultiNotifier:
    """依環境變數組出多通道 notifier：
    TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID → Telegram；DISCORD_WEBHOOK_URL → Discord。"""
    return MultiNotifier([TelegramNotifier(), DiscordNotifier()])
