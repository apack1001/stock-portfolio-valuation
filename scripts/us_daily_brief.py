#!/usr/bin/env python3
"""US market daily brief — hard data layer for the 美股每日研报 feature.

Deliberately NOT a copy of a_share_daily_brief.py: the US market has no
"主力资金流" (东财的超大单/大单分类是 A 股逐笔委托口径，美股不存在). What replaces it:

  1. 板块轮动 — 11 只 SPDR 行业 ETF 的涨跌，代替"行业资金流排行"。
     真实成交价，不是估算流向。
  2. 风格/宽度 — QQQ(成长) vs SPY(大盘) vs IWM(小盘) vs DIA(价值)，看钱在哪一头。
  3. 风险偏好 — VIXY(波动) / TLT(长债) / GLD(避险) / HYG(高收益债) / UUP(美元)。
  4. 中概专区 — KWEB/FXI/YINN/CWEB + BEKE/LI/PDD，用户 26% 净资产所在。
  5. 夜盘 — pre_price/after_price，A 股没有的东西。

数据源：
  - Futu OpenD (127.0.0.1:11111) 只读行情，拉快照 + 少量历史K线算量比。绝不下单。
  - akshare index_us_stock_sina 补三大指数历史（Futu 快照不含指数）。

沉淀 artifact 到 ~/Desktop/持仓/测算归档/市场研究/:
  美股每日盘面_YTD.csv   (三大指数 YTD 收盘序列，每次重生成)

Usage:
    python3 us_daily_brief.py             # JSON to stdout + write artifacts
    python3 us_daily_brief.py --summary   # human-readable digest
    python3 us_daily_brief.py --no-write  # skip artifacts

Notes:
  - **Futu 批量快照有个坑**：只要有一个代码不可用，整批 get_market_snapshot 全部失败
    （US.DIDIY 是 OTC 粉单，Futu 不提供，就会炸掉整批）。所以这里分组拉取，
    单组失败自动降级为逐个拉，坏代码单独记进 unavailable，不影响其余。
  - akshare/新浪接口会间歇性断连，每次拉取重试 3 次；拿不到就降级为 null + note，
    绝不用估算值填充。
  - 必须用 /Library/Frameworks/Python.framework/Versions/3.11/bin/python3
    （系统 python3 没装 futu-api / akshare）。
"""
import argparse
import csv
import json
import os
import socket
import sys
import time
from datetime import datetime
from pathlib import Path

OUT_DIR = Path(os.environ.get("PORTFOLIO_DIR", str(Path.home() / "Desktop/持仓"))) / "测算归档/市场研究"

# 11 只 SPDR 行业 ETF —— 代替 A 股的"行业资金流排行"
SECTORS = {
    "US.XLK": "科技", "US.XLF": "金融", "US.XLE": "能源", "US.XLV": "医疗",
    "US.XLY": "可选消费", "US.XLP": "必需消费", "US.XLI": "工业", "US.XLB": "原材料",
    "US.XLU": "公用事业", "US.XLRE": "房地产", "US.XLC": "通信服务",
}
# 风格 / 宽度
STYLES = {"US.SPY": "标普500", "US.QQQ": "纳指100", "US.IWM": "罗素2000(小盘)", "US.DIA": "道指"}
# 风险偏好温度计
RISK = {"US.VIXY": "波动率(VIXY)", "US.TLT": "20年美债", "US.GLD": "黄金", "US.HYG": "高收益债", "US.UUP": "美元指数"}
# 中概专区 —— 用户核心敞口
CHINA = {"US.KWEB": "中概互联", "US.FXI": "中国大盘", "US.YINN": "3x做多富时中国", "US.CWEB": "2x做多中概互联"}
# 用户实际持仓（US.DIDIY 是 OTC，Futu 不支持，故意不列——列了会炸整批）
HOLDINGS = {"US.BEKE": "贝壳", "US.LI": "理想", "US.PDD": "拼多多", "US.SOXX": "半导体ETF"}
# 只对这几个算 20 日量比（Futu 历史K线有额度，少拉几个）
VOL_RATIO_CODES = ["US.SPY", "US.QQQ", "US.KWEB", "US.BEKE", "US.LI"]

