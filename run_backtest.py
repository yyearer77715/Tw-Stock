"""跑全部策略的回測並產生報告（只讀 data/ 快取，不連網、不呼叫任何 AI）：
    python run_backtest.py            # 今天的熱門股名單（有後見之明，數字偏樂觀）
    python run_backtest.py --asof     # 過去某一天的名單（python -m twstock_lab.fetch --asof 日期 抓的），驗證期沒有後見之明

做法：
1. 每個策略 × 每組參數 × 每種停損設定，在每一檔股票上模擬交易。
2. 依進場日期切成「選策略用」（TRAIN_END 以前）與「驗證用」（之後）兩段。策略只准用前一段挑，後一段是它沒看過的資料。
3. 和兩個基準比：同期間隨便哪天買進抱一樣久（對照組）、0050 買進抱著不動。
結果寫到 reports/results.csv 與 reports/REPORT.md。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from twstock_lab import backtest as bt
from twstock_lab import fetch, prep
from twstock_lab.strategies import CATALOG

TRAIN_END = pd.Timestamp("2023-12-31")
MIN_TRADES = 200   # 選策略那段至少要有這麼多筆交易，太少的統計不可信
HORIZONS = [3, 5, 10, 20, 40, 60, 120]
# (停損比例, 最長持有交易日)；None = 不設
RISK_OPTIONS: List[Tuple[Optional[float], Optional[int]]] = [(None, None), (0.08, None), (0.08, 20)]
REPORTS = Path(__file__).resolve().parent / "reports"


def label(params: dict, stop: Optional[float], max_hold: Optional[int]) -> str:
    text = "、".join(f"{k}={v}" for k, v in params.items())
    risk = ("停損{:.0%}".format(stop) if stop else "不停損") + (f"、最多抱{max_hold}天" if max_hold else "")
    return f"{text}｜{risk}"


def baseline(frames: Dict[str, pd.DataFrame], start: pd.Timestamp, end: pd.Timestamp) -> Dict[int, Tuple[float, float]]:
    """對照組：這段期間「任何一天收盤後決定、隔天開盤買、抱 h 個交易日後開盤賣」的平均報酬與勝率（成本算法和回測相同）。
    股票範圍是今天的熱門股，本來就偏向漲過的，所以任何做多策略都要贏過這個對照組才算有本事。"""
    cost = (1 - bt.SLIPPAGE) * (1 - bt.FEE - bt.TAX) / ((1 + bt.SLIPPAGE) * (1 + bt.FEE))
    out: Dict[int, Tuple[float, float]] = {}
    for h in HORIZONS:
        rets = []
        for df in frames.values():
            o = df["open"]
            r = (o.shift(-(h + 1)) / o.shift(-1)) * cost - 1
            rets.append(r[(df.index >= start) & (df.index <= end)].dropna().to_numpy())
        allr = np.concatenate(rets) if rets else np.array([])
        out[h] = (round(100 * float(allr.mean()), 2), round(100 * float((allr > 0).mean()), 1)) if len(allr) else (0.0, 0.0)
    return out


def nearest(base: Dict[int, Tuple[float, float]], days: float) -> Tuple[float, float]:
    return base[min(HORIZONS, key=lambda h: abs(h - days))]


def run(manifest_name: str = "manifest.json", suffix: str = "") -> pd.DataFrame:
    manifest = prep.load_manifest(manifest_name)
    frames, jumps = prep.load_all(manifest)
    bench = frames.pop(fetch.BENCHMARK, None)
    first = min(df.index[0] for df in frames.values())
    last = max(df.index[-1] for df in frames.values())
    base_train, base_test = baseline(frames, first, TRAIN_END), baseline(frames, TRAIN_END, last)
    rows = []
    for name, (fn, grid) in CATALOG.items():
        for params in grid:
            signals = {code: fn(df, **params) for code, df in frames.items()}
            for stop, max_hold in RISK_OPTIONS:
                trades: List[bt.Trade] = []
                for code, df in frames.items():
                    trades += bt.simulate(code, df, *signals[code], stop=stop, max_hold=max_hold)
                train = [t for t in trades if t.entry_date <= TRAIN_END]
                test = [t for t in trades if t.entry_date > TRAIN_END]
                s_train, s_test = bt.stats(train), bt.stats(test)
                row = {"策略": name, "設定": label(params, stop, max_hold)}
                for tag, s, base in (("選", s_train, base_train), ("驗", s_test, base_test)):
                    if not s.get("筆數"):
                        continue
                    b_avg, b_win = nearest(base, s["平均持有天"])
                    row.update({f"{tag}_{k}": v for k, v in s.items()})
                    row[f"{tag}_對照平均%"], row[f"{tag}_對照勝率%"] = b_avg, b_win
                    row[f"{tag}_贏對照%"] = round(s["每筆平均%"] - b_avg, 2)
                row.update({f"驗_投組{k}": v for k, v in bt.portfolio(test).items()})
                rows.append(row)
        print(f"完成 {name}", flush=True)
    table = pd.DataFrame(rows)
    REPORTS.mkdir(exist_ok=True)
    table.to_csv(REPORTS / f"results{suffix}.csv", index=False, encoding="utf-8-sig")
    meta = {
        "名單日期": manifest.get("universe_date", manifest.get("fetched")), "名單檔數": len(manifest["stocks"]),
        "股票數": len(frames), "資料起": str(first.date()), "資料迄": str(last.date()), "切分日": str(TRAIN_END.date()),
        "組合數": len(table), "還原時接起來的異常跳空": jumps,
        "對照組_選": base_train, "對照組_驗": base_test,
        "0050_選": bt.buy_and_hold(bench, first, TRAIN_END) if bench is not None else {},
        "0050_驗": bt.buy_and_hold(bench, TRAIN_END, last) if bench is not None else {},
        "個股買進抱著_驗_年化中位數%": round(float(np.nanmedian([bt.buy_and_hold(df, TRAIN_END, last).get("年化%", np.nan)
                                                        for df in frames.values()])), 1),
    }
    (REPORTS / f"meta{suffix}.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1, default=float), encoding="utf-8")
    write_report(table, meta, suffix)
    return table


def md_table(df: pd.DataFrame, cols: List[str]) -> str:
    head = "| " + " | ".join(c.replace("選_", "").replace("驗_", "") for c in cols) + " |"
    lines = [head, "|" + "---|" * len(cols)]
    for _, r in df.iterrows():
        lines.append("| " + " | ".join("" if pd.isna(r[c]) else str(r[c]) for c in cols) + " |")
    return "\n".join(lines)


def write_report(table: pd.DataFrame, meta: dict, suffix: str = "") -> None:
    hindsight = str(meta["名單日期"]) > str(TRAIN_END.date())
    ok = table[table["選_筆數"] >= MIN_TRADES].copy()
    picked = ok.sort_values("選_贏對照%", ascending=False).head(8)
    by_win = ok.sort_values("選_勝率%", ascending=False).head(8)
    cols_train = ["策略", "設定", "選_筆數", "選_勝率%", "選_每筆平均%", "選_對照平均%", "選_贏對照%", "選_獲利因子", "選_平均持有天"]
    cols_test = ["策略", "設定", "驗_筆數", "驗_勝率%", "驗_每筆平均%", "驗_對照平均%", "驗_贏對照%", "驗_獲利因子", "驗_平均賺%", "驗_平均賠%",
                 "驗_平均持有天", "驗_投組年化%", "驗_投組最大回落%"]
    beat = int((ok["驗_贏對照%"] > 0).sum())
    text = f"""# 台股技術策略回測報告

