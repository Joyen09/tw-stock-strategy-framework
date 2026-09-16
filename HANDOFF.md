# 交接文件 HANDOFF（給下一個 session / 未來的自己）

> 這份文件記錄專案目前狀態、環境設定、踩過的坑與下一步。
> **新的協作 session 請先讀這份，就能無縫接續，使用者不用重講。**
> 使用者以繁體中文溝通。最後更新：2026-08-18。

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
TELEGRAM_BOT_TOKEN=...          # ⚠️ 2026-07-06 bot 遭 Telegram 凍結
TELEGRAM_CHAT_ID=...
DISCORD_WEBHOOK_URL=...         # Discord 頻道 Webhook（頻道設定→整合→Webhook→複製URL），推播用
DISCORD_BOT_TOKEN=...           # Discord bot（雙向控制用，設定見 src/control_discord.py）
DISCORD_CHANNEL_ID=...          # 只聽這個頻道的指令
DISCORD_USER_ID=...             # (可選) 只聽這個使用者
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
- `stockbot-discord-listen.service`：常駐 Discord bot 雙向控制（三帳戶）。**改 .env 後要 restart** 才生效。
- `stockbot-listen.service`：舊 Telegram listener（bot 凍結後停用；若申請新 token 可切回）。

**通知多通道化（2026-07-06，Telegram bot 遭凍結後）**：`src/notify/` 改為 Telegram+Discord
多通道（`build_notifier()`；既有 `TelegramNotifier()` 呼叫點經向後相容工廠自動升級），
哪個通道有設定就發哪個。推播（訊號/心跳）Discord 已可完全取代 Telegram；
✅ **雙向控制已有 Discord bot 版**：`discord_listen.py` + `stockbot-discord-listen.service`
（指令大腦與 Telegram 版共用 control.py 純函式；支援 /status !status 兩種前綴；
只聽 DISCORD_CHANNEL_ID 指定頻道 + 可選 DISCORD_USER_ID 指定使用者）。
需 `pip install -U discord.py` + Developer Portal 開 MESSAGE CONTENT INTENT（步驟見 src/control_discord.py）。

## 4. 重要注意事項 / 踩過的坑

1. **市場時間**：盤中零股只有平日 09:00–13:30 能交易；但 `--paper` 模擬盤是本地記帳，任何時間都能跑。
2. **模擬帳戶餘額顯示 0** 是正常的，仍能下模擬單（線上簽署後）。
3. **"Please sign ... first" 錯誤** = 要在永豐官網「線上簽署 API」（已完成）。
4. **Git 推送**：Claude 沙箱推 main 會 503；用 GitHub API (`mcp__github__push_files`) 直推 main 可行（2026-07-03 實證），
   或推功能分支開 PR 由使用者 merge。使用者的 VM 推 main 正常。
   ⚠️ API 推送常把中文打成別字（灌→灸、卻→却、暫→暗），**每次推完必跑
   `git fetch origin main && git diff origin/main HEAD` 驗證**，有差異就用真實字元重推。
5. **`--end` 預設已改成今天**（scan/screen）。
6. **零股 bug（已修 PR#4）**：舊版把零股用 `shares//1000` 換算暴買 500 倍。已改 Common(張)+IntradayOdd(股)。
7. **零股 >999 拆單 bug（已修）**：盤中零股單筆上限 999 股，`plan_order_lots()` 拆整張+零股兩段。
8. **測試**：`tests/` 共 209 個（下單路徑/fees/兩種 PaperBroker/多帳戶/策略/基準備援/心跳/通知多通道/
   Discord 控制/成交紀錄/大盤對照/實盤冷卻期/季線緩衝/固定停利/除權息/未成交可見性/
   回測暖身窗口/離線防護）。
   改程式後先 `.venv/bin/python -m pytest -q`（約 27 秒，不打網路）。
9. **⚠️ 永豐模擬盤的持倉/成交回報不可靠**：數字會自己成長、每次查都不同，只能當送單通道，
   驗收一律看本地 PaperBroker。
10. **保險絲**：買單金額超過 `max_order_value`（預設 budget*1.5）拒單。**上真錢前一定要留著。**
11. **FinMind 額度（滾動 60 分鐘窗口 600 請求，非整點重置）**：已用磁碟快取大幅降低用量
    （財報 7 天 TTL、價格/籌碼 1 天 TTL、同日重跑幾乎 0 請求）。查用量：
    `curl -s "https://api.finmindtrade.com/api/v4/user_info?token=$FINMIND_TOKEN"`
    三個 scan 錯開兩個小時窗（14:00/14:20 一組、15:30 一組）就是為了額度。
    ⚠️ `TaiwanStockNews`**單次請求只回一天**（官方文件備註），要逐日抓；
    全量 150 檔×2 年 = 7.5 萬次請求，免費層不可行。
