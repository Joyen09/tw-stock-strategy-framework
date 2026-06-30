"""執行期設定 + Telegram 雙向控制。

讓你用 Telegram 傳指令動態調整參數（預算、最大檔數、暫停），不必改程式或重設排程。
- runtime.json 存可動態覆寫的設定（已被 .gitignore 忽略）。
- scan 每次執行會讀 runtime.json，覆寫對應參數。
- `python main.py listen` 持續監聽 Telegram 指令並更新 runtime.json（只接受你的 chat_id）。

支援指令：
  /budget 60000   單檔預算改成 60000
  /maxpos 5       最多持有檔數改成 5
  /pause          暫停買進（仍會執行賣出/出場）
  /resume         恢復買進
  /status         查目前設定
"""
from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request
from typing import Optional, Tuple

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNTIME_FILE = os.path.join(_ROOT, "runtime.json")

DEFAULTS = {"budget": None, "max_positions": None, "paused": False}


def load_runtime() -> dict:
    """讀取執行期設定；檔案不存在或壞掉時回預設。"""
    try:
        with open(RUNTIME_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return {**DEFAULTS, **data}
    except Exception:
        return dict(DEFAULTS)


def save_runtime(cfg: dict) -> None:
    keep = {k: cfg.get(k) for k in DEFAULTS}
    with open(RUNTIME_FILE, "w", encoding="utf-8") as f:
        json.dump(keep, f, ensure_ascii=False, indent=2)


def apply_command(text: str, cfg: dict) -> Tuple[str, dict]:
    """解析一行指令，回傳 (要回覆的訊息, 更新後的設定)。純函式，方便測試。"""
    cfg = {**DEFAULTS, **cfg}
    parts = (text or "").strip().split()
    if not parts:
        return "", cfg
    cmd = parts[0].lower().lstrip("/")
    arg = parts[1] if len(parts) > 1 else None

    if cmd == "budget":
        try:
            v = int(float(arg))
            if v <= 0:
                raise ValueError
            cfg["budget"] = v
            return f"✅ 單檔預算改為 {v:,}", cfg
        except (TypeError, ValueError):
            return "用法：/budget 60000（單檔最大投入金額）", cfg
    if cmd in ("maxpos", "maxpositions"):
        try:
            v = int(arg)
            if v <= 0:
                raise ValueError
            cfg["max_positions"] = v
            return f"✅ 最多持有檔數改為 {v}", cfg
        except (TypeError, ValueError):
            return "用法：/maxpos 5（最多同時持有幾檔）", cfg
    if cmd == "pause":
        cfg["paused"] = True
        return "⏸ 已暫停買進（仍會執行賣出/出場）", cfg
    if cmd == "resume":
        cfg["paused"] = False
        return "▶️ 已恢復買進", cfg
    if cmd == "status":
        b = f"{cfg['budget']:,}" if cfg.get("budget") else "(用指令預設)"
        m = cfg["max_positions"] if cfg.get("max_positions") else "(用指令預設)"
        p = "暫停中" if cfg.get("paused") else "運作中"
        return f"📊 目前設定\n單檔預算：{b}\n最多檔數：{m}\n狀態：{p}", cfg
    if cmd in ("help", "start"):
        return ("可用指令：\n/budget 60000\n/maxpos 5\n/pause\n/resume\n/status", cfg)
    return "", cfg  # 不認得的訊息不回（避免洗版）


# --- Telegram 長輪詢監聽 ---
API = "https://api.telegram.org"


def _get_updates(token: str, offset: Optional[int], timeout: int = 30):
    q = {"timeout": timeout}
    if offset is not None:
        q["offset"] = offset
    url = f"{API}/bot{token}/getUpdates?" + urllib.parse.urlencode(q)
    with urllib.request.urlopen(url, timeout=timeout + 10) as r:
        return json.loads(r.read().decode()).get("result", [])


def poll_loop():
    """持續監聽 Telegram 指令（阻塞），只處理設定好的 chat_id。給 `main.py listen` 用。"""
    from src.notify import TelegramNotifier

    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("未設定 TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID，無法監聽。")
        return
    notifier = TelegramNotifier()
    notifier.send("🤖 控制器已上線，可傳 /status 查設定、/budget 60000 改預算。")
    print("監聽中... (Ctrl+C 結束)")

    offset = None
    while True:
        try:
            updates = _get_updates(token, offset)
        except Exception as e:
            print(f"[listen] 取訊息失敗，5 秒後重試：{e}")
            time.sleep(5)
            continue
        for u in updates:
            offset = u["update_id"] + 1
            msg = u.get("message") or {}
            if str(msg.get("chat", {}).get("id")) != str(chat_id):
                continue  # 安全：只聽自己的 chat_id
            text = msg.get("text", "")
            reply, cfg = apply_command(text, load_runtime())
            if reply:
                save_runtime(cfg)
                notifier.send(reply)
                print(f"[listen] 指令「{text}」→ {reply.splitlines()[0]}")
