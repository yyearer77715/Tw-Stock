"""從全市場找「電子產業、低價、冷門、近期跌深」的股票，寫成題材名單檔給 run_theme.py 掃（0 token）。

    python run_sector.py 70              # 收盤 70 元以下 → themes/elec-cheap.json
    python run_theme.py elec-cheap 70    # 逐檔寫現況、進場條件、理由

只用公開的全市場資料，一共 8 次請求（有快取）：證交所、櫃買的公司產業別，以及今天、約 20、約 60 個交易日前三天的全市場日行情。
先用這三天的收盤價粗篩，是為了不必替幾百檔股票各抓一次多年日線（FinMind 免費額度一小時約 150 次）。
粗篩條件：電子相關產業（產業別 24～31）、收盤在上限以下、三天平均成交值 0.1～3 億、收盤比 60 個交易日前低 15% 以上；取跌最深的 TOP 檔。
注意：這是「AI 供應鏈所在的電子產業」，不是 AI 概念股名單，個別公司和 AI 有沒有關係沒有查。粗篩的跌幅沒有還原除權息。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Dict, List

import pandas as pd

from twstock_lab import fetch, prep

ROOT = Path(__file__).resolve().parent
TWSE_PROFILE = "https://openapi.twse.com.tw/v1/opendata/t187ap03_L"
TPEX_PROFILE = "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O"
ELECTRONICS = {"24": "半導體業", "25": "電腦及週邊設備業", "26": "光電業", "27": "通信網路業", "28": "電子零組件業",
               "29": "電子通路業", "30": "資訊服務業", "31": "其他電子業"}
MIN_VALUE, MAX_VALUE = 0.1, 3.0   # 億元
DROP = -0.15
TOP = 40


def industries(f) -> Dict[str, str]:
    """代號 → 產業名稱（只留電子相關）。"""
    out = {}
    for item in json.loads(f.get(TWSE_PROFILE)):
        out[item["公司代號"]] = ELECTRONICS.get(item["產業別"].strip())
    for item in json.loads(f.get(TPEX_PROFILE)):
        out[item["SecuritiesCompanyCode"]] = ELECTRONICS.get(item["SecuritiesIndustryCode"].strip())
    return {c: n for c, n in out.items() if n}


def quotes(f, day: pd.Timestamp) -> Dict[str, dict]:
    """某一天全市場的 代號 → {name, close, value}。"""
    rows: Dict[str, dict] = {}
    twse = json.loads(f.get(fetch.TWSE_DAY.format(ymd=day.strftime("%Y%m%d"))))
    for table in twse.get("tables") or []:
        fields = table.get("fields") or []
        if "證券代號" in fields and "收盤價" in fields:
            c, n, p, v = (fields.index(x) for x in ("證券代號", "證券名稱", "收盤價", "成交金額"))
            for r in table["data"]:
                rows[r[c]] = {"name": r[n], "close": fetch._number(r[p]), "value": fetch._number(r[v])}
    tpex = json.loads(f.get(fetch.TPEX_DAY.format(y=day.year, m=f"{day.month:02d}", d=f"{day.day:02d}")))
    for table in tpex.get("tables") or []:
        fields = table.get("fields") or []
        if "代號" in fields and "收盤" in fields:
            c, n, p, v = (fields.index(x) for x in ("代號", "名稱", "收盤", "成交金額(元)"))
            for r in table["data"]:
                rows[r[c]] = {"name": str(r[n]).strip(), "close": fetch._number(r[p]), "value": fetch._number(r[v])}
    return rows


def pick(now: Dict[str, dict], mid: Dict[str, dict], old: Dict[str, dict], sector: Dict[str, str], max_price: float) -> List[dict]:
    """粗篩。三天都有成交價才算；回傳照跌幅由深到淺排序的前 TOP 檔。"""
    found = []
    for code, q in now.items():
        if code not in sector or not fetch.is_common_stock(code) or code not in mid or code not in old:
            continue
        closes = [q["close"], mid[code]["close"], old[code]["close"]]
        if min(closes) <= 0 or q["close"] > max_price:
            continue
        value = sum(x[code]["value"] for x in (now, mid, old)) / 3 / 1e8
        drop = q["close"] / old[code]["close"] - 1
        if MIN_VALUE <= value < MAX_VALUE and drop <= DROP:
            found.append({"code": code, "name": q["name"], "industry": sector[code], "close": q["close"], "value": value, "drop": drop})
    return sorted(found, key=lambda r: r["drop"])[:TOP]


def main(max_price: float) -> List[dict]:
    f = fetch.fetcher()
    frames, _ = prep.load_all(prep.load_manifest("manifest.json"))
    days = frames[fetch.BENCHMARK].index           # 用 0050 的交易日當日曆
    now, mid, old = (quotes(f, days[i]) for i in (-1, -21, -61))
    rows = pick(now, mid, old, industries(f), max_price)
    spec = {
        "name": f"電子產業低價冷門股（{max_price:g} 元以下）",
        "updated": str(days[-1].date()),
        "note": f"不是 AI 概念股名單：這是 AI 供應鏈所在的電子相關產業裡，收盤 {max_price:g} 元以下、成交值 {MIN_VALUE}～{MAX_VALUE:g} 億、"
                f"比 {days[-61].date()} 低 {-DROP:.0%} 以上的股票，取跌最深的 {TOP} 檔。個別公司和 AI 有沒有關係沒有查，分級欄一律是「未查」。",
        "stocks": [{"code": r["code"], "name": r["name"], "grade": "未查",
                    "role": f"{r['industry']}；粗篩時收盤 {r['close']:g}、比 60 個交易日前 {100 * r['drop']:+.0f}%、成交值約 {r['value']:.1f} 億",
                    "source": TWSE_PROFILE if len(r["code"]) == 4 and r["code"] in _twse_codes(f) else TPEX_PROFILE} for r in rows],
    }
    out = ROOT / "themes" / "elec-cheap.json"
    out.write_text(json.dumps(spec, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"粗篩到 {len(rows)} 檔，寫到 {out.name}；接著跑：python run_theme.py elec-cheap {max_price:g}")
    return rows


def _twse_codes(f) -> set:
    return {item["公司代號"] for item in json.loads(f.get(TWSE_PROFILE))}


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("用法：python run_sector.py <股價上限>")
    result = main(float(sys.argv[1]))
    pd.set_option("display.width", 200)
    pd.set_option("display.unicode.east_asian_width", True)
    print(pd.DataFrame(result).round({"value": 2, "drop": 3}).to_string(index=False))