INDEX_SYMBOLS = {".INX": "标普500", ".IXIC": "纳斯达克", ".DJI": "道琼斯"}


def opend_alive(host, port, timeout=3.0):
    """Pre-flight the OpenD port.

    OpenQuoteContext() does NOT raise when OpenD is down — it retries forever on a
    background thread and every call blocks, so an unguarded run hangs a scheduled
    task indefinitely. Fail fast instead.
    """
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


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


def snapshot(ctx, codes):
    """Fetch snapshots, isolating bad codes.

    Futu fails the WHOLE batch if any single code is unavailable (US.DIDIY the classic
    case), so on batch failure fall back to one-at-a-time and report the bad ones.
    """
    from futu import RET_OK

    ret, df = ctx.get_market_snapshot(codes)
    if ret == RET_OK:
        return df, []
    ok_rows, bad = [], []
    for c in codes:
        r, d = ctx.get_market_snapshot([c])
        if r == RET_OK and len(d):
            ok_rows.append(d)
        else:
            bad.append(c)
    if not ok_rows:
        return None, bad
    import pandas as pd

    return pd.concat(ok_rows, ignore_index=True), bad


def row_to_quote(r, label):
    prev = float(r["prev_close_price"]) or 0.0
    last = float(r["last_price"])
    q = {
        "code": str(r["code"]).replace("US.", ""),
        "name": label,
        "last": round(last, 2),
        "prev_close": round(prev, 2),
        "pct": round((last / prev - 1) * 100, 2) if prev else None,
        "volume": int(r["volume"]) if r.get("volume") is not None else None,
        "update_time": str(r.get("update_time", "")),
    }
    for k, src in (("pre", "pre_price"), ("after", "after_price")):
        p = r.get(f"{src}")
        rate = r.get(f"{k}_change_rate")
        if p is not None and float(p) > 0:
            q[f"{k}_price"] = round(float(p), 2)
            q[f"{k}_pct"] = round(float(rate), 2) if rate is not None else None
    return q


def fetch_group(ctx, mapping, notes, label):
    df, bad = snapshot(ctx, list(mapping))
    if bad:
        notes.append(f"{label}中以下代码 Futu 无行情，已跳过: {', '.join(c.replace('US.', '') for c in bad)}")
    if df is None:
        notes.append(f"{label}全部获取失败")
        return []
    return [row_to_quote(r, mapping[str(r["code"])]) for _, r in df.iterrows()]


def fetch_vol_ratio(ctx, notes):
    """20-day average volume vs today — the closest honest analog to '资金异动'."""
    from futu import RET_OK, AuType, KLType

    out = {}
    for code in VOL_RATIO_CODES:
        ret, df, _ = ctx.request_history_kline(
            code, ktype=KLType.K_DAY, autype=AuType.QFQ, max_count=30
        )
        if ret != RET_OK or df is None or len(df) < 21:
            notes.append(f"{code.replace('US.', '')} 历史K线不足，量比未计算")
            continue
        hist = df.iloc[:-1].tail(20)  # exclude today's (possibly partial) bar
        avg = float(hist["volume"].mean())
        today = float(df.iloc[-1]["volume"])
        out[code.replace("US.", "")] = {
            "today_volume": int(today),
            "avg20_volume": int(avg),
            "ratio": round(today / avg, 2) if avg else None,
        }
        time.sleep(0.4)  # Futu history-kline rate limit
    return out


def fetch_index_history(notes):
    import akshare as ak

    series = {}
    for sym, name in INDEX_SYMBOLS.items():
        df, err = retry(lambda s=sym: ak.index_us_stock_sina(symbol=s), label=f"{name}指数")
        if err or df is None or not len(df):
            notes.append(err or f"{name}指数为空")
            continue
        df = df.copy()
        df["date"] = df["date"].astype(str)
        year = df["date"].max()[:4]
        ytd = df[df["date"] >= f"{year}-01-01"].sort_values("date")
        series[name] = [
            {"date": r["date"], "close": round(float(r["close"]), 2)} for _, r in ytd.iterrows()
        ]
    return series


