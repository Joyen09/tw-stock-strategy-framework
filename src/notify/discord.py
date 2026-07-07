"""Discord 通知 — 用 Webhook（標準函式庫 urllib，不需額外套件、不需建 bot）。

設定（1 分鐘搞定）：
1. Discord 開一個自己的伺服器（或用現有的），選一個頻道
2. 頻道設定 ⚙ → 整合 → Webhook → 新增 Webhook → 複製 Webhook URL
3. 加進 ~/stock/.env：
   DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/....."
4. 測試：python main.py notify-test

格式轉換：框架內部訊息用 Telegram 的 HTML 標記（<b>粗體</b>），Discord 用
Markdown——send() 會自動把 <b>→**、<i>→* 並還原 HTML 跳脫字元。
Discord 單則上限 2000 字，超長自動分段送出。
"""
from __future__ import annotations

import html as _html
import json
import os
import re
import urllib.request
from typing import Optional

_CHUNK = 1900  # 留餘裕給分段標記


class DiscordNotifier:
    def __init__(self, webhook_url: Optional[str] = None, timeout: int = 10):
        self.webhook_url = webhook_url or os.getenv("DISCORD_WEBHOOK_URL")
        self.timeout = timeout

    @property
    def enabled(self) -> bool:
        return bool(self.webhook_url)

    @staticmethod
    def _to_markdown(text: str) -> str:
        """Telegram HTML 標記 → Discord Markdown，並還原 &lt; &amp; 等跳脫。"""
        text = re.sub(r"</?b>", "**", text)
        text = re.sub(r"</?i>", "*", text)
        text = re.sub(r"</?code>", "`", text)
        return _html.unescape(text)

    def _post(self, content: str) -> bool:
        """POST 一段訊息到 webhook。獨立成方法方便測試替換。"""
        req = urllib.request.Request(
            self.webhook_url,
            data=json.dumps({"content": content}).encode(),
            headers={"Content-Type": "application/json", "User-Agent": "tw-stock-bot"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            return r.status in (200, 204)  # webhook 成功回 204 No Content

    def send(self, text: str) -> bool:
        """送出訊息，成功回 True；未設定或失敗回 False (不丟例外，避免影響下單流程)。"""
        if not self.webhook_url:
            return False
        content = self._to_markdown(text)
        try:
            ok = True
            for i in range(0, len(content), _CHUNK):
                ok = self._post(content[i:i + _CHUNK]) and ok
            return ok
        except Exception as e:  # 通知失敗不應中斷主流程
            print(f"[Discord] 通知失敗: {e}")
            return False
