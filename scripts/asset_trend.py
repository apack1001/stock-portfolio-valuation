#!/usr/bin/env python3
"""
今年至今（YTD）资产走势图生成器。

从 ~/Desktop/持仓/总额.csv 读取每日总资产历史，过滤当年数据，
输出一段可直接嵌入 Markdown 报告的 mermaid 折线图 + YTD 摘要。

用法:
  python3 asset_trend.py                 # 输出当年 CNY 走势图
  python3 asset_trend.py --currency usd  # 按美元口径
  python3 asset_trend.py --year 2026     # 指定年份
  python3 asset_trend.py --max-points 40 # mermaid x 轴过密时的抽稀上限

设计要点:
- 数据源与 fetch_prices.py 共用 ~/Desktop/持仓/总额.csv（同一 TCC 授权路径，
  作为 skill 目录下的真实脚本文件运行才有 Desktop 读权限）。
- 点数超过 max_points 时等距抽稀（首尾必留），避免 mermaid x 轴标签重叠。
"""
import os
import csv
import argparse
from pathlib import Path
from datetime import datetime

BASE_DIR = Path(os.environ.get("PORTFOLIO_DIR", str(Path.home() / "Desktop/持仓")))
TOTAL_PATH = BASE_DIR / "总额.csv"


def to_float(value):
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def load_ytd(year, currency):
    """读取总额.csv，返回当年按日期升序的 [(date, value_in_wan, raw_value)] 列表。"""
    col = "total_usd" if currency == "usd" else "total_cny"
    if not TOTAL_PATH.exists():
        return [], col
    with open(TOTAL_PATH, "r", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    prefix = f"{year}-"
    points = []
    for r in rows:
        d = (r.get("date") or "").strip()
        v = to_float(r.get(col))
        if d.startswith(prefix) and v is not None:
            points.append((d, v))
    points.sort(key=lambda x: x[0])
    # 同日去重，保留最后一条
    dedup = {}
    for d, v in points:
        dedup[d] = v
    return [(d, dedup[d]) for d in sorted(dedup)], col


def thin(points, max_points):
    """等距抽稀，首尾必留。"""
    if len(points) <= max_points:
        return points
    step = (len(points) - 1) / (max_points - 1)
    idx = sorted({round(i * step) for i in range(max_points)} | {0, len(points) - 1})
    return [points[i] for i in idx]


def fmt_wan(v):
    return f"{v / 10000:.1f}"


def build_chart(points, currency):
    unit = "美元" if currency == "usd" else "人民币"
    symbol = "US$" if currency == "usd" else "¥"
    shown = thin(points, args.max_points)
    xs = [d[5:] for d, _ in shown]  # MM-DD
    ys = [round(v / 10000, 1) for _, v in shown]  # 单位：万
    ymin = min(ys)
    ymax = max(ys)
    pad = max((ymax - ymin) * 0.08, 0.5)
    lo = max(0, round(ymin - pad))
    hi = round(ymax + pad)

    lines = []
    lines.append("```mermaid")
    lines.append("xychart-beta")
    lines.append(f'    title "今年至今总资产走势（{unit}，单位：万）"')
    lines.append('    x-axis [' + ", ".join(f'"{x}"' for x in xs) + "]")
    lines.append(f'    y-axis "总资产（万{symbol}）" {lo} --> {hi}')
    lines.append("    line [" + ", ".join(str(y) for y in ys) + "]")
    lines.append("```")
    return "\n".join(lines)


def build_summary(points, currency):
    symbol = "US$" if currency == "usd" else "¥"
    first_d, first_v = points[0]
    last_d, last_v = points[-1]
    delta = last_v - first_v
    pct = (delta / first_v * 100) if first_v else 0.0
    hi_d, hi_v = max(points, key=lambda x: x[1])
    lo_d, lo_v = min(points, key=lambda x: x[1])
    arrow = "▲" if delta >= 0 else "▼"
    sign = "+" if delta >= 0 else "-"
    return (
        f"**YTD 摘要（{first_d} → {last_d}，共 {len(points)} 个数据点）：** "
        f"年初 {symbol}{first_v/10000:.1f}万 → 现值 {symbol}{last_v/10000:.1f}万，"
        f"{arrow} {sign}{symbol}{abs(delta)/10000:.1f}万（{sign}{abs(pct):.1f}%）"
        f" ｜ 年内峰值 {symbol}{hi_v/10000:.1f}万（{hi_d}），谷值 {symbol}{lo_v/10000:.1f}万（{lo_d}）"
    )


def main():
    year = args.year or datetime.now().strftime("%Y")
    points, col = load_ytd(year, args.currency)
    if not points:
        print(f"⚠️ {year} 年暂无资产历史数据（{TOTAL_PATH} 中无当年记录），走势图跳过。")
        return
    if len(points) == 1:
        d, v = points[0]
        symbol = "US$" if args.currency == "usd" else "¥"
        print(f"📈 今年至今仅 1 个数据点（{d}：{symbol}{v/10000:.1f}万），"
              f"累计 2 天以上后自动生成走势曲线。")
        return
    print(build_chart(points, args.currency))
    print()
    print(build_summary(points, args.currency))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="今年至今资产走势图生成器")
    parser.add_argument("--currency", choices=["cny", "usd"], default="cny",
                        help="估值口径，默认人民币")
    parser.add_argument("--year", default=None, help="指定年份，默认当年")
    parser.add_argument("--max-points", type=int, default=40,
                        help="mermaid x 轴最多显示的点数，超出等距抽稀")
    args = parser.parse_args()
    main()
