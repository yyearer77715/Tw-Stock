"""技術指標與策略。每個策略吃一張日線表，回傳兩個布林序列：(進場訊號, 出場訊號)。

訊號一律用「當天收盤以前」的資料算，回測引擎在「隔天開盤」才成交，所以不會偷看未來。
凡是「突破前 N 日高點」這類比較，前 N 日都不含當天（先 shift(1) 再 rolling）。
"""

from __future__ import annotations

from typing import Callable, Dict, List, Tuple

import numpy as np
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


def bias_dip(df: pd.DataFrame, n: int, below: float) -> Signals:
    """乖離率過大搶反彈：收盤低於 n 日均線超過 below（負乖離）進；收盤回到均線出。"""
    ma = sma(df["close"], n)
    return df["close"] < ma * (1 - below), df["close"] >= ma


def candle(df: pd.DataFrame, kind: str, hold: int) -> Signals:
    """K 線反轉型態（都要求收盤在 20 日均線之下，代表先跌過）：
    engulfing 多頭吞噬 = 昨天收黑、今天收紅，而且今天的實體把昨天的實體整個包住；
    hammer 錘子 = 下影線至少是實體的 2 倍、上影線不超過實體、實體不能是一條線。
    型態沒有自帶的出場規則，固定抱 hold 個交易日。"""
    o, h, low, c = df["open"], df["high"], df["low"], df["close"]
    body = (c - o).abs()
    fallen = c < sma(c, 20)
    if kind == "engulfing":
        entry = (c.shift(1) < o.shift(1)) & (c > o) & (o <= c.shift(1)) & (c >= o.shift(1)) & fallen
    else:
        lower, upper = pd.concat([o, c], axis=1).min(axis=1) - low, h - pd.concat([o, c], axis=1).max(axis=1)
        entry = (lower >= 2 * body) & (upper <= body) & (body > 0.002 * c) & fallen
    return entry, entry.shift(hold, fill_value=False)


def dmi_cross(df: pd.DataFrame, n: int, adx_min: float) -> Signals:
    """DMI 趨向指標：+DI 向上穿過 −DI、且 ADX 高於 adx_min（趨勢夠強）進；+DI 向下穿過 −DI 出。"""
    up, down = df["high"].diff(), -df["low"].diff()
    plus_dm = up.where((up > down) & (up > 0), 0.0)
    minus_dm = down.where((down > up) & (down > 0), 0.0)
    prev_close = df["close"].shift(1)
    true_range = pd.concat([df["high"] - df["low"], (df["high"] - prev_close).abs(), (df["low"] - prev_close).abs()], axis=1).max(axis=1)
    smooth = lambda x: x.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()  # noqa: E731  Wilder 平滑
    atr = smooth(true_range)
    plus_di, minus_di = 100 * smooth(plus_dm) / atr, 100 * smooth(minus_dm) / atr
    adx = smooth(100 * (plus_di - minus_di).abs() / (plus_di + minus_di))
    return crossed_up(plus_di, minus_di) & (adx > adx_min), crossed_down(plus_di, minus_di)


def williams_dip(df: pd.DataFrame, n: int, trend: int) -> Signals:
    """威廉指標 %R：0 = 收在 n 日最高、−100 = 收在 n 日最低。從 −80 以下往上穿回 −80 進（trend > 0 時還要收盤在 trend 日均線之上）；高於 −20 出。"""
    high, low = df["high"].rolling(n).max(), df["low"].rolling(n).min()
    wr = -100 * (high - df["close"]) / (high - low)
    level = pd.Series(-80.0, index=df.index)
    entry = crossed_up(wr, level)
    if trend:
        entry &= df["close"] > sma(df["close"], trend)
    return entry, wr > -20


def cci_cross(df: pd.DataFrame, n: int) -> Signals:
    """CCI 順勢指標：典型價（高低收平均）偏離 n 日均值幾個「平均絕對偏差」。從 −100 以下往上穿回 −100 進；從 +100 以上往下跌破 +100 出。"""
    typical = (df["high"] + df["low"] + df["close"]) / 3
    mean = typical.rolling(n).mean()
    deviation = typical.rolling(n).apply(lambda w: np.abs(w - w.mean()).mean(), raw=True)
    cci = (typical - mean) / (0.015 * deviation)
    return crossed_up(cci, pd.Series(-100.0, index=df.index)), crossed_down(cci, pd.Series(100.0, index=df.index))


