"""技術指標與策略。每個策略吃一張日線表，回傳兩個布林序列：(進場訊號, 出場訊號)。

訊號一律用「當天收盤以前」的資料算，回測引擎在「隔天開盤」才成交，所以不會偷看未來。
凡是「突破前 N 日高點」這類比較，前 N 日都不含當天（先 shift(1) 再 rolling）。
"""

from __future__ import annotations

from typing import Callable, Dict, List, Tuple

import pandas as pd

Signals = Tuple[pd.Series, pd.Series]


# ---------- 指標 ----------

def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n).mean()


def rsi(close: pd.Series, n: int) -> pd.Series:
    """Wilder 的 RSI：平均漲幅 / (平均漲幅 + 平均跌幅)，用指數平滑。"""
    diff = close.diff()
    up = diff.clip(lower=0).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    down = (-diff.clip(upper=0)).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    return 100 * up / (up + down)


def kd(df: pd.DataFrame, n: int = 9) -> Tuple[pd.Series, pd.Series]:
    """台灣看盤軟體的 KD：RSV = (收盤 − N 日最低) / (N 日最高 − N 日最低)，K、D 各用 1/3 平滑。"""
    low, high = df["low"].rolling(n).min(), df["high"].rolling(n).max()
    rsv = (100 * (df["close"] - low) / (high - low)).fillna(50)
    k = rsv.ewm(alpha=1 / 3, adjust=False).mean()
    return k, k.ewm(alpha=1 / 3, adjust=False).mean()


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> Tuple[pd.Series, pd.Series]:
    dif = close.ewm(span=fast, adjust=False).mean() - close.ewm(span=slow, adjust=False).mean()
    return dif, dif.ewm(span=signal, adjust=False).mean()


def crossed_up(a: pd.Series, b: pd.Series) -> pd.Series:
    return (a > b) & (a.shift(1) <= b.shift(1))


def crossed_down(a: pd.Series, b: pd.Series) -> pd.Series:
    return (a < b) & (a.shift(1) >= b.shift(1))


def prior_high(s: pd.Series, n: int) -> pd.Series:
    return s.shift(1).rolling(n).max()


def prior_low(s: pd.Series, n: int) -> pd.Series:
    return s.shift(1).rolling(n).min()


# ---------- 策略 ----------

def ma_cross(df: pd.DataFrame, fast: int, slow: int) -> Signals:
    """均線黃金交叉進、死亡交叉出。"""
    f, s = sma(df["close"], fast), sma(df["close"], slow)
    return crossed_up(f, s), crossed_down(f, s)


def donchian(df: pd.DataFrame, enter: int, leave: int) -> Signals:
    """海龜式通道突破：收盤創前 enter 日新高進，跌破前 leave 日新低出。"""
    return df["close"] > prior_high(df["high"], enter), df["close"] < prior_low(df["low"], leave)


def volume_breakout(df: pd.DataFrame, n: int, times: float, exit_ma: int) -> Signals:
    """帶量突破：收盤創前 n 日新高，且成交量大於前 20 日均量的 times 倍；跌破 exit_ma 日均線出。"""
    big = df["volume"] > times * df["volume"].shift(1).rolling(20).mean()
    return (df["close"] > prior_high(df["high"], n)) & big, df["close"] < sma(df["close"], exit_ma)


def rsi_dip(df: pd.DataFrame, n: int, below: float, trend: int, exit_ma: int) -> Signals:
    """多頭中的超賣拉回：收盤在 trend 日均線之上、RSI(n) 低於 below 進；收盤站回 exit_ma 日均線出。"""
    entry = (rsi(df["close"], n) < below) & (df["close"] > sma(df["close"], trend))
    return entry, df["close"] > sma(df["close"], exit_ma)


def kd_cross(df: pd.DataFrame, low: float, high: float) -> Signals:
    """KD 低檔黃金交叉進（K 在 low 以下向上穿過 D），高檔死亡交叉出（K 在 high 以上向下穿過 D）。"""
    k, d = kd(df)
    return crossed_up(k, d) & (k < low), crossed_down(k, d) & (k > high)


def macd_cross(df: pd.DataFrame, trend: int) -> Signals:
    """MACD 的 DIF 向上穿過訊號線、且收盤在 trend 日均線之上進；DIF 向下穿過訊號線出。"""
    dif, sig = macd(df["close"])
    return crossed_up(dif, sig) & (df["close"] > sma(df["close"], trend)), crossed_down(dif, sig)


def bollinger_dip(df: pd.DataFrame, n: int, width: float, trend: int) -> Signals:
    """布林下軌反彈：收盤跌破下軌、但仍在 trend 日均線之上進；回到中軌出。"""
    mid, std = sma(df["close"], n), df["close"].rolling(n).std()
    return (df["close"] < mid - width * std) & (df["close"] > sma(df["close"], trend)), df["close"] > mid


def bollinger_breakout(df: pd.DataFrame, n: int, width: float) -> Signals:
    """布林上軌突破：收盤站上上軌進；跌回中軌出。"""
    mid, std = sma(df["close"], n), df["close"].rolling(n).std()
    return df["close"] > mid + width * std, df["close"] < mid


def trend_pullback(df: pd.DataFrame, fast: int, slow: int) -> Signals:
    """多頭排列回測均線：fast 均線在 slow 之上，當天最低碰到 fast 均線但收盤站回；收盤跌破 slow 均線出。"""
    f, s = sma(df["close"], fast), sma(df["close"], slow)
    return (f > s) & (df["low"] <= f) & (df["close"] > f), df["close"] < s


Strategy = Callable[..., Signals]

# 名稱 → (函式, 參數組合)。每組再配幾種停損 / 最長持有天數，見 run_backtest.py 的 RISK_OPTIONS
CATALOG: Dict[str, Tuple[Strategy, List[dict]]] = {
    "均線交叉": (ma_cross, [dict(fast=5, slow=20), dict(fast=10, slow=60), dict(fast=20, slow=60)]),
    "通道突破": (donchian, [dict(enter=20, leave=10), dict(enter=55, leave=20)]),
    "帶量突破": (volume_breakout, [dict(n=20, times=1.5, exit_ma=10), dict(n=20, times=2.0, exit_ma=20),
                               dict(n=60, times=1.5, exit_ma=20)]),
    "RSI 拉回": (rsi_dip, [dict(n=2, below=10, trend=200, exit_ma=5), dict(n=14, below=30, trend=200, exit_ma=20),
                         dict(n=2, below=5, trend=60, exit_ma=5)]),
    "KD 交叉": (kd_cross, [dict(low=30, high=70), dict(low=20, high=80)]),
    "MACD 交叉": (macd_cross, [dict(trend=60), dict(trend=200)]),
    "布林下軌": (bollinger_dip, [dict(n=20, width=2.0, trend=120), dict(n=20, width=2.5, trend=60)]),
    "布林突破": (bollinger_breakout, [dict(n=20, width=2.0)]),
    "均線拉回": (trend_pullback, [dict(fast=20, slow=60), dict(fast=10, slow=20)]),
}
