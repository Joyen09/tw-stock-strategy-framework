# 交接文件 HANDOFF（給下一個 session / 未來的自己）

> 這份文件記錄專案目前狀態、環境設定、踩過的坑與下一步。
> **新的協作 session 請先讀這份，就能無縫接續，使用者不用重講。**
> 使用者以繁體中文溝通。最後更新：2026-07-04。

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

**三策略模擬盤全自動運行中（lynch-tw50 / livermore-tw50 / lynch-mid100），空跑驗證中。**

已完成並驗證：
- ✅ 回測引擎（含台股手續費 0.1425% + 證交稅 0.3%）、7 策略、pick 選股、walkforward 防過度配適
- ✅ FinMind 真實資料（token 在 .env）+ 磁碟快取 data_cache/（重跑/斷線幾乎不耗額度）
- ✅ Shioaji 模擬盤：登入、即時報價、下單都成功（永豐已簽署）；但其持倉回報不可靠（見第 4 節）
- ✅ Telegram：推播 + 雙向遙控，**listener 已支援多帳戶**（/holdings 合併顯示各策略帳戶+總資產，
  /sell 自動路由到持有的帳戶）
- ✅ 本地持久化模擬盤 `--paper`（+ `--paper-file` 多策略各用獨立帳戶檔）

**策略驗證結論（2026-07-02，tw50、含成本、--regime）**：
- 多頭期 2023-07~2026-07：livermore 夏普 1.67 🥇（總報酬 162%）> oneil 1.25 > lynch 1.10 > momentum 0.97
- 含空頭期 2021-07~2024-07：lynch 夏普 1.51 🥇（**回撤僅 -7.2%**，防守王）> momentum 1.43 > livermore 1.30 > oneil 1.11
- livermore walkforward：訓練夏普 2.69 → 測試 1.19（沒看過的未來仍 +7.13%）→ **過關，不是背答案**
- **結論：lynch 防守核心 + livermore 進攻衛星**；momentum 除役（排名不穩 + 交易太頻繁）

**多流派 spec 策略實作與驗證（2026-07-03~04，來自使用者上傳的規格書）**：
- 資料層新增：三大法人買賣超 `institutional()`、現金流量表→`Fundamentals.fcf`
  （皆 FinMind 免費版可用、已接磁碟快取）；引擎支援 `requires_chips` + 籌碼 T+1 切片防前視
- ❌ mclean（麥克連法人跟單）：tw50 三關淘汰（多頭 0.75/空頭 0.26；wf 過但只在金融認養股上成立）
- ❌ trust（投信認養）/ floor（地板股）：tw50 兩關失敗後，使用者正確指出池子錯配 → 加 mid100 主場重測
- ❌ raiho（雷浩斯矩陣）：0 交易＝「AI 多頭下 tw50 沒有 A 級+便宜標的」（資料正常，實測 2330 roe/fcf/pe 均抓到）
  → 定位改為**每季跑一次的選股 screener**，不當回測策略
- 🏁 **mid100 主場重測結果（2026-07-04）——籌碼流派正式蓋棺，但挖到寶**：
  - trust 主場更慘（多頭 -29.3%！）、floor 多頭 -7.4%/空頭期 +12.3% 仍遠不及格、
    mclean 主場進步（夏普 1.03/0.88）但仍墊底 → **小哥/麥克連籌碼流派在對的池子也輸，正式淘汰**
    （且 mid100 自帶生存者偏差順風，真實只會更差）
  - 💎 **lynch × mid100 三關全過**：多頭夏普 1.57（184%）、含空頭期 1.14（61.6%）、
    **walkforward 測試期 +24.8%/夏普 1.31/回撤 -7.5%** —— 唯一贏過現任的挑戰者。
    合理：GARP 的獵場本來就是中小型成長股（彼得林區本人的玩法），tw50 沒便宜的成長股
  - → **已加第三個空跑帳戶驗證它**（見下），生存者偏差的最終裁判是空跑
- 🆕 **第三帳戶 lynch×mid100 已部署**：`stockbot-lynch-mid100.service/.timer`（**15:30**，
  與 14:00/14:20 錯開一個滾動小時窗——mid100 首掃/財報過期日 ~500 請求會撞額度）→
  paper_lynch_mid100.json，2萬/2檔。listener 的 --paper-file 已列三帳戶。
- 🔴 卡付費資料（不做）：B/C/F/H/I/J 要 FinMind 贊助會員的分點資料；N/O 要 TAIFEX 選擇權＋另一套回測引擎

## 3. 環境與設定

