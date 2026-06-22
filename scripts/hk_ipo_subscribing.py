#!/usr/bin/env python3
"""Fetch Hong Kong IPOs currently open for subscription (招股中).

Sources (default --source auto = Futu first, AASTOCKS fallback):
  1. Futu OpenD  —  PRIMARY. ctx.get_ipo_list('HK') returns the same data the user
     sees in the Futu app, including the authoritative `is_subscribe_status` flag
     (no need to infer "可打新" from dates), `entrance_price` (一手入场费), `lot_size`,
     `ipo_price_min/max`, `apply_end_time`, `list_time`. Requires OpenD running on
     127.0.0.1:11111 and `pip3 install futu-api`. Read-only (quote context only,
     never trade). Futu does NOT provide 行业/暗盘日期/中文名 — those are enriched
     from AASTOCKS when reachable, else left blank.
  2. AASTOCKS upcomingipo.aspx  —  FALLBACK + enricher. Provides 行业/暗盘/中文名.
     "可打新" here is inferred as 招股截止日 >= today.

NOTE: akshare's stock_ipo_hk_ths is NOT usable — despite the name it returns A-share
new stocks (6-digit codes), not Hong Kong IPOs.

Usage:
    python3 hk_ipo_subscribing.py                 # auto source, JSON + write CSV
    python3 hk_ipo_subscribing.py --summary       # human-readable table
    python3 hk_ipo_subscribing.py --source futu    # force Futu only
    python3 hk_ipo_subscribing.py --source aastocks # force AASTOCKS only
    python3 hk_ipo_subscribing.py --all           # include already-closed/listed
    python3 hk_ipo_subscribing.py --no-write      # don't write CSV archive
"""
import argparse
import csv
import json
import os
import re
import sys
from datetime import datetime, date
from pathlib import Path

import requests
from bs4 import BeautifulSoup


UPCOMING_URL = "https://www.aastocks.com/sc/stocks/market/ipo/upcomingipo.aspx"
ARCHIVE_DIR = Path(os.environ.get("PORTFOLIO_DIR", str(Path.home() / "Desktop/持仓"))) / "测算归档/港股IPO"
FUTU_HOST = os.environ.get("FUTU_OPEND_HOST", "127.0.0.1")
FUTU_PORT = int(os.environ.get("FUTU_OPEND_PORT", "11111"))
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
    )
}

CODE_RE = re.compile(r"(.+?)\s*(\d{4,5}\.HK)")
DATE_RE = re.compile(r"(\d{4})[/-](\d{1,2})[/-](\d{1,2})")  # accepts 2026/06/23 and 2026-06-23


def parse_date(value):
    if not value:
        return None
    m = DATE_RE.search(value)
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def to_float(value):
    try:
        return float(str(value).replace(",", "").replace("+", "").strip())
    except (TypeError, ValueError):
        return None


def parse_calendar(html):
    """Return list of dict rows from the AASTOCKS upcoming IPO calendar table."""
    soup = BeautifulSoup(html, "html.parser")
    headers = ["名称代号", "行业", "招股价", "每手股数", "入场费", "招股截止日", "暗盘日期", "上市日期"]
    rows = []
    for table in soup.find_all("table"):
        txt = table.get_text(" ", strip=True)
        if "招股截止日" not in txt or "上市日期" not in txt or "每手" not in txt:
            continue
        for tr in table.find_all("tr"):
            cells = [c.get_text(" ", strip=True) for c in tr.find_all("td")]
            cells = [c for c in cells if c]
            if len(cells) != 8:
                continue
            if cells[0].startswith("公司名称"):  # header row
                continue
            record = dict(zip(headers, cells))
            m = CODE_RE.match(record["名称代号"])
            record["名称"] = m.group(1).strip() if m else record["名称代号"]
            record["代号"] = m.group(2) if m else ""
            if not record["代号"]:
                continue
            record.pop("名称代号", None)
            rows.append(record)
        if rows:
            break
    return rows


