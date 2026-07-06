#!/usr/bin/env python3
"""A-share daily market brief — hard data layer for the 大A每日研报 feature.

One command pulls everything the daily report needs (all sources are 东方财富 via akshare):
  1. 大盘资金流 ak.stock_market_fund_flow() — full daily history: 主力/超大单/大单/中单/小单
     net flow + 上证/深证 close & pct. "Today" = the latest row.
  2. 指数快照 ak.stock_zh_index_spot_em("沪深重要指数") — realtime/last-close for
     创业板指/科创50 etc. (the fund-flow table only carries 上证/深证).
  3. 行业资金流 ak.stock_sector_fund_flow_rank(indicator="今日") — per-sector pct + main
     flow, for "谁在被卖/谁在被买" tables.

Also maintains two artifacts under ~/Desktop/持仓/测算归档/市场研究/:
  每日主力资金流_YTD.csv  (regenerated from full history each run)
  每日主力资金流_YTD.svg  (computed bar chart, no charting lib)

Usage:
    python3 a_share_daily_brief.py            # JSON to stdout + write artifacts
    python3 a_share_daily_brief.py --summary  # human-readable digest
    python3 a_share_daily_brief.py --no-write # skip artifacts
Notes:
  - akshare/EastMoney endpoints drop connections intermittently — every fetch retries
    3x; missing pieces degrade to null + a note instead of failing the whole brief.
  - If `python3` can't find akshare (PATH varies by shell), retry with
    /Library/Frameworks/Python.framework/Versions/3.11/bin/python3.
  - HAMMER_YI: |main outflow| > 1200亿 marks a "重锤日" (top-decile outflow in 2026H1).
"""
import argparse
import csv
import json
import math
import os
import sys
import time
from pathlib import Path

OUT_DIR = Path(os.environ.get("PORTFOLIO_DIR", str(Path.home() / "Desktop/持仓"))) / "测算归档/市场研究"
HAMMER_YI = -1200.0
INDEX_WANTED = ["上证指数", "深证成指", "创业板指", "科创50"]


def retry(fn, tries=3, wait=3, label=""):
    last = None
    for i in range(tries):
        try:
            return fn(), None
        except Exception as e:  # noqa: BLE001
            last = e
            if i < tries - 1:
                time.sleep(wait)
    return None, f"{label}获取失败: {type(last).__name__}"


def col_like(df, *subs):
    """Find first column whose name contains all substrings (column names drift across akshare versions)."""
    for c in df.columns:
        if all(s in str(c) for s in subs):
            return c
    return None


def fetch_fund_flow(ak):
    df = ak.stock_market_fund_flow()
    df["日期"] = df["日期"].astype(str)
    year = df["日期"].max()[:4]
    ytd = df[df["日期"] >= f"{year}-01-01"].sort_values("日期").reset_index(drop=True)
    rows = []
    for _, r in ytd.iterrows():
        rows.append({
            "date": r["日期"],
            "main_yi": round(r["主力净流入-净额"] / 1e8, 1),
            "super_yi": round(r["超大单净流入-净额"] / 1e8, 1),
            "big_yi": round(r["大单净流入-净额"] / 1e8, 1),
            "mid_yi": round(r["中单净流入-净额"] / 1e8, 1),
            "small_yi": round(r["小单净流入-净额"] / 1e8, 1),
            "sh_close": round(r["上证-收盘价"], 1),
            "sh_pct": round(r["上证-涨跌幅"], 2),
            "sz_close": round(r["深证-收盘价"], 1),
            "sz_pct": round(r["深证-涨跌幅"], 2),
        })
    return rows


def fetch_indexes(ak):
    df = ak.stock_zh_index_spot_em(symbol="沪深重要指数")
    name_c = col_like(df, "名称")
    price_c = col_like(df, "最新价")
    pct_c = col_like(df, "涨跌幅")
    out = []
    for want in INDEX_WANTED:
        hit = df[df[name_c] == want]
        if len(hit):
            r = hit.iloc[0]
            out.append({"name": want, "close": round(float(r[price_c]), 2), "pct": round(float(r[pct_c]), 2)})
    return out


