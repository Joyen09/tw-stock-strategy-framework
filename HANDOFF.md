# 交接文件 HANDOFF（給下一個 session / 未來的自己）

> 這份文件記錄專案目前狀態、環境設定、踩過的坑與下一步。
> **新的協作 session 請先讀這份，就能無縫接續，使用者不用重講。**
> 使用者以繁體中文溝通。最後更新：2026-07-03。

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

**雙策略模擬盤已上線全自動運行，空跑驗證中。**

已完成並驗證：
- ✅ 回測引擎（含台股手續費 0.1425% + 證交稅 0.3%）、7 策略、pick 選股、walkforward 防過度配適
- ✅ FinMind 真實資料（token 在 .env）+ 磁碟快取 data_cache/（重跑/斷線幾乎不耗額度）
- ✅ Shioaji 模擬盤：登入、即時報價、下單都成功（永豐已簽署）；但其持倉回報不可靠（見第 4 節）
- ✅ Telegram：推播 + 雙向遙控，**listener 已支援多帳戶**（/holdings 合併顯示兩個策略帳戶+總資產，
  /sell 自動路由到持有的帳戶）
- ✅ 本地持久化模擬盤 `--paper`（+ `--paper-file` 多策略各用獨立帳戶檔）

**策略驗證結論（2026-07-02，tw50、含成本、--regime）**：
- 多頭期 2023-07~2026-07：livermore 夏普 1.67 🥇（總報酬 162%）> oneil 1.25 > lynch 1.10 > momentum 0.97
- 含空頭期 2021-07~2024-07：lynch 夏普 1.51 🥇（**回撤僅 -7.2%**，防守王）> momentum 1.43 > livermore 1.30 > oneil 1.11
- livermore walkforward：訓練夏普 2.69 → 測試 1.19（沒看過的未來仍 +7.13%）→ **過關，不是背答案**
- **結論：lynch 防守核心 + livermore 進攻衛星，雙策略配置**；momentum 除役（兩期排名不穩 + 270 筆交易太頻繁）

**目前運行中：雙策略空跑 2–4 週**：lynch 3萬/3檔（`stockbot.service` → paper_account.json，14:00）+
livermore 2萬/2檔（`stockbot-livermore.service` → paper_livermore.json，14:20），皆收盤後一天一次。
空跑穩定後 → 才考慮小額真錢（需先走永豐實單開通審核；且要把執行端改成「盤後算訊號、隔日開盤送單」）。

**多流派 spec 策略實作與驗證（2026-07-03，來自使用者上傳的規格書）**：
- 資料層新增：三大法人買賣超 `institutional()`（籌碼策略用）、現金流量表→`Fundamentals.fcf`
  （皆 FinMind 免費版可用、已接磁碟快取）；引擎支援 `requires_chips` + 籌碼 T+1 切片防前視
- ❌ **策略 K `mclean`（麥克連法人跟單）：已實作、三關驗證後淘汰（不部署）**
  - 多頭期夏普 0.75（479 筆交易，+10% 停利在大多頭一直放生大魚）、含空頭期 0.26（372 筆）
  - walkforward 反而過關（測試期夏普 1.90、回撤 -1.37%，但只在「選出的5檔金融/大型股」上成立）
  - 結論：法人籌碼跟單只在「籌碼乾淨的認養股」上有效，且防守輸 lynch、進攻輸 livermore，兩頭不到岸
- 🆕 **策略 A `trust`（投信認養）/ G `floor`（地板股搝反彈）/ P `raiho`（雷浩斯獲利能力矩陣）已實作，待三關驗證**：
  ```bash
  python main.py compare --strategy trust,floor,raiho,lynch,livermore --source finmind --universe tw50 --start 2023-07-01 --end 2026-07-01 --regime
  python main.py compare --strategy trust,floor,raiho,lynch,livermore --source finmind --universe tw50 --start 2021-07-01 --end 2024-07-01 --regime
  python main.py walkforward --strategy trust --source finmind --universe tw50 --regime   # (表現好的才跑)
  ```
  - trust：投量比門檻預設 3%（spec 原版 10% 是中小型股設計，tw50 達不到）；「主力同買」條件因分點資料付費而省略
  - floor：地板線=個股月線乖離歷史 P5 分位（分位只用昨日以前分布算，無前視）＋爆量 2 倍；-4% 緊停損、回月線獲利了結
  - raiho：⚠️ 基本面是當下快照，回測有前視偏差且「降級出場」不會觸發 → 回測≈選股能力測試，僅供相對比較
- 🔴 卡付費資料（不做）：B/C/F/H/I/J 要 FinMind 贊助會員的分點資料；N/O 要 TAIFEX 選擇權＋另一套回測引擎

## 3. 環境與設定

**資金設定**：本金 5 萬 = lynch 3萬(3檔×1萬) + livermore 2萬(2檔×1萬)。

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
⚠️ **這個會覆蓋指令上的 `--budget`，且對兩個策略都生效（全域）**。若金額不對先 `cat runtime.json`。

**systemd 服務**（部署檔在 `deploy/`）：
- `stockbot.timer`(14:00) + `stockbot.service`：lynch 收盤後掃描（tw50+regime+3檔+paper）
- `stockbot-livermore.timer`(14:20) + `stockbot-livermore.service`：livermore 衛星（獨立帳戶檔）
- `stockbot-listen.service`：常駐監聽 Telegram（多帳戶）。**改 .env 後要 restart** 才生效。

## 4. 重要注意事項 / 踩過的坑

