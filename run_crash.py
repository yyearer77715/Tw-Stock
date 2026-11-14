"""逢低訊號遇到大跌（政治、戰爭、利空）時，加哪種「先等一下」的條件比較不會接到刀子（只讀快取，0 token）：python run_crash.py

事件本身沒辦法預測，能回測的是「市場已經在反應」的樣子。條件都只用訊號當天收盤以前的資料，進場仍是隔天開盤：
  等止穩   個股當天收紅、或收盤站上前一天的最高價，才進場
  恐慌程度 0050 近 5 天跌多少、0050 在不在 200 日均線之上
  廣度     同一天名單裡有多少比例的股票也跌到 60 日線下 15%（很多 = 全面性大跌）
加條件 =「等到條件也成立那天才進場」，所以各組不是把同一批交易切開。結果寫到 reports/CRASH.md。
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List

import numpy as np
import pandas as pd

import run_backtest as rb
from twstock_lab import backtest as bt
from twstock_lab import fetch, prep
from twstock_lab import strategies as st

REPORTS = Path(__file__).resolve().parent / "reports"
STRATEGIES = {
    "乖離率（60 日線下 15%）": (st.bias_dip, dict(n=60, below=0.15)),
    "量縮止跌（60 日新低、量不到七成）": (st.dry_up_low, dict(n=60, ratio=0.7, exit_ma=20)),
}
BIG_LOSS = -0.20


def market_state(frames: Dict[str, pd.DataFrame], bench: pd.DataFrame) -> pd.DataFrame:
    """每天的大盤狀態：0050 近 5 天漲跌、是否在 200 日均線之上、名單裡深跌股票的比例。"""
    close = bench["close"]
    out = pd.DataFrame(index=bench.index)
    out["近5天"] = close / close.shift(5) - 1
    ma200 = st.sma(close, 200)
    out["多頭"] = close > ma200
    out["有200日線"] = ma200.notna()
    deep = pd.DataFrame({c: df["close"] < st.sma(df["close"], 60) * 0.85 for c, df in frames.items()})
    listed = pd.DataFrame({c: df["close"].notna() for c, df in frames.items()})
    out["深跌比例"] = (deep.sum(axis=1) / listed.sum(axis=1)).reindex(out.index)
    return out


def filters(df: pd.DataFrame, market: pd.DataFrame) -> Dict[str, pd.Series]:
    m = market.reindex(df.index)
    bull = m["多頭"].fillna(False).astype(bool)
    known = m["有200日線"].fillna(False).astype(bool)
    return {
        "不加條件": pd.Series(True, index=df.index),
        "等止穩：當天收紅": df["close"] > df["close"].shift(1),
        "等止穩：收盤站上前一天最高": df["close"] > df["high"].shift(1),
        "恐慌：0050 近 5 天跌超過 5%": m["近5天"] <= -0.05,
        "不恐慌：0050 近 5 天跌不到 5%": m["近5天"] > -0.05,
        "0050 在 200 日線之上": bull,
        "0050 在 200 日線之下": ~bull & known,
        "全面大跌：深跌股票超過 10%": m["深跌比例"] > 0.10,
        "個別下跌：深跌股票不到 10%": m["深跌比例"] <= 0.10,
    }


UNIVERSES = {
    "": ("manifest_asof.json", "2023-12-29 成交值前 150 檔"),
    "small": ("manifest_small.json", "2023-12-29 成交值 0.1～3 億的冷門股，照成交值排序後等距取 150 檔"),
}


def run(universe: str = "") -> pd.DataFrame:
    """universe = "small" 改用冷門股名單（先跑 python -m twstock_lab.fetch --asof 2023-12-29 --small），報告檔名加 _small。"""
    manifest_name, universe_text = UNIVERSES[universe]
    suffix = f"_{universe}" if universe else ""
    frames, _ = prep.load_all(prep.load_manifest(manifest_name))
    bench = frames.pop(fetch.BENCHMARK)
    market = market_state(frames, bench)
    first, last = min(df.index[0] for df in frames.values()), max(df.index[-1] for df in frames.values())
    base = {"選": rb.baseline(frames, first, rb.TRAIN_END), "驗": rb.baseline(frames, rb.TRAIN_END, last)}
    rows = []
    for name, (fn, params) in STRATEGIES.items():
        signals = {c: fn(df, **params) for c, df in frames.items()}
        conds = {c: filters(df, market) for c, df in frames.items()}
        for label in next(iter(conds.values())):
            trades: List[bt.Trade] = []
            for c, df in frames.items():
                entry, exit_ = signals[c]
                trades += bt.simulate(c, df, entry & conds[c][label].fillna(False), exit_)
            row = {"策略": name, "條件": label}
            for tag, part in (("選", [t for t in trades if t.entry_date <= rb.TRAIN_END]),
                              ("驗", [t for t in trades if t.entry_date > rb.TRAIN_END])):
                s = bt.stats(part)
                if not s.get("筆數"):
                    continue
                r = np.array([t.ret for t in part])
                b_avg, _ = rb.nearest(base[tag], s["平均持有天"])
                row.update({f"{tag}_筆數": s["筆數"], f"{tag}_勝率%": s["勝率%"], f"{tag}_每筆平均%": s["每筆平均%"],
                            f"{tag}_贏對照%": round(s["每筆平均%"] - b_avg, 2), f"{tag}_平均賠%": s["平均賠%"],
                            f"{tag}_最慘一筆%": round(100 * float(r.min()), 1),
                            f"{tag}_賠超過20%的比例%": round(100 * float((r < BIG_LOSS).mean()), 1)})
            if len(row) > 2:
                rows.append(row)
    table = pd.DataFrame(rows)
    REPORTS.mkdir(exist_ok=True)
    table.to_csv(REPORTS / f"crash{suffix}.csv", index=False, encoding="utf-8-sig")
    cols = ["策略", "條件", "選_筆數", "選_勝率%", "選_贏對照%", "驗_筆數", "驗_勝率%", "驗_每筆平均%", "驗_贏對照%", "驗_平均賠%",
            "驗_最慘一筆%", "驗_賠超過20%的比例%"]
    lines = ["| " + " | ".join(c.replace("_", " ") for c in cols) + " |", "|" + "---|" * len(cols)]
    for _, r in table.iterrows():
        lines.append("| " + " | ".join("" if pd.isna(r.get(c)) else str(r.get(c)) for c in cols) + " |")
    (REPORTS / f"CRASH{suffix}.md").write_text(f"""# 逢低訊號遇到大跌時，先等哪種條件

