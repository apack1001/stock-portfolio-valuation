#!/usr/bin/env python3
"""
富途持仓自动同步（只读）—— 通过 Futu OpenAPI（本地 OpenD 网关）拉取富途账户的
真实持仓与现金，并与 ~/Desktop/持仓/明细.csv 中 account=富途 的行对账。

这是「可选增强」：需要本地运行 OpenD 并已登录富途账号，且安装 futu-api。
未满足时脚本会优雅降级并打印指引，不影响 skill 的其余功能。

用法:
  python3 fetch_futu_positions.py                 # 只读对账，输出 JSON
  python3 fetch_futu_positions.py --summary       # 人类可读的对账表
  python3 fetch_futu_positions.py --write-back     # 保守写回 明细.csv（见下方安全规则）

环境变量:
  PORTFOLIO_DIR     持仓目录（默认 ~/Desktop/持仓）
  FUTU_OPEND_HOST   OpenD 地址（默认 127.0.0.1）
  FUTU_OPEND_PORT   OpenD 端口（默认 11111）

安全/边界:
  - 全程只读：仅 position_list_query / accinfo_query，不调用任何下单或解锁交易接口。
  - --write-back 时，若某代码在 CSV 中除 富途 账户外还出现在其它账户（如 LTI 长期激励），
    视为「普通股+激励双重持仓」，无法用 Futu 合计股数安全拆分，故跳过该代码、仅标记，
    由你手动核对。其余唯一归属 富途 的代码才会被更新。
  - CSV 中有、但 Futu 已查不到的 富途 股票行，仅标记疑似已清仓，绝不自动删除。
"""
import sys, os, json, csv, argparse, logging
from pathlib import Path
from datetime import datetime
from collections import defaultdict

# futu-api 会把连接日志打到 stdout，污染本脚本的 JSON 输出；全局关闭日志
logging.disable(logging.CRITICAL)

BASE_DIR = Path(os.environ.get("PORTFOLIO_DIR", str(Path.home() / "Desktop/持仓")))
CSV_PATH = BASE_DIR / "明细.csv"
OPEND_HOST = os.environ.get("FUTU_OPEND_HOST", "127.0.0.1")
OPEND_PORT = int(os.environ.get("FUTU_OPEND_PORT", "11111"))
FUTU_ACCOUNT = "富途"
TICK = 1e-6


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
    """'HK.00700'->('HK','00700'); 'US.YINN'->('US','YINN'); 'SH.600000'/'SZ.000001'->('CN', code)"""
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
        return None
    with open(CSV_PATH, "r", encoding="utf-8-sig") as f:
        return [{k: (v or "").strip() for k, v in row.items()} for row in csv.DictReader(f)]


