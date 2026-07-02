# 交接文件 HANDOFF（給下一個 session / 未來的自己）

> 這份文件記錄專案目前狀態、環境設定、踩過的坑與下一步。
> **新的協作 session 請先讀這份，就能無縫接續，使用者不用重講。**
> 使用者以繁體中文溝通。最後更新：2026-07-01（台股收盤後）。

---

## 1. 這是什麼


台股名人策略「回測 → 模擬盤 → 實單」自動交易框架。使用者是**軟體工程師**（自架 GCP VM、
熟 git/CLI/Linux），但對「交易 / 量化」領域較新手。目標：把有紀律、扛得住空頭、不被情緒
綁架的交易流程，變成手機上的 Telegram 通知/遙控。


- **GitHub**：`https://github.com/Joyen09/tw-stock-strategy-framework`（原名 `stock`，已改名）
- **預設分支**：`main`
- **開發分支**：`claude/taiwan-stock-trading-api-6vfsp7`
- **VM**：GCP `pionex-bot`（zone `us-west1-a`），程式在 `~/stock`，venv 在 `~/stock/.venv`

## 2. 目前進度（做到哪）

**幾乎完成，卡在「等明天盤中做最後驗證」。**

已完成並驗證：
- ✅ 回測引擎（含台股手續費 0.1425% + 證交稅 0.3%）、6 策略、pick 選股、walkforward 防過度配適
- ✅ 策略結論：**`lynch`（彼得林區）+ `--regime` 風向濾網**最穩，walkforward 測試期夏普 ~1.3、年化 ~7.5%
- ✅ FinMind 真實資料（token 在 .env）
- ✅ Shioaji 模擬盤：**登入成功、即時報價正常、下單成功**（永豐已線上簽署、Python API 已測試通過）
- ✅ Telegram：推播通知 + 雙向遙控（/budget /maxpos /pause /resume /status /holdings /sell）
- ✅ scan 動態選股：`--universe tw50 --max-positions 5` 自動挑訊號最強的 N 檔、換股補位
- ✅ 逐日行為模擬 `examples/simulate_days.py`（已驗證鋪倉/續抱/換股）

**待辦（下一步）**：
- ⏳ **明天平日盤中（09:00–13:30）做乾淨的 --live 測試**，確認零股正確成交（見第 5 節）
- ⏳ 驗證 OK 後 → 啟用 `stockbot.timer` systemd 排程，進入全自動
- ⏳ 模擬盤空跑 2–4 週穩定後 → 才考慮小額真錢（需開通實單權限）

## 3. 環境與設定

**資金設定**：本金 5 萬，單檔 budget 10000，最多持有 5 檔。

**`~/stock/.env`**（已設定，gitignore 忽略，不會進 git）：
```
FINMIND_TOKEN=...
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
SHIOAJI_API_KEY=...
SHIOAJI_SECRET_KEY=...
SHIOAJI_CA_PATH=/home/linjoyen/stock/Sinopac.pfx
SHIOAJI_CA_PASSWD=...
SHIOAJI_PERSON_ID=<你的身分證字號>   # 實際值只放 VM 的 .env，勿寫進 repo
```

**`~/stock/runtime.json`**（Telegram 動態設定，gitignore 忽略）：`budget` / `max_positions` / `paused`。
⚠️ **這個會覆蓋指令上的 `--budget`**。之前使用者 Telegram 傳過 `/budget 60000` 導致下單金額爆大，
若金額不對先檢查這個檔（`cat runtime.json`）或傳 `/budget 10000`。

**systemd 服務**（部署檔在 `deploy/`）：
- `stockbot.timer` + `stockbot.service`：盤中每 5 分鐘自動 scan（預設 lynch + tw50 + regime + max-positions 5，模擬盤）
- `stockbot-listen.service`：常駐監聽 Telegram 指令。**改 .env 後要 `sudo systemctl restart stockbot-listen.service`** 才會生效。

## 4. 重要注意事項 / 踩過的坑

1. **市場時間**：策略下的是**盤中零股（IntradayOdd），只有平日 09:00–13:30 能交易**。收盤後測試 →
   訂單掛著不成交、持倉不即時更新，會看起來「怪怪的」但其實正常。**測試務必在盤中。**
2. **模擬帳戶餘額顯示 0** 是正常的，仍能下模擬單（線上簽署後）。假錢、不花真錢。
3. **"Please sign ... first" 錯誤** = 要在永豐官網「線上簽署 API」（已完成）。憑證 .pfx 也設好了。
4. **Git 推送**：Claude 的沙箱環境**推 `main` 會一直 503**（GitHub 伺服器對 main 特別卡），
   但**推既有的 `claude/taiwan-stock-trading-api-6vfsp7` 分支會成功**。
   → 流程：改動 push 到該功能分支 → 用 `mcp__github__create_pull_request` 開 PR → 請使用者按 merge。
   使用者的 VM 網路推 main 正常。
5. **`--end` 預設已改成今天**（scan/screen），不用手動帶日期。
6. **零股 bug（已修 PR#4）**：舊版把零股用 `shares//1000` 換算，會把 2 股暴放成 1000 股（暴買 500 倍）。
   已改成整股用 Common(張)、零股用 IntradayOdd(股)。**幸好模擬盤抓到。**