- 股票名單：{meta['名單日期']} 當天成交值前 {meta['名單檔數']} 的上市櫃普通股，其中 {meta['股票數']} 檔有足夠資料。{'**名單日期在驗證期之後，有後見之明。**' if hindsight else '**名單在驗證期開始前就定好，驗證期沒有後見之明。**'}
- 日線 {meta['資料起']} ～ {meta['資料迄']}，已用除權息參考價還原。
- 切分：{meta['切分日']} 以前用來挑策略，之後是策略沒看過的驗證期。
- 共 {meta['組合數']} 個組合（9 種策略 × 參數 × 3 種停損設定），其中 {len(ok)} 個在挑選期交易滿 {MIN_TRADES} 筆。
- 成本：手續費買賣各 0.1425%（未打折）、證交稅 0.3%、滑價買賣各 0.1%；隔天開盤成交；漲停買不到、跌停賣不掉。
- 對照組：同一段期間、同一批股票，隨便哪天買進抱一樣久的平均結果。「贏對照」= 策略每筆平均 − 對照組平均。

## 對照組與大盤

| 抱幾個交易日 | 挑選期 平均% / 勝率% | 驗證期 平均% / 勝率% |
|---|---|---|
""" + "\n".join(f"| {h} | {meta['對照組_選'][h][0]} / {meta['對照組_選'][h][1]} | {meta['對照組_驗'][h][0]} / {meta['對照組_驗'][h][1]} |"
                 for h in HORIZONS) + f"""

- 0050 買進抱著：挑選期 {meta['0050_選']}，驗證期 {meta['0050_驗']}。
- 這 {meta['股票數']} 檔個股在驗證期買進抱著的年化報酬中位數：{meta['個股買進抱著_驗_年化中位數%']}%。

## 用挑選期選出的前 8 名（依「贏對照」排序）

{md_table(picked, cols_train)}

### 這 8 名在驗證期（沒看過的資料）的表現

{md_table(picked, cols_test)}

## 挑選期勝率最高的 8 名

{md_table(by_win, cols_train)}

### 它們在驗證期的表現

{md_table(by_win, cols_test)}

## 整體

- {len(ok)} 個組合中，驗證期每筆平均贏過對照組的有 {beat} 個。
- 完整數字在 `results.csv`。

## 讀這份報告要知道的限制

1. **股票名單**：{'用的是驗證期結束時成交值最大的股票，這些多半是過去幾年漲上來的，所以所有做多策略、連同對照組和個股買進抱著，數字都偏樂觀，尤其對順勢策略有利。' if hindsight else '名單在驗證期開始前決定，驗證期的數字沒有名單造成的後見之明；挑選期仍然有（名單是挑選期結束時的熱門股）。之後下市或資料抓不到的股票不在內。'}要看的是策略有沒有贏過對照組，不是絕對報酬。
2. **投組年化與最大回落是簡化算法**：資金分 10 等份、同一天訊號太多時照代號取，權益只在出場時更新，最大回落會比實際樂觀。
3. **還原股價**：除權息用官方參考價還原；另有 {sum(meta['還原時接起來的異常跳空'].values())} 次隔夜跳空超過 11%（減資、分割等），以「當晚漲跌為 0」接起來。
4. **沒算進去的**：處置股與全額交割限制、零股與流動性衝擊、當沖與融資融券、借券費。
5. 過去的回測結果不代表未來會賺錢。
"""
    (REPORTS / f"REPORT{suffix}.md").write_text(text, encoding="utf-8")


if __name__ == "__main__":
    import sys

    asof = "--asof" in sys.argv
    result = run("manifest_asof.json", "_asof") if asof else run()
    print(f"寫出 reports/REPORT{'_asof' if asof else ''}.md，共 {len(result)} 個組合")
