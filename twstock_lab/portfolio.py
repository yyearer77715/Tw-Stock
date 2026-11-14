"""每天結算的投組模擬：比 backtest.portfolio（只在出場時更新權益）更接近真實的最大回落。

資金分成兩塊：
- 核心：一開始就買進核心標的（例如 0050）抱到底，占 1 − sleeve。
- 策略：占 sleeve，再分成 slots 等份，照交易清單的進出場日期使用；同一天訊號比空位多時照股票代號先後取。
  策略那一塊沒用到的錢有兩種放法：idle_in_core=False 放現金；True 平常也放在核心標的，要進場時才賣出來換（輪動）。

每筆交易的出場金額直接用回測算好的淨報酬（已含手續費、證交稅、滑價）；持有期間每天用收盤價估值。
核心標的買賣另外算成本（ETF 證交稅 0.1%）。
"""

from __future__ import annotations

from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from .backtest import FEE, SLIPPAGE, Trade

ETF_TAX = 0.001
CORE_BUY = (1 + SLIPPAGE) * (1 + FEE)          # 買核心標的：每 1 元標的要付出的錢
CORE_SELL = (1 - SLIPPAGE) * (1 - FEE - ETF_TAX)  # 賣核心標的：每 1 元標的拿回的錢


def daily_equity(frames: Dict[str, pd.DataFrame], trades: List[Trade], core: Optional[pd.DataFrame] = None,
                 sleeve: float = 1.0, slots: int = 10, idle_in_core: bool = False,
                 start: Optional[pd.Timestamp] = None, end: Optional[pd.Timestamp] = None) -> pd.DataFrame:
    """回傳每天的 equity（期初 = 1）與 invested（策略部位占總資產的比例）。"""
    if (sleeve < 1 or idle_in_core) and core is None:
        raise ValueError("要有核心標的的日線才能設定 sleeve < 1 或 idle_in_core")
    calendar = (core.index if core is not None else sorted(set().union(*[df.index for df in frames.values()])))
    calendar = pd.DatetimeIndex(calendar)
    calendar = calendar[(calendar >= (start or calendar[0])) & (calendar <= (end or calendar[-1]))]
    used = {t.code for t in trades}
    close = pd.DataFrame({c: frames[c]["close"] for c in used}).reindex(calendar).ffill()
    core_open = core["open"].reindex(calendar).ffill().bfill() if core is not None else None
    core_close = core["close"].reindex(calendar).ffill().bfill() if core is not None else None

    by_entry: Dict[pd.Timestamp, List[Trade]] = {}
    for t in sorted(trades, key=lambda t: (t.entry_date, t.code)):
        if calendar[0] <= t.entry_date <= calendar[-1]:
            by_entry.setdefault(t.entry_date, []).append(t)

    cash = sleeve                 # 策略那一塊的現金
    idle_units = 0.0              # 策略那一塊暫放在核心標的的單位數
    core_units = (1 - sleeve) / (core_open.iloc[0] * CORE_BUY) if sleeve < 1 else 0.0
    if idle_in_core:
        idle_units, cash = cash / (core_open.iloc[0] * CORE_BUY), 0.0
    positions: List[dict] = []    # {trade, shares, alloc}
    rows = []
    last_close: Dict[str, float] = {}
    for day in calendar:
        # 1. 出場（開盤價出場的當天一早就結算；期末未出場的在最後一天收盤結算）
        for pos in [p for p in positions if p["trade"].exit_date <= day and not p["trade"].open_end]:
            positions.remove(pos)
            cash += pos["alloc"] * (1 + pos["trade"].ret)
        # 2. 進場
        for t in by_entry.get(day, []):
            if len(positions) >= slots:
                break
            idle_value = idle_units * core_open[day] * CORE_SELL if idle_units else 0.0
            held = sum(p["shares"] * last_close.get(p["trade"].code, p["buy"]) for p in positions)
            alloc = (cash + idle_value + held) / slots
            if idle_in_core and cash < alloc:   # 賣一部分核心標的來換
                need = min(alloc - cash, idle_value)
                idle_units -= need / (core_open[day] * CORE_SELL)
                cash += need
            alloc = min(alloc, cash)
            if alloc <= 1e-9:
                continue
            buy = frames[t.code]["open"].get(day)
            if buy is None or not np.isfinite(buy):
                continue
            buy *= 1 + SLIPPAGE
            cash -= alloc
            positions.append({"trade": t, "alloc": alloc, "buy": buy, "shares": alloc / (buy * (1 + FEE))})
        # 3. 策略那一塊閒置的現金放回核心標的（輪動模式）
        if idle_in_core and cash > 1e-9:
            idle_units += cash / (core_open[day] * CORE_BUY)
            cash = 0.0
        # 4. 收盤估值
        held = 0.0
        for p in positions:
            price = close.at[day, p["trade"].code]
            last_close[p["trade"].code] = price if np.isfinite(price) else last_close.get(p["trade"].code, p["buy"])
            held += p["shares"] * last_close[p["trade"].code]
        core_value = (core_units + idle_units) * core_close[day] if core is not None else 0.0
        equity = cash + held + core_value
        rows.append((day, equity, held / equity if equity else 0.0))
    return pd.DataFrame(rows, columns=["date", "equity", "invested"]).set_index("date")


def summary(curve: pd.DataFrame, start: Optional[pd.Timestamp] = None, end: Optional[pd.Timestamp] = None) -> Dict[str, float]:
    """某一段期間的年化報酬、最大回落（每天收盤算）、策略部位平均占比。"""
    part = curve[(curve.index >= (start or curve.index[0])) & (curve.index <= (end or curve.index[-1]))]
    if len(part) < 2:
        return {}
    eq = part["equity"] / part["equity"].iloc[0]
    years = max((part.index[-1] - part.index[0]).days / 365.25, 1e-9)
    return {"年化%": round(float(100 * (eq.iloc[-1] ** (1 / years) - 1)), 1),
            "最大回落%": round(float(100 * (eq / eq.cummax() - 1).min()), 1),
            "策略部位平均占比%": round(float(100 * part["invested"].mean()), 1)}
