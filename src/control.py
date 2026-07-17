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
        return ("可用指令：\n/budget 60000\n/maxpos 5\n/pause\n/resume\n/status\n"
                "/holdings（看持倉，成本計）\n/report（看績效，市值計含報酬率）\n"
                "/sell 2330 或 /sell all（手動賣）", cfg)
    return "", cfg  # 不認得的訊息不回（避免洗版）


# --- 需要券商連線的指令 (/holdings, /sell)；apply_command 只處理設定類 ---
def is_broker_command(text: str) -> bool:
    parts = (text or "").strip().lstrip("/").split()
    return bool(parts) and parts[0].lower() in ("holdings", "positions", "sell", "report", "pnl")


def handle_broker_command(text: str, broker) -> str:
    parts = (text or "").strip().split()
    cmd = parts[0].lower().lstrip("/")
    arg = parts[1] if len(parts) > 1 else None
    if broker is None:
        return "此指令需要 Shioaji 連線；等你 API 金鑰設定好、模擬盤接上後就能用。"

    # 本地持久化模擬盤：重讀磁碟，讓 /holdings /sell 反映排程 scan 這段時間寫入的最新狀態。
    if hasattr(broker, "reload"):
        broker.reload()

    # 多帳戶模擬盤 (雙策略各自記帳)：合併顯示、賣出自動路由到持有的帳戶。
    if hasattr(broker, "brokers"):
        return _handle_multi_paper(cmd, arg, broker)

    # PaperBroker 系需帶價格才能撮合 (賣出用持倉均價成交)；Shioaji 用 None=市價。
    is_paper = hasattr(broker, "account")

    if cmd in ("holdings", "positions"):
        ps = [p for p in broker.positions() if p.shares > 0]
        if not ps:
            return "📭 目前無持倉"
        lines = ["📦 目前持倉："]
        for p in ps:
            lines.append(f"　{p.symbol} {p.shares} 股 @ {p.avg_price:.1f}")
        try:
            lines.append(f"現金 {broker.cash():,.0f}")
        except Exception:
            pass
        return "\n".join(lines)

    if cmd == "sell":
        if not arg:
            return "用法：/sell 2330（賣某檔）或 /sell all（全部賣出）"
        from src.broker.base import Order, OrderSide

        ps = [p for p in broker.positions() if p.shares > 0]
        targets = ps if arg.lower() == "all" else [p for p in ps if p.symbol == arg]
        if not targets:
            return f"找不到 {arg} 的持倉"
        done = []
        for p in targets:
            price = p.avg_price if is_paper else None  # 模擬盤用均價撮合；實單/Shioaji 用市價
            broker.place_order(Order(p.symbol, OrderSide.SELL, p.shares, price, "Telegram 手動賣出"))
            done.append(f"{p.symbol} {p.shares} 股")
        return "🔴 已送出賣單：\n　" + "\n　".join(done)

    return ""


def _handle_multi_paper(cmd: str, arg, broker) -> str:
    """多帳戶模擬盤的 /holdings /sell：合起來一則訊息看總帳。"""
    if cmd in ("holdings", "positions"):
        sections = broker.holdings_by_account()
        any_pos = any(ps for _, ps, _, _ in sections)
        lines = ["📦 目前持倉（各策略帳戶合計）：" if any_pos else "📭 目前無持倉"]
        for label, ps, cash, exists in sections:
            if not exists and not ps:
                continue  # 還沒開始跑的帳戶不顯示，避免誤導
            lines.append(f"【{label}】")
            for p in ps:
                lines.append(f"　{p.symbol} {p.shares} 股 @ {p.avg_price:.1f}"
                             f"（{p.shares * p.avg_price:,.0f} 元）")
            if not ps:
                lines.append("　(無持倉)")
            lines.append(f"　現金 {cash:,.0f}")
        lines.append(f"💰 總資產（成本計）：{broker.total_equity():,.0f}")
        return "\n".join(lines)

    if cmd == "sell":
        if not arg:
            return "用法：/sell 2330（賣某檔，自動找持有的帳戶）或 /sell all（全部帳戶出清）"
        done = broker.sell(arg)
        if not done:
            return f"找不到 {arg} 的持倉"
        return "🔴 已送出賣單：\n　" + "\n　".join(
            f"[{label}] {sym} {shares} 股" for label, sym, shares in done)

    if cmd in ("report", "pnl"):
        return _format_report(broker.report(_latest_price_fn()))

    return ""


def _latest_price_fn():
    """回傳 price_fn(symbol)->最新收盤價 或 None。用磁碟快取的 FinMind，
    同日重查 0 請求；抓不到（無 token/停牌）回 None，report 會退回用成本價（不灌水）。"""
    try:
        from src.data.finmind import FinMindProvider
        from src.data.cache import DiskCachingProvider
    except Exception:
        return lambda sym: None
    try:
        provider = DiskCachingProvider(FinMindProvider())
    except Exception:
        return lambda sym: None
    # 用「今天」當 end；start 取近兩週足夠抓到最後一個交易日收盤
    end = _today_str()
    start = _days_ago_str(14)

    def _price(sym: str):
        try:
            df = provider.history(sym, start, end)
            if df is None or df.empty:
                return None
            return float(df["close"].iloc[-1])
        except Exception:
            return None

    return _price