- 名單：{universe_text}（實際有資料的 {len(frames)} 檔）；挑選期到 {rb.TRAIN_END.date()}，之後是驗證期。
- 政治、戰爭這類事件沒辦法預測，這裡測的是「市場已經在反應」時的幾種判斷方式。條件只用訊號當天收盤以前的資料，進場是隔天開盤。
- 加條件 = 等到條件也成立那天才進場，各組不是把同一批交易切開，筆數相加會超過「不加條件」。
- 「贏對照」= 每筆平均 − 同期間隨便買抱一樣久的平均。條件是跑之前就寫在程式裡的 8 個，沒有事後加。

{chr(10).join(lines)}

## 限制
- 一次比 8 個條件 × 2 個策略，最好的那個有運氣成分；沒有做統計檢定。
- 驗證期只有不到三年，真正的全面大跌只有幾次，「恐慌」那幾組的交易擠在同幾天，不是獨立樣本。
- 這裡看的是每筆交易，不是整個帳戶每天的淨值；帳戶的最大回落要用 run_portfolio.py 另外算。
""", encoding="utf-8")
    return table


if __name__ == "__main__":
    import sys
    result = run(sys.argv[1] if len(sys.argv) > 1 else "")
    pd.set_option("display.width", 280)
    pd.set_option("display.unicode.east_asian_width", True)
    cols = ["策略", "條件", "選_贏對照%", "驗_筆數", "驗_勝率%", "驗_每筆平均%", "驗_贏對照%", "驗_平均賠%", "驗_最慘一筆%", "驗_賠超過20%的比例%"]
    print(result[cols].to_string(index=False))
