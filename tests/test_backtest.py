"""回測引擎與還原股價的行為測試（人造資料，不連網）：python -m unittest discover -s tests"""

import sys
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from twstock_lab import backtest as bt  # noqa: E402
from twstock_lab import prep, strategies as st  # noqa: E402


def frame(opens, closes=None):
    closes = closes or opens
    idx = pd.bdate_range("2024-01-01", periods=len(opens))
    return pd.DataFrame({"open": opens, "high": [max(a, b) for a, b in zip(opens, closes)],
                         "low": [min(a, b) for a, b in zip(opens, closes)], "close": closes,
                         "volume": [1000.0] * len(opens), "value": [1e6] * len(opens)}, index=idx)


def flags(n, *on):
    return pd.Series([i in on for i in range(n)])


class Engine(unittest.TestCase):
    def test_fills_at_next_open_never_same_day(self):
        df = frame([100, 100, 110, 120, 130])
        entry, exit_ = flags(5, 0), flags(5, 2)
        entry.index = exit_.index = df.index
        (t,) = bt.simulate("X", df, entry, exit_)
        self.assertEqual((t.entry_date, t.exit_date), (df.index[1], df.index[3]), "第 0 天出訊號 → 第 1 天開盤買；第 2 天出訊號 → 第 3 天開盤賣")
        gross = 120 * (1 - bt.SLIPPAGE) / (100 * (1 + bt.SLIPPAGE))
        self.assertAlmostEqual(t.ret, gross * (1 - bt.FEE - bt.TAX) / (1 + bt.FEE) - 1, places=9)

    def test_round_trip_at_same_price_loses_the_costs(self):
        df = frame([100] * 5)
        entry, exit_ = flags(5, 0), flags(5, 1)
        entry.index = exit_.index = df.index
        (t,) = bt.simulate("X", df, entry, exit_)
        self.assertLess(t.ret, -0.006, "原地進出要賠掉手續費 + 證交稅 + 滑價（約 0.78%）")
        self.assertGreater(t.ret, -0.009)

    def test_limit_up_open_cannot_be_bought_and_limit_down_cannot_be_sold(self):
        df = frame([100, 110, 110, 110], [100, 110, 110, 110])
        entry = flags(4, 0)
        entry.index = df.index
        self.assertEqual(bt.simulate("X", df, entry, flags(4).set_axis(df.index)), [], "隔天開盤漲停，買不到")
        df = frame([100, 100, 90, 89, 89], [100, 100, 90, 89, 89])
        entry, exit_ = flags(5, 0), flags(5, 1)
        entry.index = exit_.index = df.index
        (t,) = bt.simulate("X", df, entry, exit_)
        self.assertEqual(t.exit_date, df.index[3], "第 2 天開盤跌停賣不掉，順延到第 3 天")

    def test_stop_loss_and_max_hold(self):
        df = frame([100, 100, 100, 100, 100, 100], [100, 100, 90, 90, 90, 90])
        entry = flags(6, 0)
        entry.index = df.index
        never = flags(6).set_axis(df.index)
        (t,) = bt.simulate("X", df, entry, never, stop=0.08)
        self.assertEqual(t.exit_date, df.index[3], "第 2 天收盤跌破停損 → 第 3 天開盤出")
        flat = frame([100] * 8)
        entry = flags(8, 0).set_axis(flat.index)
        (t,) = bt.simulate("X", flat, entry, flags(8).set_axis(flat.index), max_hold=3)
        self.assertEqual(t.days, 3)

    def test_open_position_at_end_is_marked(self):
        df = frame([100, 100, 105])
        entry = flags(3, 0).set_axis(df.index)
        (t,) = bt.simulate("X", df, entry, flags(3).set_axis(df.index))
        self.assertTrue(t.open_end)

    def test_stats_and_portfolio(self):
        d = pd.Timestamp("2024-01-01")
        trades = [bt.Trade("A", d, d + pd.Timedelta(days=10), 0.10, 7, False),
                  bt.Trade("B", d, d + pd.Timedelta(days=10), -0.05, 7, False),
                  bt.Trade("C", d, d + pd.Timedelta(days=10), 0.50, 7, False)]
        s = bt.stats(trades)
        self.assertEqual((s["筆數"], s["勝率%"]), (3, 66.7))
        self.assertAlmostEqual(s["獲利因子"], 12.0)
        p = bt.portfolio(trades, slots=2)
        self.assertEqual((p["成交筆數"], p["略過筆數"]), (2, 1), "只有 2 個空位，第三筆同一天的訊號進不去")


class NoLookAhead(unittest.TestCase):
    def test_signals_do_not_change_when_future_rows_are_appended(self):
        """每個策略：只給前 300 天算出來的訊號，要和給全部 400 天再取前 300 天的一模一樣。"""
        import numpy as np
        rng = np.random.default_rng(7)
        close = 100 * np.exp(np.cumsum(rng.normal(0, 0.02, 400)))
        df = frame(list(close * (1 + rng.normal(0, 0.005, 400))), list(close))
        df["volume"] = rng.uniform(500, 3000, 400)
        for name, (fn, grid) in st.CATALOG.items():
            for params in grid:
                full_e, full_x = fn(df, **params)
                part_e, part_x = fn(df.iloc[:300], **params)
                pd.testing.assert_series_equal(full_e.iloc[:300].fillna(False), part_e.fillna(False), check_names=False, obj=f"{name}{params} 進場")
                pd.testing.assert_series_equal(full_x.iloc[:300].fillna(False), part_x.fillna(False), check_names=False, obj=f"{name}{params} 出場")


class Adjust(unittest.TestCase):
    def test_dividend_is_not_a_loss_after_adjusting(self):
        df = frame([100, 100, 95, 95], [100, 100, 95, 95])   # 第 2 天除息 5 元
        out, jumps = prep.adjust(df, [(df.index[2], 95 / 100)])
        self.assertEqual(jumps, 0)
        self.assertAlmostEqual(out["close"].iloc[1], 95.0)
        self.assertAlmostEqual(out["close"].iloc[2] / out["close"].iloc[1], 1.0)

    def test_unexplained_overnight_jump_is_joined(self):
        df = frame([100, 100, 50, 50], [100, 100, 50, 50])   # 一拆二，但除權息表沒有這筆
        out, jumps = prep.adjust(df, [])
        self.assertEqual(jumps, 1)
        self.assertAlmostEqual(out["close"].iloc[1], 50.0)

    def test_roc_dates(self):
        self.assertEqual(prep.roc_date("112年01月04日"), pd.Timestamp("2023-01-04"))
        self.assertEqual(prep.roc_date("112/01/05"), pd.Timestamp("2023-01-05"))


if __name__ == "__main__":
    unittest.main()