12. **TAIEX 請求會 hang**：benchmark 已包 15s timeout，逾時自動用選股池等權平均當大盤代理，regime 照常運作。
13. **籌碼策略 (mclean/trust) 若要部署**：法人資料 15:00–16:00 才公布，timer 應設 18:00 後；
    回測用 T-1 籌碼比實盤保守，方向一致。（目前籌碼策略全數淘汰，僅存檔備查。）
14. **mid100 生存者偏差**：mid100 是「今天活著」的名單，回測結果偏樂觀；lynch×mid100 的漂亮回測
    要靠空跑帳戶用「真實的現在」驗證，別直接拿回測數字規劃報酬。
15. **心跳通知（2026-07-06 加）**：無訊號時也推「🫀 掃描完成…運作正常/⏸暫停買進中」——
    起因是 /pause 忘了解除、空跑一週買不了東西卻無從察覺。判讀：該出現的心跳沒出現=系統掛了。
16. **⚠️ 實盤曾漏掉 cooldown（2026-08-18 修）**：Backtester 預設 `cooldown_days=5`（賣出後 5 個交易日
    不重買），但 LiveTrader 當初沒有這個參數——**三關驗證是在有防洗盤的條件下通過的，實盤卻沒有**，
    等於跑著沒驗證過的系統。已補上並用帳戶成交紀錄判斷。教訓：回測與實盤的參數要逐項對照。
17. **成交紀錄（2026-08-18 加）**：帳戶檔原本只存「現在剩多少」，回答不了「錢是怎麼虧的」。
    現在每筆成交都落地（上限 500 筆），`/trades` 可看已實現損益與手續費。
    ⚠️ 這之前的交易沒有紀錄，永遠查不到。
18. **空頭壓測不能空過**：第一版把空頭期設 2022-01-01 起，結果 0 筆交易——大盤濾網全年禁止做多，
    策略從頭空手到尾，那一關等於沒測到。已改成從 2021-07 起算（先建倉再遇下跌），
    且 0 筆交易一律判未通過。**空過比失敗更危險，它給出假的安全感。**
19. **報告要分「已實現/未實現」**：2026-08 週報顯示合計 +0.49% 看似轉正，拆開後是
    已實現 -8,113、未實現 +8,446，且浮盈六成來自單一持股（川湖佔該帳戶 52%）。
    report 現在會拆開顯示並在單一持股 >50% 時示警。
20. **新聞熱度無超額報酬（2026-08 驗證）**：事件研究法測「新聞量暴增後隔天進場」，
    事件日當天已平均漲 +0.67%（進場前就被反映），事後 1/5/10/20 日全部**輸給隨機日基準**
    （-0.85%/-2.22%/-3.32%/-4.35%）。→ 新聞看板只當**風險雷達/理解工具**，不當買進訊號。
    工具留存：`tools/news_event_study.py`。
21. **「都沒在交易」通常不是壞掉，是滿倉**：lynch-mid100 從頭到尾 0 筆成交，原因是
    `--max-positions 2` 而且已持有 2 檔 → `slots = 2 - 2 = 0`，`buy_cands[:0]` 是空的，
    結構上就買不進；兩檔持股又都沒觸發賣出條件，所以一直卡住。心跳只會說「無交易訊號」，
    分不出是「沒訊號」還是「輪不到評估」。→ `tools/why_idle.py` 會照 scan() 的順序把
    暫停／大盤濾網／空位／冷卻期／訊號逐關檢查，直接指出卡在哪一關。
    （附帶觀察：這個交易最少的帳戶報酬最好，而交易最多的 livermore 虧最多。）
22. **買單是「全有全無」，而且失敗過去是靜默的**：下單股數算的是 `budget × 訊號強度`，
    **不會縮到帳上剩餘現金**；金額超過現金就整筆不成交（`broker/paper.py`）。
    沒成交就不進 `plans`，於是心跳每天照樣回報「無交易訊號」——跟「策略真的沒看上任何
    股票」長得一模一樣。lynch-mid100 現金剩 3,072、budget 設 10,000，34 檔候選一檔都
    買不起，就這樣卡了很久沒人發現。已修：`trader.rejected` 記錄未成交並印出「需要多少
    ／帳上多少」，心跳有被拒單時改口說「有訊號但 N 筆未成交」。
    **心跳說謊比沒有心跳更糟**——它讓一個買不進東西的帳戶看起來一切正常。
