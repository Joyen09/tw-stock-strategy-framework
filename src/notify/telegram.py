"""Telegram 通知 — 用標準函式庫 (urllib)，不需額外套件。

設定：
1. 在 Telegram 找 @BotFather，輸入 /newbot 建立機器人，拿到 token。
2. 對你的新 bot 傳任意一句話 (例如 hi)。
3. 取得 chat_id：  python main.py notify-chatid
4. 設環境變數：
   export TELEGRAM_BOT_TOKEN="123456:ABC..."
   export TELEGRAM_CHAT_ID="你的 chat id"
"""
from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from typing import List, Optional

API = "https://api.telegram.org"


class TelegramNotifier:
    def __init__(self, token: Optional[str] = None, chat_id: Optional[str] = None, timeout: int = 10):
        self.token = token or os.getenv("TELEGRAM_BOT_TOKEN")
        self.chat_id = chat_id or os.getenv("TELEGRAM_CHAT_ID")
        self.timeout = timeout

    @property
    def enabled(self) -> bool:
        return bool(self.token and self.chat_id)

    def send(self, text: str) -> bool:
        """送出訊息，成功回 True；未設定或失敗回 False (不丟例外，避免影響下單流程)。"""
        if not self.token or not self.chat_id:
            return False
        url = f"{API}/bot{self.token}/sendMessage"
        data = urllib.parse.urlencode(
            {"chat_id": self.chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": "true"}
        ).encode()
        try:
            with urllib.request.urlopen(url, data=data, timeout=self.timeout) as r:
                return r.status == 200
        except Exception as e:  # 通知失敗不應中斷主流程
            print(f"[Telegram] 通知失敗: {e}")
            return False

    def get_chat_ids(self) -> List[dict]:
        """呼叫 getUpdates，列出曾跟 bot 對話的 chat id (用來查自己的 chat_id)。"""
        if not self.token:
            raise RuntimeError("未設定 TELEGRAM_BOT_TOKEN")
        url = f"{API}/bot{self.token}/getUpdates"
        with urllib.request.urlopen(url, timeout=self.timeout) as r:
            payload = json.loads(r.read().decode())
        seen, out = set(), []
        for upd in payload.get("result", []):
            msg = upd.get("message") or upd.get("channel_post") or {}
            chat = msg.get("chat", {})
            cid = chat.get("id")
            if cid is not None and cid not in seen:
                seen.add(cid)
                out.append({"chat_id": cid, "name": chat.get("title") or chat.get("username") or chat.get("first_name", "")})
        return out
