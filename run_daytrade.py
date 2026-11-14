"""從資料找出「隔日沖」分點，並檢查避開它們有沒有用（0 token）。

    python run_daytrade.py fetch    # 抓大漲日當天與隔天的分點排行（溫和抓取，有快取可接續）
    python run_daytrade.py          # 找隔日沖分點、驗證、寫 reports/DAYTRADE.md

做法（門檻都在抓資料前寫死，不事後調）：
  事件 = 2023-12-29 名單裡的股票，在驗證期某天收盤漲 7% 以上（那天叫 D）。
  隔日沖 = 某分點在 D 日排進買超前 15 名，隔天（D+1）又排進賣超前 15 名，而且賣掉的至少是前一天買超的一半。
  認定期（D 在 2025-06-30 以前）：出現在買超榜至少 MIN_SEEN 次、其中一半以上隔天倒貨的分點，列為「隔日沖分點」。
  檢驗期（之後）：① 這些分點是不是繼續隔天倒貨；② 大漲日買盤裡隔日沖分點佔比高的股票，隔天開盤買進後表現有沒有比較差。
D 日的分點排行收盤後才公布，隔天開盤進場來得及，所以沒有偷看未來。
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

import run_backtest as rb
from twstock_lab import backtest as bt
from twstock_lab import fetch, prep

sys.path.insert(0, str(Path.home() / ".claude/ai-memory-hub/skills/tw-branch-chips/scripts/pipeline"))
import fubon_parse  # noqa: E402

HOSTS = ["fubon-ebrokerdj.fbs.com.tw", "jdata.yuanta.com.tw", "justdata.moneydj.com"]
UP = 0.07                 # 大漲日：收盤漲幅
MAX_EVENTS = 600          # 事件太多就等距取樣
LEARN_END = pd.Timestamp("2025-06-30")
MIN_SEEN = 8              # 認定期至少出現在買超榜幾次
FLIP_RATE = 0.5           # 其中多少比例隔天倒貨
FLIP_PART = 0.5           # 隔天賣超至少是前一天買超的多少
SHARE = 0.2               # 大漲日買盤裡隔日沖分點佔比的分界
HOLDS = (1, 5, 10)        # 隔天開盤買進後抱幾個交易日
REPORTS = Path(__file__).resolve().parent / "reports"
INDEX = fetch.DATA / "branch" / "daytrade_pages.json"


def flips(buys: List[dict], next_sells: List[dict]) -> Dict[str, bool]:
    """D 日買超榜上的每個分點，隔天有沒有倒貨。"""
    sold = {x["name"]: -x["net"] for x in next_sells}
    return {x["name"]: sold.get(x["name"], 0) >= FLIP_PART * x["net"] for x in buys if x["net"] > 0}


def daytrade_share(buys: List[dict], flagged: set) -> Optional[float]:
    """買超前 15 名的買超張數裡，隔日沖分點佔多少。"""
    total = sum(x["net"] for x in buys if x["net"] > 0)
    return sum(x["net"] for x in buys if x["net"] > 0 and x["name"] in flagged) / total if total > 0 else None


def forward_return(df: pd.DataFrame, pos: int, hold: int) -> Optional[float]:
    """D 日在 df 的位置是 pos：D+1 開盤買、抱 hold 個交易日後的開盤賣，成本和回測引擎一樣。"""
    if pos + 1 + hold >= len(df):
        return None
    o, c = df["open"].to_numpy(), df["close"].to_numpy()
    if o[pos + 1] / c[pos] - 1 >= bt.LIMIT:      # 隔天開盤就漲停，買不到
        return None
    buy, sell = o[pos + 1] * (1 + bt.SLIPPAGE), o[pos + 1 + hold] * (1 - bt.SLIPPAGE)
    return sell * (1 - bt.FEE - bt.TAX) / (buy * (1 + bt.FEE)) - 1


def events() -> Tuple[Dict[str, pd.DataFrame], List[Tuple[str, pd.Timestamp, pd.Timestamp]]]:
    """(股票, D, D+1) 清單；超過 MAX_EVENTS 就照日期排序後等距取樣。"""
    frames, _ = prep.load_all(prep.load_manifest("manifest_asof.json"))
    frames.pop(fetch.BENCHMARK, None)
    found = []
    for code, df in frames.items():
        up = df["close"].pct_change() >= UP
        for pos in np.flatnonzero(up.to_numpy()):
            if df.index[pos] > rb.TRAIN_END and pos + 1 < len(df):
                found.append((code, df.index[pos], df.index[pos + 1]))
    found.sort(key=lambda e: (e[1], e[0]))
    if len(found) > MAX_EVENTS:
        step = len(found) / MAX_EVENTS
        found = [found[int(i * step)] for i in range(MAX_EVENTS)]
    return frames, found


def day_url(code: str, day: pd.Timestamp, host: str) -> str:
    d = f"{day.year}-{day.month}-{day.day}"
    return f"https://{host}/z/zc/zco/zco.djhtm?a={code}&e={d}&f={d}"


def do_fetch() -> None:
    _, found = events()
    wanted = [(c, d) for c, d0, d1 in found for d in (d0, d1)]
    f = fetch.GentleFetcher(str(fetch.DATA / "branch" / "cache"), encoding="big5")
    index: Dict[str, str] = json.loads(INDEX.read_text(encoding="utf-8")) if INDEX.is_file() else {}
    blocked: set = set()
    print(f"事件 {len(found)} 個，要抓 {len(wanted)} 頁，已有 {sum(f'{c}|{d.date()}' in index for c, d in wanted)} 頁", flush=True)
    for i, (code, day) in enumerate(wanted):
        key = f"{code}|{day.date()}"
        if key in index:
            continue
        host = next((h for h in HOSTS[i % 3:] + HOSTS[:i % 3] if h not in blocked), None)
        if host is None:
            print("三個來源都被拒絕，停止", flush=True)
            break
        url = day_url(code, day, host)
        try:
            f.get(url)
            index[key] = url
        except fetch.SiteBlocked as error:
            blocked.add(host)
            print(f"{host} 被拒絕：{error}", flush=True)
        except Exception as error:  # noqa: BLE001
            print(f"  {key} 失敗：{type(error).__name__}", flush=True)
        if len(index) % 25 == 0:
            print(f"分點頁 {len(index)}/{len(wanted)}", flush=True)
            INDEX.write_text(json.dumps(index, ensure_ascii=False, indent=0), encoding="utf-8")
    INDEX.write_text(json.dumps(index, ensure_ascii=False, indent=0), encoding="utf-8")
    print(f"完成：{sum(f'{c}|{d.date()}' in index for c, d in wanted)}/{len(wanted)} 頁", flush=True)


def summarise(values: List[float]) -> Dict[str, float]:
    r = np.array(values)
    return {"筆數": len(r), "勝率%": round(100 * float((r > 0).mean()), 1), "平均%": round(100 * float(r.mean()), 2),
            "中位數%": round(100 * float(np.median(r)), 2)}


def analyse() -> None:
    frames, found = events()
    if not INDEX.is_file():
        raise SystemExit("還沒有分點資料，先跑：python run_daytrade.py fetch")
    index = json.loads(INDEX.read_text(encoding="utf-8"))
    f = fetch.GentleFetcher(str(fetch.DATA / "branch" / "cache"), encoding="big5")
    rows = []
    for code, d0, d1 in found:
        u0, u1 = index.get(f"{code}|{d0.date()}"), index.get(f"{code}|{d1.date()}")
        if not u0 or not u1:
            continue
        p0, p1 = fubon_parse.parse_summary(f.get(u0)), fubon_parse.parse_summary(f.get(u1))
        if p0["buys"] and p1["sells"]:
            rows.append((code, d0, p0["buys"], flips(p0["buys"], p1["sells"])))
    if not rows:
        raise SystemExit("分點頁都解析不出買賣超")
    seen, flipped = defaultdict(int), defaultdict(int)
    for _, d0, _, fl in rows:
        if d0 <= LEARN_END:
            for name, yes in fl.items():
                seen[name] += 1
                flipped[name] += yes
    flagged = {n for n in seen if seen[n] >= MIN_SEEN and flipped[n] / seen[n] >= FLIP_RATE}
    learn_base = sum(flipped.values()) / max(sum(seen.values()), 1)
    # 檢驗期：這些分點還會不會隔天倒貨
    t_seen, t_flip = defaultdict(int), defaultdict(int)
    for _, d0, _, fl in rows:
        if d0 > LEARN_END:
            for name, yes in fl.items():
                group = "隔日沖分點" if name in flagged else "其他分點"
                t_seen[group] += 1
                t_flip[group] += yes
    # 檢驗期：隔日沖佔比高的大漲日，隔天買進的結果
    outcome = []
    for code, d0, buys, _ in rows:
        if d0 <= LEARN_END:
            continue
        share = daytrade_share(buys, flagged)
        if share is None:
            continue
        df = frames[code]
        pos = df.index.get_loc(d0)
        gap = df["open"].iloc[pos + 1] / df["close"].iloc[pos] - 1
        outcome.append((share >= SHARE, gap, {h: forward_return(df, pos, h) for h in HOLDS}))
    table = []
    for label, pick in ((f"隔日沖佔比 ≥ {SHARE:.0%}", True), (f"隔日沖佔比 < {SHARE:.0%}", False)):
        part = [o for o in outcome if o[0] == pick]
        row = {"分組": label, "事件數": len(part), "隔天開盤跳空%": round(100 * float(np.mean([o[1] for o in part])), 2) if part else None}
        for h in HOLDS:
            vals = [o[2][h] for o in part if o[2][h] is not None]
            if vals:
                s = summarise(vals)
                row.update({f"抱{h}天_筆數": s["筆數"], f"抱{h}天_勝率%": s["勝率%"], f"抱{h}天_平均%": s["平均%"]})
        table.append(row)
    out = pd.DataFrame(table)
    REPORTS.mkdir(exist_ok=True)
    out.to_csv(REPORTS / "daytrade.csv", index=False, encoding="utf-8-sig")
    ranked = sorted(flagged, key=lambda n: (-flipped[n] / seen[n], -seen[n]))
    branch_lines = ["| 分點 | 認定期上買超榜次數 | 隔天倒貨次數 | 倒貨比例 |", "|---|---|---|---|"]
    branch_lines += [f"| {n} | {seen[n]} | {flipped[n]} | {100 * flipped[n] / seen[n]:.0f}% |" for n in ranked[:40]]
    (fetch.DATA / "branch" / "daytrade_branches.json").write_text(
        json.dumps({n: {"seen": seen[n], "flipped": flipped[n]} for n in ranked}, ensure_ascii=False, indent=1), encoding="utf-8")
    rate = lambda g: f"{100 * t_flip[g] / t_seen[g]:.1f}%（{t_flip[g]} / {t_seen[g]}）" if t_seen[g] else "沒有資料"  # noqa: E731
    cols = list(out.columns)
    lines = ["| " + " | ".join(c.replace("_", " ") for c in cols) + " |", "|" + "---|" * len(cols)]
    lines += ["| " + " | ".join("" if pd.isna(v) else str(v) for v in r) + " |" for r in out.itertuples(index=False)]
    learn_n = sum(1 for r in rows if r[1] <= LEARN_END)
    (REPORTS / "DAYTRADE.md").write_text(f"""# 隔日沖分點：從資料認定，再檢查避開有沒有用

