#!/usr/bin/env python3
"""
今年至今（YTD）资产走势图生成器。

从 ~/Desktop/持仓/总额.csv 读取每日总资产历史，过滤当年数据，
输出一段可直接嵌入 Markdown 报告的 mermaid 折线图 + YTD 摘要。

用法:
  python3 asset_trend.py                 # 输出当年 CNY 走势图（mermaid）
  python3 asset_trend.py --currency usd  # 按美元口径
  python3 asset_trend.py --year 2026     # 指定年份
  python3 asset_trend.py --max-points 40 # mermaid x 轴过密时的抽稀上限
  python3 asset_trend.py --png           # 额外渲染真实 PNG 到 持仓目录/asset_trend_<year>.png
  python3 asset_trend.py --png /path.png # 指定 PNG 输出路径

设计要点:
- 数据源与 fetch_prices.py 共用 ~/Desktop/持仓/总额.csv（同一 TCC 授权路径，
  作为 skill 目录下的真实脚本文件运行才有 Desktop 读权限）。
- 点数超过 max_points 时等距抽稀（首尾必留），避免 mermaid x 轴标签重叠。
- PNG 用全量点绘制：红实线=总资产(含 LTI，左轴)，灰虚线=投资盘累计盈亏(不含 LTI，右轴)，
  两轴背离一眼看出"总资产涨≠投资赚钱"。matplotlib 缺失时仅告警、不影响 mermaid 输出。
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


def load_ytd_pnl(year, currency):
    """读取当年 (date, pnl_excl_lti) 升序列表，用于 PNG 右轴。缺列返回空。"""
    pnl_col = "pnl_usd_excl_lti" if currency == "usd" else "pnl_cny_excl_lti"
    if not TOTAL_PATH.exists():
        return {}
    with open(TOTAL_PATH, "r", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    prefix = f"{year}-"
    out = {}
    for r in rows:
        d = (r.get("date") or "").strip()
        v = to_float(r.get(pnl_col))
        if d.startswith(prefix) and v is not None:
            out[d] = v  # 同日保留最后一条
    return out


def _cjk_font():
    """在 macOS 上挑一个中日韩可用字体并注册，返回其字体名（挑不到返回 None）。"""
    from matplotlib import font_manager as fm
    candidates = [
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
        "/System/Library/Fonts/STHeiti Medium.ttc",
        "/System/Library/Fonts/STHeiti Light.ttc",
        "/System/Library/Fonts/PingFang.ttc",
    ]
    for fp in candidates:
        if os.path.exists(fp):
            try:
                fm.fontManager.addfont(fp)
                return fm.FontProperties(fname=fp).get_name()
            except Exception:
                continue
    return None


def render_png(points, year, currency, out_path):
    """用全量点渲染 PNG：总资产(左轴) + 投资盘累计盈亏(右轴)。返回实际写入路径或 None。"""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as e:  # noqa: BLE001
        print(f"⚠️ 未安装 matplotlib，PNG 跳过（mermaid 已正常输出）：{e}")
        return None

    fname = _cjk_font()
    if fname:
        plt.rcParams["font.family"] = fname
    else:
        print("⚠️ 未找到中日韩字体，PNG 中文可能显示为方块。")
    plt.rcParams["axes.unicode_minus"] = False

    unit = "美元" if currency == "usd" else "人民币"
    symbol = "US$" if currency == "usd" else "¥"
    dates = [d for d, _ in points]
    tc = [v / 10000 for _, v in points]
    pnl_map = load_ytd_pnl(year, currency)
    pnl = [pnl_map.get(d) for d in dates]
    x = list(range(len(tc)))

    fig, ax = plt.subplots(figsize=(13, 6))
    ax.plot(x, tc, color="#c0392b", lw=2, label="总资产(含LTI)")
    ax.fill_between(x, tc, min(tc) - 3, color="#c0392b", alpha=0.06)

    pi = tc.index(max(tc))
    ti = tc.index(min(tc))
    ax.scatter([pi, ti], [tc[pi], tc[ti]], color=["#27ae60", "#e67e22"], zorder=5, s=45)
    ax.annotate(f"峰值 {tc[pi]:.1f}万\n{dates[pi]}", (pi, tc[pi]),
                textcoords="offset points", xytext=(0, 12), ha="center", fontsize=9, color="#27ae60")
    ax.annotate(f"谷值 {tc[ti]:.1f}万\n{dates[ti]}", (ti, tc[ti]),
                textcoords="offset points", xytext=(0, -28), ha="center", fontsize=9, color="#e67e22")
    ax.scatter([x[-1]], [tc[-1]], color="#c0392b", zorder=5, s=45)
    ax.annotate(f"当前 {tc[-1]:.1f}万", (x[-1], tc[-1]),
                textcoords="offset points", xytext=(-8, 10), ha="right", fontsize=9, color="#c0392b")

    handles, labels = ax.get_legend_handles_labels()
    if any(p is not None for p in pnl):
        ax2 = ax.twinx()
        px = [i for i, p in zip(x, pnl) if p is not None]
        py = [p / 10000 for p in pnl if p is not None]
        ax2.plot(px, py, color="#7f8c8d", lw=1.3, ls="--", label="投资盘累计盈亏(不含LTI)")
        ax2.set_ylabel("投资盘累计盈亏（万%s）" % symbol, color="#7f8c8d", fontsize=10)
        ax2.tick_params(axis="y", colors="#7f8c8d")
        h2, l2 = ax2.get_legend_handles_labels()
        handles += h2
        labels += l2

    step = max(1, len(dates) // 20)
    ax.set_xticks(x[::step])
    ax.set_xticklabels([dates[i][5:] for i in x[::step]], rotation=45, fontsize=8)
    ax.set_ylabel("总资产（万%s）" % symbol, fontsize=10)
    ax.set_title(f"{year} YTD 总资产走势（{unit}）  ·  截至 {dates[-1][5:]}", fontsize=13)
    ax.grid(alpha=0.25)
    ax.legend(handles, labels, loc="lower right", fontsize=9)
    fig.tight_layout()
    try:
        fig.savefig(out_path, dpi=130)
    except Exception as e:  # noqa: BLE001
        print(f"⚠️ PNG 写入失败：{e}")
        return None
    finally:
        plt.close(fig)
    return out_path


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
    if args.png is not None:
        out_path = args.png if args.png != "__default__" else str(BASE_DIR / f"asset_trend_{year}.png")
        saved = render_png(points, year, args.currency, out_path)
        if saved:
            print()
            print(f"🖼️ PNG 已保存：{saved}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="今年至今资产走势图生成器")
    parser.add_argument("--currency", choices=["cny", "usd"], default="cny",
                        help="估值口径，默认人民币")
    parser.add_argument("--year", default=None, help="指定年份，默认当年")
    parser.add_argument("--max-points", type=int, default=40,
                        help="mermaid x 轴最多显示的点数，超出等距抽稀")
    parser.add_argument("--png", nargs="?", const="__default__", default=None,
                        help="额外渲染真实 PNG；可跟输出路径，缺省存到 持仓目录/asset_trend_<year>.png")
    args = parser.parse_args()
    main()
