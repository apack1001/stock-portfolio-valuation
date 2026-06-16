#!/usr/bin/env python3
"""
富途历史成交 → 已实现盈亏 / 过往买卖审计（只读）

通过 Futu OpenAPI（本地 OpenD）拉取历史成交记录，按 FIFO 计算每个标的的已实现盈亏，
并审计是否存在「成交过但当前 明细.csv 未反映」的标的。是「可选增强」，需本地 OpenD 已登录、
且 `pip3 install futu-api`。

用法:
  python3 fetch_futu_deals.py                          # 默认 2026-01-01 至今，输出 JSON
  python3 fetch_futu_deals.py --start 2025-01-01       # 指定起始日期
  python3 fetch_futu_deals.py --summary                # 人类可读表
  python3 fetch_futu_deals.py --write-ledger           # 把已实现盈亏写入 已实现盈亏.csv

边界/说明:
  - 全程只读：仅 history_deal_list_query，不下单、不解锁交易。
  - 仅覆盖富途证券成交（美股/港股 股票/期权）。不含：转仓（如股票转户）、基金申赎、
    支付宝/腾讯交易——这些成交流水里没有。
  - FIFO 在查询窗口内匹配买卖；若某笔卖出的对应买入在窗口之外，对未覆盖部分回退用
    明细.csv 的 cost_price 估算并标记 incomplete_basis，需人工复核。
  - 历史区间按 90 天分批查询，规避单次跨度限制；不含手续费（realized 为税费前近似）。

环境变量: PORTFOLIO_DIR / FUTU_OPEND_HOST / FUTU_OPEND_PORT
"""
import sys, os, json, csv, argparse, logging, socket, time
from pathlib import Path
from datetime import datetime, timedelta
from collections import defaultdict, deque

logging.disable(logging.CRITICAL)  # 屏蔽 futu 打到 stdout 的连接日志

BASE_DIR = Path(os.environ.get("PORTFOLIO_DIR", str(Path.home() / "Desktop/持仓")))
CSV_PATH = BASE_DIR / "明细.csv"
REALIZED_PATH = BASE_DIR / "已实现盈亏.csv"
OPEND_HOST = os.environ.get("FUTU_OPEND_HOST", "127.0.0.1")
OPEND_PORT = int(os.environ.get("FUTU_OPEND_PORT", "11111"))
CHUNK_DAYS = 180
THROTTLE_SEC = 3.2  # history_deal_list_query 限频：每 30 秒最多 10 次


def fail(msg, code=2):
    print(json.dumps({"ok": False, "error": msg}, ensure_ascii=False, indent=2))
    sys.exit(code)


def to_float(v):
    try:
        if v is None or str(v).strip() in ("", "---"):
            return None
        return float(str(v).strip().replace(",", ""))
    except (ValueError, TypeError):
        return None


def normalize_futu_code(futu_code: str):
    if not futu_code or "." not in futu_code:
        return (None, futu_code)
    prefix, sym = futu_code.split(".", 1)
    prefix = prefix.upper()
    if prefix == "HK":
        return ("HK", sym.zfill(5))
    if prefix == "US":
        return ("US", sym)
    if prefix in ("SH", "SZ"):
        return ("CN", sym)
    return (prefix, sym)


def load_csv():
    if not CSV_PATH.exists():
        return []
    with open(CSV_PATH, "r", encoding="utf-8-sig") as f:
        return [{k: (v or "").strip() for k, v in row.items()} for row in csv.DictReader(f)]


def date_chunks(start, end):
    cur = start
    while cur <= end:
        chunk_end = min(cur + timedelta(days=CHUNK_DAYS - 1), end)
        yield cur, chunk_end
        cur = chunk_end + timedelta(days=1)


