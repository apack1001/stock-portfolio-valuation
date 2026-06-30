#!/usr/bin/env python3
"""Scaffold a HK-IPO 牛逼度评估 (market-cap + ★ quality rating) sheet.

WHAT THIS DOES (and what it deliberately does NOT):
The 牛逼度评估 is part data, part research judgement. This script handles ONLY the
deterministic, data-derived columns by seeding them from the day's subscribing list
(代号/名称/赛道/招股价/每手/一手入场费/上市日/暗盘日). The qualitative columns
(预期上市后市值, 市值口径, 牛逼度★, 是否盈利, 基石, 保荐人, 打新倾向) are LEFT BLANK
for the assistant to fill via web research following SKILL.md Step 3.6's framework —
a script cannot estimate market cap or assign ★ ratings on its own.

So the workflow is:
  1. python3 hk_ipo_subscribing.py            # refresh the in-subscription universe
  2. python3 hk_ipo_niubility.py              # seed a scaffold from that universe
  3. assistant fills the blank research columns (预期市值 / ★ / 基石 / 倾向) per the
     framework, then saves over the scaffold as hk_ipo_niubility_<date>.csv

牛逼度 framework (mirror of SKILL.md Step 3.6), for the assistant filling the sheet:
  ★★★★★ 超大盘巨头（千亿级 A+H 真盈利龙头 + 顶级基石）
  ★★★★  行业龙头大盘
  ★★★   细分龙头中小盘（~百亿）
  ★★    题材/概念中小盘（未盈利/盘小波动大）
  ★     小盘/冷门/GEM（回避倾向，GEM 尤慎）
市值口径：A+H = A股总股本 × 港币招股价上限（整体口径，提示 H 股折价）；纯 H 股 =
基石股数 ÷ 基石占发行后股本比例 反推（标“推测”）；切勿把募资额当市值。

USAGE:
    python3 hk_ipo_niubility.py                       # newest subscribing csv -> scaffold
    python3 hk_ipo_niubility.py --subscribing PATH    # use a specific subscribing csv
    python3 hk_ipo_niubility.py --summary             # also print which fields need research
    python3 hk_ipo_niubility.py --stdout              # print CSV to stdout, don't write file
"""
import argparse
import csv
import sys
from datetime import date
from pathlib import Path

ARCHIVE_DIR = Path.home() / "Desktop" / "持仓" / "测算归档" / "港股IPO"

# columns the assistant must fill via web research (the rest are seeded from the list)
RESEARCH_COLS = ["预期上市后市值", "市值口径", "牛逼度", "是否盈利", "基石", "保荐人", "打新倾向"]
# stable, readable column order for the scaffold
ALL_COLS = [
    "代号", "名称", "赛道", "招股价", "每手股数", "一手入场费HKD",
    "预期上市后市值", "市值口径", "牛逼度", "是否盈利", "基石", "保荐人",
    "上市日", "暗盘日", "打新倾向",
]


def find_subscribing_csv(explicit=None):
    if explicit:
        return Path(explicit).expanduser()
    cands = sorted(ARCHIVE_DIR.glob("hk_ipo_subscribing_*.csv"),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    return cands[0] if cands else None


def load_subscribing(path):
    rows = []
    with path.open(encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            code = (r.get("代号") or "").strip()
            if not code:
                continue
            # only stocks still subscribable (defensive: keep 招股中 / non-已截止)
            status = (r.get("status") or "").strip()
            if status and status in ("已截止", "已上市"):
                continue
            rows.append({
                "代号": code,
                "名称": (r.get("名称") or "").strip(),
                "赛道": (r.get("行业") or "").strip(),
                "招股价": (r.get("招股价") or "").strip(),
                "每手股数": (r.get("每手股数") or "").strip(),
                "一手入场费HKD": (r.get("入场费") or "").strip(),
                "上市日": (r.get("上市日期") or "").strip(),
                "暗盘日": (r.get("暗盘日期") or "").strip(),
                # research columns blank
                "预期上市后市值": "",
                "市值口径": "",
                "牛逼度": "",
                "是否盈利": "",
                "基石": "",
                "保荐人": "",
                "打新倾向": "",
            })
    return rows


def write_csv(rows, today):
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    out = ARCHIVE_DIR / f"hk_ipo_niubility_scaffold_{today.isoformat()}.csv"
    with out.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=ALL_COLS)
        w.writeheader()
        w.writerows(rows)
        # trailing guidance comment row (CSV-safe single cell)
        f.write("# 脚手架：上面8个结构列已从招股清单 seed；预期市值/市值口径/牛逼度/是否盈利/基石/保荐人/打新倾向 7列需联网研究填写（见 SKILL.md Step 3.6 牛逼度框架）。填完另存为 hk_ipo_niubility_<date>.csv。非投资建议。\n")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--subscribing", help="path to hk_ipo_subscribing_*.csv (default: newest)")
    ap.add_argument("--summary", action="store_true")
    ap.add_argument("--stdout", action="store_true")
    args = ap.parse_args()

    sub_csv = find_subscribing_csv(args.subscribing)
    if not sub_csv or not sub_csv.exists():
        print("找不到 hk_ipo_subscribing_*.csv，请先运行 hk_ipo_subscribing.py", file=sys.stderr)
        sys.exit(1)
    rows = load_subscribing(sub_csv)
    print(f"招股清单来源：{sub_csv.name}（{len(rows)} 只在招）", file=sys.stderr)

    if args.stdout:
        w = csv.DictWriter(sys.stdout, fieldnames=ALL_COLS)
        w.writeheader()
        w.writerows(rows)
    else:
        out = write_csv(rows, date.today())
        print(f"已生成脚手架：{out}", file=sys.stderr)

    if args.summary:
        print(f"\n需联网研究补全的 7 列：{', '.join(RESEARCH_COLS)}", file=sys.stderr)
        print("待评估标的：", file=sys.stderr)
        for r in rows:
            print(f"  {r['代号']:<12}{r['名称']:<18}赛道={r['赛道'] or '?':<14}入场费={r['一手入场费HKD'] or '?'}", file=sys.stderr)


if __name__ == "__main__":
    main()