7. **零股 >999 拆單 bug（已修 `claude/handoff-continuation-wimy1z`）**：盤中零股單一委託上限 999 股，
   舊版把「非整張倍數且 ≥1000 股」直接當單一 IntradayOdd 送出會被拒單（低價股 + 較大 budget 才踩到）。
   已抽出純函式 `plan_order_lots()` 拆成 整股(Common)+零股(IntradayOdd) 兩段，並補回歸測試。
   tw50 都是高價股、budget 1 萬時股數 <999，實務上少見，但先修掉以防擴大選股池。
8. **測試**：`tests/` 從只有策略測試補到涵蓋下單路徑（fees / PaperBroker / LiveTrader sizing+名額+regime+paused+保險絲 /
   plan_order_lots 拆單），共 35 個測試。改下單/成本相關程式碼後請先 `python -m pytest tests/ -q`。
9. **⚠️ 永豐模擬盤(simulation)的持倉/成交回報不可靠（2026-07-02 盤中實測）**：--live 送出的委託是正確的
   （order dump 證實 2891×105、2886×164、1216×100 IntradayOdd，股數精準），但 `list_positions` 回報的
   持倉數字**會自己成長、每次查都不同**（例：只送 100 股，持倉卻顯示 16000→36000→…；`/sell all` 還回報賣
   「2886 42000 股」）。這是**模擬盤撮合引擎的 artifact，不是我們的 bug**。
   → **教訓：不要用永豐模擬盤的「持倉數字」當驗收依據**（它是垃圾）。要驗證下單正確性，看 scan 印出的
   order 內容 / order dump 即可；要驗證持倉/損益行為，用 PaperBroker（`examples/simulate_days.py` 或不帶
   `--live/--realtime` 的 scan）才是乾淨可信的。
10. **保險絲（已加）**：LiveTrader 送買單前檢查 `shares*price`，超過 `max_order_value`（預設 budget*1.5）就
    拒單並印 `[safety] ⛔`。CLI 可用 `--max-order-value`（設 0 關閉）。正常買單金額 <= budget 不受影響；
    純粹防未來 sizing/報價/髒資料把單筆金額放大。**上真錢前這道一定要留著。**

## 5. 明天要做的事（最重要）

**平日盤中 09:00–13:30**，在 VM 上：
```bash
cd ~/stock && source .venv/bin/activate
git pull                        # 確保是最新（含所有修正）

# 乾淨的 --live 測試（模擬盤假錢）
python main.py scan --strategy lynch --source finmind \
  --symbols 2330,2317,2891,2303,2886,3037,6669,2308 \
  --regime --realtime --live --max-positions 5 --budget 10000 --notify

# 確認持倉是「小零股」(2股/44股...) 不是 1000 股
python main.py shioaji-test        # 或 Telegram 傳 /holdings
```
**驗收標準**：持倉是小零股數字（不是 1000 股）、金額每檔約 5k–7.5k、5 檔合計約 3 萬多（在 5 萬內）。

驗收 OK 後 → 啟用自動排程（`deploy/README_DEPLOY.md` 步驟四），進入全自動模擬盤。

## 6. 常用指令速查

```bash
python main.py list                                   # 列策略
python main.py backtest --strategy lynch --regime --trades          # 回測
python main.py pick --strategy lynch --source finmind --regime --top 5   # 選股
python main.py walkforward --strategy lynch --source finmind --regime    # 防過度配適驗證
python main.py compare --source finmind --symbols ... --regime      # 策略比較
python main.py scan --strategy lynch --source finmind --regime --realtime --live --max-positions 5 --budget 10000 --notify   # 模擬盤自動交易
python main.py shioaji-test                           # 測 Shioaji 連線/持倉
python main.py listen                                 # Telegram 遙控監聽
python main.py notify-test                            # 測 Telegram 通知
python examples/simulate_days.py finmind 2330,2317,... 20    # 逐日行為模擬
```
Telegram 指令：`/status /budget N /maxpos N /pause /resume /holdings /sell 2330 /sell all`

## 7. 系統架構速覽

```
src/
├── models.py         # Signal / Fundamentals / Position
├── indicators.py     # 技術指標
├── control.py        # runtime.json 設定 + Telegram 雙向監聽
├── strategies/       # buffett/graham/lynch/oneil/livermore/us_overnight
├── data/             # sample(離線) / finmind(真實) / us_lead / cache / universe
├── broker/           # paper(模擬) / shioaji_broker(實單) / fees(台股成本)
└── engine/           # backtest(回測) / trader(實盤 scan) / screener
main.py               # CLI 入口（list/backtest/compare/pick/walkforward/screen/scan/listen/shioaji-test...）
examples/simulate_days.py   # 逐日持倉模擬
deploy/               # setup_vm.sh + systemd service/timer + README_DEPLOY.md
```

## 8. 給下一個 session 的提醒


- 使用者是**軟體工程師**：技術操作（git/CLI/VM/systemd）可直接給指令、講細節；但**交易/量化觀念要白話**，並誠實說明風險。
- 一路的核心原則：**不盲信「聽起來很厲害」的東西，一切用數據驗證**（回測含成本、walkforward 防背答案、空頭壓測、模擬盤先跑）。
- 目前就差「明天盤中驗證零股 → 開排程」。別急著上真錢。
