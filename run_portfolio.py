"""每天結算的投組比較（只讀快取，0 token）：python run_portfolio.py

比的是「怎麼配置資金」：只做策略、平常抱 0050 大跌時換成跌深股（輪動）、七成 0050 加三成預備金做策略，
和單純抱 0050 比年化報酬與最大回落。名單用 manifest_asof.json（2023-12-29 的成交值前 150 檔）。
結果寫到 reports/PORTFOLIO.md。
"""

from __future__ import annotations

from pathlib import Path
from typing import List

import pandas as pd

from twstock_lab import backtest as bt
from twstock_lab import fetch, portfolio as pf, prep
from twstock_lab import strategies as st

TRAIN_START, TRAIN_END = pd.Timestamp("2019-01-01"), pd.Timestamp("2023-12-31")
REPORTS = Path(__file__).resolve().parent / "reports"

STRATEGIES = {
    "乖離率（60 日線下 15%）": (st.bias_dip, dict(n=60, below=0.15)),
    "乖離率（20 日線下 12%）": (st.bias_dip, dict(n=20, below=0.12)),
    "布林下軌反彈": (st.bollinger_dip, dict(n=20, width=2.0, trend=120)),
}
# 名稱 → (策略占比, 格數, 閒置資金是否放 0050)
LAYOUTS = {
    "只做策略，10 格，閒錢放現金": (1.0, 10, False),
    "只做策略，20 格，閒錢放現金": (1.0, 20, False),
    "輪動：平常全抱 0050，有訊號才換過去（10 格）": (1.0, 10, True),
    "七成 0050 + 三成預備金做策略（5 格）": (0.3, 5, False),
}


def all_trades(frames, fn, params) -> List[bt.Trade]:
    trades: List[bt.Trade] = []
    for code, df in frames.items():
        trades += bt.simulate(code, df, *fn(df, **params))
    return trades


def run() -> pd.DataFrame:
    frames, _ = prep.load_all(prep.load_manifest("manifest_asof.json"))
    core = frames.pop(fetch.BENCHMARK)
    last = core.index[-1]
    rows = []
    bench = pf.daily_equity(frames, [], core=core, sleeve=0.0, start=TRAIN_START)
    rows.append({"策略": "（對照）0050 買進抱著", "配置": "全部 0050",
                 **{f"選_{k}": v for k, v in pf.summary(bench, TRAIN_START, TRAIN_END).items()},
                 **{f"驗_{k}": v for k, v in pf.summary(bench, TRAIN_END, last).items()}})
    for name, (fn, params) in STRATEGIES.items():
        trades = all_trades(frames, fn, params)
        old = bt.portfolio([t for t in trades if t.entry_date > TRAIN_END])
        for layout, (sleeve, slots, idle) in LAYOUTS.items():
            curve = pf.daily_equity(frames, trades, core=core, sleeve=sleeve, slots=slots, idle_in_core=idle, start=TRAIN_START)
            row = {"策略": name, "配置": layout,
                   **{f"選_{k}": v for k, v in pf.summary(curve, TRAIN_START, TRAIN_END).items()},
                   **{f"驗_{k}": v for k, v in pf.summary(curve, TRAIN_END, last).items()}}
            if layout.startswith("只做策略，10 格"):
                row["驗_舊算法最大回落%"] = old.get("最大回落%")
            rows.append(row)
    table = pd.DataFrame(rows)
    REPORTS.mkdir(exist_ok=True)
    table.to_csv(REPORTS / "portfolio.csv", index=False, encoding="utf-8-sig")
    cols = ["策略", "配置", "選_年化%", "選_最大回落%", "驗_年化%", "驗_最大回落%", "驗_策略部位平均占比%", "驗_舊算法最大回落%"]
    lines = ["| " + " | ".join(c.replace("_", " ") for c in cols) + " |", "|" + "---|" * len(cols)]
    for _, r in table.iterrows():
        lines.append("| " + " | ".join("" if pd.isna(r.get(c)) else str(r.get(c)) for c in cols) + " |")
    (REPORTS / "PORTFOLIO.md").write_text(f"""# 每天結算的投組比較

- 名單：2023-12-29 成交值前 150 檔。挑選期 {TRAIN_START.date()} ～ {TRAIN_END.date()}，驗證期之後到 {last.date()}（驗證期沒有名單造成的後見之明）。
- 每天用收盤價估值，所以最大回落包含持有期間的帳面虧損；「舊算法」是只在出場時更新權益的版本，拿來對照。
- 策略都不設停損；同一天訊號比空位多時照股票代號先後取。成本同回測；0050 買賣另計（證交稅 0.1%）。

{chr(10).join(lines)}

## 限制
- 0050 在驗證期年化約五成，是很少見的大多頭；任何把資金從 0050 移出去的配置在這段期間都會少賺，不代表以後也一樣。
- 輪動模式假設每天都能用開盤價買賣 0050，沒有算零股與流動性。
- 過去的結果不代表未來。
""", encoding="utf-8")
    return table


if __name__ == "__main__":
    result = run()
    pd.set_option("display.width", 260)
    pd.set_option("display.max_colwidth", 50)
    pd.set_option("display.unicode.east_asian_width", True)
    print(result.to_string(index=False))
