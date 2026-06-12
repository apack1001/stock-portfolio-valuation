#!/usr/bin/env python3
"""
持仓实时估值 - 从 ~/Desktop/持仓/明细.csv 加载持仓，实时获取股票价格，输出 JSON
用法:
  python3 fetch_prices.py
  python3 fetch_prices.py --fund-mode official
"""
import sys, os, json, csv, re, argparse
from pathlib import Path
from datetime import datetime, timedelta

def parse_tax_rate(note: str):
    """从 note 中提取税率，如「税率20%」→ 0.20，未找到返回 None"""
    m = re.search(r'税率\s*(\d+(?:\.\d+)?)\s*%', note or "")
    return float(m.group(1)) / 100 if m else None

def option_after_tax_value(price, strike, shares, tax_rate):
    """未行权期权的税后内在价值市值；价外（price<=strike）时为 0。"""
    intrinsic = max(0.0, price - strike)
    return round(intrinsic * shares * (1 - tax_rate), 2)
BASE_DIR = Path(os.environ.get("PORTFOLIO_DIR", str(Path.home() / "Desktop/持仓")))
CSV_PATH = BASE_DIR / "明细.csv"
TOTAL_PATH = BASE_DIR / "总额.csv"
_FUND_ESTIMATION_CACHE = None
_GOLD_PRICE_CACHE = None
_KGI_FUND_CACHE = {}

def to_float(value):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip().replace(",", "")
    if not s or s == "---":
        return None
    try:
        return float(s)
    except ValueError:
        return None

def fetch_us_price(code: str):
    import akshare as ak
    df = ak.stock_us_daily(symbol=code.upper(), adjust="")
    if df is None or df.empty:
        return None
    return float(df["close"].iloc[-1])
def fetch_hk_price(code: str):
    """港股价格：优先用新浪数据源（stock_hk_daily），失败则回退东方财富（stock_hk_hist）"""
    import akshare as ak
    sym = str(code).zfill(5)
    # 主数据源：新浪财经（稳定，国内可访问）
    try:
        df = ak.stock_hk_daily(symbol=sym, adjust="")
        if df is not None and not df.empty and "close" in df.columns:
            return float(df["close"].iloc[-1])
    except Exception:
        pass
    # 备用数据源：东方财富
    try:
        start = (datetime.now() - timedelta(days=10)).strftime("%Y%m%d")
        end = datetime.now().strftime("%Y%m%d")
        df = ak.stock_hk_hist(symbol=sym, period="daily", start_date=start, end_date=end, adjust="")
        if df is not None and not df.empty:
            return float(df["收盘"].iloc[-1])
    except Exception:
        pass
    return None
def fetch_cn_price(code: str):
    import akshare as ak
    start = (datetime.now() - timedelta(days=10)).strftime("%Y%m%d")
    end = datetime.now().strftime("%Y%m%d")
    df = ak.stock_zh_a_hist(symbol=code, period="daily", start_date=start, end_date=end, adjust="")
    if df is None or df.empty:
        return None
    return float(df["收盘"].iloc[-1])
def fetch_gold_cny_per_gram():
    """取上海金 Au99.99 的人民币/克价格；实时接口失败时回退最近历史收盘价。"""
    global _GOLD_PRICE_CACHE
    if _GOLD_PRICE_CACHE is not None:
        return _GOLD_PRICE_CACHE
    import akshare as ak
    try:
        df = ak.spot_quotations_sge()
        if df is not None and not df.empty:
            name_col = next((c for c in df.columns if "品种" in str(c) or "合约" in str(c)), None)
            price_col = next((c for c in df.columns if "最新" in str(c) or "现价" in str(c) or "中间价" in str(c)), None)
            if name_col and price_col:
                row = df[df[name_col].astype(str).str.contains("Au99.99", regex=False)]
                if not row.empty:
                    price = to_float(row.iloc[0].get(price_col))
                    if price:
                        _GOLD_PRICE_CACHE = (price, "gold_sge_realtime", datetime.now().strftime("%Y-%m-%d %H:%M"))
                        return _GOLD_PRICE_CACHE
    except Exception:
        pass
    df = ak.spot_hist_sge(symbol="Au99.99")
    if df is None or df.empty:
        return None
    row = df.iloc[-1]
    price = to_float(row.get("close"))
    if not price:
        return None
    _GOLD_PRICE_CACHE = (price, "gold_sge_close", str(row.get("date", "")))
    return _GOLD_PRICE_CACHE