def write_csv(rows):
    base_fields = [
        "account", "category", "name", "code", "market", "currency",
        "shares", "cost_price", "cost_total", "last_market_value",
        "last_pnl", "last_updated", "note",
    ]
    existing = list(rows[0].keys()) if rows else base_fields
    fieldnames = existing + [f for f in base_fields if f not in existing]
    with open(CSV_PATH, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def fetch_futu():
    """连接 OpenD，返回 (positions, cash_by_currency)。positions: list of dict。"""
    # 预探测：OpenD 未监听时立即退出，避免 futu SDK 进入无限重连
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(2)
    try:
        if s.connect_ex((OPEND_HOST, OPEND_PORT)) != 0:
            fail(f"无法连接 OpenD（{OPEND_HOST}:{OPEND_PORT}）。请先启动 FutuOpenD 并登录富途账号后重试。")
    finally:
        s.close()

    try:
        import futu as ft
    except ImportError:
        fail("未安装 futu-api。请先 `pip3 install futu-api`，并启动 OpenD 登录富途账号后重试。")

    # 不同 futu-api 版本构造签名略有差异，做兼容
    def open_trade_ctx(market):
        kwargs = dict(host=OPEND_HOST, port=OPEND_PORT)
        try:
            return ft.OpenSecTradeContext(
                filter_trdmarket=market,
                security_firm=ft.SecurityFirm.FUTUSECURITIES,
                **kwargs,
            )
        except TypeError:
            # 旧版本回退
            return ft.OpenSecTradeContext(**kwargs)

    markets = []
    for name in ("HK", "US", "CN"):
        m = getattr(ft.TrdMarket, name, None)
        if m is not None:
            markets.append((name, m))

    positions, account = [], None
    errors = []
    seen = set()
    for name, market in markets:
        ctx = None
        try:
            ctx = open_trade_ctx(market)
            ret, data = ctx.position_list_query(trd_env=ft.TrdEnv.REAL)
            if ret == ft.RET_OK:
                for _, r in data.iterrows():
                    code = str(r.get("code", ""))
                    if code in seen:
                        continue
                    seen.add(code)
                    nmkt, ncode = normalize_futu_code(code)
                    positions.append({
                        "futu_code": code,
                        "market": nmkt,
                        "code": ncode,
                        "name": r.get("stock_name", ""),
                        "qty": to_float(r.get("qty")),
                        "cost_price": to_float(r.get("cost_price")),
                        "market_val": to_float(r.get("market_val")),
                        "pl_val": to_float(r.get("pl_val")),
                        "today_pl_val": to_float(r.get("today_pl_val")),
                        "currency": str(r.get("currency", "")).replace("Currency.", "") or None,
                    })
            else:
                errors.append(f"{name} 持仓查询失败: {data}")
            # 账户总览只取一次（首个市场=HK 的上下文返回整账户的 HKD 视图）
            if account is None:
                try:
                    aret, ainfo = ctx.accinfo_query(trd_env=ft.TrdEnv.REAL)
                    if aret == ft.RET_OK and not ainfo.empty:
                        row = ainfo.iloc[0]
                        account = {k: to_float(row.get(k)) for k in
                                   ("total_assets", "securities_assets", "fund_assets",
                                    "bond_assets", "market_val", "cash",
                                    "hk_cash", "us_cash", "cn_cash")}
                        account["currency"] = str(row.get("currency", "")).replace("Currency.", "")
                except Exception as e:
                    errors.append(f"账户总览查询跳过: {e}")
        except Exception as e:
            errors.append(f"{name} 连接/查询异常: {e}")
        finally:
            if ctx is not None:
                try:
                    ctx.close()
                except Exception:
                    pass

    if not positions and errors:
        fail("未取得任何持仓。常见原因：OpenD 未启动/未登录、行情或交易权限不足。详情: "
             + " | ".join(errors))
    return positions, account, errors


def reconcile(positions, cash, rows):
    """对账：把 Futu 实仓与 CSV(account=富途 / 含其它账户合计) 比对。"""
    # CSV 中按 (market, code) 聚合
    csv_by_key = {}
    for r in rows or []:
        if r.get("category") != "股票":
            continue
        key = (r.get("market"), r.get("code"))
        csv_by_key.setdefault(key, []).append(r)

    report = []
    for p in positions:
        key = (p["market"], p["code"])
        csv_rows = csv_by_key.get(key, [])
        futu_rows = [r for r in csv_rows if r.get("account") == FUTU_ACCOUNT]
        other_rows = [r for r in csv_rows if r.get("account") != FUTU_ACCOUNT]
        csv_total = sum(to_float(r.get("shares")) or 0 for r in csv_rows)
        futu_shares = sum(to_float(r.get("shares")) or 0 for r in futu_rows)
        item = {
            **p,
            "csv_futu_shares": futu_shares if futu_rows else None,
            "csv_total_shares_all_accounts": csv_total if csv_rows else None,
            "has_lti_or_other_account": bool(other_rows),
            "other_accounts": sorted({r.get("account") for r in other_rows}),
        }
        if not csv_rows:
            item["status"] = "futu_only"          # Futu 有、CSV 没有 → 可新增
        elif other_rows:
            item["status"] = "ambiguous_multi_account"  # 含 LTI 等，跳过自动写
        elif abs((p["qty"] or 0) - futu_shares) <= TICK:
            item["status"] = "match"
        else:
            item["status"] = "diff"               # 唯一归属富途但股数/成本不一致
        report.append(item)

    # CSV 中富途有、Futu 查不到的（疑似已清仓）
    futu_keys = {(p["market"], p["code"]) for p in positions}
    csv_only = []
    for r in rows or []:
        if r.get("category") == "股票" and r.get("account") == FUTU_ACCOUNT:
            if (r.get("market"), r.get("code")) not in futu_keys and (to_float(r.get("shares")) or 0) > 0:
                csv_only.append({"market": r.get("market"), "code": r.get("code"),
                                 "name": r.get("name"), "csv_shares": to_float(r.get("shares")),
                                 "status": "csv_only_maybe_closed"})
    return report, csv_only


def apply_write_back(report, cash, rows):
    """保守写回：仅更新 status in (diff, futu_only)；ambiguous/csv_only 不动。"""
    today = datetime.now().strftime("%Y-%m-%d")
    changes = []
    by_key = {}
    for r in rows:
        by_key.setdefault((r.get("account"), r.get("market"), r.get("code")), r)

    for it in report:
        if it["status"] == "diff":
            r = by_key.get((FUTU_ACCOUNT, it["market"], it["code"]))
            if not r:
                continue
            qty, cp = it["qty"], it["cost_price"]
            r["shares"] = f"{qty:g}" if qty is not None else r.get("shares", "")
            if cp is not None:
                r["cost_price"] = f"{cp:g}"
                if qty is not None:
                    r["cost_total"] = f"{qty * cp:.2f}"
            r["last_updated"] = today
            r["note"] = (r.get("note", "") + " ｜Futu同步").strip("｜ ")
            changes.append(f"更新 {it['market']}.{it['code']} 股数→{qty} 成本→{cp}")
        elif it["status"] == "futu_only":
            qty, cp = it["qty"], it["cost_price"]
            rows.append({
                "account": FUTU_ACCOUNT, "category": "股票", "name": it.get("name", ""),
                "code": it["code"], "market": it["market"],
                "currency": it.get("currency") or "",
                "shares": f"{qty:g}" if qty is not None else "",
                "cost_price": f"{cp:g}" if cp is not None else "",
                "cost_total": f"{qty * cp:.2f}" if (qty is not None and cp is not None) else "",
                "last_market_value": "", "last_pnl": "",
                "last_updated": today, "note": "Futu同步新增",
            })
            changes.append(f"新增 {it['market']}.{it['code']} 股数{qty}")

    # 现金（保守：仅当存在对应 富途 自由现金 行时更新其市值）
    cur_to_name = {"USD": "自由现金(USD)", "HKD": "自由现金(HKD)"}
    for cur, val in (cash or {}).items():
        nm = cur_to_name.get(cur)
        if not nm:
            continue
        for r in rows:
            if r.get("account") == FUTU_ACCOUNT and r.get("category") == "活期" and r.get("name") == nm:
                r["cost_total"] = f"{val:.2f}"
                r["last_market_value"] = f"{val:.2f}"
                r["last_updated"] = today
                r["note"] = (r.get("note", "") + " ｜Futu同步").strip("｜ ")
                changes.append(f"更新 富途{nm}→{val}")
    return changes


def main():
    ap = argparse.ArgumentParser(description="富途持仓只读同步/对账")
    ap.add_argument("--write-back", action="store_true", help="把对账结果保守写回 明细.csv")
    ap.add_argument("--summary", action="store_true", help="输出人类可读对账表而非 JSON")
    args = ap.parse_args()

    rows = load_csv()
    if rows is None:
        fail(f"未找到 {CSV_PATH}，请先初始化持仓。")

    positions, account, errors = fetch_futu()
    report, csv_only = reconcile(positions, account, rows)

    # 今日盈亏（来自 Futu today_pl_val，与 App 一致），按持仓币种分开
    today_pl = defaultdict(float)
    for p in positions:
        if p.get("today_pl_val") is not None:
            today_pl[p.get("currency") or "?"] += p["today_pl_val"]

    # 写回现金用：从账户总览派生分币种现金
    cash = {}
    if account:
        if account.get("hk_cash") is not None:
            cash["HKD"] = account["hk_cash"]
        if account.get("us_cash") is not None:
            cash["USD"] = account["us_cash"]

    changes = []
    if args.write_back:
        changes = apply_write_back(report, cash, rows)
        if changes:
            write_csv(rows)

    out = {
        "ok": True,
        "opend": f"{OPEND_HOST}:{OPEND_PORT}",
        "account_summary": account,          # 总资产/证券/基金/现金（accinfo，HKD 视图）
        "today_pl_by_currency": {k: round(v, 2) for k, v in today_pl.items()},
        "futu_positions": len(positions),
        "reconcile": report,
        "csv_only_suspected_closed": csv_only,
        "warnings": errors,
        "write_back_changes": changes if args.write_back else None,
    }

    if args.summary:
        if account:
            cur = account.get("currency", "")
            print(f"账户总览({cur}): 总资产 {account.get('total_assets'):,.2f} ｜ "
                  f"证券 {account.get('securities_assets'):,.2f} ｜ "
                  f"基金 {account.get('fund_assets'):,.2f} ｜ 现金 {account.get('cash'):,.2f}")
        print("今日盈亏(Futu, 按币种):", {k: round(v, 2) for k, v in today_pl.items()})
        print(f"\nOpenD {out['opend']} ｜ Futu 实仓 {len(positions)} 笔")
        print(f"{'代码':<11}{'名称':<13}{'股数':>8}{'今日盈亏':>10}{'累计盈亏':>11}{'状态':>20}")
        for it in report:
            print(f"{it['market']}.{it['code']:<7}{(it.get('name') or '')[:11]:<13}"
                  f"{(it['qty'] or 0):>8g}{(it.get('today_pl_val') or 0):>10,.0f}"
                  f"{(it.get('pl_val') or 0):>11,.0f}{it['status']:>20}")
        for c in csv_only:
            print(f"{c['market']}.{c['code']}  {c['name']}  CSV{c['csv_shares']}  {c['status']}")
        if errors:
            print("warnings:", " | ".join(errors))
        if args.write_back:
            print("write-back:", changes or "无改动")
    else:
        print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