def _today_str() -> str:
    import datetime as _dt
    return _dt.date.today().isoformat()


def _days_ago_str(n: int) -> str:
    import datetime as _dt
    return (_dt.date.today() - _dt.timedelta(days=n)).isoformat()


def _format_report(rows) -> str:
    """把 MultiPaperBroker.report() 的結果排成一則績效訊息。"""
    if not rows:
        return "📭 尚無已建檔的模擬帳戶，還沒有績效可看。"
    lines = ["📊 模擬盤績效（市值計，最新收盤價）："]
    tot_init = tot_mtm = 0.0
    any_estimated = False
    for r in rows:
        tot_init += r["initial"]
        tot_mtm += r["mtm"]
        sign = "🟢" if r["ret"] >= 0 else "🔴"
        lines.append(f"【{r['label']}】{sign} 報酬 {r['ret']:+.2%}"
                     f"（市值 {r['mtm']:,.0f} / 初始 {r['initial']:,.0f}）")
        for d in r["positions"]:
            mark = "" if d["priced"] else "⚠成本價"
            lines.append(f"　{d['symbol']} {d['shares']}股：{d['pnl']:+,.0f} 元 "
                         f"(現 {d['last']:.1f} / 成本 {d['avg']:.1f}){mark}")
            if not d["priced"]:
                any_estimated = True
        if not r["positions"]:
            lines.append("　(無持倉，全現金)")
        lines.append(f"　現金 {r['cash']:,.0f}｜未實現損益 {r['unreal']:+,.0f}")
    tot_ret = (tot_mtm / tot_init - 1) if tot_init > 0 else 0.0
    tsign = "🟢" if tot_ret >= 0 else "🔴"
    lines.append(f"━━━━━━━━━━")
    lines.append(f"💰 三帳戶合計 {tsign} {tot_ret:+.2%}（市值 {tot_mtm:,.0f} / 初始 {tot_init:,.0f}）")
    if any_estimated:
        lines.append("⚠標記者抓不到最新價，暫用成本價（顯示 0 損益）")
    return "\n".join(lines)


def _try_broker(simulation: bool = True, paper: bool = False, paper_path=None):
    if paper:
        # 本地持久化模擬盤。paper_path 可以是：
        #   單一路徑字串（單帳戶，維持舊行為）
        #   [(標籤, 路徑), ...] 多帳戶 → 用 MultiPaperBroker 合併檢視/賣出
        if isinstance(paper_path, (list, tuple)):
            from src.broker.multi_paper import MultiPaperBroker
            return MultiPaperBroker(list(paper_path))
        from src.broker.persistent_paper import PersistentPaperBroker
        return PersistentPaperBroker(path=paper_path)
    try:
        from src.broker.shioaji_broker import ShioajiBroker
        return ShioajiBroker(simulation=simulation)
    except Exception as e:
        print(f"[listen] 未連 Shioaji（{e}）；/holdings /sell 暫不可用，設定類指令仍正常")
        return None


# --- Telegram 長輪詢監聽 ---
API = "https://api.telegram.org"


def _get_updates(token: str, offset: Optional[int], timeout: int = 30):
    q = {"timeout": timeout}
    if offset is not None:
        q["offset"] = offset
    url = f"{API}/bot{token}/getUpdates?" + urllib.parse.urlencode(q)
    with urllib.request.urlopen(url, timeout=timeout + 10) as r:
        return json.loads(r.read().decode()).get("result", [])


def poll_loop(simulation: bool = True, paper: bool = False, paper_path=None):
    # paper_path：單一路徑字串，或 [(標籤, 路徑), ...] 多帳戶 (見 _try_broker)
    """持續監聽 Telegram 指令（阻塞），只處理設定好的 chat_id。給 `main.py listen` 用。"""
    from src.notify import TelegramNotifier

    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("未設定 TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID，無法監聽。")
        return
    notifier = TelegramNotifier()
    # 沒有 API 也能跑，只是 /holdings /sell 暫不可用；paper=True 則對本地持久化模擬盤帳戶
    broker = _try_broker(simulation, paper=paper, paper_path=paper_path)
    notifier.send("🤖 控制器已上線。傳 /help 看指令、/status 查設定。")
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
            if is_broker_command(text):
                reply = handle_broker_command(text, broker)
            else:
                reply, cfg = apply_command(text, load_runtime())
                if reply:
                    save_runtime(cfg)
            if reply:
                notifier.send(reply)
                print(f"[listen] 指令「{text}」→ {reply.splitlines()[0]}")
