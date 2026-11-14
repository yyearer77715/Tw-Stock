"""今天有哪些股票符合回測驗證過的逢低條件（只讀快取，0 token）：python run_scan.py

掃兩份名單（今天成交值前 150、2023-12-29 成交值前 150）的最後一個交易日，列出：
  乖離率  收盤低於 60 日均線 15% 以上
  量縮止跌 收盤創 60 日新低、成交量不到前 20 日均量的七成
再附上回測裡有差別的三個判斷：是不是全面性大跌、有沒有止穩（收盤站上前一天最高）、外資有沒有連 3 天買超（有法人資料才有）。
資料是上次抓的快取；要更新先跑 python -m twstock_lab.fetch 與 python -m twstock_lab.fetch --asof 2023-12-29。
"""

from __future__ import annotations

from typing import Dict, List

import pandas as pd

import run_chips
import run_crash
from twstock_lab import fetch, prep
from twstock_lab import strategies as st

LOOKBACK = 5   # 最近幾個交易日內出過訊號也列出來（還在等止穩的）


def load(name: str):
    manifest = prep.load_manifest(name)
    frames, _ = prep.load_all(manifest)
    return manifest, frames


def scan() -> pd.DataFrame:
    rows: List[dict] = []
    seen = set()
    market = None
    for name in ("manifest.json", "manifest_asof.json"):
        try:
            manifest, frames = load(name)
        except Exception:  # noqa: BLE001  沒抓過那份名單就跳過
            continue
        bench = frames.pop(fetch.BENCHMARK)
        state = run_crash.market_state(frames, bench)
        market = market if market is not None else (bench, state)
        names = {s["code"]: s["name"] for s in manifest.get("stocks", [])} if isinstance(manifest.get("stocks"), list) else {}
        inst = manifest.get("datasets", {}).get(run_chips.DATASET, {})
        for code, df in frames.items():
            if code in seen or len(df) < 250 or df.index[-1] != bench.index[-1]:
                continue
            seen.add(code)
            close, ma60 = df["close"], st.sma(df["close"], 60)
            bias = close / ma60 - 1
            bias_sig, _ = st.bias_dip(df, n=60, below=0.15)
            dry_sig, _ = st.dry_up_low(df, n=60, ratio=0.7, exit_ma=20)
            hit = []
            if bias_sig.iloc[-LOOKBACK:].any():
                hit.append("乖離率" + ("" if bias_sig.iloc[-1] else "（前幾天）"))
            if dry_sig.iloc[-LOOKBACK:].any():
                hit.append("量縮止跌" + ("" if dry_sig.iloc[-1] else "（前幾天）"))
            if not hit:
                continue
            foreign = None
            if code in inst:
                flow = run_chips.load_flows(manifest, code, df.index)
                foreign = bool((flow["外資"].iloc[-3:] > 0).all())
            rows.append({"代號": code, "名稱": names.get(code, ""), "訊號": "、".join(hit), "收盤": round(float(close.iloc[-1]), 2),
                         "離60日線%": round(100 * float(bias.iloc[-1]), 1),
                         "近5天%": round(100 * float(close.iloc[-1] / close.iloc[-6] - 1), 1),
                         "止穩": "是" if close.iloc[-1] > df["high"].iloc[-2] else "否",
                         "外資連3買": {True: "是", False: "否", None: "沒資料"}[foreign],
                         "量/均量": round(float(df["volume"].iloc[-1] / df["volume"].iloc[-21:-1].mean()), 2)})
    table = pd.DataFrame(rows).sort_values("離60日線%") if rows else pd.DataFrame()
    bench, state = market
    last = state.iloc[-1]
    print(f"資料到 {bench.index[-1].date()}｜0050 近 5 天 {100 * last['近5天']:+.1f}%｜0050 在 200 日線之{'上' if last['多頭'] else '下'}"
          f"｜名單裡跌到 60 日線下 15% 的股票佔 {100 * last['深跌比例']:.1f}%（超過 10% 才算全面大跌）")
    return table


if __name__ == "__main__":
    result = scan()
    pd.set_option("display.width", 200)
    pd.set_option("display.unicode.east_asian_width", True)
    print("今天沒有股票符合條件" if result.empty else result.to_string(index=False))