def enrich(record, today):
    close_d = parse_date(record.get("招股截止日"))
    list_d = parse_date(record.get("上市日期"))
    record["招股截止日_parsed"] = close_d.isoformat() if close_d else None
    record["上市日期_parsed"] = list_d.isoformat() if list_d else None
    record["入场费_num"] = to_float(record.get("入场费"))
    record["每手股数_num"] = to_float(record.get("每手股数"))
    # actionable status
    if close_d is None:
        record["status"] = "未知"
        record["days_to_close"] = None
    else:
        delta = (close_d - today).days
        record["days_to_close"] = delta
        if delta < 0:
            record["status"] = "已截止"
        elif delta == 0:
            record["status"] = "今日截止"
        else:
            record["status"] = "招股中"
    return record


def fetch_aastocks(include_closed=False, today=None):
    today = today or date.today()
    r = requests.get(UPCOMING_URL, headers=HEADERS, timeout=25)
    r.raise_for_status()
    rows = [enrich(rec, today) for rec in parse_calendar(r.text)]
    for rec in rows:
        rec["source"] = "aastocks"
    if not include_closed:
        rows = [r for r in rows if r["status"] != "已截止"]
    rows.sort(key=lambda x: (x["招股截止日_parsed"] or "9999", x["代号"]))
    return rows


def _price_range(pmin, pmax):
    """Format Futu ipo_price_min/max into a display string."""
    pmin = to_float(pmin)
    pmax = to_float(pmax)
    if not pmax:
        return "N/A"
    if pmin and pmin > 0 and abs(pmin - pmax) > 1e-9:
        return f"{pmin:g}-{pmax:g}"
    if pmin and pmin > 0:
        return f"{pmin:g}"
    return f"≤{pmax:g}"  # min==0 means only the cap is fixed


def fetch_futu(include_closed=False, today=None):
    """PRIMARY source. Read-only quote context; never trades."""
    today = today or date.today()
    from futu import OpenQuoteContext, Market, RET_OK  # noqa: PLC0415

    ctx = OpenQuoteContext(host=FUTU_HOST, port=FUTU_PORT)
    try:
        ret, data = ctx.get_ipo_list(Market.HK)
    finally:
        ctx.close()
    if ret != RET_OK:
        raise RuntimeError(f"Futu get_ipo_list failed: {data}")

    rows = []
    for _, r in data.iterrows():
        subscribing = bool(r.get("is_subscribe_status"))
        if not subscribing and not include_closed:
            continue
        code = str(r.get("code", "")).replace("HK.", "")
        close_d = parse_date(str(r.get("apply_end_time") or ""))
        start_d = parse_date(str(r.get("apply_start_time") or ""))
        list_d = parse_date(str(r.get("list_time") or ""))
        if subscribing:
            if close_d is not None and close_d == today:
                status = "今日截止"
            else:
                status = "招股中"
        else:
            status = "已结束/已上市"
        rec = {
            "代号": f"{code}.HK",
            "名称": str(r.get("name", "")),  # English/pinyin from Futu; enriched later
            "行业": "",
            "招股价": _price_range(r.get("ipo_price_min"), r.get("ipo_price_max")),
            "每手股数": int(to_float(r.get("lot_size")) or 0),
            "入场费": round(to_float(r.get("entrance_price")) or 0, 2),
            "招股开始日": start_d.isoformat() if start_d else None,
            "招股截止日": close_d.strftime("%Y/%m/%d") if close_d else "N/A",
            "暗盘日期": "",
            "上市日期": list_d.strftime("%Y/%m/%d") if list_d else "N/A",
            "招股截止日_parsed": close_d.isoformat() if close_d else None,
            "上市日期_parsed": list_d.isoformat() if list_d else None,
            "入场费_num": round(to_float(r.get("entrance_price")) or 0, 2),
            "每手股数_num": to_float(r.get("lot_size")),
            "days_to_close": (close_d - today).days if close_d else None,
            "is_subscribe_status": subscribing,
            "status": status,
            "source": "futu",
        }
        rows.append(rec)
    rows.sort(key=lambda x: (x["招股截止日_parsed"] or "9999", x["代号"]))
    return rows


def enrich_with_aastocks(rows, today):
    """Best-effort: fill 行业/暗盘日期/中文名 onto Futu rows by code. Non-fatal."""
    try:
        aa = {r["代号"]: r for r in fetch_aastocks(include_closed=True, today=today)}
    except Exception:  # noqa: BLE001
        return rows  # AASTOCKS unreachable; keep Futu-only data
    for rec in rows:
        match = aa.get(rec["代号"])
        if not match:
            continue
        rec["行业"] = match.get("行业", "") or rec.get("行业", "")
        rec["暗盘日期"] = match.get("暗盘日期", "") or rec.get("暗盘日期", "")
        cn = match.get("名称", "")
        if cn and not re.search(r"[一-鿿]", rec["名称"]):
            rec["名称_en"] = rec["名称"]
            rec["名称"] = cn
    return rows