- 事件：2023-12-29 成交值前 150 檔在 {rb.TRAIN_END.date()} 之後收盤漲 {UP:.0%} 以上的日子，取樣 {len(found)} 個，其中 {len(rows)} 個抓得到當天與隔天的分點排行。
- 隔日沖 = 當天排進買超前 15 名、隔天排進賣超前 15 名，而且隔天賣超至少是前一天買超的 {FLIP_PART:.0%}。
- 認定期（{LEARN_END.date()} 以前，{learn_n} 個事件）：上買超榜至少 {MIN_SEEN} 次、其中 {FLIP_RATE:.0%} 以上隔天倒貨的分點，共 {len(flagged)} 個。認定期全部分點的平均倒貨比例是 {100 * learn_base:.1f}%。
- 這些門檻都是抓資料前寫在程式裡的。

## 檢驗期（{LEARN_END.date()} 之後，{len(rows) - learn_n} 個事件）

### ① 這些分點是不是繼續隔天倒貨
- 隔日沖分點：{rate("隔日沖分點")}
- 其他分點：{rate("其他分點")}

### ② 大漲日買盤裡隔日沖分點佔比高，隔天開盤買進的結果
報酬已扣手續費、證交稅、滑價；隔天開盤漲停當成買不到。

{chr(10).join(lines)}

## 認定出來的隔日沖分點（前 40 個）

{chr(10).join(branch_lines)}

## 限制
- 分點排行只有前 15 名，隔天賣在 15 名以外的看不到，倒貨比例是低估。
- 外資券商的分點是很多客戶共用的，被列入不代表整個分點都在做隔日沖。
- 只看這 150 檔、一段期間；事件超過 {MAX_EVENTS} 個時是等距取樣。
- 沒有做統計檢定；分組後事件數少的結果不可靠。
""", encoding="utf-8")
    pd.set_option("display.width", 240)
    pd.set_option("display.unicode.east_asian_width", True)
    print(f"隔日沖分點 {len(flagged)} 個；檢驗期倒貨比例：隔日沖分點 {rate('隔日沖分點')}、其他 {rate('其他分點')}")
    print(out.to_string(index=False))


if __name__ == "__main__":
    do_fetch() if len(sys.argv) > 1 and sys.argv[1] == "fetch" else analyse()