def fetch_sectors(ak):
    df = ak.stock_sector_fund_flow_rank(indicator="今日", sector_type="行业资金流")
    name_c = col_like(df, "名称")
    pct_c = col_like(df, "涨跌幅")
    flow_c = col_like(df, "主力净流入", "净额")
    recs = []
    for _, r in df.iterrows():
        try:
            recs.append({
                "sector": str(r[name_c]),
                "pct": round(float(r[pct_c]), 2),
                "main_yi": round(float(r[flow_c]) / 1e8, 1),
            })
        except (TypeError, ValueError):
            continue
    by_flow = sorted(recs, key=lambda x: x["main_yi"])
    by_pct = sorted(recs, key=lambda x: x["pct"])
    return {
        "flow_bottom": by_flow[:8],
        "flow_top": by_flow[-5:][::-1],
        "pct_bottom": by_pct[:8],
        "pct_top": by_pct[-5:][::-1],
    }


def build_signals(rows):
    today = rows[-1]
    mains = [r["main_yi"] for r in rows]
    consec = 0
    for r in reversed(rows):
        if r["main_yi"] < 0:
            consec += 1
        else:
            break
    outflows = sorted([m for m in mains if m < 0])
    rank = (outflows.index(today["main_yi"]) + 1) if today["main_yi"] in outflows else None
    mtd = round(sum(r["main_yi"] for r in rows if r["date"][:7] == today["date"][:7]), 1)
    ma5 = round(sum(mains[-5:]) / min(5, len(mains)), 1)
    return {
        "consecutive_outflow_days": consec,
        "today_outflow_rank_ytd": rank,
        "ytd_outflow_days_total": len(outflows),
        "hammer_day": today["main_yi"] < HAMMER_YI,
        "mtd_sum_yi": mtd,
        "ma5_main_yi": ma5,
        "ytd_sum_yi": round(sum(mains), 1),
        "retail_buying_dip": today["main_yi"] < 0 and (today["mid_yi"] + today["small_yi"]) > 0,
    }


def write_csv(rows):
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / "每日主力资金流_YTD.csv"
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["日期", "主力净流入(亿)", "超大单净流入(亿)", "上证收盘", "上证涨跌幅(%)"])
        for r in rows:
            w.writerow([r["date"], r["main_yi"], r["super_yi"], r["sh_close"], r["sh_pct"]])
    return str(path)


def write_svg(rows):
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    vals = [r["main_yi"] for r in rows]
    n = len(rows)
    W, H, L, R, T, B = 920, 470, 72, 24, 46, 54
    pw, ph = W - L - R, H - T - B
    vmax = max(500.0, math.ceil(max(vals) / 500) * 500)
    vmin = min(-1500.0, math.floor(min(vals) / 500) * 500)
    yfor = lambda v: T + (vmax - v) / (vmax - vmin) * ph  # noqa: E731
    zero = yfor(0)
    step = pw / n
    bw = step * 0.78
    col = lambda v: "#e24b4a" if v >= 0 else ("#173404" if v < HAMMER_YI else "#639922")  # noqa: E731
    year = rows[-1]["date"][:4]
    P = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" font-family="-apple-system,Segoe UI,Helvetica,Arial,sans-serif">',
         f'<rect width="{W}" height="{H}" fill="#fff"/>',
         f'<text x="{L}" y="26" font-size="16" font-weight="600" fill="#1a1a1a">{year}年 A股每日主力资金净流入（亿元，东方财富口径）截至{rows[-1]["date"][5:]}</text>']
    gv = vmax
    while gv >= vmin:
        gy = yfor(gv)
        dash = "" if gv == 0 else ' stroke-dasharray="3 3"'
        swd = 1.5 if gv == 0 else 1
        P.append(f'<line x1="{L}" y1="{gy:.1f}" x2="{W-R}" y2="{gy:.1f}" stroke="#c9c9c4" stroke-width="{swd}"{dash}/>')
        P.append(f'<text x="{L-8}" y="{gy+4:.1f}" font-size="11" text-anchor="end" fill="#8a8a84">{gv:,.0f}</text>')
        gv -= 500
    for i, v in enumerate(vals):
        x = L + i * step + (step - bw) / 2
        y = yfor(v) if v >= 0 else zero
        h = abs(yfor(v) - zero)
        P.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bw:.1f}" height="{h:.1f}" fill="{col(v)}" rx="1"/>')
    seen = set()
    for i, r in enumerate(rows):
        mo = r["date"][:7]
        if mo not in seen:
            seen.add(mo)
            P.append(f'<text x="{L+i*step+bw/2:.1f}" y="{H-30}" font-size="11" text-anchor="middle" fill="#8a8a84">{r["date"][5:7]}月</text>')
    lo_i = vals.index(min(vals))
    for di, anchor in [(lo_i, "middle"), (n - 1, "end")]:
        r = rows[di]
        x = L + di * step + bw / 2
        P.append(f'<text x="{x:.1f}" y="{yfor(r["main_yi"])+13:.1f}" font-size="10" text-anchor="{anchor}" fill="#173404">{r["date"][5:].replace("-", "/")} {r["main_yi"]:.0f}</text>')
    lx, ly = W - R - 322, 42
    for dx, c, t in [(0, "#e24b4a", "净流入"), (72, "#639922", "净流出"), (150, "#173404", f"流出&gt;{abs(HAMMER_YI):.0f}亿")]:
        P.append(f'<rect x="{lx+dx}" y="{ly-9}" width="11" height="11" fill="{c}" rx="2"/><text x="{lx+dx+16}" y="{ly}" font-size="11" fill="#555">{t}</text>')
    P.append("</svg>")
    path = OUT_DIR / "每日主力资金流_YTD.svg"
    path.write_text("\n".join(P), encoding="utf-8")
    return str(path)