def fetch(include_closed=False, today=None, source="auto"):
    """Dispatch to Futu (primary) / AASTOCKS (fallback) per `source`.

    Returns (rows, source_used, note). note is a human-readable string about any fallback.
    """
    today = today or date.today()
    if source in ("futu", "auto"):
        try:
            rows = fetch_futu(include_closed=include_closed, today=today)
            rows = enrich_with_aastocks(rows, today)
            return rows, "futu", None
        except Exception as e:  # noqa: BLE001
            note = f"富途 OpenD 不可用（{type(e).__name__}: {e}），已回退 AASTOCKS"
            if source == "futu":
                raise
    else:
        note = None
    rows = fetch_aastocks(include_closed=include_closed, today=today)
    return rows, "aastocks", note


def write_csv(rows, today):
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    out = ARCHIVE_DIR / f"hk_ipo_subscribing_{today.isoformat()}.csv"
    fields = ["代号", "名称", "行业", "招股价", "每手股数", "入场费",
              "招股开始日", "招股截止日", "暗盘日期", "上市日期", "status", "days_to_close", "source"]
    with out.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)
    return out


def print_summary(rows, today, source="?", note=None):
    src_label = {"futu": "富途 OpenD（实时·账户同源）", "aastocks": "AASTOCKS"}.get(source, source)
    if note:
        print(f"⚠️  {note}\n")
    if not rows:
        print(f"今日（{today.isoformat()}）无在招/可打新的新股。来源：{src_label}")
        return
    print(f"📅 港股打新 · 当日可打新清单（{today.isoformat()}，共 {len(rows)} 只）｜来源：{src_label}\n")
    hdr = f"{'代号':<11}{'名称':<16}{'状态':<8}{'招股价':<14}{'每手':<7}{'入场费':<11}{'暗盘':<12}{'上市日':<12}行业"
    print(hdr)
    print("-" * 124)
    for r in rows:
        name = str(r.get("名称", ""))[:14]
        print(f"{r['代号']:<11}{name:<16}{r['status']:<8}{str(r.get('招股价','')):<14}"
              f"{str(r.get('每手股数','')):<7}{str(r.get('入场费','')):<11}{str(r.get('暗盘日期') or '-'):<12}"
              f"{str(r.get('上市日期','')):<12}{r.get('行业','') or '-'}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--summary", action="store_true", help="human-readable table")
    ap.add_argument("--all", action="store_true", help="include already-closed rows")
    ap.add_argument("--no-write", action="store_true", help="do not write CSV archive")
    ap.add_argument("--source", choices=["auto", "futu", "aastocks"], default="auto",
                    help="data source: auto=Futu first then AASTOCKS (default)")
    ap.add_argument("--date", help="override today (YYYY-MM-DD), for testing")
    args = ap.parse_args()

    today = datetime.strptime(args.date, "%Y-%m-%d").date() if args.date else date.today()

    try:
        rows, source_used, note = fetch(include_closed=args.all, today=today, source=args.source)
    except Exception as e:  # noqa: BLE001
        msg = {"error": f"{type(e).__name__}: {e}", "rows": []}
        if args.summary:
            hint = "富途 OpenD 未运行或 futu-api 未安装" if args.source == "futu" else "数据源暂不可用"
            print(f"获取招股清单失败：{msg['error']}（{hint}，可稍后重试或换 --source）")
        else:
            print(json.dumps(msg, ensure_ascii=False, indent=2))
        sys.exit(1)

    csv_path = None
    if not args.no_write and rows:
        csv_path = write_csv(rows, today)

    if args.summary:
        print_summary(rows, today, source=source_used, note=note)
        if csv_path:
            print(f"\n已归档：{csv_path}")
    else:
        print(json.dumps({
            "date": today.isoformat(),
            "source": source_used,
            "note": note,
            "count": len(rows),
            "csv": str(csv_path) if csv_path else None,
            "rows": rows,
        }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