def sar_flip(df: pd.DataFrame, step: float, limit: float) -> Signals:
    """拋物線 SAR（停損點轉向）：SAR 從價格上方翻到下方進，從下方翻到上方出。加速因子每創新高加 step，上限 limit。"""
    high, low = df["high"].to_numpy(), df["low"].to_numpy()
    n = len(df)
    up = np.zeros(n, dtype=bool)          # 當天結束時 SAR 在價格下方（多方）
    if n < 2:
        return pd.Series(False, index=df.index), pd.Series(False, index=df.index)
    rising = high[1] >= high[0]
    sar, extreme, factor = (low[0], high[1], step) if rising else (high[0], low[1], step)
    up[1] = rising
    for i in range(2, n):
        sar += factor * (extreme - sar)
        if rising:
            sar = min(sar, low[i - 1], low[i - 2])
            if low[i] < sar:
                rising, sar, extreme, factor = False, extreme, low[i], step
            elif high[i] > extreme:
                extreme, factor = high[i], min(factor + step, limit)
        else:
            sar = max(sar, high[i - 1], high[i - 2])
            if high[i] > sar:
                rising, sar, extreme, factor = True, extreme, high[i], step
            elif low[i] < extreme:
                extreme, factor = low[i], min(factor + step, limit)
        up[i] = rising
    state = pd.Series(up, index=df.index)
    before = state.shift(1, fill_value=False)
    return state & ~before, ~state & before


def obv_breakout(df: pd.DataFrame, n: int, exit_ma: int) -> Signals:
    """OBV 能量潮：上漲日加成交量、下跌日減。OBV 創前 n 日新高、而且收盤在 exit_ma 日均線之上進；收盤跌破 exit_ma 日均線出。"""
    direction = np.sign(df["close"].diff()).fillna(0)
    obv = (direction * df["volume"]).cumsum()
    ma = sma(df["close"], exit_ma)
    return (obv > prior_high(obv, n)) & (df["close"] > ma), df["close"] < ma


def gap_up(df: pd.DataFrame, times: float, exit_ma: int) -> Signals:
    """向上跳空缺口：當天最低高於前一天最高，而且成交量大於前 20 日均量的 times 倍進；收盤跌破 exit_ma 日均線出。"""
    big = df["volume"] > times * df["volume"].shift(1).rolling(20).mean()
    return (df["low"] > df["high"].shift(1)) & big, df["close"] < sma(df["close"], exit_ma)


def granville(df: pd.DataFrame, n: int) -> Signals:
    """葛蘭碧法則的第一個買點與賣點：n 日均線往上、收盤由下往上穿過均線進；均線往下、收盤由上往下跌破均線出。"""
    ma = sma(df["close"], n)
    rising, falling = ma > ma.shift(5), ma < ma.shift(5)
    return crossed_up(df["close"], ma) & rising, crossed_down(df["close"], ma) & falling


def ichimoku(df: pd.DataFrame, fast: int, slow: int) -> Signals:
    """一目均衡表：轉換線（fast 日高低中點）往上穿過基準線（slow 日高低中點），而且收盤在雲層之上進；收盤跌破基準線出。
    雲層 = 先行帶 A、B，是 slow 天前算好的值往後移到今天，所以只用到過去的資料。"""
    middle = lambda k: (df["high"].rolling(k).max() + df["low"].rolling(k).min()) / 2  # noqa: E731
    tenkan, kijun = middle(fast), middle(slow)
    span_a, span_b = ((tenkan + kijun) / 2).shift(slow), middle(2 * slow).shift(slow)
    above_cloud = df["close"] > pd.concat([span_a, span_b], axis=1).max(axis=1)
    return crossed_up(tenkan, kijun) & above_cloud, df["close"] < kijun


def dry_up_low(df: pd.DataFrame, n: int, ratio: float, exit_ma: int) -> Signals:
    """量縮止跌（價量背離的一種）：收盤創前 n 日新低，成交量卻不到前 20 日均量的 ratio 倍（賣壓變小）進；收盤站回 exit_ma 日均線出。"""
    quiet = df["volume"] < ratio * df["volume"].shift(1).rolling(20).mean()
    return (df["close"] < prior_low(df["low"], n)) & quiet, df["close"] >= sma(df["close"], exit_ma)


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
    "乖離率": (bias_dip, [dict(n=20, below=0.08), dict(n=20, below=0.12), dict(n=60, below=0.15)]),
    "K 線型態": (candle, [dict(kind="engulfing", hold=5), dict(kind="engulfing", hold=10),
                        dict(kind="hammer", hold=5), dict(kind="hammer", hold=10)]),
    "DMI": (dmi_cross, [dict(n=14, adx_min=20), dict(n=14, adx_min=25)]),
    "威廉指標": (williams_dip, [dict(n=14, trend=0), dict(n=14, trend=60)]),
    "CCI": (cci_cross, [dict(n=20), dict(n=14)]),
    "拋物線 SAR": (sar_flip, [dict(step=0.02, limit=0.2)]),
    "OBV 能量潮": (obv_breakout, [dict(n=20, exit_ma=20), dict(n=60, exit_ma=20)]),
    "跳空缺口": (gap_up, [dict(times=1.5, exit_ma=10), dict(times=2.0, exit_ma=20)]),
    "葛蘭碧": (granville, [dict(n=20), dict(n=60)]),
    "一目均衡表": (ichimoku, [dict(fast=9, slow=26)]),
    "量縮止跌": (dry_up_low, [dict(n=20, ratio=0.7, exit_ma=20), dict(n=60, ratio=0.7, exit_ma=20)]),
}
