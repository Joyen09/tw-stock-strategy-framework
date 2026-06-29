# 部署到 GCP VM（取代本機跑）

## 為什麼放 VM 很划算

這個框架是**「跑一次→算完→退出」**的排程程式，不是 24 小時常駐 bot：

- **平常閒置時 CPU / 記憶體都是 0**（程式已結束）。
- 每次掃描只花幾秒、峰值記憶體約 120–180MB。
- 你截圖那台 `pionex-bot` CPU 才 0.05~0.25 核，幾乎全閒 → **可直接共用同一台**，不必另開 VM。
- 搬上 VM 後，本機那三個工作完全不受影響。

建議規格：`e2-small`(2GB) 最穩；`e2-micro`(1GB) 也能跑（已在 service 裡限制記憶體上限 400MB，避免影響同台其他服務）。

---

## 步驟一：把程式放上 VM

```bash
# SSH 進你的 VM（GCP Console 點 SSH，或 gcloud）
gcloud compute ssh pionex-bot --zone=us-west1-a

# 在 VM 上 clone（用你的 repo 網址）
git clone <你的 repo 網址> ~/stock
cd ~/stock

# 一鍵安裝（建立 venv、裝 pandas/numpy）
bash deploy/setup_vm.sh
```

## 步驟二：先回測 / dry-run 測試

```bash
cd ~/stock && source .venv/bin/activate

python main.py backtest --strategy buffett        # 確認能跑
python main.py scan --strategy oneil              # dry-run，只印出會下的單
```

## 步驟三（選用）：接真實資料與下單金鑰

```bash
pip install FinMind shioaji

# 金鑰寫進 .env（已被 .gitignore 忽略，chmod 600 只有自己能讀）
cat > ~/stock/.env <<'EOF'
FINMIND_TOKEN=你的token
SHIOAJI_API_KEY=...
SHIOAJI_SECRET_KEY=...
# Telegram 通知（見步驟五）
TELEGRAM_BOT_TOKEN=123456:ABC...
TELEGRAM_CHAT_ID=你的chat_id
EOF
chmod 600 ~/stock/.env
```

## 步驟五：Telegram 通知設定

1. 在 Telegram 搜尋 **@BotFather**，輸入 `/newbot`，照指示命名，拿到 **bot token**。
2. 在 Telegram 點開你的新 bot，對它**傳一句話**（例如 `hi`）。
3. 在 VM 上查自己的 chat_id：
   ```bash
   cd ~/stock && source .venv/bin/activate
   export TELEGRAM_BOT_TOKEN="上一步拿到的 token"
   python main.py notify-chatid          # 會印出 chat_id
   ```
4. 把 `TELEGRAM_BOT_TOKEN` 與 `TELEGRAM_CHAT_ID` 填進 `~/stock/.env`，然後測試：
   ```bash
   python main.py notify-test            # 手機應收到「測試訊息」
   ```

之後只要掃描掃到買/賣訊號，就會自動推播到你的 Telegram（沒訊號不推，不會洗版）。

> ⚠️ 第一次務必 `simulation=True` + `dry_run`（service 預設沒加 `--live`，就是不真的下單）。確認連續幾天訊號正常，再把 `--live` 加進 `ExecStart`。

## 步驟四：設定每日自動掃描（systemd timer）

```bash
# 1. 改 service 裡的 YOUR_USER 為你的帳號（whoami 可查）
sed -i "s/YOUR_USER/$(whoami)/g" deploy/stockbot.service

# 2. 安裝
sudo cp deploy/stockbot.service /etc/systemd/system/
sudo cp deploy/stockbot.timer   /etc/systemd/system/
sudo systemctl daemon-reload

# 3. 設定時區（很重要！否則排程時間對不上台股盤中）
sudo timedatectl set-timezone Asia/Taipei

# 4. 啟用排程
sudo systemctl enable --now stockbot.timer
systemctl list-timers 'stockbot*'    # 看下次觸發時間

# 5. 手動跑一次測試 + 看 log
sudo systemctl start stockbot.service
journalctl -u stockbot.service -n 50 --no-pager
```

完成後，VM 會在**每個交易日盤中 09:00–13:30、每 5 分鐘**自動掃描一次（`stockbot.timer` 已設定好），掃到訊號就推 Telegram；關機重開也會自動恢復排程。

---

## 關於「每 5 分鐘掃描」與下單時效

- **下單很快**：Shioaji 送單是毫秒~秒級，有訊號就來得及。
- **重點是即時報價**：service 已加 `--realtime`，盤中會用 **Shioaji 即時成交價**當作「今天這根 K 的現價」，所以**突破策略（李佛摩 / 歐尼爾）在盤中就會即時觸發**，不必等收盤。
- **日 K 的限制**：像巴菲特 / 葛拉漢這種**基本面**策略，本來就是看年/季趨勢，盤中每 5 分鐘掃意義不大；建議這類策略用 `OnCalendar=Mon-Fri 14:00`（收盤後一天一次）即可。**動能突破型**策略才適合 5 分鐘盤中掃。
- 想更快（如每 1 分鐘）：把 timer 的 `00/5` 改成 `00/1`。但 FinMind 免費額度有限，頻率太高建議改用 Shioaji 的歷史 K 線當資料源。

## 為什麼用 systemd timer 而不是 cron？

- 開機自動恢復、可補跑錯過的排程。
- 直接用 `MemoryMax` / `CPUQuota` 限制資源，**保證不會吃爆 VM 影響到 pionex-bot**。
- log 用 `journalctl` 集中查看，比 cron 好除錯。