def print_summary(brief):
    t = brief["fund_flow"]["today"]
    s = brief["signals"]
    print(f"📊 大A每日速览（{t['date']}）")
    for ix in brief["indexes"] or []:
        print(f"  {ix['name']:<6} {ix['close']:>10.2f}  {ix['pct']:+.2f}%")
    if not brief["indexes"]:
        print(f"  上证 {t['sh_close']} ({t['sh_pct']:+.2f}%) | 深证 {t['sz_close']} ({t['sz_pct']:+.2f}%)")
    hammer = " 💥重锤日" if s["hammer_day"] else ""
    print(f"\n主力净流向：{t['main_yi']:+,.0f} 亿（超大单 {t['super_yi']:+,.0f} / 中单 {t['mid_yi']:+,.0f} / 小单 {t['small_yi']:+,.0f}）{hammer}")
    if s["retail_buying_dip"]:
        print("  结构：大资金卖出、中小单逆势接盘")
    if s["today_outflow_rank_ytd"]:
        print(f"  今日流出规模列年内第 {s['today_outflow_rank_ytd']} / {s['ytd_outflow_days_total']} 个流出日")
    print(f"  连续流出 {s['consecutive_outflow_days']} 日 | 近5日均 {s['ma5_main_yi']:+,.0f} 亿 | 本月累计 {s['mtd_sum_yi']:+,.0f} 亿 | 年内累计 {s['ytd_sum_yi']:+,.0f} 亿")
    sec = brief.get("sectors")
    if sec:
        print("\n主力卖最狠的行业：")
        for x in sec["flow_bottom"][:6]:
            print(f"  {x['sector']:<8} {x['main_yi']:>8,.0f} 亿  ({x['pct']:+.2f}%)")
        print("主力买入的行业：")
        for x in sec["flow_top"][:4]:
            print(f"  {x['sector']:<8} {x['main_yi']:>8,.0f} 亿  ({x['pct']:+.2f}%)")
    for note in brief["notes"]:
        print(f"⚠️  {note}")
    for k, v in (brief.get("artifacts") or {}).items():
        print(f"已更新 {k}: {v}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--summary", action="store_true")
    ap.add_argument("--no-write", action="store_true")
    args = ap.parse_args()

    import akshare as ak  # noqa: PLC0415  (import late so --help works without akshare)

    notes = []
    rows, err = retry(lambda: fetch_fund_flow(ak), label="大盘资金流")
    if err or not rows:
        print(json.dumps({"error": err or "大盘资金流为空"}, ensure_ascii=False))
        sys.exit(1)

    indexes, err = retry(lambda: fetch_indexes(ak), label="指数快照")
    if err:
        notes.append(err + "（上证/深证仍可从资金流表取得）")
    sectors, err = retry(lambda: fetch_sectors(ak), label="行业资金流")
    if err:
        notes.append(err)
    notes.append("北向资金自2024-05起不再披露单日净买入，任何具体数字均为第三方估算，不可引用")
    notes.append(f"口径：指数快照与行业资金流为实时/盘中数据；大盘资金流历史最新行为 {rows[-1]['date']}（收盘后更新）。盘中运行时两者相差一个交易日，撰写研报须分别标注日期")

    brief = {
        "date": rows[-1]["date"],
        "indexes": indexes or [],
        "fund_flow": {"today": rows[-1], "recent_10d": rows[-10:]},
        "signals": build_signals(rows),
        "sectors": sectors,
        "notes": notes,
        "artifacts": None,
    }
    if not args.no_write:
        brief["artifacts"] = {"csv": write_csv(rows), "svg": write_svg(rows)}

    if args.summary:
        print_summary(brief)
    else:
        print(json.dumps(brief, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