**資金設定（模擬）**：lynch-tw50 3萬(3檔×1萬) + livermore-tw50 2萬(2檔×1萬) + lynch-mid100 2萬(2檔×1萬)。
（模擬帳戶是假錢，三帳戶合計 7 萬只是實驗配置；真錢上線時再重新分配 5 萬。）

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
⚠️ **這個會覆蓋指令上的 `--budget`，且對所有策略都生效（全域）**。若金額不對先 `cat runtime.json`。

**systemd 服務**（部署檔在 `deploy/`）：
- `stockbot.timer`(14:00) + `stockbot.service`：lynch×tw50（3檔+paper）
- `stockbot-livermore.timer`(14:20) + `stockbot-livermore.service`：livermore×tw50（獨立帳戶檔）
- `stockbot-lynch-mid100.timer`(15:30) + `stockbot-lynch-mid100.service`：lynch×mid100（獨立帳戶檔）
- `stockbot-listen.service`：常駐監聽 Telegram（三帳戶）。**改 .env 後要 restart** 才生效。

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
    三個 scan 錯開兩個小時窗（14:00/14:20 一組、15:30 一組）就是為了額度。
12. **TAIEX 請求會 hang**：benchmark 已包 15s timeout，逾時自動用選股池等權平均當大盤代理，regime 照常運作。
13. **籌碼策略 (mclean/trust) 若要部署**：法人資料 15:00–16:00 才公布，timer 應設 18:00 後；
    回測用 T-1 籌碼比實盤保守，方向一致。（目前籌碼策略全數淘汰，僅存檔備查。）
14. **mid100 生存者偏差**：mid100 是「今天活著」的名單，回測結果偏樂觀；lynch×mid100 的漂亮回測
    要靠空跑帳戶用「真實的現在」驗證，別直接拿回測數字規劃報酬。

## 5. 下一步

1. **三帳戶空跑觀察 2–4 週**：/holdings 看總帳，重點比較 lynch-mid100 vs 現任雙雄的實際表現。
2. 空跑穩定後要上真錢：先改執行端為「盤後算訊號 → 隔日開盤送單」+ 永豐實單審核 + 重新分配 5 萬到表現最好的組合。
3. raiho 可當季度 screener 手動跑（財報季後）：`python main.py screen/pick --strategy raiho ...`。

## 6. 常用指令速查

```bash
python main.py list                                   # 列策略
python main.py backtest --strategy lynch --regime --trades          # 回測
python main.py pick --strategy lynch --source finmind --universe mid100 --regime --top 5   # 選股
python main.py walkforward --strategy lynch --source finmind --universe mid100 --regime    # 防過度配適驗證
python main.py compare --strategy lynch,livermore --source finmind --universe mid100 --start 2023-07-01 --end 2026-07-01 --regime
python main.py scan --strategy lynch --source finmind --universe tw50 --regime --paper --cash 30000 --max-positions 3 --budget 10000 --notify
python main.py scan --strategy livermore --source finmind --universe tw50 --regime --paper --paper-file paper_livermore.json --cash 20000 --max-positions 2 --budget 10000 --notify
python main.py scan --strategy lynch --source finmind --universe mid100 --regime --paper --paper-file paper_lynch_mid100.json --cash 20000 --max-positions 2 --budget 10000 --notify
python main.py listen --paper --paper-file "lynch=paper_account.json,livermore=paper_livermore.json,lynch-mid100=paper_lynch_mid100.json"
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
├── data/             # sample(離線) / finmind(真實,含法人買賣超+現金流) / cache(記憶體+磁碟) / universe(top15/tw50/mid100)
├── broker/           # paper / persistent_paper / multi_paper(多帳戶聚合) / shioaji_broker / fees
└── engine/           # backtest(回測,含籌碼T+1切片) / trader(實盤 scan) / screener
main.py               # CLI 入口
examples/simulate_days.py   # 逐日持倉模擬
deploy/               # systemd: stockbot(lynch-tw50) / stockbot-livermore / stockbot-lynch-mid100 / stockbot-listen
```

## 8. 給下一個 session 的提醒


- 使用者是**軟體工程師**：技術操作（git/CLI/VM/systemd）可直接給指令、講細節；但**交易/量化觀念要白話**，並誠實說明風險。
- 一路的核心原則：**不盲信「聽起來很厲害」的東西，一切用數據驗證**（回測含成本、walkforward 防背答案、空頭壓測、模擬盤先跑）。
- 測策略要用**對的池子**（籌碼/爆量類 → mid100；權值基本面 → tw50），池子錯配會得出錯誤結論。
- 挑戰者記分板：oneil/momentum/mclean/trust/floor 已驗證淘汰；raiho 轉 screener；lynch×mid100 三關全過、空跑驗證中。
- 新策略一律先過三關再談部署。別急著上真錢。
