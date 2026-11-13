"""逢低條件加上籌碼條件有沒有比較好（只讀快取，0 token）：python run_chips.py

先抓法人資料：python -m twstock_lab.fetch --dataset TaiwanStockInstitutionalInvestorsBuySell
做法：原本的進場訊號再加一個「訊號當天收盤後就知道的」籌碼條件（法人買賣超收盤後公布，隔天開盤進場來得及），
重新模擬交易，和不加條件的版本、以及同期間對照組比。只用有法人資料的股票。結果寫到 reports/CHIPS.md。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List

import pandas as pd

import run_backtest as rb
from twstock_lab import backtest as bt
from twstock_lab import fetch, prep
from twstock_lab import strategies as st

DATASET = "TaiwanStockInstitutionalInvestorsBuySell"
REPORTS = Path(__file__).resolve().parent / "reports"
FOREIGN = ("Foreign_Investor", "Foreign_Dealer_Self")
TRUST = ("Investment_Trust",)

STRATEGIES = {
    "乖離率（60 日線下 15%）": (st.bias_dip, dict(n=60, below=0.15)),
    "乖離率（20 日線下 12%）": (st.bias_dip, dict(n=20, below=0.12)),
    "布林下軌反彈": (st.bollinger_dip, dict(n=20, width=2.0, trend=120)),
}


def load_flows(manifest: dict, code: str, index: pd.DatetimeIndex) -> pd.DataFrame:
    """每天的外資、投信買賣超（張），對齊到這檔股票的交易日。"""
    data = json.loads(fetch.fetcher().get(manifest["datasets"][DATASET][code]))["data"]
    raw = pd.DataFrame(data)
    raw["date"] = pd.to_datetime(raw["date"])
    raw["net"] = (raw["buy"] - raw["sell"]) / 1000
    wide = raw.pivot_table(index="date", columns="name", values="net", aggfunc="sum")
    out = pd.DataFrame(index=index)
    out["外資"] = wide.reindex(columns=list(FOREIGN)).sum(axis=1).reindex(index).fillna(0)
    out["投信"] = wide.reindex(columns=list(TRUST)).sum(axis=1).reindex(index).fillna(0)
    return out


def filters(flow: pd.DataFrame) -> Dict[str, pd.Series]:
    """籌碼條件（都只用當天收盤後已公布的資料）。"""
    return {
        "不加條件": pd.Series(True, index=flow.index),
        "外資當天買超": flow["外資"] > 0,
        "外資近 5 日合計買超": flow["外資"].rolling(5).sum() > 0,
        "外資近 20 日合計買超": flow["外資"].rolling(20).sum() > 0,
        "投信近 5 日合計買超": flow["投信"].rolling(5).sum() > 0,
        "外資近 5 日合計賣超": flow["外資"].rolling(5).sum() < 0,
        "外資連 3 天買超": (flow["外資"] > 0).rolling(3).sum() == 3,
    }


def run() -> pd.DataFrame:
    manifest = prep.load_manifest("manifest_asof.json")
    frames, _ = prep.load_all(manifest)
    frames.pop(fetch.BENCHMARK, None)
    have = manifest.get("datasets", {}).get(DATASET, {})
    frames = {c: df for c, df in frames.items() if c in have}
    if not frames:
        raise SystemExit("還沒有法人資料，先跑：python -m twstock_lab.fetch --dataset " + DATASET)
    flows = {c: load_flows(manifest, c, df.index) for c, df in frames.items()}
    first, last = min(df.index[0] for df in frames.values()), max(df.index[-1] for df in frames.values())
    base = {"選": rb.baseline(frames, first, rb.TRAIN_END), "驗": rb.baseline(frames, rb.TRAIN_END, last)}
    rows = []
    for name, (fn, params) in STRATEGIES.items():
        signals = {c: fn(df, **params) for c, df in frames.items()}
        conds = {c: filters(flows[c]) for c in frames}
        for label in next(iter(conds.values())):
            trades: List[bt.Trade] = []
            for c, df in frames.items():
                entry, exit_ = signals[c]
                trades += bt.simulate(c, df, entry & conds[c][label], exit_)
            row = {"策略": name, "籌碼條件": label}
            for tag, part in (("選", [t for t in trades if t.entry_date <= rb.TRAIN_END]),
                              ("驗", [t for t in trades if t.entry_date > rb.TRAIN_END])):
                s = bt.stats(part)
                if not s.get("筆數"):
                    continue
                b_avg, _ = rb.nearest(base[tag], s["平均持有天"])
                row.update({f"{tag}_筆數": s["筆數"], f"{tag}_勝率%": s["勝率%"], f"{tag}_每筆平均%": s["每筆平均%"],
                            f"{tag}_贏對照%": round(s["每筆平均%"] - b_avg, 2), f"{tag}_平均賠%": s["平均賠%"],
                            f"{tag}_平均持有天": s["平均持有天"]})
            rows.append(row)
    table = pd.DataFrame(rows)
    REPORTS.mkdir(exist_ok=True)
    table.to_csv(REPORTS / "chips.csv", index=False, encoding="utf-8-sig")
    cols = ["策略", "籌碼條件", "選_筆數", "選_勝率%", "選_贏對照%", "驗_筆數", "驗_勝率%", "驗_每筆平均%", "驗_贏對照%", "驗_平均賠%", "驗_平均持有天"]
    lines = ["| " + " | ".join(c.replace("_", " ") for c in cols) + " |", "|" + "---|" * len(cols)]
    for _, r in table.iterrows():
        lines.append("| " + " | ".join("" if pd.isna(r.get(c)) else str(r.get(c)) for c in cols) + " |")
    (REPORTS / "CHIPS.md").write_text(f"""# 逢低條件加上籌碼條件

- 名單：2023-12-29 成交值前 150 檔中有法人資料的 {len(frames)} 檔；挑選期到 {rb.TRAIN_END.date()}，之後是驗證期。
- 籌碼條件只用訊號當天收盤後已公布的法人買賣超；進場仍是隔天開盤。外資 = 外資及陸資 + 外資自營商。
- 「贏對照」= 每筆平均 − 同期間隨便買抱一樣久的平均。加了條件後交易筆數會變少，筆數少的結果不可靠。

{chr(10).join(lines)}
""", encoding="utf-8")
    return table


if __name__ == "__main__":
    result = run()
    pd.set_option("display.width", 260)
    pd.set_option("display.unicode.east_asian_width", True)
    print(result.to_string(index=False))
