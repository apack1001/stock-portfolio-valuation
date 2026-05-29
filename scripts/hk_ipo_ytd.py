#!/usr/bin/env python3
"""Fetch Hong Kong IPO year-to-date performance from AASTOCKS."""
import argparse
import csv
import re
from datetime import datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup


DEFAULT_OUT = Path.home() / "Desktop/持仓/测算归档/港股IPO"
LISTED_IPO_URL = "https://www.aastocks.com/sc/stocks/market/ipo/listedipo.aspx?s=3&o=0&page={page}"


def to_float(value):
    try:
        return float(str(value).replace(",", "").replace("%", "").replace("+", "").strip())
    except (TypeError, ValueError):
        return None


def parse_listed_ipo_page(html, year):
    soup = BeautifulSoup(html, "html.parser")
    rows = []
    headers = [
        "名称代号", "上市日期", "每手股数", "上市市值", "招股价", "上市价",
        "超额倍数", "稳中一手", "中签率", "现价", "首日表现", "累积表现",
    ]
    for table in soup.find_all("table"):
        text = table.get_text(" ", strip=True)
        if "上市日期" not in text or "首日" not in text or "中签率" not in text:
            continue
        for tr in table.find_all("tr")[1:]:
            cells = [c.get_text(" ", strip=True) for c in tr.find_all("td")]
            if len(cells) != 13:
                continue
            record = dict(zip(headers, cells[1:]))
            if not record["上市日期"].startswith(f"{year}/"):
                continue
            match = re.search(r"(.+?)\s+(\d{5}\.HK)", record["名称代号"])
            record["名称"] = match.group(1) if match else record["名称代号"]
            record["代号"] = match.group(2) if match else ""
            enrich_metrics(record)
            rows.append(record)
        break
    return rows


def enrich_metrics(record):
    lot = to_float(record.get("每手股数"))
    ipo = to_float(record.get("上市价"))
    first_pct = to_float(record.get("首日表现"))
    current = to_float(record.get("现价"))
    if lot is None or ipo is None:
        record.update({"首日收盘价": "N/A", "一手首日盈亏": "N/A", "一手当前盈亏": "N/A", "当前收益率": "N/A"})
        return
    if first_pct is not None:
        first_close = ipo * (1 + first_pct / 100)
        record["首日收盘价"] = f"{first_close:.3f}"
        record["一手首日盈亏"] = f"{(first_close - ipo) * lot:.2f}"
    else:
        record["首日收盘价"] = "N/A"
        record["一手首日盈亏"] = "N/A"
    if current is not None:
        record["一手当前盈亏"] = f"{(current - ipo) * lot:.2f}"
        record["当前收益率"] = f"{(current / ipo - 1) * 100:.2f}%"
    else:
        record["一手当前盈亏"] = "N/A"
        record["当前收益率"] = "N/A"


def fetch_hk_ipo_ytd(year, pages):
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0"})
    rows = []
    last_html = ""
    for page in range(1, pages + 1):
        resp = session.get(LISTED_IPO_URL.format(page=page), timeout=20)
        resp.raise_for_status()
        last_html = resp.text
        page_rows = parse_listed_ipo_page(resp.text, year)
        rows.extend(page_rows)
        if page > 1 and not page_rows:
            break
        if page_rows and not any(r["上市日期"].startswith(f"{year}/") for r in page_rows):
            break
    return rows, last_html


def write_csv(rows, output):
    output.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "上市日期", "代号", "名称", "每手股数", "上市价", "首日表现", "首日收盘价",
        "一手首日盈亏", "现价", "一手当前盈亏", "当前收益率", "累积表现",
        "超额倍数", "稳中一手", "中签率",
    ]
    with open(output, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows([{field: row.get(field, "") for field in fields} for row in rows])


def summarize(rows):
    current_pnl = [to_float(row.get("一手当前盈亏")) for row in rows]
    first_pnl = [to_float(row.get("一手首日盈亏")) for row in rows]
    current_pnl = [x for x in current_pnl if x is not None]
    first_pnl = [x for x in first_pnl if x is not None]
    return {
        "count": len(rows),
        "first_day_total_hkd": round(sum(first_pnl), 2),
        "hold_to_now_total_hkd": round(sum(current_pnl), 2),
        "hold_to_now_winners": sum(1 for x in current_pnl if x > 0),
        "hold_to_now_losers": sum(1 for x in current_pnl if x < 0),
    }


def main():
    parser = argparse.ArgumentParser(description="Fetch HK IPO YTD performance and calculate one-lot returns.")
    parser.add_argument("--year", type=int, default=datetime.now().year)
    parser.add_argument("--pages", type=int, default=6)
    parser.add_argument("--output", default=str(DEFAULT_OUT / "hk_ipo_ytd.csv"))
    parser.add_argument("--save-html", action="store_true")
    args = parser.parse_args()

    rows, html = fetch_hk_ipo_ytd(args.year, args.pages)
    output = Path(args.output)
    write_csv(rows, output)
    if args.save_html:
        html_path = output.with_suffix(".html")
        html_path.write_text(html, encoding="utf-8")
    print({"output": str(output), **summarize(rows)})


if __name__ == "__main__":
    main()
