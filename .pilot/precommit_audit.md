# precommit audit（2026-11-13 theme_screen.py：題材名單初篩）
- 變更範圍：theme_screen.py（新）。沒有改到既有程式。
- 呼叫鏈：theme_screen.main → screen_one → stock_check.find_stock / exdiv_events / FINMIND 網址（和個股體檢同一組，快取共用）→ prep.load_prices / adjust → strategies.sma / prior_high / bollinger_dip。
- 資料鏈檢查：
  - ✔ 六項起漲條件都只用當天收盤以前的資料；「創 20 日新高」用 prior_high（先 shift(1)）。
  - ✔ 兩個逢低條件和回測、個股體檢同一個函式與參數（bollinger_dip 20/2.0/120；60 日乖離 < −15%）。
  - ✔ 錯誤路徑：名稱對不到、FinMind 額度用完（402）都只略過那一檔並印在 stderr，其他照跑（實跑時 3583 因額度用完被略過）。
  - ⚠ 起漲分數不是回測驗證過的買點（順勢訊號逐年不穩定），腳本開頭的說明與輸出欄位名稱都只叫「分數」，沒有叫「買進訊號」。
- 驗證：對 20 檔中介層相關個股實跑，19 檔有結果；tests 10 個全過（既有程式沒動）。
- 未驗證：theme_screen.py 沒有自己的單元測試。

# precommit audit（2026-11-13 補齊十種常見技術方法：乖離率、K 線型態、DMI）
- 變更範圍：twstock_lab/strategies.py（bias_dip、candle、dmi_cross 三個函式與 CATALOG 三筆）、run_backtest.py（報告裡的策略種數改成實際數字）、stock_check.py（多印 60 日乖離率與乖離率條件）、reports/ 兩輪結果重產（60 → 87 個組合）。
- 資料鏈檢查：
  - ✔ 不偷看未來：tests 的 NoLookAhead 會自動跑 CATALOG 裡每一個策略與每組參數，新增的 9 組都通過（K 線型態的固定持有天數用 entry.shift(hold)，只往後移）。
  - ✔ 原有 60 個組合的數字和上一版相同（只多了新策略的列）。
  - ✔ DMI 用 Wilder 平滑（ewm alpha = 1/n），和 RSI 同一種算法。
  - ⚠ 乖離率（60 日線下 15%）不停損的結果要連風險一起看：驗證期 522 筆裡 86 筆到資料結束還沒出場（用最後收盤價結算）、22 筆賠超過 20%、最長抱 186 天、六成的進場集中在 4 個大跌的月份。已寫進 stock_check 的輸出與全域技能的回報規則。
- 驗證：tests 10 個全過；兩輪回測各 87 個組合跑完；stock_check 對 4931、2495 實跑。
- 未驗證：K 線型態只做了多頭吞噬與錘子兩種，定義是我選的常見版本，不同看盤軟體的定義有出入。

# precommit audit（2026-11-13 stock_check.py 加季財報與本益比）
- 變更範圍：stock_check.py 多一段「季財報與評價」（FinMind TaiwanStockFinancialStatements、TaiwanStockPER）。回測程式沒動。
- 資料鏈檢查：
  - ✔ 單季還是累計：新盛力 2026Q3 財報營收 13.69 億 = 7～9 月月營收 4.49 + 4.95 + 4.25，確認是單季數字，近四季每股盈餘可以直接相加。
  - ✔ 自己算的本益比（收盤 ÷ 近四季每股盈餘）和官方資料集一致：新盛力 23.0 / 23.01、普安 14.8 / 14.77。
  - ✔ 單位：金額換成億元；毛利率、營益率 = 毛利 / 營業利益 ÷ 營收。
  - ✔ 錯誤路徑：近四季虧損時不印本益比；財報不足 8 季不算成長率；本益比資料少於 60 天不算區間。
  - 加一欄業外損益：普安 2025Q2、Q3 的淨利主要來自業外（-1.92 億、+2.17 億），只看每股盈餘會誤判本業。
- 驗證：tests 10 個全過；新盛力、普安實跑。
- 未驗證：金融股、虧損股的財報欄位名稱可能不同（沒有 GrossProfit 時會印 nan，不會當掉）。

# precommit audit（2026-11-13 資料位置可指定 + 匯出成全域技能 tw-stock-check）
- 變更範圍：twstock_lab/fetch.py（DATA 可由環境變數 TWSTOCK_DATA_DIR 指定，CACHE 與 manifest 都跟著它）、prep.py（load_manifest / data_dir 改用 fetch.DATA）、stock_check.py（分點快取改用 fetch.DATA）、export_skill.sh（新）、README。
- 資料鏈：沒設環境變數時 DATA = 專案的 data/，路徑與之前完全相同（回測重跑結果一致）；全域技能的入口 check.py 把它設成 ~/.claude-max-pilot/data/tw_stock，快取不會寫進技能資料夾、也就不會被記憶中樞同步出去。
  - ✔ 專案裡已經沒有任何寫死的 ROOT / "data"（Grep 確認只剩 DATA 的定義那一行）。
  - ✔ export_skill.sh 只複製 stock_check.py 與 twstock_lab 的五個檔，不帶資料、報告、測試。
- 驗證：tests 10 個全過；run_backtest --asof 重跑成功；從 /tmp 用全域技能的指令實跑 2330，六段輸出正常，資料落在 ~/.claude-max-pilot/data/tw_stock，技能資料夾裡沒有多出資料檔。
- 未驗證：Windows 電腦上的實跑（路徑用 Path.home()，理論上相同）。

# precommit audit（2026-11-13 stock_check.py：單一個股體檢）
- 變更範圍：stock_check.py（新）、twstock_lab/backtest.py（stats 的數字轉成一般 float，只影響顯示）。
- 呼叫鏈：stock_check.check → find_stock（證交所 / 櫃買清單）→ prep.load_prices / adjust（沿用回測的還原）→ strategies.bollinger_dip → backtest.simulate / stats / buy_and_hold；finmind（月營收、法人、融資）；記憶中樞 tw-branch-chips 的 fubon_parse.parse_summary（分點排行）。沒有改到回測流程。
- 資料鏈檢查：
  - ✔ 還原股價用的除權息表與回測同一組網址與同一個 load_exdiv。
  - ✔ 進場條件與回測同一個函式、同一組參數（20, 2.0, 120），不是另外寫一份。
  - ✔ 單位：成交量與法人、融資、分點都換成「張」；營收換成「億元」。
  - ✔ 上市股票的清單沒有發行股數欄位 → 不印「占股本」比例，不亂估。
  - ✔ 錯誤路徑：名稱對到 0 檔或多檔會列出候選並要求改用代號；分點單一期間抓不到只略過那一段。
- 驗證：tests 10 個全過；實跑普安（2495），數字合理（收盤價與證交所清單一致）。新盛力是用同一段邏輯的手動版算的，沒有用這支腳本重跑。
- 未驗證：stock_check.py 沒有自己的單元測試（它是把已測過的函式和抓到的資料印出來）；分點三個來源的一致性只在 2026-11-13 對新盛力驗過一次。

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
