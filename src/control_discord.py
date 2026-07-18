"""Discord bot 雙向控制 — 取代被凍結的 Telegram listener。

指令大腦（/status /budget /maxpos /pause /resume /holdings /sell）完全重用
control.py 的純函式，這支只負責 Discord 傳輸層（discord.py gateway）。

設定步驟（一次性，約 5 分鐘）：
1. https://discord.com/developers/applications → New Application → 取名
2. 左側 Bot 頁 → Reset Token → 複製 = DISCORD_BOT_TOKEN
   同頁往下捲 Privileged Gateway Intents → 開啟 **MESSAGE CONTENT INTENT**（必開！）
3. 左側 OAuth2 → URL Generator → Scopes 勾 bot →
   Bot Permissions 勾 View Channels / Send Messages / Read Message History →
   複製產生的邀請連結 → 瀏覽器開啟 → 邀進你的伺服器
4. Discord 使用者設定 → 進階 → 開啟「開發者模式」→
   右鍵你的頻道 → 複製頻道 ID = DISCORD_CHANNEL_ID
   （可選）右鍵自己的頭像 → 複製使用者 ID = DISCORD_USER_ID（多一道鎖，只聽你）
5. pip install -U discord.py，.env 加上述變數

安全：只回應指定頻道（+可選指定使用者）的訊息，其他一律忽略。
指令前綴：/ 或 !（Discord 打 / 會跳出斜線指令選單很煩，可改用 !status）。
"""
from __future__ import annotations

import asyncio
import os
from typing import Optional

from .control import (
    apply_command,
    handle_broker_command,
    is_broker_command,
    load_runtime,
    save_runtime,
    _try_broker,
)


_KNOWN_CMDS = {"budget", "maxpos", "maxpositions", "pause", "resume", "status",
               "help", "start", "holdings", "positions", "sell", "report", "pnl"}


def process_command(text: str, broker) -> str:
    """處理一行指令，回傳回覆文字（空字串=不回）。與 Telegram listener 同一套大腦。

    穩健性：整段包 try/except——指令出錯回一行錯誤訊息而不是靜默無回應
    （靜默最難除錯：分不清是「沒收到」「不認得」還是「壞了」）。
    """
    raw = (text or "").strip()
    if not raw:
        return ""
    # 前綴正規化：! 或 / 皆可，且容忍前綴後有空白（"! report" == "/report"）。
    if raw[0] in "!/":
        body = raw[1:].strip()
        if not body:
            return ""
        cmd_word = body.split()[0].lower()
        norm = "/" + body
    else:
        return ""  # 沒前綴＝一般聊天，不回避免吵

    try:
        if cmd_word not in _KNOWN_CMDS:
            return (f"❓ 不認得指令「{cmd_word}」。\n"
                    "可用：/status /report /holdings /pause /resume "
                    "/budget N /maxpos N /sell 2330｜傳 /help 看說明。\n"
                    "（若剛更新過程式，記得 sudo systemctl restart stockbot-discord-listen）")
        if is_broker_command(norm):
            return handle_broker_command(norm, broker)
        reply, cfg = apply_command(norm, load_runtime())
        if reply:
            save_runtime(cfg)
        return reply
    except Exception as e:  # 絕不靜默：把錯誤丟回頻道，方便手機上就看到問題
        import traceback
        traceback.print_exc()
        return f"⚠️ 指令「{cmd_word}」執行出錯：{type(e).__name__}: {e}"


def run_bot(simulation: bool = True, paper: bool = False, paper_path=None):
    """啟動 Discord bot 監聽（阻塞）。給 discord_listen.py 入口用。

    paper_path 與 Telegram poll_loop 相同：單一路徑或 [(標籤, 路徑), ...] 多帳戶。
    """
    try:
        import discord
    except ImportError:
        raise SystemExit("需要 discord.py 套件：pip install -U discord.py")

    token = os.getenv("DISCORD_BOT_TOKEN")
    channel_id_raw = os.getenv("DISCORD_CHANNEL_ID")
    if not token or not channel_id_raw:
        print("未設定 DISCORD_BOT_TOKEN / DISCORD_CHANNEL_ID，無法監聽。")
        return
    channel_id = int(channel_id_raw)
    user_id_raw = os.getenv("DISCORD_USER_ID")
    allowed_user = int(user_id_raw) if user_id_raw else None

    # 沒有 Shioaji 憑證也能跑（/holdings /sell 暫不可用，設定類指令仍正常）
    broker = _try_broker(simulation, paper=paper, paper_path=paper_path)

    intents = discord.Intents.default()
    intents.message_content = True  # 需在 Developer Portal 開 MESSAGE CONTENT INTENT
    client = discord.Client(intents=intents)

    @client.event
    async def on_ready():
        ch = client.get_channel(channel_id)
        if ch is not None:
            await ch.send("🤖 Discord 控制器已上線。傳 /help（或 !help）看指令、/status 查設定。")
        print(f"監聽中 (bot: {client.user}, channel: {channel_id}) ... Ctrl+C 結束")

    @client.event
    async def on_message(msg):
        if msg.author == client.user:
            return
        if msg.channel.id != channel_id:
            return  # 安全：只聽指定頻道
        if allowed_user is not None and msg.author.id != allowed_user:
            return  # 安全：只聽指定使用者
        # broker 指令可能讀檔/打券商 API（同步阻塞），丟到執行緒避免卡住事件迴圈
        reply = await asyncio.to_thread(process_command, msg.content, broker)
        if reply:
            for i in range(0, len(reply), 1900):  # Discord 單則上限 2000 字
                await msg.channel.send(reply[i:i + 1900])
            print(f"[discord] 指令「{msg.content}」→ {reply.splitlines()[0]}")

    client.run(token)
