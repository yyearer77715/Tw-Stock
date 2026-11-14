"""題材名單掃描：每檔寫出現況、進場條件、理由、看錯的訊號（0 token）。

    python run_theme.py ai-cold          # 讀 themes/ai-cold.json，結果寫 reports/THEME_ai-cold.md
    python run_theme.py                  # 列出有哪些題材名單

名單檔 themes/<題材>.json 是事先整理好的（代號、和題材的關係、分級、出處）；這支程式不上網查名單，只抓每檔的日線與季財報（有快取）。
「冷門」沒辦法直接量，這裡用兩個算得出來的條件代替：近 20 日平均成交值不到 COLD_VALUE 億，而且近 120 個交易日沒有連續兩天漲停。
進場條件全部沿用回測驗證過的（乖離率、量縮止跌、等止穩、全面大跌與否），理由附回測數字。
注意：這些條件是用成交值前 150 大的股票回測的，冷門小型股沒有回測過。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

import run_crash
import stock_check
from twstock_lab import fetch, prep
from twstock_lab import strategies as st

ROOT = Path(__file__).resolve().parent
THEMES, REPORTS = ROOT / "themes", ROOT / "reports"
COLD_VALUE = 3.0        # 億元：近 20 日平均成交值低於這個才算冷門
DEEP = 0.15             # 低於 60 日線多少算跌深（和回測的乖離率策略同一個數字）
BROAD = 0.10            # 名單裡跌深的股票超過這個比例算全面大跌（和 run_crash.py 同一個數字）
LIMIT_UP = 0.095


def measure(df: pd.DataFrame, eps_ttm: Optional[float]) -> Dict[str, object]:
    """一檔股票今天的狀態。df 是還原後的日線。"""
    c = df["close"]
    ma20, ma60 = st.sma(c, 20), st.sma(c, 60)
    up = (c.pct_change() >= LIMIT_UP).tail(120)
    dry, _ = st.dry_up_low(df, n=60, ratio=0.7, exit_ma=20)
    return {
        "收盤": float(c.iloc[-1]), "前一天最高": float(df["high"].iloc[-2]),
        "60日線": float(ma60.iloc[-1]), "20日線": float(ma20.iloc[-1]),
        "離60日線": float(c.iloc[-1] / ma60.iloc[-1] - 1),
        "近5日": float(c.iloc[-1] / c.iloc[-6] - 1), "近20日": float(c.iloc[-1] / c.iloc[-21] - 1),
        "離250日高": float(c.iloc[-1] / c.tail(250).max() - 1),
        "均成交值億": float(df["value"].tail(20).mean() / 1e8),
        "連續漲停": bool((up & up.shift(1, fill_value=False)).any()),
        "止穩": bool(c.iloc[-1] > df["high"].iloc[-2]),
        "量縮止跌": bool(dry.iloc[-1]),
        "近四季EPS": eps_ttm,
    }


def is_cold(m: Dict[str, object]) -> bool:
    return m["均成交值億"] < COLD_VALUE and not m["連續漲停"]


def advice(m: Dict[str, object], broad: bool) -> Dict[str, str]:
    """照回測驗證過的條件給：狀態、進場條件、理由、看錯的訊號。broad = 現在是不是全面性大跌。"""
    deep_price = m["60日線"] * (1 - DEEP)
    deep = m["離60日線"] <= -DEEP
    exit_text = f"出場：收盤回到 60 日線（現在約 {m['60日線']:.1f}）就賣。回測不設停損（設了反而變差），所以每檔資金要小，約 3%。"
    wrong = exit_text + "看錯的訊號：進場後公司公布虧損擴大或營收連續年減；或大盤轉成長期下跌（這種行情沒有被回測考驗過）。"
    if m["近四季EPS"] is not None and m["近四季EPS"] < 0:
        return {"狀態": "不建議",
                "進場條件": "沒有。公司近四季是虧損的，價格條件成立也不列為進場點。",
                "理由": f"近四季每股盈餘 {m['近四季EPS']:.2f} 元。回測裡表現最差的就是「只有它自己跌」的股票（輸對照組 0.88 個百分點、6.6% 的交易賠超過 20%），"
                        "虧損股跌深多半是公司自己的問題。",
                "看錯的訊號": "如果之後單季轉盈、營收年增轉正，再重新看。"}
    if deep and not broad:
        return {"狀態": "不建議（個別下跌）",
                "進場條件": "沒有。等大盤也出現全面性下跌、或它自己止穩後再重新掃描。",
                "理由": f"收盤低於 60 日線 {-100 * m['離60日線']:.1f}%，但現在不是全面性大跌。回測裡大盤沒事、個股自己跌深的那組輸對照組 0.88 個百分點，勝率 65.6%。",
                "看錯的訊號": wrong}
    if deep and m["止穩"]:
        return {"狀態": "符合進場條件",
                "進場條件": "下一個交易日開盤買進（開盤漲停就不追）。",
                "理由": f"收盤低於 60 日線 {-100 * m['離60日線']:.1f}%（門檻 15%），今天收盤 {m['收盤']:.1f} 站上前一天最高 {m['前一天最高']:.1f}，而且現在是全面性大跌。"
                        "回測驗證期：乖離率加上「收盤站上前一天最高」315 筆，勝率 78.1%、每筆平均 +11.85%、贏對照組 7.82 個百分點；全面大跌時 479 筆，勝率 78.3%。",
                "看錯的訊號": wrong}
    if deep:
        return {"狀態": "等止穩",
                "進場條件": f"某天收盤站上前一天的最高價（以今天來說要收在 {m['前一天最高']:.1f} 之上），而且仍低於 {deep_price:.1f}（60 日線的 85%），隔天開盤買。",
                "理由": f"收盤低於 60 日線 {-100 * m['離60日線']:.1f}%，跌深條件成立，但還沒有止穩。回測裡等止穩再進場，賠超過 20% 的比例從 4.2% 降到 2.9%，每筆平均從 +8.54% 變 +11.85%。"
                        + ("另外今天符合「量縮止跌」（創 60 日新低、量不到均量七成），回測 320 筆勝率 78.1%。" if m["量縮止跌"] else ""),
                "看錯的訊號": wrong}
    gap = m["收盤"] / deep_price - 1
    if m["離60日線"] < 0:
        return {"狀態": "還沒到條件",
                "進場條件": f"收盤跌到 {deep_price:.1f} 以下（離現在還有 {100 * gap:.1f}%）並且止穩，才是回測驗證過的買點。",
                "理由": f"收盤在 60 日線之下 {-100 * m['離60日線']:.1f}%，還沒到 15% 的門檻。回測裡乖離不到 15% 的逢低沒有優勢（20 日線下 8% 那組只贏對照組不到 1 個百分點）。",
                "看錯的訊號": "不適用（還沒進場）。"}
    return {"狀態": "沒有驗證過的買點",
            "進場條件": f"收盤在 60 日線之上。要等它跌回 {deep_price:.1f} 以下（離現在 {100 * gap:.1f}%）並止穩，才是回測驗證過的買點。",
            "理由": "股價在均線之上時，能用的只有順勢訊號（突破、黃金交叉、葛蘭碧、一目均衡表）。回測 20 種方法裡順勢類逐年很不穩定，勝率多在 28% 到 43%，不拿來當進場依據。",
            "看錯的訊號": "不適用（還沒進場）。"}


def market_now() -> Dict[str, object]:
    """用已經抓好的成交值前 150 檔算大盤狀態（只讀快取）。"""
    frames, _ = prep.load_all(prep.load_manifest("manifest.json"))
    bench = frames.pop(fetch.BENCHMARK)
    last = run_crash.market_state(frames, bench).iloc[-1]
    return {"日期": bench.index[-1], "深跌比例": float(last["深跌比例"]), "0050近5天": float(last["近5天"]), "全面大跌": bool(last["深跌比例"] > BROAD)}


def load_stock(code: str) -> pd.DataFrame:
    f = fetch.fetcher()
    start = f"{pd.Timestamp.today().year - 8}-{pd.Timestamp.today().month:02d}-01"
    raw = prep.load_prices(code, {"prices": {code: stock_check.FINMIND.format(ds="TaiwanStockPrice", code=code, start=start)}})
    df, _ = prep.adjust(raw, stock_check.exdiv_events(f, code))
    return df


def eps_ttm(code: str) -> Optional[float]:
    """近四季每股盈餘合計；抓不到（額度用完、沒有資料）回 None。"""
    try:
        rows = stock_check.finmind(fetch.fetcher(), "TaiwanStockFinancialStatements", code, f"{pd.Timestamp.today().year - 3}-01-01")
        eps = pd.DataFrame(rows).query("type == 'EPS'").sort_values("date")["value"]
        return float(eps.tail(4).sum()) if len(eps) >= 4 else None
    except Exception:  # noqa: BLE001
        return None


def run(theme: str) -> List[dict]:
    spec = json.loads((THEMES / f"{theme}.json").read_text(encoding="utf-8"))
    market = market_now()
    rows = []
    for s in spec["stocks"]:
        try:
            df = load_stock(s["code"])
        except Exception as error:  # noqa: BLE001  單一檔抓不到不影響其他
            print(f"略過 {s['code']} {s['name']}：{type(error).__name__}", file=sys.stderr)
            continue
        if df.index[-1] != market["日期"] or len(df) < 250:
            print(f"略過 {s['code']} {s['name']}：資料只到 {df.index[-1].date()} 或上市不滿一年", file=sys.stderr)
            continue
        m = measure(df, eps_ttm(s["code"]))
        rows.append({**s, **m, "冷門": is_cold(m), **advice(m, market["全面大跌"])})
    order = {"符合進場條件": 0, "等止穩": 1, "還沒到條件": 2, "沒有驗證過的買點": 3, "不建議（個別下跌）": 4, "不建議": 5}
    rows.sort(key=lambda r: (not r["冷門"], order[r["狀態"]], r["離60日線"]))
    write_report(theme, spec, market, rows)
    return rows


def block(r: dict) -> str:
    eps = "抓不到" if r["近四季EPS"] is None else f"{r['近四季EPS']:.2f} 元"
    return f"""### {r['code']} {r['name']}（分級 {r['grade']}）— {r['狀態']}
