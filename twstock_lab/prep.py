"""把快取的原始資料整理成可回測的日線表：讀股價、讀除權息參考價、還原股價。只讀快取，不連網。"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, List, Tuple

import pandas as pd

from . import fetch

MAX_OVERNIGHT_GAP = 0.11  # 台股漲跌幅上限 10%：隔夜跳空超過 11% 一定是還沒處理到的減資、分割或面額變更

Event = Tuple[pd.Timestamp, float]  # (除權息日, 還原係數 = 參考價 / 前一日收盤價)


def load_manifest(name: str = "manifest.json") -> Dict[str, object]:
    return json.loads((fetch.DATA / name).read_text(encoding="utf-8"))


def roc_date(text: str) -> pd.Timestamp:
    """民國日期（112年01月04日 或 112/01/05）→ 西元。"""
    y, m, d = (int(x) for x in re.findall(r"\d+", text)[:3])
    return pd.Timestamp(year=y + 1911, month=m, day=d)


def load_exdiv(manifest: Dict[str, object]) -> Dict[str, List[Event]]:
    """每檔股票的除權息還原係數。證交所與櫃買的表格前五欄順序相同：日期、代號、名稱、前收盤、參考價。"""
    f = fetch.fetcher()
    events: Dict[str, List[Event]] = {}
    for urls in manifest["exdiv"].values():  # type: ignore[union-attr]
        for url in urls:
            data = json.loads(f.get(url))
            rows = data.get("data") or (data.get("tables") or [{}])[0].get("data") or []
            for row in rows:
                before, ref = fetch._number(row[3]), fetch._number(row[4])
                if before > 0 and 0 < ref < before * 1.5:
                    events.setdefault(str(row[1]).strip(), []).append((roc_date(str(row[0])), ref / before))
    return events


def load_prices(code: str, manifest: Dict[str, object]) -> pd.DataFrame:
    """FinMind 的未還原日線 → 欄位 open / high / low / close / volume / value，索引是日期；沒有成交的日子拿掉。"""
    data = json.loads(fetch.fetcher().get(manifest["prices"][code]))["data"]  # type: ignore[index]
    df = pd.DataFrame(data).rename(columns={"max": "high", "min": "low", "Trading_Volume": "volume", "Trading_money": "value"})
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date").sort_index()[["open", "high", "low", "close", "volume", "value"]].astype(float)
    return df[(df["volume"] > 0) & (df["open"] > 0)]


def adjust(df: pd.DataFrame, events: List[Event]) -> Tuple[pd.DataFrame, int]:
    """往前還原：除權息日之前的價格乘上係數，讓配息、配股不會被當成下跌。
    另外把「隔夜跳空超過 11%」當成減資 / 分割等事件，假設那一晚的真實漲跌是 0 來接起來。回傳 (還原後的表, 這種跳空的次數)。"""
    out = df.copy()
    factor = pd.Series(1.0, index=out.index)
    for day, ratio in events:
        factor[out.index < day] *= ratio
    for col in ("open", "high", "low", "close"):
        out[col] = out[col] * factor
    gap = out["open"] / out["close"].shift(1) - 1
    jumps = gap[gap.abs() > MAX_OVERNIGHT_GAP]
    fix = pd.Series(1.0, index=out.index)
    for day, g in jumps.items():
        fix[out.index < day] *= 1 + g
    for col in ("open", "high", "low", "close"):
        out[col] = out[col] * fix
    out["raw_close"] = df["close"]
    return out, len(jumps)


def load_all(manifest: Dict[str, object] | None = None) -> Tuple[Dict[str, pd.DataFrame], Dict[str, int]]:
    """所有已抓到的股票 → {代號: 還原後日線}，以及每檔被當成減資 / 分割接起來的跳空次數。"""
    manifest = manifest or load_manifest()
    events = load_exdiv(manifest)
    frames: Dict[str, pd.DataFrame] = {}
    jumps: Dict[str, int] = {}
    for code in manifest["prices"]:  # type: ignore[union-attr]
        raw = load_prices(code, manifest)
        if len(raw) < 250:  # 上市不到一年，指標還算不出來
            continue
        frames[code], n = adjust(raw, events.get(code, []))
        if n:
            jumps[code] = n
    return frames, jumps


def data_dir() -> Path:
    return fetch.DATA