23. **🔴 回測窗口被暖身吃掉（2026-09-15 修正，所有舊回測數字都要重跑）**：
    舊版 `if i < warmup: continue` 把 warmup(250 根) 直接從回測窗口的頭扣掉，
    「標示的期間」≠「真正在交易的期間」：
    - 關1 多頭 2024-01-01~2025-12-31（505 根）→ 實際只交易 255 根，**2024 整年是空的**
    - 關2 空頭 2021-07-01~2022-12-31（378 根）→ 只交易最後 128 根（2022-07 之後），
      那時大盤早已跌破年線、濾網全面禁止做多 → **0 筆交易**
    - 關3 WF 測試期同樣只剩約 6 個月
    那個「空頭 0 筆」被誤診成「窗口選得不好」，改了起始日還是 0 筆——**真正的原因
    一直是暖身吃掉窗口**。權益曲線也含著 250 天不動的現金，夏普被一起壓低。
    已修：暖身資料改從 start 之前另外抓，start 當天就開始交易；曲線只涵蓋標示期間。
    **影響範圍：exit_buffer、take_profit、lynch×mid100「三關全過」等所有歷史結論
    都建立在被截斷的窗口上，需要重跑才算數。**
    教訓：`0 筆交易` 這種訊號要一路追到根因，不能只換個參數看它會不會消失。
24. **測試不准打網路**：`pytest` 在 VM 上會卡在 `test_all_strategies_backtest_without_error`
    ——它建出的 us_overnight 策略會去 yfinance 抓 6 年 ^SOX / TSM 資料（`yfinance`
    在 requirements 裡，VM 上真的裝了，所以真的會下載）。加上第 23 條修正後每個窗口
    要評估的 K 棒多一倍，在小台 VM 上就像整個當掉。
    已修：`tests/conftest.py` 設 `STOCKBOT_NO_NETWORK=1`，`USLeadProvider` 看到就直接
    回 None（注入假資料的路徑不受影響）；全策略冒煙測試的窗口也從 2 年縮成 1 年。
    **測試時間 47s → 27s，而且不再依賴 Yahoo 連不連得上。**
    原則：單元測試依賴外部服務，失敗時分不清是程式壞了還是網路壞了。
25. **長跑工作要放背景 + FinMind 會自動重試**：三關驗證一次要打幾百次 API、跑幾十分鐘，
    前景執行時 SSH 一斷線整份就沒了（2026-09 連續好幾次都這樣死）。
    - 執行一律用：`nohup .venv/bin/python -u tools/xxx.py ... > xxx.log 2>&1 &`
      （`-u` 關掉輸出緩衝，不加的話 log 會是空的直到結束）
    - `FinMindProvider._call` 已加韌性：一般網路錯誤指數退避重試（3/6/12 秒），
      撞到 API 額度改成**等額度回補**（預設每輪 5 分鐘、最多 6 輪）而不是直接死。
      兩種預算分開計算——混用的話等幾輪額度就把重試次數用光了。
      可用 `FINMIND_RATE_SLEEP` / `FINMIND_RATE_WAITS` 等環境變數調整。
    - 真的中斷也直接重跑：`data_cache/` 有快取會接續，不會全部重抓。

## 5. 下一步

1. **三帳戶空跑觀察 2–4 週**：/holdings 看總帳，重點比較 lynch-mid100 vs 現任雙雄的實際表現。
2. 空跑穩定後要上真錢：先改執行端為「盤後算訊號 → 隔日開盤送單」+ 永豐實單審核 + 重新分配 5 萬到表現最好的組合。
3. raiho 可當季度 screener 手動跑（財報季後）：`python main.py screen/pick --strategy raiho ...`。
4. ✅ Discord bot 雙向控制已完成（discord_listen.py）；Telegram listener 可停用。
5. 觀察 cooldown 補上後的效果：`/trades` 看來回洗的次數是否下降（2026-08-18 起才有紀錄）。

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
python main.py report --notify                        # 績效報告（市值計+大盤對照），可排程週推
python discord_listen.py --paper                       # Discord 雙向控制（預設三帳戶）
python main.py listen --paper --paper-file "lynch=paper_account.json,livermore=paper_livermore.json,lynch-mid100=paper_lynch_mid100.json"   # (Telegram 版，bot 凍結中)
python main.py shioaji-test                           # 測 Shioaji 連線
python main.py notify-test                            # 測通知（Telegram+Discord 都會發）
python tools/validate_lynch_buffer.py --universe tw50 # 策略參數改動的三關驗證（標準事前寫死）
python tools/validate_take_profit.py --universe tw50  # 「賺 N% 就走」該不該開的三關驗證
python tools/churn_check.py                           # 交易品質健檢（來回洗、持有天數、勝率）
python tools/why_idle.py --strategy lynch --universe mid100 --paper-file paper_lynch_mid100.json --regime --max-positions 2 --budget 10000   # 帳戶為什麼沒交易
```
⚠️ VM 上要用 `.venv/bin/python`，系統沒有裸 `python`。
控制指令（Discord 用 / 或 ! 前綴）：`/status /budget N /maxpos N /pause /resume /holdings /report /trades /sell 2330 /sell all`

## 7. 系統架構速覽

```
src/
├── models.py         # Signal / Fundamentals(含fcf) / Position
├── indicators.py     # 技術指標 (sma/ema/rsi/macd/kd/atr/...)
├── control.py        # runtime.json 設定 + Telegram 雙向監聽 (多帳戶，指令純函式供兩種 listener 共用)
├── control_discord.py # Discord bot 雙向監聽 (入口: discord_listen.py)
├── notify/           # telegram / discord(webhook) / multi(多通道聚合+build_notifier)
├── strategies/       # buffett/graham/lynch/oneil/livermore/mclean(法人籌碼)/momentum(短線快層)/
│                     #   trust(投信認養)/floor(地板股)/raiho(雷浩斯矩陣)/us_overnight
├── data/             # sample(離線) / finmind(真實,含法人買賣超+現金流) / cache(記憶體+磁碟) / universe(top15/tw50/mid100)
├── broker/           # paper / persistent_paper(含成交紀錄) / multi_paper(多帳戶聚合) / shioaji_broker / fees
└── engine/           # backtest(回測,含籌碼T+1切片) / trader(實盤 scan,含心跳+冷卻期) / screener
main.py               # CLI 入口
discord_listen.py     # Discord 雙向控制入口
examples/simulate_days.py   # 逐日持倉模擬
tools/                # news_event_study.py(新聞事件研究) / validate_lynch_buffer.py(參數三關驗證)
deploy/               # systemd: stockbot(lynch-tw50) / stockbot-livermore / stockbot-lynch-mid100 /
                      #          stockbot-discord-listen / stockbot-report(週報) / stockbot-listen(Telegram,已停用)
