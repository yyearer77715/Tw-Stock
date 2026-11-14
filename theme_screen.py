"""題材名單初篩（0 token，本機腳本）：python theme_screen.py 聯電 6770 世界 ...

給一串代號或名稱，每檔只抓日線（一次請求，有快取），列出趨勢狀態並排序。
用來從一份概念股名單裡先挑出值得細看的幾檔，再用 stock_check.py 逐檔體檢。

「起漲分數」六項各 1 分，全部是當天收盤就看得到的客觀條件：
  站上 20 日線｜站上 60 日線｜20 日線上彎（高於 5 天前）｜近 5 日內收盤創前 20 日新高｜
  近 5 日均量大於 20 日均量｜沒有離 20 日線太遠（乖離 10% 以內）
注意：回測顯示「突破、黃金交叉」這類順勢訊號逐年很不穩定，這個分數只描述現況，不是驗證過的買點。
另外列出兩個回測驗證過的逢低條件：布林下軌反彈、60 日乖離率低於 −15%。
"""

from __future__ import annotations

import sys

import pandas as pd

import stock_check
from twstock_lab import fetch, prep
from twstock_lab import strategies as st


def screen_one(key: str) -> dict:
    f = fetch.fetcher()
    stock = stock_check.find_stock(key)
    code = stock["code"]
    start = f"{pd.Timestamp.today().year - 8}-{pd.Timestamp.today().month:02d}-01"
    raw = prep.load_prices(code, {"prices": {code: stock_check.FINMIND.format(ds="TaiwanStockPrice", code=code, start=start)}})
    df, _ = prep.adjust(raw, stock_check.exdiv_events(f, code))
    c, v = df["close"], df["volume"]
    ma20, ma60 = st.sma(c, 20), st.sma(c, 60)
    new_high = (c > st.prior_high(df["high"], 20)).tail(5).any()
    checks = {
        "站上20日線": c.iloc[-1] > ma20.iloc[-1],
        "站上60日線": c.iloc[-1] > ma60.iloc[-1],
        "20日線上彎": ma20.iloc[-1] > ma20.iloc[-6],
        "近5日創20日新高": bool(new_high),
        "量增": v.tail(5).mean() > v.tail(20).mean(),
        "未過熱": c.iloc[-1] / ma20.iloc[-1] - 1 < 0.10,
    }
    ret = lambda n: round(100 * (c.iloc[-1] / c.iloc[-1 - n] - 1), 1)  # noqa: E731
    dip, _ = st.bollinger_dip(df, 20, 2.0, 120)
    return {
        "代號": code, "名稱": stock["name"], "收盤": raw["close"].iloc[-1],
        "5日%": ret(5), "20日%": ret(20), "60日%": ret(60),
        "距250日高%": round(100 * (c.iloc[-1] / c.tail(250).max() - 1), 1),
        "20日乖離%": round(100 * (c.iloc[-1] / ma20.iloc[-1] - 1), 1),
        "60日乖離%": round(100 * (c.iloc[-1] / ma60.iloc[-1] - 1), 1),
        "成交值億": round(df["value"].iloc[-1] / 1e8, 1),
        "起漲分數": sum(bool(x) for x in checks.values()),
        "沒過的": "、".join(k for k, ok in checks.items() if not ok) or "—",
        "布林下軌": "有" if dip.iloc[-1] else "",
        "乖離<-15%": "有" if c.iloc[-1] / ma60.iloc[-1] - 1 < -0.15 else "",
    }


def main(keys: list) -> pd.DataFrame:
    rows = []
    for key in keys:
        try:
            rows.append(screen_one(key))
        except SystemExit as error:  # 名稱對不到或對到多檔
            print(f"略過 {key}：{error}", file=sys.stderr)
        except Exception as error:  # noqa: BLE001  單一檔抓不到不影響其他
            print(f"略過 {key}：{type(error).__name__} {str(error)[:80]}", file=sys.stderr)
    table = pd.DataFrame(rows).sort_values(["起漲分數", "20日%"], ascending=False)
    pd.set_option("display.width", 250)
    pd.set_option("display.unicode.east_asian_width", True)
    print(table.to_string(index=False))
    return table


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit("用法：python theme_screen.py <代號或名稱> [更多...]")
    main(sys.argv[1:])
