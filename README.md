# 台股策略交易框架 · tw-stock-strategy-framework

一個可以**回測 → 模擬盤 → 實單**的台股自動交易框架，內建多位投資名人的選股策略，並串接 Telegram 做買賣提醒。資料源（FinMind）與券商（永豐金 Shioaji）都封裝在統一介面後可以抽換；不用任何金鑰、不用連網就能跑回測。

> ⚠️ **風險聲明**：本專案僅供程式與策略研究教學，不構成投資建議。回測績效不代表未來表現，自動下單請務必先用模擬帳戶充分測試，並自行承擔交易風險。

---

## 我為什麼做這個

我自己是台股散戶，做這個框架是為了解決兩個我一直克服不了的問題。

第一個是**心理問題**。盤一綠，人就會慌；明明事前想好的紀律，看到數字往下掉就守不住，常常該停損的時候捨不得、該續抱的時候手癢賣掉。我發現問題不在策略，在「人」——機器看到同一個數字不會慌，會照規則執行。所以我想把進出場的判斷交給一套寫死的規則，把情緒從決策裡拿掉。

第二個是**現實問題**。我是上班族，工作時沒辦法一直盯盤，常常等下班打開看才發現訊號早就過了。所以這個框架設計成可以在盤後排程自動掃描，有買賣訊號就透過 Telegram 推到我手機，我不用守在螢幕前，也不會漏掉。

## 我從實測中學到的東西

框架內建了巴菲特、葛拉漢、林區、歐尼爾、李佛摩等幾位名人的策略，但我不是把它們列出來就算了——我用真實台股資料（FinMind）一個一個跑過、互相比較。幾個對我來說很反直覺、但資料逼我接受的結論：

- **聽起來最厲害的策略，不一定能賺。** 我試過一個「美股隔夜領先台股」的套利想法（費半 / 台積電 ADR 領先），回測看起來有道理，但實際跑下來交易太頻繁，獲利全被手續費和證交稅吃光，夏普趨近 0。這讓我學到回測一定要把真實交易成本算進去（我把手續費 0.1425%、證交稅 0.3% 都建進回測引擎），不然會嚴重高估績效。
- **實測下來，對我最有幫助的是彼得林區（GARP）那套**，搭配大盤風向濾網（加權指數跌破年線就禁止做多、只准出場）。它在多頭能賺、在空頭靠濾網保本，這種攻守兼備比單純追求高報酬更符合我想要的「安心」。
- **我刻意防自己背答案。** 量化最容易騙自己的地方是 overfitting——挑歷史上表現最好的股票和參數，回測當然漂亮，但那是事後諸葛。所以我加了 walk-forward 驗證：用訓練期選股、在沒看過的測試期驗證。林區策略在測試期夏普還有 ~1.3、年化 ~7.5%，這才是我願意相信的數字，不是回測那種會騙人的高報酬。

對我來說，這個專案真正的價值不是「自動賺錢」（它不保證賺錢），而是把一套有紀律、扛得住空頭、不被情緒綁架的流程，變成我下班後手機上的一則通知。

---

## 設計重點

- **資料源與券商可抽換**：下單、風控、回測邏輯共用一套介面，資料源（FinMind / 樣本）與券商（Paper 模擬 / Shioaji 實單）都封裝在介面後，要換券商或資料來源不必動到策略與引擎。
- **回測即實盤同一套規則**：回測與實單共用「單一部位、進出成對、套用停損停利」的邏輯，避免回測賺、實盤賠的落差。
- **避免未來函數**：策略的 `evaluate()` 只拿得到「截至當下」的資料（引擎已切片），不會偷看未來。
- **真實交易成本內建**：回測套用台股手續費 0.1425%、證交稅 0.3%（可設折扣）。
- **大盤風向濾網**：加權指數跌破年線（200MA）時禁止做多、只准出場，用來在空頭保本。

## 內建策略

| 代號 | 名人 | 類型 | 核心邏輯 |
|------|------|------|----------|
| `buffett` | 巴菲特 | 價值 / 護城河 | 高 ROE(≥15%)、低負債、合理本益比、有配息 + 站上年線 |
| `graham` | 葛拉漢 | 深度價值 / 安全邊際 | 低 PE(≤15)、低 PB(≤1.5)、葛拉漢數字 PE×PB≤22.5、財務安全 |
| `lynch` | 彼得林區 | 成長合理價 GARP | PEG≤1.2、EPS 成長 15~50%、營收成長 + 站上季線 |
| `oneil` | 歐尼爾 | 動能突破 CANSLIM | 帶量突破 52 週新高、相對強弱 RS≥1、停損 8% |
| `livermore` | 李佛摩 | 順勢趨勢 | 突破關鍵高點 + 順勢，ATR 移動停損、跌破關鍵低點出場 |
| `us_overnight` | 華爾街隔夜 | lead-lag | 美股（費半 / 台積電 ADR）隔夜領先台股；回測證實成本吃光、不建議實用 |

每個策略都在對應檔案開頭詳細註解理念與條件，方便調參或新增（`src/strategies/`）。

---

## 快速開始

