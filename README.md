# Tw-Stock：台股技術策略回測

用公開的日線資料回測常見的技術面策略，找出哪些在「沒看過的資料」上還站得住。全部是本機 Python 腳本，重跑不需要 AI。

## 怎麼跑

```bash
pip install pandas numpy

# 1. 抓資料（約 40 分鐘；溫和抓取：一次一個請求、同站隔 6～12 秒、有快取，中斷後重跑不重抓）
python -m twstock_lab.fetch

# 2. 回測並產生報告（只讀快取，約 1 分鐘）
python run_backtest.py

# 測試
python -m unittest discover -s tests
```

單一個股體檢（技術面、月營收、法人、融資、分點買賣超，並檢查回測驗證過的進場條件）：

```bash
python stock_check.py 2495      # 或 python stock_check.py 普安
```

結果在 `reports/REPORT.md`（摘要）與 `reports/results.csv`（全部組合）。

抓資料用的是記憶中樞的 `gentle-fetch` 技能（`~/.claude/ai-memory-hub/skills/gentle-fetch/scripts/gentle_fetch.py`）；放在別處時設定環境變數 `GENTLE_FETCH_DIR`。

## 檔案

| 檔案 | 做什麼 |
|---|---|
| `twstock_lab/fetch.py` | 選股票範圍（成交值前 150 檔上市櫃普通股）、抓日線與除權息資料 |
| `twstock_lab/prep.py` | 把原始資料整理成日線表，用除權息參考價還原股價 |
| `twstock_lab/strategies.py` | 技術指標與 9 種策略（均線交叉、通道突破、帶量突破、RSI 拉回、KD、MACD、布林下軌、布林突破、均線拉回） |
| `twstock_lab/backtest.py` | 回測引擎：隔天開盤成交、手續費、證交稅、滑價、漲跌停、停損、統計 |
| `run_backtest.py` | 跑全部組合，切成挑選期與驗證期，和對照組比較，寫報告 |
| `tests/test_backtest.py` | 引擎、還原股價、「策略不偷看未來」的測試 |

## 資料來源

- 股票清單與成交值：證交所、櫃買中心的 OpenAPI
- 日線：FinMind `TaiwanStockPrice`
- 除權息參考價：證交所 TWT49U、櫃買 exDailyQ
- 沒用 Yahoo Finance：它的 robots.txt 不允許程式抓取

`data/` 不進 git（原始資料可以重抓）。

## 重要限制

回測不是獲利保證。股票範圍是「今天」的熱門股，帶有後見之明；報告裡每個策略都和「同期間隨便買」的對照組比較，請看有沒有贏過對照組，不要只看勝率或絕對報酬。詳細限制寫在報告最後一節。