```

## 8. 給下一個 session 的提醒


- 使用者是**軟體工程師**：技術操作（git/CLI/VM/systemd）可直接給指令、講細節；但**交易/量化觀念要白話**，並誠實說明風險。
- 一路的核心原則：**不盲信「聽起來很厲害」的東西，一切用數據驗證**（回測含成本、walkforward 防背答案、空頭壓測、模擬盤先跑）。
- 測策略要用**對的池子**（籌碼/爆量類 → mid100；權值基本面 → tw50），池子錯配會得出錯誤結論。
- 挑戰者記分板：oneil/momentum/mclean/trust/floor 已驗證淘汰；raiho 轉 screener；lynch×mid100 三關全過、空跑驗證中。
- **參數改動記分板**：lynch 季線緩衝 `exit_buffer`（2026-08-18）——實盤出現「台積電抱 2 天被砍 -4.3%」
  才提案，但三關驗證 **tw50 與 mid100 兩個池子、2%/3%/5% 三個值全部未通過**：緩衝確實讓交易數
  大幅下降（59→41→37→31，機制有效），但多頭報酬單調下滑（59.5%→57.9%→56.8%→50.1%）。
  **決議：維持 exit_buffer=0**，程式碼保留參數供日後重驗。
  值得記的觀察：walkforward（樣本外）反而略有改善，與多頭期（樣本內）結論相反——
  但事前寫死的標準就是標準，不因為看到有利數字就改判。
- **參數改動記分板**：lynch 固定停利 `take_profit`（2026-09-15）——提案動機是
  「交易最少的帳戶報酬最好，那乾脆賺 N% 就走」。三關驗證 **tw50 的 10/15/20/30%
  四個門檻全部未通過**，而且結果是單調的：停利越緊，報酬越差、交易越多、勝率越高。
  ```
  版本        多頭報酬   夏普   交易數   勝率   最大單筆
  不停利        48.70%  1.66     54     30%   +122.6%
  停利10%       19.49%  1.00    115     43%    +17.7%
  停利15%       24.12%  1.19    102     40%    +21.6%
  停利20%       29.95%  1.28     94     40%    +28.4%
  停利30%       29.23%  1.34     87     30%    +35.5%
  ```
  **決議：維持 take_profit=0**。教科書般的結果：勝率 30%→43%（感覺變好），
  總報酬卻砍半——最大單筆 +122.6% 被壓到 +17.7%，撐起整體報酬的大贏家被提前賣掉。
  停利 30% 只觸發 8 次就讓報酬掉四成，正是「報酬靠少數大贏家」的直接證據。
  傷害還有第二層：停利賣出→冷卻結束→更高價買回，交易數 54→115，手續費與稅再吃一輪。
  ⚠️ 這份結果跑在**修正暖身 bug 之前**（見注意事項 23）：方向極明確可以信，
  但絕對數字要重跑；mid100 那次撞到 FinMind 額度上限，也還沒跑完。
- 新策略一律先過三關再談部署。別急著上真錢。
- **改策略邏輯前先問：這是 bug 還是策略改動？** bug（實盤與回測不一致）直接修；
  策略改動一律先參數化、預設關閉、寫死標準後跑三關，通過才啟用。