```bash
# 1. 安裝核心套件（回測只需要 pandas / numpy）
pip install pandas numpy

# 2. 列出策略
python main.py list

# 3. 用內建樣本資料回測（免金鑰、免連網）
python main.py backtest --strategy lynch --regime --trades

# 4. 指定股票與期間
python main.py backtest --strategy livermore --symbols 2330,2454 --start 2024-01-01

# 5. 模擬盤掃描訊號（dry-run，只印出會下的單，不會真的下單）
python main.py scan --strategy lynch --regime
```

回測輸出包含總報酬率、年化報酬（CAGR）、最大回撤、夏普值、交易次數與明細，並已套用真實台股交易成本。

### 完整指令

| 指令 | 用途 |
|------|------|
| `list` | 列出所有策略 |
| `backtest` | 回測單一策略（`--regime` 風向濾網、`--params` 調參、`--cooldown` 防洗盤） |
| `compare` | 一次比較所有策略跑同一批股票，按夏普排名 |
| `pick` | 逐檔回測整個股池，挑夏普最高的前 N 檔 |
| `walkforward` | 訓練期選股 → 測試期驗證，揭露真實前瞻能力（防 overfitting） |
| `screen` | 列出今日各策略的買進名單（`--notify` 推 Telegram） |
| `scan` | 模擬盤 / 實單自動交易（`--live` 送單、`--realtime` 即時報價、`--notify` 通知） |
| `shioaji-test` | 測試 Shioaji 連線（預設模擬盤） |

### 建議的研究 → 上線流程

```bash
# ① 選股（用 lynch 從股池挑夏普最高 5 檔，務必開 --regime）
python main.py pick --strategy lynch --source finmind --regime --top 5

# ② 防背答案驗證（訓練期選股 → 測試期驗證，看測試期是否還賺）
python main.py walkforward --strategy lynch --source finmind --regime --top 5

# ③ 用選出的組合掃今日訊號（dry-run + Telegram，不下單）
python main.py scan --strategy lynch --source finmind --regime --symbols 2330,2891,2308 --notify

# ④ 確認無誤後 → 模擬盤自動交易（--live 但無真實帳戶 = 假錢）
python main.py scan --strategy lynch --source finmind --regime --realtime --live --notify
```

---

## 接上真實資料（FinMind）

```bash
pip install FinMind
export FINMIND_TOKEN="你的 token"   # 申請：https://finmindtrade.com
python main.py backtest --strategy graham --source finmind --symbols 2330,2317
```

`src/data/finmind.py` 已把 FinMind 轉成框架標準格式。

## 接上實單（永豐金 Shioaji）

金鑰一律走環境變數，切勿寫進程式或 commit：

```bash
pip install shioaji
export SHIOAJI_API_KEY="..."
export SHIOAJI_SECRET_KEY="..."
export SHIOAJI_CA_PATH="/path/to/ca.pfx"
export SHIOAJI_CA_PASSWD="..."
export SHIOAJI_PERSON_ID="身分證字號"
```

```python
from src.data.finmind import FinMindProvider
from src.broker.shioaji_broker import ShioajiBroker
from src.engine.trader import LiveTrader
from src import strategies

provider = FinMindProvider()
broker   = ShioajiBroker(simulation=True)   # 先用模擬盤，確認無誤再改 False
trader   = LiveTrader(provider, broker, strategies.build("lynch"),
                      position_budget=200_000, dry_run=True)  # dry_run 再保險一層

for p in trader.scan(["2330", "2454"], end="2025-12-31"):
    print(p)
```

確認模擬盤 + dry-run 行為正確後，再依序關閉 `dry_run`、把 `simulation` 改 `False`。建議用 cron / APScheduler 在每日盤後觸發 `trader.scan(...)`，而非 `while True`。

---

## 專案結構

```
tw-stock-strategy-framework/
├── main.py                 # 命令列入口
├── config.example.yaml     # 設定範本（複製成 config.yaml）
├── requirements.txt
└── src/
    ├── models.py           # Signal / Fundamentals / Position 等資料模型
    ├── indicators.py       # 技術指標（SMA/EMA/RSI/MACD/ATR/突破/相對強弱）
    ├── strategies/         # 名人策略
    ├── data/               # 資料源（sample 離線樣本 / finmind 真實）
    ├── broker/             # 券商（paper 模擬 / shioaji 實單）+ 台股交易成本
    └── engine/             # backtest 回測引擎 / trader 實單執行器
```

## 測試

```bash
python tests/test_strategies.py      # 內建 runner，免裝 pytest
# 或
pytest tests/
```

## 新增自己的策略

1. 在 `src/strategies/` 新增檔案，繼承 `Strategy`，實作 `evaluate(ctx) -> Signal`。
2. 在 `src/strategies/__init__.py` 的 `REGISTRY` 註冊名稱。
3. 用 `python main.py backtest --strategy <你的名稱>` 回測。

`evaluate()` 只會拿到截至當下的資料（引擎已切片），避免未來函數；回傳 `Signal(action, strength, reason)` 即可，下單與資金控管交給引擎處理。
