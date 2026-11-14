"""逢低訊號出現時，分點買方集中或賣方集中，之後的結果有沒有差（0 token）。

    python run_branch.py fetch    # 只抓訊號日的分點排行（一筆交易一頁，溫和抓取，有快取可接續）
    python run_branch.py          # 分組比較，寫 reports/BRANCH.md

事先定好只看一個指標，不事後挑：
  買方集中度 = 買超前 15 名合計 ÷（買超前 15 名合計 + 賣超前 15 名合計），用訊號當天往前 5 個交易日的分點排行。
  大於 0.5 = 買方比較集中（少數分點在買、賣的人分散）；小於 0.5 = 賣方比較集中。
分點資料收盤後才有，進場仍是隔天開盤，所以沒有偷看未來。
來源：富邦 / 元大 / MoneyDJ 的公開頁面（三者 2026-11-13 交叉驗證一致），輪流抓。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd

import run_backtest as rb
from twstock_lab import backtest as bt
from twstock_lab import fetch, prep
from twstock_lab import strategies as st

sys.path.insert(0, str(Path.home() / ".claude/ai-memory-hub/skills/tw-branch-chips/scripts/pipeline"))
import fubon_parse  # noqa: E402

HOSTS = ["fubon-ebrokerdj.fbs.com.tw", "jdata.yuanta.com.tw", "justdata.moneydj.com"]
WINDOW = 5
STRATEGY = (st.bias_dip, dict(n=60, below=0.15), "乖離率（60 日線下 15%）")
REPORTS = Path(__file__).resolve().parent / "reports"


def concentration(buys: List[dict], sells: List[dict]) -> Optional[float]:
    """買方集中度：0～1，沒有資料回 None。"""
    buy, sell = sum(x["net"] for x in buys), -sum(x["net"] for x in sells)
    return buy / (buy + sell) if buy + sell > 0 else None


def branch_url(code: str, start: pd.Timestamp, end: pd.Timestamp, host: str) -> str:
    day = lambda d: f"{d.year}-{d.month}-{d.day}"  # noqa: E731
    return f"https://{host}/z/zc/zco/zco.djhtm?a={code}&e={day(start)}&f={day(end)}"


def validation_trades() -> Tuple[Dict[str, pd.DataFrame], List[bt.Trade]]:
    frames, _ = prep.load_all(prep.load_manifest("manifest_asof.json"))
    frames.pop(fetch.BENCHMARK, None)
    fn, params, _ = STRATEGY
    trades: List[bt.Trade] = []
    for code, df in frames.items():
        trades += bt.simulate(code, df, *fn(df, **params))
    return frames, sorted((t for t in trades if t.entry_date > rb.TRAIN_END), key=lambda t: (t.entry_date, t.code))


def window_of(df: pd.DataFrame, entry_date: pd.Timestamp) -> Tuple[pd.Timestamp, pd.Timestamp]:
    """訊號日 = 進場日的前一個交易日；回傳往前 WINDOW 個交易日的起訖。"""
    pos = df.index.get_loc(entry_date) - 1
    return df.index[max(pos - WINDOW + 1, 0)], df.index[pos]


def url_for(i: int, trade: bt.Trade, frames: Dict[str, pd.DataFrame]) -> str:
    start, end = window_of(frames[trade.code], trade.entry_date)
    return branch_url(trade.code, start, end, HOSTS[i % len(HOSTS)])


def do_fetch() -> None:
    frames, trades = validation_trades()
    f = fetch.GentleFetcher(str(fetch.DATA / "branch" / "cache"), encoding="big5")
    index_file = fetch.DATA / "branch" / "signal_pages.json"
    index: Dict[str, str] = json.loads(index_file.read_text(encoding="utf-8")) if index_file.is_file() else {}
    blocked = set()
    for i, t in enumerate(trades):
        key = f"{t.code}|{t.entry_date.date()}"
        if key in index:
            continue
        host = next((h for h in HOSTS[i % 3:] + HOSTS[:i % 3] if h not in blocked), None)
        if host is None:
            print("三個來源都被拒絕，停止", flush=True)
            break
        start, end = window_of(frames[t.code], t.entry_date)
        url = branch_url(t.code, start, end, host)
        try:
            f.get(url)
            index[key] = url
        except fetch.SiteBlocked as error:
            blocked.add(host)
            print(f"{host} 被拒絕：{error}", flush=True)
        except Exception as error:  # noqa: BLE001
            print(f"  {key} 失敗：{type(error).__name__}", flush=True)
        if len(index) % 25 == 0:
            print(f"分點頁 {len(index)}/{len(trades)}", flush=True)
            index_file.write_text(json.dumps(index, ensure_ascii=False, indent=0), encoding="utf-8")
    index_file.write_text(json.dumps(index, ensure_ascii=False, indent=0), encoding="utf-8")
    print(f"完成：{len(index)}/{len(trades)} 筆交易有分點頁", flush=True)


def analyse() -> pd.DataFrame:
    frames, trades = validation_trades()
    index_file = fetch.DATA / "branch" / "signal_pages.json"
    if not index_file.is_file():
        raise SystemExit("還沒有分點資料，先跑：python run_branch.py fetch")
    index = json.loads(index_file.read_text(encoding="utf-8"))
    f = fetch.GentleFetcher(str(fetch.DATA / "branch" / "cache"), encoding="big5")
    tagged = []
    for t in trades:
        url = index.get(f"{t.code}|{t.entry_date.date()}")
        if not url:
            continue
        page = fubon_parse.parse_summary(f.get(url))
        ratio = concentration(page["buys"], page["sells"])
        if ratio is not None:
            tagged.append((t, ratio))
    if not tagged:
        raise SystemExit("分點頁都解析不出買賣超")
    last = max(df.index[-1] for df in frames.values())
    base = rb.baseline(frames, rb.TRAIN_END, last)
    ratios = pd.Series([r for _, r in tagged])
    low, high = ratios.quantile(1 / 3), ratios.quantile(2 / 3)
    groups = {
        "全部（有分點資料的）": [t for t, _ in tagged],
        "買方集中（集中度 > 0.5）": [t for t, r in tagged if r > 0.5],
        "賣方集中（集中度 ≤ 0.5）": [t for t, r in tagged if r <= 0.5],
        f"集中度最高的三分之一（> {high:.2f}）": [t for t, r in tagged if r > high],
        f"集中度中間的三分之一": [t for t, r in tagged if low < r <= high],
        f"集中度最低的三分之一（≤ {low:.2f}）": [t for t, r in tagged if r <= low],
    }
    rows = []
    for name, part in groups.items():
        s = bt.stats(part)
        if not s.get("筆數"):
            continue
        b_avg, _ = rb.nearest(base, s["平均持有天"])
        rows.append({"分組": name, "筆數": s["筆數"], "勝率%": s["勝率%"], "每筆平均%": s["每筆平均%"], "中位數%": s["中位數%"],
                     "贏對照%": round(s["每筆平均%"] - b_avg, 2), "平均賺%": s["平均賺%"], "平均賠%": s["平均賠%"], "平均持有天": s["平均持有天"],
                     "期末未出場": sum(t.open_end for t in part)})
    table = pd.DataFrame(rows)
    REPORTS.mkdir(exist_ok=True)
    table.to_csv(REPORTS / "branch.csv", index=False, encoding="utf-8-sig")
    lines = ["| " + " | ".join(table.columns) + " |", "|" + "---|" * len(table.columns)]
    lines += ["| " + " | ".join(str(v) for v in r) + " |" for r in table.itertuples(index=False)]
    (REPORTS / "BRANCH.md").write_text(f"""# 逢低訊號加上分點買方集中度

- 策略：{STRATEGY[2]}，不停損；名單 2023-12-29 成交值前 150 檔；只看驗證期（{rb.TRAIN_END.date()} 之後進場）。
- 驗證期共 {len(trades)} 筆交易，其中 {len(tagged)} 筆抓得到訊號日往前 {WINDOW} 個交易日的分點排行。
- 買方集中度 = 買超前 15 名合計 ÷（買超前 15 名合計 + 賣超前 15 名合計）。這個指標是抓資料前就定好的，沒有事後挑。
- 集中度的分布：最小 {ratios.min():.2f}、中位數 {ratios.median():.2f}、最大 {ratios.max():.2f}。

{chr(10).join(lines)}

## 限制
- 分點排行只有前 15 名；外資券商的分點是很多客戶共用的。
- 訊號集中在少數幾個大跌的月份，分組後每組的獨立樣本更少。
- 只驗證了一個指標、一個策略、一段期間。
""", encoding="utf-8")
    return table


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "fetch":
        do_fetch()
    else:
        result = analyse()
        pd.set_option("display.width", 240)
        pd.set_option("display.unicode.east_asian_width", True)
        print(result.to_string(index=False))
