# precommit audit（2026-11-14 run_scan.py：列出今天符合逢低條件的股票）
- 變更範圍：run_scan.py（新，只讀快取，不改既有程式）。
- 呼叫鏈：scan → prep.load_manifest / load_all（兩份名單）→ run_crash.market_state（大盤狀態）→ strategies.bias_dip / dry_up_low（和回測同一組參數 60 / 0.15、60 / 0.7 / 20）→ run_chips.load_flows（有法人資料的才算外資連 3 買）。
- 資料鏈檢查：
  - ✔ 條件和回測用同一個函式、同一組參數，沒有另外寫一份判斷。
  - ✔ 只列最後交易日和 0050 同一天的股票（停牌或資料沒更新的不列）；兩份名單重複的股票只列一次。
  - ✔ 輸出第一行印資料日期與大盤狀態，避免把舊快取當成今天。
  - ⚠ 「止穩」「外資連 3 買」只是把回測裡有差別的條件列出來，沒有排序或評分；今天名單的股票多數沒有法人資料（只抓了 2023-12-29 那份名單的）。
- 驗證：實跑一次，資料到 2026-11-13，73 檔當天在訊號中；乖離率數字抽查 2308、2303 與 stock_check 的算法一致（同一個 sma）。
- 未驗證：沒有單元測試；股價是還原後的價格，除權息後和看盤軟體的均線可能差一點。

# precommit audit（2026-11-14 八種新技術方法、大跌濾網 run_crash.py、隔日沖分點 run_daytrade.py）
- 變更範圍：twstock_lab/strategies.py（新增 williams_dip、cci_cross、sar_flip、obv_breakout、gap_up、granville、ichimoku、dry_up_low，CATALOG 12 → 20 種）；run_crash.py（新）；run_daytrade.py（新，分點資料還在抓，analyse 還沒跑過真資料）；tests（14 → 17）；reports/REPORT_asof.md、results_asof.csv、meta_asof.json（重跑，87 → 129 個組合）、reports/CRASH.md、crash.csv。
- 呼叫鏈：run_backtest.run → CATALOG 每個函式 → backtest.simulate（沒改）；run_crash.run → prep.load_all → market_state（0050 與全名單）→ filters → simulate → stats → run_backtest.baseline / nearest；run_daytrade.events → do_fetch（GentleFetcher、三個來源輪流）→ analyse → fubon_parse.parse_summary → flips / daytrade_share / forward_return。
- 資料鏈檢查：
  - ✔ 不偷看未來：新策略都由既有的 NoLookAhead 測試逐一檢查（CATALOG 全部參數）；SAR 是逐日往前算；一目均衡表的雲層是 26 天前的值往後移。大盤狀態與 8 個濾網另有測試（前 250 天算的 = 全部算再取前 250 天）。
  - ✔ 舊策略數字沒變：乖離率 60 / 0.15 不停損仍是 522 筆、74.7%、+8.54%、贏對照 4.51，和上一版報告一致。
  - ✔ 修掉兩個自己寫的錯：「0050 在 200 日線之下」原本對含缺值的布林欄取反，結果整欄為真、數字和「不加條件」一模一樣，改成先轉布林、且均線還沒算出來的日子不算線下；量縮止跌配「當天收紅」不可能成立，空的列不寫進報告。
  - ✔ 成本一致：forward_return 用 backtest 的手續費、證交稅、滑價與漲停門檻常數。
  - ✔ 隔日沖的門檻（漲 7%、至少上榜 8 次、五成倒貨、佔比 20%、認定期到 2025-06-30）都在抓資料前寫在程式開頭。
  - ⚠ 加條件 = 等條件成立才進場，各組不是切開同一批交易；8 個條件 × 2 個策略同時比，有運氣成分。
  - ⚠ 事件 3,571 個等距取樣 600 個；分點排行只有前 15 名，倒貨比例是低估。
- 驗證：tests 17 個全過；run_backtest.py --asof 28 秒跑完；run_crash.py 可重跑；run_daytrade.py 試抓一個事件（3260，2025-12-24 與隔天）兩頁都解析出 15 名買賣超。
- 未驗證：run_daytrade.py 的 analyse 要等 1,200 頁抓完；沒有統計檢定；濾網還沒放進每日結算的投組。

# precommit audit（2026-11-13 法人籌碼條件回測結果：reports/CHIPS.md、chips.csv）
- 變更範圍：只新增兩個報告檔，run_chips.py 沒有改。
- 資料鏈檢查：
  - ✔ 法人資料 150 / 150 檔抓齊（manifest_asof.json 的 datasets）；「不加條件」那幾列和 REPORT_asof.md 同策略數字一致（522 筆、74.7%、+8.54%）。
  - ✔ 條件只用訊號當天收盤後已公布的法人買賣超，進場仍是隔天開盤；條件是抓資料前寫在程式裡的 6 個，沒有事後加。
  - ⚠ 各條件不是把同一批交易切開：逢低訊號會連續好幾天成立，加條件後是「等到條件也成立那天才進場」，所以買超與賣超兩組筆數相加會超過原本筆數，兩組也都可能比不加條件好。
  - ⚠ 一次比 6 個條件 × 3 個策略，挑最好的那個會有運氣成分；「外資近 5 日賣超」也沒有比較差，表示方向性不強。