def _find_col(columns, pattern):
    for col in columns:
        if pattern in str(col):
            return col
    return None
def fund_name_matches(expected: str, actual: str):
    """粗校验基金代码是否指向同一只基金，避免代码填错时误用净值。
    通用做法：归一化后，首二字（基金公司）相同或整体相似度达标即视为匹配，
    不依赖任何与具体持仓相关的关键词。"""
    expected = re.sub(r"[\s（）()·\-_/]", "", expected or "")
    actual = re.sub(r"[\s（）()·\-_/]", "", actual or "")
    if not expected or not actual:
        return False
    if expected[:2] == actual[:2]:
        return True
    from difflib import SequenceMatcher
    return SequenceMatcher(None, expected, actual).ratio() >= 0.5
def fetch_fund_estimation_map():
    """东方财富盘中净值估算：返回 code -> 估算净值/公布净值等。"""
    global _FUND_ESTIMATION_CACHE
    if _FUND_ESTIMATION_CACHE is not None:
        return _FUND_ESTIMATION_CACHE
    import akshare as ak
    df = ak.fund_value_estimation_em(symbol="全部")
    estimate_col = _find_col(df.columns, "估算数据-估算值")
    estimate_pct_col = _find_col(df.columns, "估算数据-估算增长率")
    official_col = _find_col(df.columns, "公布数据-单位净值")
    last_nav_col = None
    for col in df.columns:
        if str(col).endswith("-单位净值") and "公布数据" not in str(col):
            last_nav_col = col
            break
    result = {}
    for _, row in df.iterrows():
        code = str(row.get("基金代码", "")).zfill(6)
        result[code] = {
            "estimated_nav": to_float(row.get(estimate_col)) if estimate_col else None,
            "estimated_pct": str(row.get(estimate_pct_col, "")).strip() if estimate_pct_col else "",
            "official_nav": to_float(row.get(official_col)) if official_col else None,
            "last_nav": to_float(row.get(last_nav_col)) if last_nav_col else None,
            "fund_name": str(row.get("基金名称", "")).strip(),
        }
    _FUND_ESTIMATION_CACHE = result
    return result
def fetch_fund_official_nav(code: str):
    """最新公布单位净值，适合作为正式记账口径。"""
    import akshare as ak
    df = ak.fund_open_fund_info_em(symbol=str(code).zfill(6), indicator="单位净值走势")
    if df is None or df.empty:
        return None, ""
    row = df.iloc[-1]
    return to_float(row.get("单位净值")), str(row.get("净值日期", ""))
def fetch_kgi_fund_nav(code: str, currency: str):
    """
    尝试从 KGI 基金详情页抓取 USD/HKD 基金净值。
    这是 FUND_HKD / FUND_USD 的首选自动来源；失败时由调用方回退快照。
    """
    global _KGI_FUND_CACHE
    code = str(code or "").strip().upper()
    currency = str(currency or "").strip().lower()
    cache_key = (code, currency)
    if cache_key in _KGI_FUND_CACHE:
        return _KGI_FUND_CACHE[cache_key]

    if not code or currency not in ("usd", "hkd"):
        _KGI_FUND_CACHE[cache_key] = None
        return None

    import requests

    # KGI 页面最后一段通常是 share class 代号；不同基金常见为 0 / c / r。
    share_class_candidates = ("0", "c", "r")
    headers = {"User-Agent": "Mozilla/5.0"}
    for share_class in share_class_candidates:
        url = (
            "https://www.kgi.com.hk/en/products-overview/wealth-products/mutual-funds/"
            f"fund-detail?funds={code.lower()}%3A{currency}%3A{share_class}"
        )
        try:
            resp = requests.get(url, headers=headers, timeout=15)
            if resp.status_code != 200:
                continue
            text = resp.text
            price_match = re.search(r"Price</span>.*?<span>([0-9.,]+)</span>", text, re.S)
            date_match = re.search(r"Nav Date</span>.*?<span>([0-9.\-]+)</span>", text, re.S)
            if not (price_match and date_match):
                continue

            price = to_float(price_match.group(1))
            nav_date = date_match.group(1).replace(".", "-")
            if price is None or not nav_date:
                continue

            result = {
                "price": price,
                "nav_date": nav_date,
                "fund_source": "kgi_nav",
                "updated_at": nav_date,
                "url": url,
            }
            _KGI_FUND_CACHE[cache_key] = result
            return result
        except Exception:
            continue

    _KGI_FUND_CACHE[cache_key] = None
    return None