def build_signals(styles, risk, china, sectors, holdings):
    by = lambda lst: {q["name"]: q for q in lst}  # noqa: E731
    S, R, C = by(styles), by(risk), by(china)
    g = lambda d, k: (d.get(k) or {}).get("pct")  # noqa: E731

    spy, qqq, iwm = g(S, "标普500"), g(S, "纳指100"), g(S, "罗素2000(小盘)")
    kweb, fxi = g(C, "中概互联"), g(C, "中国大盘")
    vixy, tlt, gld = g(R, "波动率(VIXY)"), g(R, "20年美债"), g(R, "黄金")

    pcts = [s["pct"] for s in sectors if s["pct"] is not None]
    sig = {
        "growth_vs_broad": round(qqq - spy, 2) if None not in (qqq, spy) else None,
        "smallcap_vs_broad": round(iwm - spy, 2) if None not in (iwm, spy) else None,
        "china_vs_us": round(kweb - spy, 2) if None not in (kweb, spy) else None,
        "china_internet_vs_largecap": round(kweb - fxi, 2) if None not in (kweb, fxi) else None,
        "sector_dispersion": round(max(pcts) - min(pcts), 2) if pcts else None,
        "sectors_up": sum(1 for p in pcts if p > 0),
        "sectors_total": len(pcts),
        "vixy_pct": vixy,
        "tlt_pct": tlt,
        "gld_pct": gld,
    }
    # risk-on 需要三个条件同时成立，任一缺失就判 None（不猜）
    if None not in (qqq, spy, vixy):
        sig["risk_on"] = qqq > spy and vixy < 0
    else:
        sig["risk_on"] = None
    # 避险共振：股跌 + 债涨 + 金涨
    if None not in (spy, tlt, gld):
        sig["flight_to_safety"] = spy < 0 and tlt > 0 and gld > 0
    else:
        sig["flight_to_safety"] = None
    sig["holdings_up"] = sum(1 for h in holdings if (h["pct"] or 0) > 0)
    sig["holdings_total"] = len(holdings)
    return sig


def market_phase(quotes):
    """Label which session the prices belong to — critical for not over-reading the data."""
    ts = [q["update_time"] for q in quotes if q.get("update_time")]
    has_pre = any("pre_price" in q for q in quotes)
    has_after = any("after_price" in q for q in quotes)
    if has_after:
        phase = "盘后(夜盘)"
    elif has_pre:
        phase = "盘前"
    else:
        phase = "盘中或已收盘"
    return {"phase": phase, "quote_time": max(ts) if ts else None,
            "local_time": datetime.now().strftime("%Y-%m-%d %H:%M")}


def write_csv(series):
    if not series:
        return None
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    dates = sorted({p["date"] for s in series.values() for p in s})
    idx = {name: {p["date"]: p["close"] for p in pts} for name, pts in series.items()}
    path = OUT_DIR / "美股每日盘面_YTD.csv"
    names = list(series)
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["日期"] + names)
        for d in dates:
            w.writerow([d] + [idx[n].get(d, "") for n in names])
    return str(path)


