"""回測引擎與還原股價的行為測試（人造資料，不連網）：python -m unittest discover -s tests"""

import sys
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from twstock_lab import backtest as bt  # noqa: E402
from twstock_lab import portfolio as pf  # noqa: E402
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


class DailyPortfolio(unittest.TestCase):
    def one_trade(self, closes):
        df = frame(closes, closes)
        entry, exit_ = flags(len(closes), 0).set_axis(df.index), flags(len(closes), len(closes) - 3).set_axis(df.index)
        return df, bt.simulate("X", df, entry, exit_)

    def test_drawdown_inside_a_trade_is_visible_daily(self):
        """進場後中途跌三成又漲回來：只在出場時結算看不到回落，每天結算要看得到。"""
        df, trades = self.one_trade([100, 100, 70, 70, 100, 100, 100])
        curve = pf.daily_equity({"X": df}, trades, slots=1)
        self.assertLess(pf.summary(curve)["最大回落%"], -25)
        self.assertGreater(bt.portfolio(trades, slots=1)["最大回落%"], -2, "舊算法只看到手續費那一點點")
        self.assertAlmostEqual(curve["equity"].iloc[-1], 1 + trades[0].ret, places=9, msg="出場後的權益 = 這筆的淨報酬")

    def test_slots_split_capital_and_idle_cash_stays_flat(self):
        df, trades = self.one_trade([100, 100, 100, 110, 110, 110, 110])
        curve = pf.daily_equity({"X": df}, trades, slots=4)
        self.assertAlmostEqual(curve["equity"].iloc[-1], 1 + trades[0].ret / 4, places=9, msg="只用四分之一的資金")
        self.assertLessEqual(curve["invested"].max(), 0.30)

    def test_core_holding_and_rotation(self):
        df, trades = self.one_trade([100] * 7)
        core = frame([50, 50, 55, 55, 60, 60, 60], [50, 50, 55, 55, 60, 60, 60])
        only_core = pf.daily_equity({"X": df}, [], core=core, sleeve=0.0)
        self.assertAlmostEqual(only_core["equity"].iloc[-1], 60 / (50 * pf.CORE_BUY), places=9, msg="全部抱核心標的")
        mixed = pf.daily_equity({"X": df}, trades, core=core, sleeve=0.3, slots=1)
        rotated = pf.daily_equity({"X": df}, trades, core=core, sleeve=1.0, slots=1, idle_in_core=True)
        self.assertLess(mixed["equity"].iloc[-1], only_core["equity"].iloc[-1], "三成資金換去不會漲的股票，少賺")
        self.assertLess(rotated["equity"].iloc[-1], mixed["equity"].iloc[-1], "全部輪動出去，核心的漲幅沒吃到更多")
        with self.assertRaises(ValueError):
            pf.daily_equity({"X": df}, trades, sleeve=0.5)


class BranchConcentration(unittest.TestCase):
    def test_ratio_and_signal_window(self):
        import run_branch
        self.assertAlmostEqual(run_branch.concentration([{"net": 300.0}, {"net": 100.0}], [{"net": -100.0}]), 0.8)
        self.assertIsNone(run_branch.concentration([], []), "沒有買賣超資料就不分組")
        df = frame([100.0] * 10)
        start, end = run_branch.window_of(df, df.index[7])
        self.assertEqual((start, end), (df.index[2], df.index[6]), "訊號日是進場日的前一個交易日，往前共 5 個交易日，不含進場日")
        url = run_branch.branch_url("4931", pd.Timestamp("2024-08-01"), pd.Timestamp("2024-08-05"), "example.com")
        self.assertTrue(url.endswith("a=4931&e=2024-8-1&f=2024-8-5"))


class DayTradeBranches(unittest.TestCase):
    def test_flip_needs_next_day_selling_of_at_least_half(self):
        import run_daytrade as dt
        buys = [{"name": "甲", "net": 100.0}, {"name": "乙", "net": 100.0}, {"name": "丙", "net": 100.0}]
        sells = [{"name": "甲", "net": -60.0}, {"name": "乙", "net": -40.0}]
        self.assertEqual(dt.flips(buys, sells), {"甲": True, "乙": False, "丙": False})
        self.assertAlmostEqual(dt.daytrade_share(buys, {"甲"}), 1 / 3)
        self.assertIsNone(dt.daytrade_share([], {"甲"}))

    def test_forward_return_buys_next_open_and_skips_limit_up(self):
        import run_daytrade as dt
        df = frame([100, 100, 100, 110, 110])
        cost = (1 - bt.SLIPPAGE) * (1 - bt.FEE - bt.TAX) / ((1 + bt.SLIPPAGE) * (1 + bt.FEE))
        self.assertAlmostEqual(dt.forward_return(df, 0, 2), 1.1 * cost - 1)   # 第 1 天開盤 100 買、第 3 天開盤 110 賣
        self.assertIsNone(dt.forward_return(df, 3, 5), "資料不夠長就不算")
        self.assertIsNone(dt.forward_return(frame([100, 110, 110, 110]), 0, 1), "隔天開盤漲停買不到")


class DayTradeWarning(unittest.TestCase):
    def test_stock_check_warns_only_when_a_listed_branch_is_a_top_buyer(self):
        import json
        import stock_check
        listed = next(iter(json.loads(stock_check.DAYTRADE_FILE.read_text(encoding="utf-8"))))
        note = stock_check.daytrade_note([{"name": listed, "net": 300.0}, {"name": "不在名單的分點", "net": 700.0}])
        self.assertIn(listed, note)
        self.assertIn("30%", note)
        self.assertEqual(stock_check.daytrade_note([{"name": "不在名單的分點", "net": 700.0}]), "")
        self.assertEqual(stock_check.daytrade_note([{"name": listed, "net": -50.0}]), "", "賣超的不算")


