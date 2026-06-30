#!/usr/bin/env python3
"""Pull HK IPO 暗盘收盘 (grey-market closing) data for the year's new listings.

WHY NOT Futu OpenD: 暗盘 (grey market) trades the evening BEFORE the listing day on
brokers' internal venues, outside the exchange session. Futu OpenD's get_ipo_list has
NO 暗盘 field, and its history K-line starts only on the listing day (verified: the
暗盘 day returns 0 rows). So 暗盘 closing prices are NOT obtainable from OpenD.

SOURCE: AASTOCKS per-stock news page
  http://www.aastocks.com/sc/stocks/analysis/stock-aafn/{code}/0/all/1
carries that stock's own headline e.g.
  《新股》礼邦医药－Ｂ暗盘收报42.52元 高上市价88.1%
which is reliable historical 暗盘收市价 + 涨跌幅 (vs 发行价/上市价). Stocks with no 暗盘
(不設暗盤 / 供应商不支援) simply have no such headline -> recorded as N/A.

UNIVERSE: the year's listed IPOs come from hk_ipo_ytd.csv (run hk_ipo_ytd.py first to
refresh). Each row gives 代号/名称/上市价(=发行价)/每手股数/上市日期.

USAGE:
    python3 hk_ipo_grey_close.py --summary       # fast single-page scan + 年度盘点表
    python3 hk_ipo_grey_close.py --deep 8 --summary  # also recover headlines buried beyond
                                                 #   news page 1 (high-profile/early-year stocks)
    python3 hk_ipo_grey_close.py --universe /path/to/hk_ipo_ytd.csv  # custom universe
    python3 hk_ipo_grey_close.py --year 2026     # name the stable yearly dump explicitly
    python3 hk_ipo_grey_close.py --no-write      # don't write CSV archive
    python3 hk_ipo_grey_close.py --limit 5       # debug: first N codes only

OUTPUT (under ~/Desktop/持仓/测算归档/港股IPO/):
    hk_ipo_grey_close_<date>.csv   dated snapshot
    hk_ipo_grey_close_<year>.csv   stable per-year dump (overwritten each run, for ongoing analysis)

Read-only web scraping; никогда trades. Non-investment-advice research tool.
"""
import argparse
import csv
import json
import re
import sys
import time
from datetime import date
from pathlib import Path

import requests

ARCHIVE_DIR = Path.home() / "Desktop" / "持仓" / "测算归档" / "港股IPO"
NEWS_URL = "http://www.aastocks.com/sc/stocks/analysis/stock-aafn/{code}/0/all/1"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    )
}
# 《新股》<名称>暗盘收报42.52元 高上市价88.1%   (高=涨, 低=跌, 平=持平)
GREY_PAT = re.compile(r"暗盘收报\s*([\d,.]+)\s*元\s*(高|低|平)?上市价\s*([\d,.]+)?%?")


def norm_code(raw):
    """'09637.HK' / 'HK.09637' / '9637' -> '09637' (5-digit AASTOCKS symbol)."""
    digits = re.sub(r"\D", "", str(raw))
    return digits.zfill(5) if digits else ""


def to_float(v):
    try:
        return float(str(v).replace(",", "").strip())
    except (ValueError, TypeError):
        return None


def find_universe_csv(explicit=None):
    if explicit:
        return Path(explicit).expanduser()
    cands = sorted(ARCHIVE_DIR.glob("hk_ipo_ytd*.csv"), key=lambda p: p.stat().st_mtime, reverse=True)
    return cands[0] if cands else None