def fetch_stockevents_fund_price(code: str, currency: str):
    """KGI 不覆盖时，尝试从 Stock Events 的 FUND 页面提取境外基金报价。"""
    code = str(code or "").strip().upper()
    currency = str(currency or "").strip().upper()
    if not code or currency not in ("USD", "HKD"):
        return None

    import requests

    url = f"https://stockevents.app/en/stock/{code}.FUND"
    try:
        resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
        if resp.status_code != 200:
            return None
        symbol = "HK\\$" if currency == "HKD" else "\\$"
        match = re.search(rf"{symbol}([0-9]+(?:\\.[0-9]+)?)", resp.text)
        if not match:
            return None
        price = to_float(match.group(1))
        if price is None:
            return None
        updated_at = datetime.now().strftime("%Y-%m-%d")
        return {
            "price": price,
            "nav_date": updated_at,
            "fund_source": "stockevents_fund",
            "updated_at": updated_at,
            "url": url,
        }
    except Exception:
        return None
def price_cny_fund(row, fund_mode: str):
    """人民币公募基金：有 code + shares 时自动估值；失败则回退 CSV 快照。"""
    code = str(row.get("code", "")).strip()
    shares = to_float(row.get("shares", ""))
    if not code or shares is None:
        return None
    code = code.zfill(6)
    if fund_mode == "estimate":
        estimate_map = fetch_fund_estimation_map()
        item = estimate_map.get(code)
        if item:
            if not fund_name_matches(row.get("name", ""), item.get("fund_name", "")):
                return {
                    "price": None,
                    "market_value": None,
                    "fund_source": "snapshot",
                    "fund_name_from_source": item.get("fund_name", ""),
                    "mismatch": True,
                }
            nav = item.get("estimated_nav") or item.get("official_nav") or item.get("last_nav")
            if nav is not None:
                source = "estimate" if item.get("estimated_nav") is not None else "official_nav"
                return {
                    "price": nav,
                    "market_value": round(nav * shares, 2),
                    "fund_source": source,
                    "fund_name_from_source": item.get("fund_name", ""),
                    "estimated_pct": item.get("estimated_pct", ""),
                    "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
                }
    nav, nav_date = fetch_fund_official_nav(code)
    if nav is None:
        return None
    return {
        "price": nav,
        "market_value": round(nav * shares, 2),
        "fund_source": "official_nav",
        "nav_date": nav_date,
        "updated_at": nav_date or datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
def price_offshore_fund(row):
    """港元/美元基金：优先用 KGI 最新净值，失败则回退 CSV 快照。"""
    code = str(row.get("code", "")).strip()
    shares = to_float(row.get("shares", ""))
    if not code or shares is None:
        return None

    priced = fetch_kgi_fund_nav(code, row.get("currency", ""))
    if not priced:
        priced = fetch_stockevents_fund_price(code, row.get("currency", ""))
    if not priced:
        return None

    return {
        "price": priced["price"],
        "market_value": round(priced["price"] * shares, 2),
        "fund_source": priced["fund_source"],
        "nav_date": priced["nav_date"],
        "updated_at": priced["updated_at"],
        "source_url": priced["url"],
    }
def price_gold_gram_asset(row):
    """黄金克数持仓：shares 按克，市值 = SGE Au99.99 价格 * 克数。"""
    if not is_gold_gram_asset(row):
        return None
    grams = to_float(row.get("shares", ""))
    if grams is None:
        return None
    priced = fetch_gold_cny_per_gram()
    if not priced:
        return None
    price, source, updated_at = priced
    return {
        "price": price,
        "market_value": round(price * grams, 2),
        "fund_source": source,
        "updated_at": updated_at,
    }
def is_gold_gram_asset(row):
    """识别按黄金克数记账的基金行：约定在 note 中标注「黄金克数」。
    估值脚本写回备注时会保留该标记，故无需绑定任何具体基金代码。"""
    return "黄金克数" in (row.get("note", "") or "")
def load_csv():
    if not CSV_PATH.exists():
        return None
    rows = []
    with open(CSV_PATH, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append({k: v.strip() for k, v in row.items()})
    return rows
def format_money(value):
    if value is None:
        return ""
    return f"{float(value):.2f}"
def build_fund_note(result):
    """生成可追溯的基金估值备注，不改动持仓份额/成本。"""
    source = result.get("fund_source", "")
    price = result.get("price")
    currency = result.get("currency", "")
    updated_at = result.get("nav_date") or result.get("updated_at", "")
    if price is None:
        return result.get("note", "")
    if source == "kgi_nav":
        return f"NAV {price} {currency}（KGI，价格日期{updated_at}）"
    if source == "stockevents_fund":
        return f"NAV {price} {currency}（Stock Events，获取日期{updated_at}）"
    if source == "estimate":
        pct = result.get("estimated_pct", "")
        pct_text = f"，估算涨跌{pct}" if pct else ""
        return f"盘中估算净值 {price} {currency}（东方财富，{updated_at}{pct_text}）"
    if source == "official_nav":
        return f"NAV {price} {currency}（公布净值，价格日期{updated_at}）"
    if source in ("gold_sge_realtime", "gold_sge_close"):
        return f"黄金克数；Au99.99 {price} CNY/克（{source}，{updated_at}）"
    return result.get("note", "")
def write_back_funds(rows, results):
    """把本次成功取到的基金估值写回 CSV，供下次默认读取。"""
    result_by_key = {
        (r.get("account"), r.get("name"), r.get("code")): r
        for r in results
        if r.get("category") == "基金"
    }
    changed = 0
    for row in rows:
        if row.get("category") != "基金":
            continue
        key = (row.get("account"), row.get("name"), row.get("code"))
        result = result_by_key.get(key)
        if not result or result.get("market_value") is None:
            continue
        if result.get("fund_source") == "snapshot":
            continue
        row["last_market_value"] = format_money(result.get("market_value"))
        row["last_pnl"] = format_money(result.get("pnl"))
        row["last_updated"] = result.get("nav_date") or result.get("updated_at") or row.get("last_updated", "")
        note = build_fund_note(result)
        if note:
            row["note"] = note
        changed += 1
    if changed:
        # 以 CSV 原有列为准，避免写死列导致丢列/报错；缺失的标准列补在末尾
        base_fields = [
            "account", "category", "name", "code", "market", "currency",
            "shares", "cost_price", "cost_total", "last_market_value",
            "last_pnl", "last_updated", "note",
        ]
        existing = list(rows[0].keys()) if rows else base_fields
        fieldnames = existing + [f for f in base_fields if f not in existing]
        with open(CSV_PATH, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
    return changed
def get_fx_rates():
    """获取 USD/CNY 与 HKD/CNY；用于日历史落盘。"""
    try:
        import akshare as ak
        import math

        df = ak.fx_spot_quote()
        usd_row = df[df["货币对"] == "USD/CNY"][["买报价", "卖报价"]].iloc[0]
        hkd_row = df[df["货币对"] == "HKD/CNY"][["买报价", "卖报价"]].iloc[0]
        usd_cny = (float(usd_row["买报价"]) + float(usd_row["卖报价"])) / 2
        hkd_cny = (float(hkd_row["买报价"]) + float(hkd_row["卖报价"])) / 2
        if math.isnan(usd_cny) or math.isnan(hkd_cny):
            raise ValueError("NaN from akshare")
        return usd_cny, hkd_cny
    except Exception:
        import requests

        resp = requests.get("https://open.er-api.com/v6/latest/USD", timeout=8)
        rates = resp.json()["rates"]
        usd_cny = float(rates["CNY"])
        hkd_cny = usd_cny / float(rates["HKD"])
        return usd_cny, hkd_cny
def summarize_totals(results, usd_cny, hkd_cny):
    """生成与 ~/Desktop/持仓/总额.csv 兼容的日汇总。"""
    usd_mv = hkd_mv = cny_mv = 0.0
    pnl_cny_excl_lti = 0.0
    for item in results:
        market_value = to_float(item.get("market_value")) or 0.0
        pnl = to_float(item.get("pnl")) or 0.0
        currency = item.get("currency", "")
        if currency == "USD":
            usd_mv += market_value
            pnl_cny = pnl * usd_cny
        elif currency == "HKD":
            hkd_mv += market_value
            pnl_cny = pnl * hkd_cny
        else:
            cny_mv += market_value
            pnl_cny = pnl

        if item.get("account") != "LTI":
            pnl_cny_excl_lti += pnl_cny

    total_cny = usd_mv * usd_cny + hkd_mv * hkd_cny + cny_mv
    return {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "total_usd": total_cny / usd_cny if usd_cny else 0.0,
        "total_cny": total_cny,
        "usd_mv": usd_mv,
        "hkd_mv": hkd_mv,
        "cny_mv": cny_mv,
        "usd_cny_rate": usd_cny,
        "hkd_cny_rate": hkd_cny,
        "pnl_usd_excl_lti": pnl_cny_excl_lti / usd_cny if usd_cny else 0.0,
        "pnl_cny_excl_lti": pnl_cny_excl_lti,
    }
def classify_bucket(item):
    """把单条持仓归入五大口径之一：lti / cash / restricted / invest / other。"""
    account = item.get("account", "")
    category = item.get("category", "")
    note = item.get("note", "") or ""
    if account == "LTI":
        return "lti"
    if category in ("活期", "存款"):
        return "cash"
    if category in ("应收款", "社保"):
        return "restricted"
    if category == "加密货币" and ("类现金" in note or "稳定币" in note):
        return "cash"
    if category == "基金" and ("货币基金" in note or "类现金" in note):
        return "cash"
    if category in ("股票", "基金", "加密货币"):
        return "invest"
    return "other"


def summarize_buckets(results, usd_cny, hkd_cny):
    """生成投资/LTI/现金/应收限制四口径的分币种与折算CNY汇总。"""
    def rate(currency):
        return usd_cny if currency == "USD" else hkd_cny if currency == "HKD" else 1.0

    raw = {}
    for item in results:
        bucket = classify_bucket(item)
        currency = item.get("currency", "CNY")
        cell = raw.setdefault(bucket, {}).setdefault(currency, {"mv": 0.0, "pnl": 0.0, "cost": 0.0})
        cell["mv"] += to_float(item.get("market_value")) or 0.0
        cell["pnl"] += to_float(item.get("pnl")) or 0.0
        cell["cost"] += to_float(item.get("cost_total")) or 0.0

    out = {}
    for bucket, by_currency in raw.items():
        total = {"mv_cny": 0.0, "pnl_cny": 0.0, "cost_cny": 0.0}
        for currency, cell in by_currency.items():
            total["mv_cny"] += cell["mv"] * rate(currency)
            total["pnl_cny"] += cell["pnl"] * rate(currency)
            total["cost_cny"] += cell["cost"] * rate(currency)
        total["ret_pct"] = (
            total["pnl_cny"] / total["cost_cny"] * 100 if total["cost_cny"] else None
        )
        if bucket == "lti":
            # LTI 一律不计盈亏，仅统计市值
            total["pnl_cny"] = None
            total["cost_cny"] = None
            total["ret_pct"] = None
        out[bucket] = {"by_currency": by_currency, "total_cny": total}

    grand_cny = sum(out[b]["total_cny"]["mv_cny"] for b in out)
    return {
        "buckets": out,
        "grand_total_cny": grand_cny,
        "grand_total_usd": grand_cny / usd_cny if usd_cny else 0.0,
    }


def yesterday_compare(today_total_cny, today_total_usd):
    """读取总额.csv 取上一交易日，返回与今日的对比。"""
    if not TOTAL_PATH.exists():
        return None
    with open(TOTAL_PATH, "r", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    today = datetime.now().strftime("%Y-%m-%d")
    prior = [r for r in rows if r.get("date") != today]
    if not prior:
        return None
    last = prior[-1]
    prev_cny = to_float(last.get("total_cny")) or 0.0
    delta = today_total_cny - prev_cny
    return {
        "yesterday_date": last.get("date"),
        "yesterday_cny": prev_cny,
        "yesterday_usd": to_float(last.get("total_usd")) or 0.0,
        "today_cny": today_total_cny,
        "today_usd": today_total_usd,
        "delta_cny": delta,
        "delta_pct": (delta / prev_cny * 100) if prev_cny else None,
    }


def upsert_total_history(results):
    """按日期覆盖写入总额.csv，避免同一天重复追加。"""
    usd_cny, hkd_cny = get_fx_rates()
    summary = summarize_totals(results, usd_cny, hkd_cny)
    fieldnames = [
        "date", "total_usd", "total_cny", "usd_mv", "hkd_mv", "cny_mv",
        "usd_cny_rate", "hkd_cny_rate", "pnl_usd_excl_lti", "pnl_cny_excl_lti",
    ]
    rows = []
    if TOTAL_PATH.exists():
        with open(TOTAL_PATH, "r", encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
    rows = [row for row in rows if row.get("date") != summary["date"]]
    rows.append({key: format_money(summary[key]) if key != "date" else summary[key] for key in fieldnames})
    rows.sort(key=lambda row: row["date"])
    TOTAL_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(TOTAL_PATH, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return summary
def process(rows, fund_mode="official"):
    results = []
    for row in rows:
        market = row["market"]
        shares_s = row.get("shares", "")
        cost_price_s = row.get("cost_price", "")
        cost_total_s = row.get("cost_total", "")
        last_mv_s = row.get("last_market_value", "")
        last_pnl_s = row.get("last_pnl", "")
        base = {
            "account": row["account"],
            "category": row["category"],
            "name": row["name"],
            "code": row["code"],
            "market": market,
            "currency": row["currency"],
            "note": row.get("note", ""),
        }
        # 股票：实时获取价格
        if market in ("US", "HK", "CN") and shares_s:
            try:
                shares = to_float(shares_s)
                cost_price = to_float(cost_price_s) or 0
                if shares is None:
                    raise ValueError(f"shares 字段无法解析为数字: {shares_s!r}")
                if market == "US":
                    price = fetch_us_price(row["code"])
                elif market == "HK":
                    price = fetch_hk_price(row["code"])
                else:
                    price = fetch_cn_price(row["code"])
                if price is not None:
                    tax_rate = parse_tax_rate(row.get("note", ""))
                    if tax_rate is not None:
                        # 未行权期权：市值 = 税后内在价值（价外时为0）
                        intrinsic = max(0.0, price - cost_price)
                        mv  = option_after_tax_value(price, cost_price, shares, tax_rate)
                        pnl = mv  # 税后净收益即为税后内在价值
                        pnl_pct = round(intrinsic / cost_price * 100 * (1 - tax_rate), 2) if cost_price > 0 else None
                        base.update({
                            "shares": shares, "cost_price": cost_price,
                            "cost_total": round(shares * cost_price, 2),
                            "price": round(price, 4), "market_value": mv,
                            "pnl": pnl, "pnl_pct": pnl_pct,
                            "tax_rate": tax_rate,
                            "intrinsic_gross": round((price - cost_price) * shares, 2),
                            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
                        })
                    else:
                        mv = round(price * shares, 2)
                        pnl = round((price - cost_price) * shares, 2)
                        pnl_pct = round((price - cost_price) / cost_price * 100, 2) if cost_price > 0 else None
                        base.update({
                            "shares": shares, "cost_price": cost_price,
                            "cost_total": round(shares * cost_price, 2),
                            "price": round(price, 4), "market_value": mv,
                            "pnl": pnl, "pnl_pct": pnl_pct,
                            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
                        })
                else:
                    base.update({
                        "shares": shares, "cost_price": cost_price,
                        "market_value": None, "pnl": None, "pnl_pct": None,
                        "error": "未获取到价格（休市或代码有误）",
                    })
            except Exception as e:
                base.update({
                    "shares": to_float(shares_s), "cost_price": to_float(cost_price_s) or 0,
                    "market_value": None, "pnl": None, "pnl_pct": None,
                    "error": str(e),
                })
        # 黄金克数持仓：按实时/最近 Au99.99 金价估值
        elif market == "FUND_CNY" and is_gold_gram_asset(row):
            cost = float(cost_total_s) if cost_total_s else 0
            try:
                priced = price_gold_gram_asset(row)
            except Exception as e:
                priced = None
                base["fund_pricing_error"] = str(e)
            if priced:
                shares = to_float(shares_s)
                mv = priced["market_value"]
                pnl = round(mv - cost, 2)
                pnl_pct = round(pnl / cost * 100, 2) if cost > 0 else None
                base.update({
                    "shares": shares,
                    "cost_price": to_float(cost_price_s),
                    "cost_total": cost,
                    "price": round(priced["price"], 4),
                    "market_value": mv,
                    "pnl": pnl,
                    "pnl_pct": pnl_pct,
                    "updated_at": priced.get("updated_at", ""),
                    "fund_source": priced.get("fund_source", ""),
                })
            else:
                mv = float(last_mv_s) if last_mv_s else 0
                pnl = float(last_pnl_s) if last_pnl_s else 0
                cost = float(cost_total_s) if cost_total_s else round(mv - pnl, 2)
                pnl_pct = round(pnl / cost * 100, 2) if cost > 0 else None
                base.update({
                    "shares": to_float(shares_s),
                    "cost_price": to_float(cost_price_s),
                    "cost_total": cost,
                    "price": None, "market_value": mv,
                    "pnl": pnl, "pnl_pct": pnl_pct,
                    "updated_at": row.get("last_updated", ""),
                    "fund_source": "snapshot",
                })
        # 人民币公募基金：有 code + shares 时自动按净值估值；失败回退 CSV 快照
        elif market == "FUND_CNY" and row.get("category") == "基金":
            cost = float(cost_total_s) if cost_total_s else 0
            try:
                priced = price_cny_fund(row, fund_mode)
            except Exception as e:
                priced = None
                base["fund_pricing_error"] = str(e)
            if priced and priced.get("market_value") is not None:
                shares = to_float(shares_s)
                mv = priced["market_value"]
                pnl = round(mv - cost, 2)
                pnl_pct = round(pnl / cost * 100, 2) if cost > 0 else None
                base.update({
                    "shares": shares,
                    "cost_price": None,
                    "cost_total": cost,
                    "price": round(priced["price"], 6),
                    "market_value": mv,
                    "pnl": pnl,
                    "pnl_pct": pnl_pct,
                    "updated_at": priced.get("updated_at", ""),
                    "fund_source": priced.get("fund_source", ""),
                })
                for key in ("nav_date", "estimated_pct", "fund_name_from_source"):
                    if priced.get(key):
                        base[key] = priced[key]
            else:
                mv = float(last_mv_s) if last_mv_s else 0
                pnl = float(last_pnl_s) if last_pnl_s else 0
                cost = float(cost_total_s) if cost_total_s else round(mv - pnl, 2)
                pnl_pct = round(pnl / cost * 100, 2) if cost > 0 else None
                base.update({
                    "shares": to_float(shares_s),
                    "cost_price": None, "cost_total": cost,
                    "price": None, "market_value": mv,
                    "pnl": pnl, "pnl_pct": pnl_pct,
                    "updated_at": row.get("last_updated", ""),
                    "fund_source": "snapshot",
                })
                if priced and priced.get("mismatch"):
                    base["fund_pricing_error"] = "基金代码与行情源名称不匹配，已回退快照"
                    base["fund_name_from_source"] = priced.get("fund_name_from_source", "")
        # 港元/美元基金：优先用 KGI 最新净值；失败再回退 CSV 快照
        elif market in ("FUND_HKD", "FUND_USD") and row.get("category") == "基金":
            cost = float(cost_total_s) if cost_total_s else 0
            try:
                priced = price_offshore_fund(row)
            except Exception as e:
                priced = None
                base["fund_pricing_error"] = str(e)
            if priced and priced.get("market_value") is not None:
                shares = to_float(shares_s)
                mv = priced["market_value"]
                pnl = round(mv - cost, 2)
                pnl_pct = round(pnl / cost * 100, 2) if cost > 0 else None
                base.update({
                    "shares": shares,
                    "cost_price": None,
                    "cost_total": cost,
                    "price": round(priced["price"], 6),
                    "market_value": mv,
                    "pnl": pnl,
                    "pnl_pct": pnl_pct,
                    "updated_at": priced.get("updated_at", ""),
                    "fund_source": priced.get("fund_source", ""),
                })
                for key in ("nav_date", "source_url"):
                    if priced.get(key):
                        base[key] = priced[key]
            else:
                mv = float(last_mv_s) if last_mv_s else 0
                pnl = float(last_pnl_s) if last_pnl_s else 0
                cost = float(cost_total_s) if cost_total_s else round(mv - pnl, 2)
                pnl_pct = round(pnl / cost * 100, 2) if cost > 0 else None
                base.update({
                    "shares": to_float(shares_s), "cost_price": None, "cost_total": cost,
                    "price": None, "market_value": mv,
                    "pnl": pnl, "pnl_pct": pnl_pct,
                    "updated_at": row.get("last_updated", ""),
                    "fund_source": "snapshot",
                })
        # 其他基金/活期：使用 CSV 存储值
        else:
            mv = float(last_mv_s) if last_mv_s else 0
            pnl = float(last_pnl_s) if last_pnl_s else 0
            cost = float(cost_total_s) if cost_total_s else round(mv - pnl, 2)
            pnl_pct = round(pnl / cost * 100, 2) if cost > 0 else None
            base.update({
                "shares": to_float(shares_s), "cost_price": None, "cost_total": cost,
                "price": None, "market_value": mv,
                "pnl": pnl, "pnl_pct": pnl_pct,
                "updated_at": row.get("last_updated", ""),
                "fund_source": "snapshot" if market.startswith("FUND_") else "",
            })
        results.append(base)
    return results
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--fund-mode",
        choices=("official", "estimate"),
        default="estimate",
        help="estimate=盘中估算净值优先，失败回退正式净值/快照; official=最新公布净值",
    )
    parser.add_argument(
        "--write-back-funds",
        action="store_true",
        help="将本次成功获取到的基金估值写回 ~/Desktop/持仓/明细.csv",
    )
    parser.add_argument(
        "--no-write-history",
        action="store_true",
        help="不将本次总资产汇总写入 ~/Desktop/持仓/总额.csv",
    )
    parser.add_argument(
        "--report",
        action="store_true",
        help="输出报告所需的一揽子结构：fx 汇率 + 五口径汇总 + 昨日对比 + 明细，免去内联聚合代码",
    )
    args = parser.parse_args()
    try:
        import akshare
    except ImportError:
        print(json.dumps({"error": "请先安装依赖: pip3 install akshare"}))
        sys.exit(1)
    rows = load_csv()
    if rows is None:
        print(json.dumps({
            "error": f"CSV文件不存在: {CSV_PATH}",
            "hint": "首次使用可先运行: python3 ~/.claude/skills/stock-portfolio-valuation/scripts/init_portfolio.py",
            "hint_2": "也可以直接提供股票/基金截图或自然语言持仓信息，由 skill 帮你初始化",
        }, ensure_ascii=False))
        sys.exit(1)
    results = process(rows, fund_mode=args.fund_mode)
    history_summary = None if args.no_write_history else upsert_total_history(results)
    if args.report:
        if history_summary is not None:
            usd_cny = history_summary["usd_cny_rate"]
            hkd_cny = history_summary["hkd_cny_rate"]
        else:
            usd_cny, hkd_cny = get_fx_rates()
        totals = history_summary or summarize_totals(results, usd_cny, hkd_cny)
        buckets = summarize_buckets(results, usd_cny, hkd_cny)
        payload = {
            "fx": {
                "usd_cny": usd_cny,
                "hkd_cny": hkd_cny,
                "usd_hkd": usd_cny / hkd_cny if hkd_cny else None,
                "date": datetime.now().strftime("%Y-%m-%d"),
            },
            "summary": buckets,
            "totals": totals,
            "compare": yesterday_compare(totals["total_cny"], totals["total_usd"]),
            "results": results,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        sys.exit(0)
    if args.write_back_funds:
        changed = write_back_funds(rows, results)
        payload = {
            "write_back_funds_changed": changed,
            "history_upserted": history_summary,
            "results": results,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(results, ensure_ascii=False, indent=2))
