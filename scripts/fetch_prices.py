#!/usr/bin/env python3
"""
持仓实时估值 - 从 ~/Desktop/持仓/明细.csv 加载持仓，实时获取股票价格，输出 JSON
用法:
  python3 fetch_prices.py
  python3 fetch_prices.py --fund-mode official
"""
import sys, json, csv, re, argparse
from pathlib import Path
from datetime import datetime, timedelta

def parse_tax_rate(note: str):
    """从 note 中提取税率，如「税率20%」→ 0.20，未找到返回 None"""
    m = re.search(r'税率\s*(\d+(?:\.\d+)?)\s*%', note or "")
    return float(m.group(1)) / 100 if m else None
CSV_PATH = Path.home() / "Desktop/持仓/明细.csv"
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
    """粗校验基金代码是否指向同一只基金，避免代码填错时误用净值。"""
    expected = re.sub(r"[\s（）()·\-_/]", "", expected or "")
    actual = re.sub(r"[\s（）()·\-_/]", "", actual or "")
    if not expected or not actual:
        return False
    if expected[:2] == actual[:2]:
        return True
    keywords = ["人工智能", "半导体", "机器人", "卫星", "新能源", "创业板", "证券", "黄金"]
    return any(k in expected and k in actual for k in keywords)
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
    note = row.get("note", "")
    if "黄金克数" not in note:
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
def load_csv():
    if not CSV_PATH.exists():
        return None
    rows = []
    with open(CSV_PATH, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append({k: v.strip() for k, v in row.items()})
    return rows
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
            shares = float(shares_s)
            cost_price = float(cost_price_s) if cost_price_s else 0
            try:
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
                        mv  = round(intrinsic * shares * (1 - tax_rate), 2)
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
                    "shares": shares, "cost_price": cost_price,
                    "market_value": None, "pnl": None, "pnl_pct": None,
                    "error": str(e),
                })
        # 黄金克数持仓：按实时/最近 Au99.99 金价估值
        elif market == "FUND_CNY" and "黄金克数" in row.get("note", ""):
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
    print(json.dumps(results, ensure_ascii=False, indent=2))
