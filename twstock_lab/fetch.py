"""抓資料（全部走記憶中樞的 gentle-fetch：一次一個請求、同站隔 6～12 秒、看 robots.txt、有快取、被拒就停）。

資料來源（2026-11-13 實測）：
- 股票清單與當日成交值：證交所 openapi STOCK_DAY_ALL、櫃買 openapi tpex_mainboard_quotes
- 日線（未還原）：FinMind TaiwanStockPrice，一檔一次請求就有多年資料
- 除權息參考價：證交所 TWT49U、櫃買 exDailyQ，一次請求拿全市場一整年
- 沒用 Yahoo：它的 robots.txt 不允許；FinMind 的還原股價資料集要付費，所以自己用除權息參考價還原
"""

from __future__ import annotations

import json
import os
import sys
from datetime import date
from pathlib import Path
from typing import Dict, List

GENTLE_DIR = Path(os.environ.get("GENTLE_FETCH_DIR", Path.home() / ".claude/ai-memory-hub/skills/gentle-fetch/scripts"))
sys.path.insert(0, str(GENTLE_DIR))
try:
    from gentle_fetch import GentleFetcher, SiteBlocked  # noqa: E402
except ImportError as error:  # pragma: no cover
    raise SystemExit(f"找不到 gentle_fetch.py（{GENTLE_DIR}）。請設定環境變數 GENTLE_FETCH_DIR 指到 gentle-fetch 技能的 scripts 資料夾。") from error

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "cache"
BENCHMARK = "0050"

TWSE_ALL = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
TPEX_ALL = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_quotes"
FINMIND_PRICE = "https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockPrice&data_id={code}&start_date={start}"
TWSE_DAY = "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?date={ymd}&type=ALLBUT0999&response=json"
TPEX_DAY = "https://www.tpex.org.tw/www/zh-tw/afterTrading/dailyQuotes?date={y}%2F{m}%2F{d}&response=json"
TWSE_EXDIV = "https://www.twse.com.tw/rwd/zh/exRight/TWT49U?startDate={y}0101&endDate={y}1231&response=json"
TPEX_EXDIV = "https://www.tpex.org.tw/www/zh-tw/bulletin/exDailyQ?startDate={y}%2F01%2F01&endDate={y}%2F12%2F31&response=json"


def fetcher() -> GentleFetcher:
    CACHE.mkdir(parents=True, exist_ok=True)
    return GentleFetcher(str(CACHE))


def _number(text: object) -> float:
    try:
        return float(str(text).replace(",", "").strip())
    except ValueError:
        return 0.0


def is_common_stock(code: str) -> bool:
    """只留一般股票：4 碼數字且不是 00 開頭（ETF、權證、特別股、存託憑證代號都不符合）。"""
    return len(code) == 4 and code.isdigit() and not code.startswith("00")


def universe(f: GentleFetcher, top: int = 150) -> List[Dict[str, object]]:
    """上市 + 上櫃，依最近一個交易日的成交值由大到小取前 top 檔。"""
    rows: List[Dict[str, object]] = []
    for item in json.loads(f.get(TWSE_ALL)):
        if is_common_stock(item["Code"]):
            rows.append({"code": item["Code"], "name": item["Name"], "market": "twse", "value": _number(item["TradeValue"])})
    for item in json.loads(f.get(TPEX_ALL)):
        code = item["SecuritiesCompanyCode"]
        if is_common_stock(code):
            rows.append({"code": code, "name": item["CompanyName"].strip(), "market": "tpex",
                         "value": _number(item.get("TransactionAmount", 0))})
    rows.sort(key=lambda r: -float(r["value"]))
    return rows[:top]