class ThemeAdvice(unittest.TestCase):
    BASE = {"收盤": 80.0, "前一天最高": 82.0, "60日線": 100.0, "20日線": 90.0, "離60日線": -0.20, "近5日": 0.0, "近20日": -0.1,
            "離250日高": -0.4, "均成交值億": 1.0, "連續漲停": False, "止穩": False, "量縮止跌": False, "近四季EPS": 2.0}

    def advice(self, broad=True, **change):
        import run_theme
        return run_theme.advice({**self.BASE, **change}, broad)

    def test_status_follows_the_backtested_conditions(self):
        self.assertEqual(self.advice()["狀態"], "等止穩")
        self.assertIn("82.0", self.advice()["進場條件"], "要寫出得站上的價位")
        self.assertIn("85.0", self.advice()["進場條件"], "要寫出 60 日線的 85%")
        self.assertEqual(self.advice(止穩=True, 收盤=83.0, 離60日線=-0.17)["狀態"], "符合進場條件")
        self.assertEqual(self.advice(broad=False, 止穩=True)["狀態"], "不建議（個別下跌）")
        self.assertEqual(self.advice(離60日線=-0.10, 收盤=90.0)["狀態"], "還沒到條件")
        self.assertEqual(self.advice(離60日線=0.05, 收盤=105.0)["狀態"], "沒有驗證過的買點")

    def test_losing_company_is_never_an_entry_even_when_price_conditions_hold(self):
        out = self.advice(止穩=True, 近四季EPS=-2.0)
        self.assertEqual(out["狀態"], "不建議")
        self.assertIn("-2.00", out["理由"])
        self.assertEqual(self.advice(止穩=True, 近四季EPS=None)["狀態"], "符合進場條件", "抓不到財報時不當成虧損")

    def test_cold_means_small_turnover_and_no_consecutive_limit_ups(self):
        import run_theme
        self.assertTrue(run_theme.is_cold(self.BASE))
        self.assertFalse(run_theme.is_cold({**self.BASE, "均成交值億": 3.5}))
        self.assertFalse(run_theme.is_cold({**self.BASE, "連續漲停": True}))

    def test_measure_reads_the_last_day(self):
        import run_theme
        closes = [100.0] * 260 + [70.0, 75.0]
        df = frame(closes)
        m = run_theme.measure(df, 1.5)
        self.assertAlmostEqual(m["收盤"], 75.0)
        self.assertAlmostEqual(m["前一天最高"], 70.0)
        self.assertTrue(m["止穩"])
        self.assertLess(m["離60日線"], -0.15)
        self.assertFalse(m["連續漲停"])


class SectorPick(unittest.TestCase):
    def test_pick_keeps_cheap_quiet_fallen_electronics_only(self):
        import run_sector
        q = lambda close, value=5e7: {"name": "x", "close": close, "value": value}  # noqa: E731
        now = {"1111": q(40), "2222": q(40), "3333": q(90), "4444": q(40, 9e8), "5555": q(40), "6666": q(40), "0050": q(40)}
        old = {"1111": q(60), "2222": q(42), "3333": q(150), "4444": q(60, 9e8), "5555": q(60), "6666": q(60), "0050": q(60)}
        sector = {c: "半導體業" for c in now if c != "5555"}
        mid = dict(old)
        del mid["6666"]                                   # 中間那天沒有成交價
        got = run_sector.pick(now, mid, old, sector, 70)
        self.assertEqual([r["code"] for r in got], ["1111"], "跌不夠、太貴、成交值太大、不是電子業、缺資料、ETF 都要排除")
        self.assertAlmostEqual(got[0]["drop"], 40 / 60 - 1)

    def test_price_cap_is_part_of_cold_when_given(self):
        import run_theme
        m = {"收盤": 80.0, "均成交值億": 1.0, "連續漲停": False}
        self.assertTrue(run_theme.is_cold(m))
        self.assertFalse(run_theme.is_cold(m, 70))
        self.assertTrue(run_theme.is_cold({**m, "收盤": 70.0}, 70))


class CrashFilters(unittest.TestCase):
    def test_market_state_and_filters_use_only_past_days(self):
        """大盤狀態與濾網：只給前 250 天算出來的，要和給全部 300 天再取前 250 天的一樣。"""
        import numpy as np
        import run_crash
        rng = np.random.default_rng(3)
        make = lambda: frame(list(100 * np.exp(np.cumsum(rng.normal(0, 0.02, 300)))))  # noqa: E731
        bench, stocks = make(), {"A": make(), "B": make()}
        full = run_crash.market_state(stocks, bench)
        part = run_crash.market_state({c: d.iloc[:250] for c, d in stocks.items()}, bench.iloc[:250])
        pd.testing.assert_frame_equal(full.iloc[:250], part)
        f_full, f_part = run_crash.filters(stocks["A"], full), run_crash.filters(stocks["A"].iloc[:250], part)
        for name in f_full:
            pd.testing.assert_series_equal(f_full[name].iloc[:250], f_part[name], check_names=False, obj=name)
        below, above = f_full["0050 在 200 日線之下"], f_full["0050 在 200 日線之上"]
        self.assertFalse((below & above).any(), "同一天不能既在線上又在線下")
        self.assertTrue(below.iloc[210:].eq(~above.iloc[210:]).all())


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