def fetch_deals(start, end):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(2)
    try:
        if s.connect_ex((OPEND_HOST, OPEND_PORT)) != 0:
            fail(f"无法连接 OpenD（{OPEND_HOST}:{OPEND_PORT}）。请先启动 FutuOpenD 并登录后重试。")
    finally:
        s.close()

    try:
        import futu as ft
    except ImportError:
        fail("未安装 futu-api。请先 `pip3 install futu-api` 并启动 OpenD 后重试。")

    def open_ctx(market):
        try:
            return ft.OpenSecTradeContext(filter_trdmarket=market,
                                          security_firm=ft.SecurityFirm.FUTUSECURITIES,
                                          host=OPEND_HOST, port=OPEND_PORT)
        except TypeError:
            return ft.OpenSecTradeContext(host=OPEND_HOST, port=OPEND_PORT)

    deals, errors, seen = [], [], set()
    for name in ("HK", "US"):
        market = getattr(ft.TrdMarket, name, None)
        if market is None:
            continue
        ctx = None
        try:
            ctx = open_ctx(market)
            for cs, ce in date_chunks(start, end):
                ret, data = ctx.history_deal_list_query(
                    start=cs.strftime("%Y-%m-%d"), end=ce.strftime("%Y-%m-%d"),
                    trd_env=ft.TrdEnv.REAL)
                time.sleep(THROTTLE_SEC)  # 节流，规避每30秒10次限制
                if ret != ft.RET_OK:
                    errors.append(f"{name} {cs}~{ce} 成交查询失败: {data}")
                    continue
                for _, r in data.iterrows():
                    did = str(r.get("deal_id", ""))
                    if did and did in seen:
                        continue
                    seen.add(did)
                    nmkt, ncode = normalize_futu_code(str(r.get("code", "")))
                    side = str(r.get("trd_side", "")).upper().replace("TRDSIDE.", "")
                    ccy = str(r.get("currency", "")).replace("Currency.", "").strip()
                    if not ccy or ccy.upper() in ("N/A", "NONE"):
                        ccy = {"US": "USD", "HK": "HKD", "CN": "CNY"}.get(nmkt)
                    deals.append({
                        "deal_id": did, "time": str(r.get("create_time", "")),
                        "market": nmkt, "code": ncode, "name": r.get("stock_name", ""),
                        "side": side, "qty": to_float(r.get("qty")),
                        "price": to_float(r.get("price")),
                        "currency": ccy,
                    })
        except Exception as e:
            errors.append(f"{name} 异常: {e}")
        finally:
            if ctx is not None:
                try:
                    ctx.close()
                except Exception:
                    pass

    deals.sort(key=lambda d: d["time"])
    return deals, errors


def fifo_realized(deals, csv_rows):
    """按 (market,code) FIFO 匹配，仅计算窗口内能匹配到买入的部分；匹配不到的不猜，标记需人工。"""
    lots = defaultdict(deque)   # (market,code) -> deque of [qty, price]
    realized = []
    for d in deals:
        key = (d["market"], d["code"])
        qty, price = d["qty"] or 0, d["price"] or 0
        if d["side"].startswith("BUY"):
            lots[key].append([qty, price])
        elif d["side"].startswith("SELL"):
            remain, pnl, matched = qty, 0.0, 0.0
            q = lots[key]
            while remain > TICK and q:
                lot = q[0]
                take = min(remain, lot[0])
                pnl += (price - lot[1]) * take
                lot[0] -= take
                remain -= take
                matched += take
                if lot[0] <= TICK:
                    q.popleft()
            # remain>0 表示对应买入在查询窗口之外，无法计算成本：不编数，单独标记
            realized.append({
                "time": d["time"], "market": d["market"], "code": d["code"],
                "name": d["name"], "currency": d["currency"], "sell_qty": qty,
                "sell_price": price,
                "matched_qty": round(matched, 4),
                "realized_pnl": round(pnl, 2) if matched > TICK else None,
                "incomplete_basis_qty": round(remain, 4),
                "reliable": remain <= TICK,
            })
    return realized


TICK = 1e-6


def audit_vs_csv(deals, csv_rows):
    csv_keys = {(r.get("market"), r.get("code")) for r in csv_rows if r.get("category") == "股票"}
    traded_keys = {(d["market"], d["code"]) for d in deals}
    return sorted([f"{m}.{c}" for (m, c) in traded_keys - csv_keys])