def universe_asof(f: GentleFetcher, day: date, top: int = 150) -> List[Dict[str, object]]:
    """用「過去某一天」的成交值選前 top 檔。拿這份名單回測那一天之後的表現，名單本身就沒有後見之明。"""
    rows: List[Dict[str, object]] = []
    twse = json.loads(f.get(TWSE_DAY.format(ymd=day.strftime("%Y%m%d"))))
    for table in twse.get("tables") or []:
        fields = table.get("fields") or []
        if "證券代號" in fields and "成交金額" in fields:
            c, n, v = fields.index("證券代號"), fields.index("證券名稱"), fields.index("成交金額")
            rows += [{"code": r[c], "name": r[n], "market": "twse", "value": _number(r[v])}
                     for r in table["data"] if is_common_stock(r[c])]
    tpex = json.loads(f.get(TPEX_DAY.format(y=day.year, m=f"{day.month:02d}", d=f"{day.day:02d}")))
    for table in tpex.get("tables") or []:
        fields = table.get("fields") or []
        if "代號" in fields and "成交金額(元)" in fields:
            c, n, v = fields.index("代號"), fields.index("名稱"), fields.index("成交金額(元)")
            rows += [{"code": r[c], "name": str(r[n]).strip(), "market": "tpex", "value": _number(r[v])}
                     for r in table["data"] if is_common_stock(r[c])]
    rows.sort(key=lambda r: -float(r["value"]))
    return rows[:top]


def price_url(code: str, start: str) -> str:
    return FINMIND_PRICE.format(code=code, start=start)


def exdiv_urls(start_year: int, end_year: int) -> Dict[str, List[str]]:
    years = range(start_year, end_year + 1)
    return {"twse": [TWSE_EXDIV.format(y=y) for y in years], "tpex": [TPEX_EXDIV.format(y=y) for y in years]}


def fetch_all(top: int = 150, years: int = 8, asof: date | None = None) -> Dict[str, object]:
    """抓齊回測要的資料並寫 manifest（之後的步驟只讀快取，不再連網）。被某個網站拒絕就停抓那個網站並記下來。
    asof 有給：改用那一天的成交值選股，寫 data/manifest_asof.json；已經抓過的股票直接用快取。"""
    f = fetcher()
    today = date.today()
    start = f"{today.year - years}-{today.month:02d}-01"
    stocks = universe_asof(f, asof, top) if asof else universe(f, top)
    out_file = ROOT / "data" / ("manifest_asof.json" if asof else "manifest.json")
    manifest: Dict[str, object] = {"fetched": today.isoformat(), "start": start, "stocks": stocks,
                                   "universe_date": (asof or today).isoformat(),
                                   "prices": {}, "exdiv": {"twse": [], "tpex": []}, "failed": []}
    failed: List[str] = manifest["failed"]  # type: ignore[assignment]
    for market, urls in exdiv_urls(today.year - years, today.year).items():
        for url in urls:
            try:
                f.get(url)
                manifest["exdiv"][market].append(url)  # type: ignore[index]
            except SiteBlocked as error:
                failed.append(f"{url}：{error}")
                break
            except Exception as error:  # noqa: BLE001  單一年份失敗不影響其他
                failed.append(f"{url}：{type(error).__name__} {error}")
    codes = [BENCHMARK] + [str(s["code"]) for s in stocks]
    for i, code in enumerate(codes, 1):
        url = price_url(code, start)
        try:
            f.get(url)
            manifest["prices"][code] = url  # type: ignore[index]
        except SiteBlocked as error:
            failed.append(f"{code}：{error}")
            break
        except Exception as error:  # noqa: BLE001
            failed.append(f"{code}：{type(error).__name__} {error}")
            if "402" in str(error):  # FinMind 免費額度這個小時用完了：停下來，等額度恢復再重跑（已抓的有快取）
                failed.append("FinMind 額度用完，停止；約一小時後重跑同一個指令會從這裡接續")
                break
        if i % 10 == 0:
            print(f"股價 {i}/{len(codes)}", flush=True)
            out_file.write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    out_file.write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    return manifest


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="抓回測用的資料（溫和抓取）")
    parser.add_argument("--asof", help="用這一天（YYYY-MM-DD）的成交值選股，名單沒有後見之明")
    args = parser.parse_args()
    result = fetch_all(asof=date.fromisoformat(args.asof) if args.asof else None)
    print(f"完成：股價 {len(result['prices'])} 檔、除權息 {sum(len(v) for v in result['exdiv'].values())} 份、失敗 {len(result['failed'])} 筆")
    for line in result["failed"]:
        print("  失敗：", line)
