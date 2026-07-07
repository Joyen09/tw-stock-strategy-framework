#!/usr/bin/env python
"""Discord 雙向控制入口 — 取代被凍結的 Telegram listener。

用法（與 main.py listen 的參數格式相同）：
  python discord_listen.py --paper \
    --paper-file "lynch=paper_account.json,livermore=paper_livermore.json,lynch-mid100=paper_lynch_mid100.json"

需要環境變數（設定步驟見 src/control_discord.py 開頭註解）：
  DISCORD_BOT_TOKEN / DISCORD_CHANNEL_ID / (可選) DISCORD_USER_ID
"""
import argparse
import os


def _parse_paper_specs(arg: str, root: str):
    """'標籤=檔名,標籤=檔名' → [(標籤, 絕對路徑), ...]；單一項回傳純路徑（沿用舊行為）。"""
    specs = []
    for item in arg.split(","):
        item = item.strip()
        if not item:
            continue
        if "=" in item:
            label, fname = item.split("=", 1)
        else:
            label = os.path.splitext(os.path.basename(item))[0].removeprefix("paper_")
            fname = item
        specs.append((label.strip(), os.path.join(root, fname.strip())))
    return specs if len(specs) > 1 else (specs[0][1] if specs else None)


def main():
    ap = argparse.ArgumentParser(description="Discord 雙向控制（/status /pause /holdings /sell...）")
    ap.add_argument("--paper", action="store_true",
                    help="/holdings /sell 對本地持久化模擬盤帳戶")
    ap.add_argument("--paper-file",
                    default="lynch=paper_account.json,livermore=paper_livermore.json,lynch-mid100=paper_lynch_mid100.json",
                    help="模擬盤帳戶，逗號分隔可多個（標籤=檔名）")
    ap.add_argument("--real-account", action="store_true",
                    help="/holdings /sell 用 Shioaji 實單帳戶（預設模擬盤）")
    args = ap.parse_args()

    root = os.path.dirname(os.path.abspath(__file__))
    paper_path = _parse_paper_specs(args.paper_file, root)

    from src.control_discord import run_bot
    try:
        run_bot(simulation=not args.real_account, paper=args.paper, paper_path=paper_path)
    except KeyboardInterrupt:
        print("\n已停止監聽。")


if __name__ == "__main__":
    main()