- 驗證：tests 14 個全過；python run_chips.py 只讀快取可重跑。
- 未驗證：沒有做統計檢定；沒有把籌碼條件放進每日結算的投組。

# precommit audit（2026-11-13 分點集中度回測結果：reports/BRANCH.md、branch.csv）
- 變更範圍：只新增兩個報告檔，程式沒有改。
- 資料鏈檢查：
  - ✔ 522 / 522 筆驗證期交易都抓到分點頁、都解析得出集中度；「全部」那一列（74.7%、+8.54%、贏對照 4.51）和 REPORT_asof.md 的同一策略數字一致，代表交易集合沒有走樣。
  - ✔ 分組門檻（0.5、三等分）是抓資料前寫在程式裡的，沒有看了結果再調。
  - ⚠ 買方集中（> 0.5）只有 87 筆；各組差距 1～3 個百分點，訊號又集中在少數大跌月份，差距可能只是運氣，報告的限制段有寫。
- 驗證：tests 14 個全過；python run_branch.py 可重跑（只讀快取）。
- 未驗證：沒有做統計檢定；只測一個指標、一個策略、一段期間。

# precommit audit（2026-11-13 run_branch.py：逢低訊號加分點買方集中度）
- 變更範圍：run_branch.py（新）、tests（多 1 個，共 14 個）。沒有改到既有程式。
- 呼叫鏈：run_branch.validation_trades → prep.load_all → backtest.simulate（和回測同一個 bias_dip 60 / 0.15）；do_fetch → window_of → branch_url → GentleFetcher（big5、三個來源輪流）→ data/branch/signal_pages.json；analyse → fubon_parse.parse_summary → concentration → backtest.stats → run_backtest.baseline / nearest。
- 資料鏈檢查：
  - ✔ 不偷看未來：分點視窗的最後一天是訊號日（進場日的前一個交易日），不含進場日（測試驗證）。
  - ✔ 指標在抓資料前就定好一個（買超前 15 名合計 ÷ 買賣超前 15 名合計），腳本與報告都寫明，沒有事後挑。
  - ✔ 可行性：指定過去日期區間的排行頁實測抓得到（4931 的 2026-07 與 2024-08 各一頁，買賣各 15 名）。
  - ✔ 錯誤路徑：某個來源被拒絕就停用它、三個都被拒絕才停；單頁失敗跳過；索引每 25 頁存一次，可接續。
  - ⚠ 賣超合計在 parse_summary 裡是負數，concentration 取負號變正；買賣都 0 時回 None 不分組（測試驗證）。
- 驗證：tests 14 個全過。
- 未驗證：analyse 還沒有資料可跑（522 頁抓取中）。

# precommit audit（2026-11-13 每天結算的投組、0050 搭配、法人資料抓取、籌碼條件腳本）
- 變更範圍：twstock_lab/portfolio.py（新：daily_equity、summary）、run_portfolio.py（新）、run_chips.py（新，還沒有資料可跑）、twstock_lab/fetch.py（fetch_dataset、--dataset）、tests（多 3 個）、reports/PORTFOLIO.md 與 portfolio.csv。
- 呼叫鏈：run_portfolio.run → prep.load_all → backtest.simulate（交易清單與回測相同）→ portfolio.daily_equity → summary。run_chips.run → load_flows（FinMind 法人買賣超）→ filters → backtest.simulate（進場訊號 & 籌碼條件）→ run_backtest.baseline / nearest（對照組沿用回測的）。
- 資料鏈檢查：
  - ✔ 出場金額用回測算好的淨報酬（alloc × (1 + ret)），所以每天結算版在出場後的權益和逐筆報酬一致（測試驗證到小數 9 位）。
  - ✔ 持有期間用收盤價估值，沒有成交的日子沿用前一個收盤價；核心標的買賣成本另算（ETF 證交稅 0.1%）。
  - ✔ 籌碼條件只用訊號當天（含）以前的法人資料，rolling 視窗不含未來；進場仍是隔天開盤。
  - ✖→已修：沒有核心標的時進場那一步去讀 core_open（None）會當掉 → 沒有閒置單位就不讀（兩個測試先失敗後通過）。
  - ⚠ 進場時每格資金 = 當下策略資產 ÷ 格數，其中持有部位用「前一天收盤價」估值（當天開盤還不知道收盤）。
- 驗證：tests 13 個全過（新增：持有中途的回落每天結算看得到而舊算法看不到、分格與閒置現金、核心標的與輪動）；run_portfolio 實跑 13 列結果。
- 未驗證：run_chips.py 還沒有資料可跑（法人資料抓取中）。

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
