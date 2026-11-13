# precommit audit（2026-11-13 第一輪回測結果 + 「過去某一天的名單」）
- 變更範圍：twstock_lab/fetch.py（universe_asof、--asof、FinMind 額度用完就停）、prep.load_manifest 可指定檔名、run_backtest.py（--asof、報告標明名單有沒有後見之明）、backtest.buy_and_hold 回傳一般 float、reports/ 第一輪結果。
- 資料鏈：fetch --asof → data/manifest_asof.json（多一個 universe_date）→ run_backtest --asof → prep.load_manifest("manifest_asof.json") → load_all → reports/REPORT_asof.md、results_asof.csv、meta_asof.json。原本不帶參數的路徑輸出檔名不變。
  - ✔ 名單日期與切分日比較決定報告裡「有 / 沒有後見之明」的文字，兩份報告不會寫錯。
  - ✔ 真實資料驗證：FinMind 收盤價和證交所 / 櫃買 2023-12-29 官方行情比對，144 檔一致、0 檔不一致。
  - ✔ 0050 在 2025-06-18 的 1 拆 4（隔夜 -74.8%）被「異常跳空」規則接起來，沒有被當成暴跌。
  - ✖→已修：報告裡 0050 的數字印成 np.float64(...)，改成一般 float。
  - 更正：策略參數是 20 組 × 3 種停損 = 60 個組合（上一版清查寫成 21 組）。
- 驗證：tests 10 個全過；第一輪回測 60 個組合 16 秒跑完。
- 未驗證：--asof 名單的回測（84 檔還在抓）。

# precommit audit（2026-11-13 第一版：抓資料、還原股價、9 種策略、回測引擎、報告）
- 變更範圍：整個專案第一次提交——twstock_lab/（fetch、prep、strategies、backtest）、run_backtest.py、tests/test_backtest.py、README.md。
- 呼叫鏈（code_map.py trace）：run_backtest.run → prep.load_all → load_manifest / load_exdiv / load_prices / adjust；run → strategies.CATALOG 各函式 → backtest.simulate → stats / portfolio；run → baseline / buy_and_hold → write_report。fetch.fetch_all 只由 `python -m twstock_lab.fetch` 呼叫，回測不連網。
- 資料鏈檢查：
  - ✔ 來源 → 快取 → 整理：證交所 / 櫃買清單 → universe（只留 4 碼、非 00 開頭）；FinMind 日線 → load_prices（拿掉零成交日）；證交所 / 櫃買除權息表 → load_exdiv（民國日期轉西元、係數 = 參考價 / 前收盤，超出合理範圍的丟掉）→ adjust。
  - ✔ 不偷看未來：訊號用當天收盤以前的資料，隔天開盤成交；「前 N 日高低點」先 shift(1)。測試逐一對 9 種策略、21 組參數驗證「加上未來資料後，過去的訊號不變」。
  - ✔ 成本一致：simulate 與對照組 baseline、buy_and_hold 用同一組手續費、證交稅、滑價常數。
  - ✔ 單位：報酬率內部用小數，報表欄位名稱都帶 %，乘 100 只在 stats / portfolio / baseline 的輸出處做一次。
  - ✔ 錯誤路徑：被網站拒絕（SiteBlocked）就停抓那個網站並寫進 manifest 的 failed；單一股票失敗不影響其他；上市不滿 250 天的不回測。
  - ⚠ 已知限制（寫進報告）：股票範圍是今天的熱門股，有後見之明 → 一律和同期間對照組比；投組權益只在出場時更新，最大回落偏樂觀；隔夜跳空超過 11% 以「當晚漲跌 0」接起來。
- 驗證：tests 10 個全過（成交時點、原地進出賠掉成本、漲停買不到 / 跌停賣不掉、停損與最長持有、期末未出場標記、統計與投組、不偷看未來、除息還原、異常跳空、民國日期）。
- 未驗證：真實資料的完整回測還沒跑（資料抓取中）；FinMind 與證交所價格的交叉比對還沒做。
