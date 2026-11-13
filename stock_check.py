"""單一個股體檢（0 token，本機腳本）：python stock_check.py <代號或名稱>

抓日線、月營收、季財報、本益比、三大法人、融資、分點買賣超排行，印出技術面 / 基本面 / 籌碼面的數字，
並檢查回測驗證過的「布林下軌反彈」進場條件。抓取一律走 gentle-fetch（有快取，同一天重跑不重抓）。
分點來源是富邦 / 元大 / MoneyDJ 的公開頁面（2026-11-13 交叉驗證三者一致）。
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from twstock_lab import backtest as bt
from twstock_lab import fetch, prep
from twstock_lab import strategies as st

sys.path.insert(0, str(Path.home() / ".claude/ai-memory-hub/skills/tw-branch-chips/scripts/pipeline"))
import fubon_parse  # noqa: E402  記憶中樞 tw-branch-chips 技能的解析器

FINMIND = "https://api.finmindtrade.com/api/v4/data?dataset={ds}&data_id={code}&start_date={start}"
BRANCH_HOSTS = ["fubon-ebrokerdj.fbs.com.tw", "jdata.yuanta.com.tw", "justdata.moneydj.com"]
BRANCH_PERIODS = {1: "1日", 2: "5日", 4: "20日", 6: "60日"}
INVESTORS = {"Foreign_Investor": "外資", "Investment_Trust": "投信", "Dealer_self": "自營(自行)",
             "Dealer_Hedging": "自營(避險)", "Foreign_Dealer_Self": "外資自營"}


def find_stock(key: str) -> dict:
    """用代號或名稱（部分符合）在上市櫃清單裡找股票，順便帶回發行股數（上櫃才有）。"""
    f = fetch.fetcher()
    found = []
    for item in json.loads(f.get(fetch.TWSE_ALL)):
        if key == item["Code"] or (not key.isdigit() and key in item["Name"]):
            found.append({"code": item["Code"], "name": item["Name"], "market": "上市", "shares": None})
    for item in json.loads(f.get(fetch.TPEX_ALL)):
        if key == item["SecuritiesCompanyCode"] or (not key.isdigit() and key in item["CompanyName"]):
            found.append({"code": item["SecuritiesCompanyCode"], "name": item["CompanyName"].strip(), "market": "上櫃",
                          "shares": fetch._number(item.get("Capitals", 0)) or None})
    found = [s for s in found if fetch.is_common_stock(s["code"])]
    if len(found) != 1:
        raise SystemExit(f"找到 {len(found)} 檔符合「{key}」：{[(s['code'], s['name']) for s in found][:10]}，請改用代號")
    return found[0]


def finmind(f, dataset: str, code: str, start: str) -> list:
    return json.loads(f.get(FINMIND.format(ds=dataset, code=code, start=start)))["data"]


def exdiv_events(f, code: str) -> list:
    today = date.today()
    urls = fetch.exdiv_urls(today.year - 8, today.year)
    for group in urls.values():
        for url in group:
            f.get(url)
    return prep.load_exdiv({"exdiv": urls}).get(code, [])


def check(key: str) -> None:
    f = fetch.fetcher()
    stock = find_stock(key)
    code, today = stock["code"], date.today()
    print(f"# {stock['name']}（{code}，{stock['market']}）")

    # ---------- 價格與技術面 ----------
    raw = prep.load_prices(code, {"prices": {code: FINMIND.format(ds="TaiwanStockPrice", code=code, start=f"{today.year - 8}-{today.month:02d}-01")}})
    df, jumps = prep.adjust(raw, exdiv_events(f, code))
    c, last = df["close"], df.index[-1]
    ret = lambda n: round(100 * (c.iloc[-1] / c.iloc[-1 - n] - 1), 1) if len(c) > n else None  # noqa: E731
    print(f"\n## 技術面（資料到 {last.date()}，已還原；異常跳空 {jumps} 次）")
    print(f"收盤 {raw['close'].iloc[-1]}｜漲跌幅 5日 {ret(5)}%、20日 {ret(20)}%、60日 {ret(60)}%、120日 {ret(120)}%、250日 {ret(250)}%")
    print("均線：" + "、".join(f"{n}日 {st.sma(c, n).iloc[-1]:.1f}" for n in (5, 20, 60, 120, 240)))
    high, low = c.tail(250).max(), c.tail(250).min()
    print(f"近 250 日高 {high:.1f}、低 {low:.1f}；距高點 {100 * (c.iloc[-1] / high - 1):.1f}%、距低點 +{100 * (c.iloc[-1] / low - 1):.1f}%")
    mid, sd = st.sma(c, 20), c.rolling(20).std()
    k, d = st.kd(df)
    print(f"布林(20,2) 上 {(mid + 2 * sd).iloc[-1]:.1f}／中 {mid.iloc[-1]:.1f}／下 {(mid - 2 * sd).iloc[-1]:.1f}｜KD {k.iloc[-1]:.0f}/{d.iloc[-1]:.0f}｜RSI14 {st.rsi(c, 14).iloc[-1]:.0f}")
    vol = df["volume"] / 1000
    chg = raw["close"].pct_change().tail(120)
    print(f"成交量（張）今日 {vol.iloc[-1]:.0f}、20日均 {vol.tail(20).mean():.0f}、60日均 {vol.tail(60).mean():.0f}｜今日成交值 {df['value'].iloc[-1] / 1e8:.2f} 億")
    print(f"近 60 日年化波動 {100 * c.pct_change().tail(60).std() * np.sqrt(250):.0f}%｜近 120 日漲停 {(chg > 0.095).sum()} 天、跌停 {(chg < -0.095).sum()} 天")
    print("近 10 日收盤：" + "、".join(str(x) for x in raw["close"].tail(10)))

    entry, exit_ = st.bollinger_dip(df, 20, 2.0, 120)
    above = bool(c.iloc[-1] > st.sma(c, 120).iloc[-1])
    print(f"\n## 回測驗證過的進場條件（布林下軌反彈：收盤在 120 日線之上且跌破下軌）")
    print(f"今天有訊號：{'有' if entry.iloc[-1] else '沒有'}｜收盤在 120 日線之上：{'是' if above else '否'}｜最近一次訊號日 {entry[entry].index[-1].date() if entry.any() else '無'}")
    bias60 = c.iloc[-1] / st.sma(c, 60).iloc[-1] - 1
    print(f"60 日乖離率 {100 * bias60:+.1f}%｜乖離率搶反彈條件（低於 60 日線 15% 以上）：{'成立' if bias60 < -0.15 else '不成立'}"
          "（回測勝率約 75%，但訊號集中在大盤急跌時、不停損、約 4% 的交易賠超過 20%）")
    trades = bt.simulate(code, df, entry, exit_)
    print(f"這檔自己的歷史（樣本少，僅供參考）：{bt.stats(trades)}")
    for t in trades[-5:]:
        print(f"  {t.entry_date.date()} → {t.exit_date.date()}  {100 * t.ret:+.1f}%  {t.days} 天{'（未出場）' if t.open_end else ''}")
    print(f"買進抱著：全期 {bt.buy_and_hold(df, df.index[0], last)}｜2024 起 {bt.buy_and_hold(df, pd.Timestamp('2024-01-01'), last)}")

    # ---------- 月營收 ----------
    rev = pd.DataFrame(finmind(f, "TaiwanStockMonthRevenue", code, f"{today.year - 3}-01-01"))
    rev["ym"] = rev["revenue_year"] * 100 + rev["revenue_month"]
    r = rev.set_index("ym")["revenue"] / 1e8
    print("\n## 月營收（億元）與年增率")
    for ym in r.index[-8:]:
        yoy = f"{100 * (r[ym] / r[ym - 100] - 1):+.0f}%" if ym - 100 in r.index else "—"
        print(f"  {ym}：{r[ym]:.2f}　年增 {yoy}")
    year = int(r.index[-1] // 100)
    this = r[[i for i in r.index if i // 100 == year]]
    prev = r[[i - 100 for i in this.index if i - 100 in r.index]]
    if len(prev) == len(this):
        print(f"{year} 年前 {len(this)} 月累計 {this.sum():.1f} 億，去年同期 {prev.sum():.1f} 億，年增 {100 * (this.sum() / prev.sum() - 1):+.0f}%｜近 {len(r)} 個月最高單月 {r.max():.2f}（{int(r.idxmax())}）")

    # ---------- 季財報與評價 ----------
    fin = pd.DataFrame(finmind(f, "TaiwanStockFinancialStatements", code, f"{today.year - 3}-01-01"))
    q = fin.pivot_table(index="date", columns="type", values="value", aggfunc="last").sort_index()
    print("\n## 季財報（單季）")
    print("  季度　　　營收(億)　毛利率　營益率　業外(億)　稅後淨利(億)　每股盈餘")
    for day, row in q.tail(8).iterrows():
        revenue = row.get("Revenue", float("nan"))
        print(f"  {day}　{revenue / 1e8:7.2f}　{100 * row.get('GrossProfit', float('nan')) / revenue:5.1f}%　"
              f"{100 * row.get('OperatingIncome', float('nan')) / revenue:5.1f}%　{row.get('TotalNonoperatingIncomeAndExpense', float('nan')) / 1e8:6.2f}　"
              f"{row.get('IncomeAfterTaxes', float('nan')) / 1e8:8.2f}　{row.get('EPS', float('nan')):8.2f}")
    if "EPS" in q and len(q) >= 8:
        ttm, prev_ttm = q["EPS"].tail(4).sum(), q["EPS"].iloc[-8:-4].sum()
        growth = f"，比前四季的 {prev_ttm:.2f} 元{'成長' if ttm >= prev_ttm else '衰退'} {abs(100 * (ttm / prev_ttm - 1)):.0f}%" if prev_ttm > 0 else ""
        print(f"近四季每股盈餘合計 {ttm:.2f} 元{growth}｜以收盤 {raw['close'].iloc[-1]} 元算，本益比 {raw['close'].iloc[-1] / ttm:.1f} 倍" if ttm > 0
              else f"近四季每股盈餘合計 {ttm:.2f} 元（虧損，本益比沒有意義）")
    per = pd.DataFrame(finmind(f, "TaiwanStockPER", code, f"{today.year - 3}-{today.month:02d}-01"))
    if len(per):
        valid = per[per["PER"] > 0]["PER"]
        now = per.iloc[-1]
        print(f"官方本益比 {now['PER']}、股價淨值比 {now['PBR']}、殖利率 {now['dividend_yield']}%" +
              (f"｜近三年本益比區間 {valid.min():.1f}～{valid.max():.1f}、中位數 {valid.median():.1f}，目前高於其中 {100 * (valid < now['PER']).mean():.0f}% 的日子" if len(valid) > 60 and now["PER"] > 0 else ""))

    # ---------- 法人與融資 ----------
    start = (pd.Timestamp(today) - pd.Timedelta(days=200)).strftime("%Y-%m-%d")
    inst = pd.DataFrame(finmind(f, "TaiwanStockInstitutionalInvestorsBuySell", code, start))
    inst["net"] = (inst["buy"] - inst["sell"]) / 1000
    pivot = inst.pivot_table(index="date", columns="name", values="net", aggfunc="sum").rename(columns=INVESTORS).fillna(0)
    print("\n## 三大法人買賣超（張）")
    for n in (5, 20, 60):
        print(f"  近 {n} 日：" + "、".join(f"{k} {v:+.0f}" for k, v in pivot.tail(n).sum().items() if abs(v) >= 1))
    shares = stock["shares"]
    if shares:
        print(f"  發行 {shares / 1000:.0f} 張｜近 60 日法人合計占股本 {100 * pivot.tail(60).sum().sum() * 1000 / shares:+.1f}%")
    margin = pd.DataFrame(finmind(f, "TaiwanStockMarginPurchaseShortSale", code, start)).set_index("date")
    bal = margin["MarginPurchaseTodayBalance"]
    text = f"\n## 融資\n餘額 {bal.iloc[-1]:.0f} 張（20 日前 {bal.iloc[-21]:.0f}、60 日前 {bal.iloc[-61]:.0f}）｜融券 {margin['ShortSaleTodayBalance'].iloc[-1]:.0f} 張"
    print(text + (f"｜融資占股本 {100 * bal.iloc[-1] * 1000 / shares:.1f}%" if shares else ""))

    # ---------- 分點 ----------
    bf = fetch.GentleFetcher(str(fetch.DATA / "branch" / "cache"), encoding="big5")
    print("\n## 分點買賣超排行（張；買超 / 賣超各列前 8 名）")
    for i, (n, label) in enumerate(BRANCH_PERIODS.items()):
        try:
            s = fubon_parse.parse_summary(bf.get(f"https://{BRANCH_HOSTS[i % 3]}/z/zc/zco/zco_{code}_{n}.djhtm"))
        except Exception as error:  # noqa: BLE001  單一期間抓不到不影響其他
            print(f"  {label}：抓不到（{type(error).__name__}）")
            continue
        buys, sells = s["buys"], s["sells"]
        print(f"  {label}（{s['range_start']}～{s['range_end']}）前 15 名合計：買超 {sum(x['net'] for x in buys):+.0f}、賣超 {sum(x['net'] for x in sells):+.0f}")
        print("    買：" + "；".join(f"{x['name']} {x['net']:+.0f}" for x in buys[:8]))
        print("    賣：" + "；".join(f"{x['name']} {x['net']:+.0f}" for x in sells[:8]))


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("用法：python stock_check.py <代號或名稱>")
    check(sys.argv[1])
