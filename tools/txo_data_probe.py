#!/usr/bin/env python3
"""選擇權能不能做？——先確認拿不拿得到歷史資料，不要先寫程式。

# 為什麼先做這支

賣選擇權收權利金有真實的風險溢酬（波動率風險溢酬是少數站得住腳的溢酬之一），
但它的風險形狀是「平常小賺、偶爾巨虧」，左尾極肥。這種東西**絕對不能**
沒回測就上線——而回測需要歷史選擇權報價。

而這份資料很可能拿不到：之前評估多流派 spec 策略時就卡過 FinMind 的
贊助會員限制（分點資料、選擇權資料）。所以第一步不是寫程式，是確認：

  1. FinMind 的 DataLoader 有沒有選擇權／期貨的方法
  2. 用**你的 token** 實際抓得到嗎（免費層很多表會回空或擋權限）
  3. 抓到的欄位夠不夠做回測（要有履約價、到期日、買賣權別、收盤價、未平倉）

拿不到就誠實收手，不要用「理論上可行」去換你的錢。

# 這支不會做的事

不下單、不改設定、不寫任何策略。它只回答「資料在不在」。

用法（在 VM 上，.env 有 FINMIND_TOKEN）：
    .venv/bin/python tools/txo_data_probe.py
    .venv/bin/python tools/txo_data_probe.py --date 2026-09-15
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 回測一個「賣價外選擇權」策略最少需要的欄位
NEEDED = {
    "履約價": ("strike_price", "strike"),
    "買賣權別": ("call_put", "type"),
    "到期月份": ("contract_date", "settlement_date", "expire_date"),
    "收盤價": ("close",),
    "未平倉": ("open_interest",),
}

# 依重要性排序：前兩個是選擇權本體，期貨是 Black-76 的標的遠期價。
# 第三個欄位是「商品代號」，參數名**不寫死**——不同 FinMind 版本叫
# data_id / option_id / futures_id，第一版寫死 data_id 結果三個全部 TypeError，
# 卻被印成「拿不到選擇權歷史資料」。那是把自己的 bug 當成資料結論，
# 是這個 session 反覆在修的同一類錯：**失敗要歸因正確，不然比沒測更糟。**
PROBES = [
    ("選擇權日成交 (TXO)", "taiwan_option_daily", "TXO"),
    ("選擇權法人未平倉", "taiwan_option_institutional_investors", "TXO"),
    ("期貨日成交 (TX)", "taiwan_futures_daily", "TX"),
]

# 候選的「商品代號」參數名，照順序比對實際簽章
ID_PARAMS = ("option_id", "futures_id", "data_id", "stock_id", "symbol")


def _id_kwarg(fn, value):
    """看函式真正的簽章決定商品代號要用哪個參數名；找不到就不傳。"""
    import inspect
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return {}, "（看不到簽章）"
    for name in ID_PARAMS:
        if name in params:
            return {name: value}, f"{name}={value!r}"
    return {}, "（簽章沒有商品代號參數，只傳日期）"


def _pad(text: str, width: int) -> str:
    """靠左補到「顯示寬度」。中文在終端算兩格，用 len() 補會排不齊。"""
    import unicodedata
    w = sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in text)
    return text + " " * max(width - w, 0)


def _probe(api, label, method, data_id, start, end):
    """回傳 (狀態, 說明, DataFrame)。

    狀態要能分辨三件完全不同的事，不可混為一談：
      「呼叫方式錯」= 本程式的 bug，**不是**資料拿不到
      「權限不足」/「空資料」= 真的拿不到
      「可用」= 拿到了
    """
    import inspect

    fn = getattr(api, method, None)
    if fn is None:
        return "方法不存在", f"DataLoader 沒有 {method}()（FinMind 版本太舊？）", None
    kwargs, how = _id_kwarg(fn, data_id)
    try:
        sig = str(inspect.signature(fn))
    except (TypeError, ValueError):
        sig = "?"
    try:
        df = fn(start_date=start, end_date=end, **kwargs)
    except TypeError as e:
        # 參數對不上 → 是本程式呼叫錯，不是資料問題。把簽章印出來好修。
        return "呼叫方式錯", f"{e}｜實際簽章 {method}{sig}", None
    except Exception as e:
        msg = str(e)
        low = msg.lower()
        if any(k in low for k in ("sponsor", "權限", "permission", "upgrade", "402", "403")):
            return "權限不足", f"需要付費/贊助會員：{msg[:110]}", None
        return "錯誤", f"{msg[:130]}｜簽章 {method}{sig}", None
    if df is None or getattr(df, "empty", True):
        return "空資料", f"有權限但沒回資料（{how}；換日期或免費層不給這張表）", None
    return "可用", f"{len(df)} 列、{len(df.columns)} 欄（{how}）", df


def main():
    ap = argparse.ArgumentParser(description="選擇權歷史資料可行性探針")
    ap.add_argument("--date", default="", help="探測日期 (預設最近的工作日附近)")
    ap.add_argument("--days", type=int, default=5, help="往前抓幾天")
    args = ap.parse_args()

    import pandas as pd

    import main as cli
    cli._load_dotenv()

    if not os.getenv("FINMIND_TOKEN"):
        print("⚠️ 沒有 FINMIND_TOKEN，抓到的會是匿名等級額度，結果不代表你的方案。")

    end = args.date or (pd.Timestamp.today() - pd.Timedelta(days=3)).strftime("%Y-%m-%d")
    start = (pd.Timestamp(end) - pd.Timedelta(days=args.days)).strftime("%Y-%m-%d")

    from src.data.finmind import FinMindProvider
    api = FinMindProvider().api

    print(f"探測期間 {start} ~ {end}\n")
    print("=" * 72)
    results = {}
    for label, method, data_id in PROBES:
        status, detail, df = _probe(api, label, method, data_id, start, end)
        icon = {"可用": "✅", "空資料": "⚠️", "權限不足": "🔴", "方法不存在": "🔴",
                "呼叫方式錯": "🐞", "錯誤": "🔴"}[status]
        print(f"{icon} {_pad(label, 22)}{status:<10} {detail}")
        results[label] = (status, df)

    # 🐞 一律先修程式再談結論——不可以把自己的 bug 說成「資料拿不到」
    bugs = [k for k, (st, _) in results.items() if st == "呼叫方式錯"]
    if bugs:
        print("\n" + "=" * 72)
        print("🐞 **這次沒有測到資料，是本程式呼叫方式錯了**："
              f"{'、'.join(bugs)}")
        print("   上面有印出實際簽章，照它修 ID_PARAMS 或 PROBES 再跑一次。")
        print("   **不要把這個當成「選擇權做不了」的結論。**")
        return 2

    # 欄位檢查：只有「可用」的選擇權表才值得看
    opt_status, opt_df = results.get("選擇權日成交 (TXO)", ("", None))
    if opt_df is not None:
        print("\n" + "=" * 72)
        print("選擇權表的欄位（回測需要的那幾個）：")
        cols = {c.lower() for c in opt_df.columns}
        missing = []
        for human, cands in NEEDED.items():
            hit = next((c for c in cands if c.lower() in cols), None)
            print(f"  {'✓' if hit else '✗'} {human:<8} {hit or '找不到（候選：' + '/'.join(cands) + '）'}")
            if not hit:
                missing.append(human)
        print(f"\n  實際欄位：{list(opt_df.columns)}")
        print(f"\n  資料樣本（前 3 列）：")
        print(opt_df.head(3).to_string(max_colwidth=18))
    else:
        missing = list(NEEDED)

    print("\n" + "=" * 72)
    print("結論：\n")
    if opt_status == "可用" and not missing:
        print("🟢 **資料拿得到，欄位也夠** → 選擇權這條路可以往下走。")
        print("   下一步（順序不可顛倒）：")
        print("   1. 先補 txo-options-lab 的 config/margin.toml（A/B/C 保證金目前是 0，")
        print("      要去期交所公告頁抄實際數字，否則模擬的保證金追繳完全不準）")
        print("   2. 寫「賣價外 call/put」的回測，標準事前寫死，特別要測**最壞那幾天**")
        print("   3. 紙上帳戶空跑至少跨過一次大波動，再談任何真錢")
        print("   ⚠️ 提醒：賣選擇權的風險形狀是「平常小賺、偶爾巨虧」。回測的平均報酬")
        print("   漂亮不代表能上線，要看的是最大單日虧損與保證金追繳會不會爆倉。")
    elif opt_status == "可用" and missing:
        print(f"🟡 **資料抓得到，但缺欄位：{'、'.join(missing)}**")
        print("   少了這些做不出可信的回測（例如沒有履約價就無法判斷價外多少）。")
        print("   可以先查 FinMind 文件看是不是別張表提供，或改用期交所公開的每日行情檔。")
    else:
        print("🔴 **拿不到選擇權歷史資料** → 這條路現在走不了，建議先收手。")
        print("   不是「做不到」，是**沒有資料就沒有回測，沒有回測就不該碰真錢**——")
        print("   尤其賣選擇權是左尾極肥的東西，憑感覺上線是最快歸零的方式。")
        print("   可行的替代路徑：")
        print("   1. 期交所官網有每日行情免費下載（需要自己寫下載+解析，工程量不小）")
        print("   2. 先用 txo-options-lab 把定價/Greeks/保證金算對，純當學習工具")
        print("   3. 衛星層先做機械式那條（tools/dip_add_compare.py），資料已經有了")
    print("\n（本檔只探測資料，不下單、不改設定、不寫策略。）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