- 和題材的關係：{r['role']}（[出處]({r['source']})）
- 現況：收盤 {r['收盤']:.1f}｜離 60 日線 {100 * r['離60日線']:+.1f}%｜近 5 日 {100 * r['近5日']:+.1f}%、近 20 日 {100 * r['近20日']:+.1f}%｜離 250 日高點 {100 * r['離250日高']:+.1f}%｜近 20 日平均成交值 {r['均成交值億']:.1f} 億｜近四季每股盈餘 {eps}
- 進場條件：{r['進場條件']}
- 理由：{r['理由']}
- 看錯的訊號：{r['看錯的訊號']}
"""


def write_report(theme: str, spec: dict, market: dict, rows: List[dict]) -> None:
    cold, rest = [r for r in rows if r["冷門"]], [r for r in rows if not r["冷門"]]
    REPORTS.mkdir(exist_ok=True)
    (REPORTS / f"THEME_{theme}.md").write_text(f"""# {spec['name']}：題材名單掃描

- 資料到 {market['日期'].date()}｜0050 近 5 天 {100 * market['0050近5天']:+.1f}%｜成交值前 150 檔裡跌到 60 日線下 15% 的佔 {100 * market['深跌比例']:.1f}%（超過 {BROAD:.0%} 算全面大跌：{'是' if market['全面大跌'] else '否'}）
- 名單 {len(spec['stocks'])} 檔（{spec['updated']} 整理），掃得到的 {len(rows)} 檔：冷門 {len(cold)} 檔、不算冷門 {len(rest)} 檔。
- 冷門 = 近 20 日平均成交值不到 {COLD_VALUE:.0f} 億，而且近 120 個交易日沒有連續兩天漲停。
- {spec['note']}
- **限制**：進場條件是用成交值前 150 大的股票回測的，冷門小型股沒有回測過；小型股成交量小，買賣價差與滑價比回測假設的大。這不是投資建議，也不保證獲利。

## 冷門的 {len(cold)} 檔

{chr(10).join(block(r) for r in cold) or '（沒有）'}
## 不算冷門的 {len(rest)} 檔（成交值較大或近期連續漲停過）

{chr(10).join(block(r) for r in rest) or '（沒有）'}""", encoding="utf-8")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("用法：python run_theme.py <題材>；現有題材：" + "、".join(sorted(p.stem for p in THEMES.glob("*.json"))))
    result = run(sys.argv[1])
    table = pd.DataFrame([{"代號": r["code"], "名稱": r["name"], "分級": r["grade"], "冷門": "是" if r["冷門"] else "", "狀態": r["狀態"],
                           "收盤": round(r["收盤"], 1), "離60日線%": round(100 * r["離60日線"], 1), "近5日%": round(100 * r["近5日"], 1),
                           "均成交值億": round(r["均成交值億"], 1), "近四季EPS": r["近四季EPS"] if r["近四季EPS"] is None else round(r["近四季EPS"], 2)}
                          for r in result])
    pd.set_option("display.width", 220)
    pd.set_option("display.unicode.east_asian_width", True)
    print(table.to_string(index=False))