1. **市場時間**：盤中零股只有平日 09:00–13:30 能交易；但 `--paper` 模擬盤是本地記帳，任何時間都能跑。
2. **模擬帳戶餘額顯示 0** 是正常的，仍能下模擬單（線上簽署後）。
3. **"Please sign ... first" 錯誤** = 要在永豐官網「線上簽署 API」（已完成）。
4. **Git 推送**：Claude 沙箱推 main 會 503；用 GitHub API (`mcp__github__push_files`) 直推 main 可行（2026-07-03 實證），
   或推功能分支開 PR 由使用者 merge。使用者的 VM 推 main 正常。
5. **`--end` 預設已改成今天**（scan/screen）。
6. **零股 bug（已修 PR#4）**：舊版把零股用 `shares//1000` 換算暴買 500 倍。已改 Common(張)+IntradayOdd(股)。
7. **零股 >999 拆單 bug（已修）**：盤中零股單筆上限 999 股，`plan_order_lots()` 拆整張+零股兩段。
8. **測試**：`tests/` 共 81 個（下單路徑/fees/兩種 PaperBroker/多帳戶/策略/基準備援）。
   改程式後先 `python -m pytest tests/ -q`。
9. **⚠️ 永豐模擬盤的持倉/成交回報不可靠**：數字會自己成長、每次查都不同，只能當送單通道，
   驗收一律看本地 PaperBroker。
10. **保險絲**：買單金額超過 `max_order_value`（預設 budget*1.5）拒單。**上真錢前一定要留著。**
11. **FinMind 額度（滾動 60 分鐘窗口 600 請求，非整點重置）**：已用磁碟快取大幅降低用量
    （財報 7 天 TTL、價格/籌碼 1 天 TTL、同日重跑幾乎 0 請求）。查用量：
    `curl -s "https://api.finmindtrade.com/api/v4/user_info?token=$FINMIND_TOKEN"`
12. **TAIEX 請求會 hang**：benchmark 已包 15s timeout，逾時自動用選股池等權平均當大盤代理，regime 照常運作。
13. **籌碼策略 (mclean/trust) 若要部署**：法人資料 15:00–16:00 才公布，timer 應設 18:00 後（非 14:00）；
    回測用 T-1 籌碼比實盤保守，方向一致。

## 5. 下一步

1. **跑 trust/floor/raiho 三關驗證**（指令在第 2 節）。籌碼與價格資料多半已在快取，
   新增請求：現金流量表 ~50（raiho 用）。判準不變：贏過現任（lynch 防守/livermore 進攻）才給部署名額。
2. **雙策略空跑觀察 2–4 週**：每天 14:00–14:30 看 Telegram，/holdings 看總帳。
3. 空跑穩定後要上真錢：先改執行端為「盤後算訊號 → 隔日開盤送單」+ 永豐實單審核。

## 6. 常用指令速查

```bash
python main.py list                                   # 列策略
python main.py backtest --strategy lynch --regime --trades          # 回測
python main.py pick --strategy lynch --source finmind --regime --top 5   # 選股
python main.py walkforward --strategy lynch --source finmind --regime    # 防過度配適驗證
python main.py compare --strategy trust,floor,raiho,lynch,livermore --source finmind --universe tw50 --start 2023-07-01 --end 2026-07-01 --regime   # spec 新策略 vs 現任雙雄
python main.py scan --strategy lynch --source finmind --universe tw50 --regime --paper --cash 30000 --max-positions 3 --budget 10000 --notify
python main.py scan --strategy livermore --source finmind --universe tw50 --regime --paper --paper-file paper_livermore.json --cash 20000 --max-positions 2 --budget 10000 --notify
python main.py listen --paper                          # Telegram 遙控，預設看兩個策略帳戶
python main.py shioaji-test                           # 測 Shioaji 連線
python main.py notify-test                            # 測 Telegram 通知
```
Telegram 指令：`/status /budget N /maxpos N /pause /resume /holdings /sell 2330 /sell all`

## 7. 系統架構速覽

```
src/
├── models.py         # Signal / Fundamentals(含fcf) / Position
├── indicators.py     # 技術指標 (sma/ema/rsi/macd/kd/atr/...)
├── control.py        # runtime.json 設定 + Telegram 雙向監聽 (多帳戶)
├── strategies/       # buffett/graham/lynch/oneil/livermore/mclean(法人籌碼)/momentum(短線快層)/
│                     #   trust(投信認養)/floor(地板股)/raiho(雷浩斯矩陣)/us_overnight
├── data/             # sample(離線) / finmind(真實,含法人買賣超+現金流) / cache(記憶體+磁碟) / universe
├── broker/           # paper / persistent_paper / multi_paper(多帳戶聚合) / shioaji_broker / fees
└── engine/           # backtest(回測,含籌碼T+1切片) / trader(實盤 scan) / screener
main.py               # CLI 入口
examples/simulate_days.py   # 逐日持倉模擬
deploy/               # systemd: stockbot(lynch) / stockbot-livermore / stockbot-listen
```

## 8. 給下一個 session 的提醒


- 使用者是**軟體工程師**：技術操作（git/CLI/VM/systemd）可直接給指令、講細節；但**交易/量化觀念要白話**，並誠實說明風險。
- 一路的核心原則：**不盲信「聽起來很厲害」的東西，一切用數據驗證**（回測含成本、walkforward 防背答案、空頭壓測、模擬盤先跑）。
- 新策略（含 mclean/trust/floor/raiho）一律先過三關再談部署；現行空跑的雙策略生產線不隨便動。別急著上真錢。