def load_universe(path):
    rows = []
    with path.open(encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            code = norm_code(r.get("代号") or r.get("code"))
            if not code:
                continue
            rows.append({
                "上市日期": (r.get("上市日期") or "").strip(),
                "代号": code,
                "名称": (r.get("名称") or "").strip(),
                "发行价": to_float(r.get("上市价") or r.get("招股价")),
                "每手股数": to_float(r.get("每手股数")),
            })
    # de-dupe by code, keep first
    seen, out = set(), []
    for r in rows:
        if r["代号"] in seen:
            continue
        seen.add(r["代号"])
        out.append(r)
    return out


def _parse_page(text):
    m = GREY_PAT.search(text)
    if not m:
        return None
    price = to_float(m.group(1))
    sign = {"高": 1, "低": -1, "平": 0}.get(m.group(2), None)
    pct_raw = to_float(m.group(3))
    pct = sign * pct_raw if (sign is not None and pct_raw is not None) else None
    seg = text[max(0, m.start() - 30): m.end() + 5]
    hl = re.sub(r"\s+", "", re.sub(r"<[^>]+>", "", seg))
    return price, pct, hl


def fetch_grey_close(code, session, retries=2, max_pages=1, page_throttle=0.4):
    """Return (price, pct, raw_headline).

    Scans the stock's AASTOCKS news pages 1..max_pages, returning on the first
    page that carries the 暗盘收报 headline. max_pages=1 (default) is fast and
    catches recent listings; --deep raises it to recover headlines buried beyond
    page 1 for high-profile / early-year stocks. (None, None, None) => no 暗盘 found.
    """
    for pg in range(1, max_pages + 1):
        url = NEWS_URL.format(code=code).rsplit("/", 1)[0] + f"/{pg}"
        got = None
        for attempt in range(retries + 1):
            try:
                r = session.get(url, headers=HEADERS, timeout=25)
                r.raise_for_status()
                got = _parse_page(r.text)
                break
            except requests.RequestException:
                if attempt < retries:
                    time.sleep(1.5)
                    continue
                return None, None, "FETCH_ERROR"
        if got:
            return got
        if max_pages > 1:
            time.sleep(page_throttle)
    return None, None, None


def build(universe, throttle=0.7, limit=None, progress=True, max_pages=1):
    session = requests.Session()
    out = []
    items = universe[:limit] if limit else universe
    for i, r in enumerate(items, 1):
        price, pct, hl = fetch_grey_close(r["代号"], session, max_pages=max_pages)
        lot = r["每手股数"]
        offer = r["发行价"]
        lot_pnl = None
        if price is not None and offer is not None and lot:
            lot_pnl = round((price - offer) * lot, 2)
        out.append({
            **r,
            "暗盘收盘价": price,
            "暗盘涨跌%": pct,
            "暗盘一手盈亏": lot_pnl,
            "数据源": "aastocks" if hl != "FETCH_ERROR" else "error",
            "headline": hl or "",
        })
        if progress:
            if price is not None:
                tag = f"{price:g} (" + (f"{pct:+.1f}%" if pct is not None else "涨跌缺") + ")"
            else:
                tag = "抓取失败" if hl == "FETCH_ERROR" else "无暗盘"
            print(f"  [{i}/{len(items)}] {r['代号']} {r['名称']}: {tag}", file=sys.stderr)
        time.sleep(throttle)
    return out


COLS = ["上市日期", "代号", "名称", "发行价", "每手股数", "暗盘收盘价", "暗盘涨跌%", "暗盘一手盈亏", "数据源", "headline"]


def _write(path, rows):
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=COLS)
        w.writeheader()
        for r in rows:
            w.writerow({k: ("" if r.get(k) is None else r.get(k)) for k in COLS})


def write_csv(rows, today, year=None):
    """Write a dated snapshot AND a stable per-year dump for ongoing analysis."""
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    snapshot = ARCHIVE_DIR / f"hk_ipo_grey_close_{today.isoformat()}.csv"
    _write(snapshot, rows)
    stable = None
    if year:
        stable = ARCHIVE_DIR / f"hk_ipo_grey_close_{year}.csv"
        _write(stable, rows)
    return snapshot, stable


