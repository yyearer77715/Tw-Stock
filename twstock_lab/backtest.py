"""回測引擎：把（進場, 出場）訊號變成一筆筆交易，再算統計。

成交規則（都往保守算）：
- 當天收盤出訊號，隔天開盤價成交，買貴賣便宜各滑價 0.1%。
- 隔天開盤已經漲 9.5% 以上：當成漲停買不到，這次進場作廢。
- 隔天開盤已經跌 9.5% 以上：當成跌停賣不掉，順延到能賣的那天。
- 成本：手續費買賣各 0.1425%（沒打折）、賣出證交稅 0.3%。
- 停損與最長持有天數都看收盤價判斷，隔天開盤出場。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

FEE = 0.001425
TAX = 0.003
SLIPPAGE = 0.001
LIMIT = 0.095


@dataclass
class Trade:
    code: str
    entry_date: pd.Timestamp
    exit_date: pd.Timestamp
    ret: float       # 扣掉手續費、證交稅、滑價後的報酬率
    days: int        # 持有的交易日數
    open_end: bool   # 資料結束時還沒出場（用最後一天收盤價結算）


def simulate(code: str, df: pd.DataFrame, entry: pd.Series, exit_: pd.Series,
             stop: Optional[float] = None, max_hold: Optional[int] = None) -> List[Trade]:
    o, c = df["open"].to_numpy(), df["close"].to_numpy()
    ent, ext = entry.fillna(False).to_numpy(bool), exit_.fillna(False).to_numpy(bool)
    dates, n = df.index, len(df)
    trades: List[Trade] = []
    buy = 0.0
    start = -1          # 進場那天的位置；-1 = 空手
    selling = False     # 已經決定要賣、等隔天開盤
    for i in range(n - 1):
        gap = o[i + 1] / c[i] - 1
        if start < 0:
            if ent[i] and gap < LIMIT:
                buy, start, selling = o[i + 1] * (1 + SLIPPAGE), i + 1, False
            continue
        if i < start:
            continue
        held = i - start + 1
        if not selling:
            selling = bool(ext[i]) or (stop is not None and c[i] <= buy * (1 - stop)) or (max_hold is not None and held >= max_hold)
        if selling and gap > -LIMIT:
            sell = o[i + 1] * (1 - SLIPPAGE)
            ret = sell * (1 - FEE - TAX) / (buy * (1 + FEE)) - 1
            trades.append(Trade(code, dates[start], dates[i + 1], ret, i + 1 - start, False))
            start = -1
    if start >= 0:
        ret = c[-1] * (1 - SLIPPAGE) * (1 - FEE - TAX) / (buy * (1 + FEE)) - 1
        trades.append(Trade(code, dates[start], dates[-1], ret, n - start, True))
    return trades


def stats(trades: List[Trade]) -> Dict[str, float]:
    """一組交易的統計。期望值 = 每筆平均報酬；獲利因子 = 總獲利 / 總虧損（大於 1 才賺錢）。"""
    if not trades:
        return {"筆數": 0}
    r = np.array([t.ret for t in trades])
    wins, losses = r[r > 0], r[r <= 0]
    return {
        "筆數": len(r),
        "勝率%": round(100 * len(wins) / len(r), 1),
        "每筆平均%": round(100 * r.mean(), 2),
        "中位數%": round(100 * float(np.median(r)), 2),
        "平均賺%": round(100 * wins.mean(), 2) if len(wins) else 0.0,
        "平均賠%": round(100 * losses.mean(), 2) if len(losses) else 0.0,
        "獲利因子": round(float(wins.sum() / -losses.sum()), 2) if losses.sum() < 0 else float("inf"),
        "平均持有天": round(float(np.mean([t.days for t in trades])), 1),
    }


def portfolio(trades: List[Trade], slots: int = 10) -> Dict[str, float]:
    """把資金分成 slots 等份、照進場日期依序使用的簡化投組：同一天訊號比空位多時照股票代號先後取（沒有預知能力的任意規則）。
    權益只在出場時更新，所以最大回落會比每天結算的版本樂觀。"""
    if not trades:
        return {}
    ordered = sorted(trades, key=lambda t: (t.entry_date, t.code))
    equity, free = 1.0, slots
    open_positions: List[tuple] = []   # (出場日, 投入金額, 報酬率)
    curve: List[tuple] = [(ordered[0].entry_date, 1.0)]
    taken = 0
    for t in ordered:
        for pos in sorted(p for p in open_positions if p[0] <= t.entry_date):
            open_positions.remove(pos)
            equity += pos[1] * pos[2]
            free += 1
            curve.append((pos[0], equity))
        if free > 0:
            open_positions.append((t.exit_date, equity / slots, t.ret))
            free -= 1
            taken += 1
    for pos in sorted(open_positions):
        equity += pos[1] * pos[2]
        curve.append((pos[0], equity))
    values = np.array([v for _, v in curve])
    years = max((curve[-1][0] - curve[0][0]).days / 365.25, 1e-9)
    drawdown = float((values / np.maximum.accumulate(values) - 1).min())
    return {"年化%": round(100 * (equity ** (1 / years) - 1), 1), "最大回落%": round(100 * drawdown, 1),
            "成交筆數": taken, "略過筆數": len(ordered) - taken}


def buy_and_hold(df: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> Dict[str, float]:
    """同一段期間買進抱著不動（含股利，扣一次買賣成本）的年化報酬與最大回落。"""
    part = df[(df.index >= start) & (df.index <= end)]["close"]
    if len(part) < 2:
        return {}
    total = part.iloc[-1] * (1 - FEE - TAX) / (part.iloc[0] * (1 + FEE))
    years = max((part.index[-1] - part.index[0]).days / 365.25, 1e-9)
    drawdown = float((part / part.cummax() - 1).min())
    return {"年化%": round(100 * (total ** (1 / years) - 1), 1), "最大回落%": round(100 * drawdown, 1)}