def write_ledger(realized):
    """只写入窗口内可靠计算的平仓；按 日期+代码+金额 去重，跳过台账中已存在的行。

    注意：去重仅能拦截"完全相同"的行。若台账里是按标的累计的口径（与逐笔口径不同），
    金额对不上则拦不住，仍可能语义重复——这类需人工判断。
    """
    header = ["date", "name", "code", "market", "currency", "realized_pnl", "note"]
    reliable = [r for r in realized if r["reliable"]]

    existing_keys = set()
    if REALIZED_PATH.exists():
        for row in csv.DictReader(open(REALIZED_PATH, encoding="utf-8-sig")):
            existing_keys.add((row.get("date", ""), row.get("code", ""),
                               str(to_float(row.get("realized_pnl")))))

    written, skipped = 0, 0
    exists = REALIZED_PATH.exists()
    with open(REALIZED_PATH, "a", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        if not exists:
            w.writerow(header)
        for r in reliable:
            key = (r["time"][:10], r["code"], str(to_float(r["realized_pnl"])))
            if key in existing_keys:
                skipped += 1
                continue
            existing_keys.add(key)
            w.writerow([r["time"][:10], r["name"], r["code"], r["market"],
                        r["currency"] or "", r["realized_pnl"], "Futu成交FIFO导入"])
            written += 1
    return {"written": written, "skipped_duplicate": skipped}


def main():
    ap = argparse.ArgumentParser(description="富途历史成交 → 已实现盈亏/审计（只读）")
    ap.add_argument("--start", default="2026-01-01", help="起始日期 YYYY-MM-DD")
    ap.add_argument("--end", default=datetime.now().strftime("%Y-%m-%d"), help="结束日期 YYYY-MM-DD")
    ap.add_argument("--summary", action="store_true")
    ap.add_argument("--write-ledger", action="store_true", help="追加写入 已实现盈亏.csv")
    args = ap.parse_args()

    try:
        start = datetime.strptime(args.start, "%Y-%m-%d").date()
        end = datetime.strptime(args.end, "%Y-%m-%d").date()
    except ValueError:
        fail("日期格式应为 YYYY-MM-DD")

    csv_rows = load_csv()
    deals, errors = fetch_deals(start, end)
    realized = fifo_realized(deals, csv_rows)
    unknown = audit_vs_csv(deals, csv_rows)

    reliable = [r for r in realized if r["reliable"]]
    needs_manual = [r for r in realized if not r["reliable"]]

    # 可靠平仓按币种合计（不同币种不可混加）
    by_ccy = defaultdict(float)
    by_code = defaultdict(float)
    for r in reliable:
        by_ccy[r["currency"] or "?"] += r["realized_pnl"]
        by_code[f"{r['market']}.{r['code']}"] += r["realized_pnl"]

    written = write_ledger(realized) if args.write_ledger else None

    out = {
        "ok": True,
        "range": [args.start, args.end],
        "deals_count": len(deals),
        "reliable_realized_trades": reliable,
        "needs_manual_trades": needs_manual,
        "realized_total_by_currency": {k: round(v, 2) for k, v in by_ccy.items()},
        "realized_by_code_reliable": {k: round(v, 2) for k, v in by_code.items()},
        "audit_traded_not_in_csv": unknown,
        "warnings": errors,
        "ledger_rows_written": written,
    }

    if args.summary:
        print(f"区间 {args.start}~{args.end} ｜ 成交 {len(deals)} 笔 ｜ 平仓 {len(realized)} 笔"
              f"（可靠 {len(reliable)} / 需人工 {len(needs_manual)}）")
        print("【可靠已实现盈亏】")
        print(f"{'日期':<12}{'代码':<11}{'卖出量':>7}{'价':>9}{'已实现':>11}{'币种':>5}")
        for r in reliable:
            print(f"{r['time'][:10]:<12}{r['market']}.{r['code']:<7}"
                  f"{(r['sell_qty'] or 0):>7g}{(r['sell_price'] or 0):>9g}"
                  f"{r['realized_pnl']:>11,.2f}{(r['currency'] or ''):>5}")
        print("按币种合计:", out["realized_total_by_currency"])
        if needs_manual:
            print("\n【需人工补成本（买入在查询窗口外）】")
            for r in needs_manual:
                print(f"{r['time'][:10]}  {r['market']}.{r['code']}  卖{r['sell_qty']:g}@{r['sell_price']:g}"
                      f"  未覆盖{r['incomplete_basis_qty']:g}股")
        if unknown:
            print("\n⚠️ 成交过但当前 CSV 无此标的(已清仓):", unknown)
        if errors:
            print("\nwarnings:", " | ".join(errors))
        if args.write_ledger:
            print(f"\n写入 已实现盈亏.csv: 新增 {written['written']} 行，"
                  f"跳过重复 {written['skipped_duplicate']} 行（仅可靠项）")
    else:
        print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