def summarize(rows):
    have = [r for r in rows if r["暗盘收盘价"] is not None]
    none = [r for r in rows if r["暗盘收盘价"] is None]
    print(f"\n港股IPO暗盘收盘汇总：共 {len(rows)} 只，有暗盘 {len(have)} 只，无暗盘/缺失 {len(none)} 只\n")
    hdr = f"{'上市日':<11}{'代号':<9}{'名称':<18}{'发行价':>8}{'暗盘收':>9}{'涨跌%':>9}{'一手盈亏':>10}"
    print(hdr)
    print("-" * len(hdr))
    for r in sorted(have, key=lambda x: (x["暗盘涨跌%"] is None, -(x["暗盘涨跌%"] or 0))):
        pnl = r["暗盘一手盈亏"]
        print(f"{r['上市日期']:<11}{r['代号']:<9}{r['名称'][:16]:<18}"
              f"{(r['发行价'] or 0):>8g}{r['暗盘收盘价']:>9g}"
              f"{(r['暗盘涨跌%'] if r['暗盘涨跌%'] is not None else 0):>+8.1f}%"
              f"{(pnl if pnl is not None else 0):>+10.0f}")
    pnls = [r["暗盘一手盈亏"] for r in have if r["暗盘一手盈亏"] is not None]
    pcts = [r["暗盘涨跌%"] for r in have if r["暗盘涨跌%"] is not None]
    if pnls:
        wins = sum(1 for p in pcts if p > 0)
        print("\n— 若每只都中1手、暗盘收盘价卖出 —")
        print(f"  合计一手盈亏：{sum(pnls):+,.0f} 港币（仅含 {len(pnls)} 只有完整数据者）")
        print(f"  暗盘上涨比例：{wins}/{len(pcts)} = {wins/len(pcts)*100:.0f}%")
        print(f"  暗盘涨跌中位数：{sorted(pcts)[len(pcts)//2]:+.1f}%  平均：{sum(pcts)/len(pcts):+.1f}%")
    if none:
        print(f"\n无暗盘/数据缺失（{len(none)}）：" + "、".join(f"{r['代号']}{r['名称']}" for r in none[:30]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--universe", help="path to hk_ipo_ytd*.csv (default: newest in archive)")
    ap.add_argument("--summary", action="store_true")
    ap.add_argument("--no-write", action="store_true")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--throttle", type=float, default=0.7)
    ap.add_argument("--deep", type=int, default=1, metavar="PAGES",
                    help="scan up to N news pages per stock to recover 暗盘 headlines buried "
                         "beyond page 1 (slower; e.g. --deep 8). Default 1 = fast single page.")
    ap.add_argument("--year", help="stable yearly dump filename suffix (default: infer from universe)")
    args = ap.parse_args()

    uni_csv = find_universe_csv(args.universe)
    if not uni_csv or not uni_csv.exists():
        print("找不到 hk_ipo_ytd*.csv 全集，请先运行 hk_ipo_ytd.py --year 2026", file=sys.stderr)
        sys.exit(1)
    universe = load_universe(uni_csv)
    print(f"全集来源：{uni_csv.name}（{len(universe)} 只）｜翻页深度 --deep={args.deep}", file=sys.stderr)

    rows = build(universe, throttle=args.throttle, limit=args.limit, max_pages=max(1, args.deep))

    # infer year for the stable dump from the universe's listing dates
    year = args.year
    if not year:
        yrs = [r["上市日期"][:4] for r in universe if r.get("上市日期")]
        year = max(set(yrs), key=yrs.count) if yrs else str(date.today().year)

    today = date.today()
    if not args.no_write:
        snapshot, stable = write_csv(rows, today, year=year)
        print(f"\n已归档快照：{snapshot}", file=sys.stderr)
        if stable:
            print(f"已更新年度数据：{stable}", file=sys.stderr)

    if args.summary:
        summarize(rows)
    else:
        print(json.dumps(rows, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