def print_summary(b):
    ph = b["session"]
    print(f"📊 美股每日速览（报价时点 {ph['quote_time']} · {ph['phase']} · 本机 {ph['local_time']}）\n")
    print("指数/风格：")
    for q in b["styles"]:
        ah = f"  夜盘 {q['after_pct']:+.2f}%" if "after_pct" in q else ""
        print(f"  {q['name']:<14} {q['last']:>9.2f}  {q['pct']:+6.2f}%{ah}")
    s = b["signals"]
    print(f"\n成长vs大盘 {s['growth_vs_broad']:+}pt | 小盘vs大盘 {s['smallcap_vs_broad']:+}pt "
          f"| 中概vs美股 {s['china_vs_us']:+}pt")
    print(f"板块 {s['sectors_up']}/{s['sectors_total']} 上涨，离散度 {s['sector_dispersion']}pt "
          f"| risk_on={s['risk_on']} | 避险共振={s['flight_to_safety']}")
    print("\n板块涨幅榜：")
    for x in b["sectors"][:4]:
        print(f"  {x['name']:<8} {x['pct']:+6.2f}%")
    print("板块跌幅榜：")
    for x in b["sectors"][-4:]:
        print(f"  {x['name']:<8} {x['pct']:+6.2f}%")
    print("\n中概：")
    for q in b["china"]:
        print(f"  {q['name']:<16} {q['last']:>8.2f}  {q['pct']:+6.2f}%")
    print("\n持仓：")
    for q in b["holdings"]:
        ah = f"  夜盘 {q['after_pct']:+.2f}%" if "after_pct" in q else ""
        print(f"  {q['name']:<8} {q['last']:>8.2f}  {q['pct']:+6.2f}%{ah}")
    for n in b["notes"]:
        print(f"⚠️  {n}")
    for k, v in (b.get("artifacts") or {}).items():
        print(f"已更新 {k}: {v}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--summary", action="store_true")
    ap.add_argument("--no-write", action="store_true")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=11111)
    args = ap.parse_args()

    try:
        from futu import OpenQuoteContext
    except ImportError:
        print(json.dumps({"error": "futu-api 未安装；请用 /Library/Frameworks/Python.framework/"
                                   "Versions/3.11/bin/python3 运行"}, ensure_ascii=False))
        sys.exit(1)

    notes = []
    if not opend_alive(args.host, args.port):
        print(json.dumps({"error": f"Futu OpenD 未监听 {args.host}:{args.port}；请先启动 OpenD 再运行"},
                         ensure_ascii=False))
        sys.exit(1)
    try:
        ctx = OpenQuoteContext(host=args.host, port=args.port)
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"error": f"连接 Futu OpenD 失败({type(e).__name__})；请确认 OpenD 已启动"
                                   f"并监听 {args.host}:{args.port}"}, ensure_ascii=False))
        sys.exit(1)

    try:
        styles = fetch_group(ctx, STYLES, notes, "风格指数")
        sectors = fetch_group(ctx, SECTORS, notes, "行业ETF")
        risk = fetch_group(ctx, RISK, notes, "风险偏好")
        china = fetch_group(ctx, CHINA, notes, "中概")
        holdings = fetch_group(ctx, HOLDINGS, notes, "持仓")
        vol = fetch_vol_ratio(ctx, notes)
    finally:
        ctx.close()

    if not styles and not sectors:
        print(json.dumps({"error": "Futu 行情全部获取失败；请检查 OpenD 连接与美股行情权限"},
                         ensure_ascii=False))
        sys.exit(1)

    sectors_sorted = sorted([s for s in sectors if s["pct"] is not None],
                            key=lambda x: -x["pct"])

    index_series = {}
    if not args.no_write:
        index_series = fetch_index_history(notes)

    notes.append("美股无'主力资金流'口径：本研报的板块强弱来自行业ETF真实涨跌与成交量，"
                 "不是资金流估算，请勿与A股主力资金流混为一谈")
    notes.append("US.DIDIY 为 OTC 粉单，Futu 不提供行情，滴滴相关估值需另行取数")
    notes.append("盘前/盘后成交量通常极小，夜盘价格参考性有限，不能等同于次日开盘价")

    brief = {
        "session": market_phase(styles + sectors),
        "styles": styles,
        "sectors": sectors_sorted,
        "risk": risk,
        "china": china,
        "holdings": holdings,
        "volume_ratio": vol,
        "signals": build_signals(styles, risk, china, sectors_sorted, holdings),
        "notes": notes,
        "artifacts": None,
    }
    if not args.no_write:
        p = write_csv(index_series)
        brief["artifacts"] = {"csv": p} if p else None
        if not p:
            notes.append("指数历史全部获取失败，未写入 美股每日盘面_YTD.csv")

    if args.summary:
        print_summary(brief)
    else:
        print(json.dumps(brief, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
