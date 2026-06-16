# stock-portfolio-valuation

> Language: [中文](README.md) | English

A Claude Code skill for personal portfolio valuation. It initializes holdings from natural language or screenshots, normalizes them into a local CSV, fetches market prices, and generates valuation, P&L, cash, and LTI summaries across USD, HKD, and CNY assets.
It also supports retirement-planning prompts such as `我什么时候可以不上班`, and can persist personal planning inputs into a local `profile.json`.

## Requirements

- Python 3.9+
- Install dependencies:

```bash
pip3 install -r requirements.txt
```

Dependencies: `akshare` (A-share / HK / US quotes, fund NAV, FX, SGE gold), `requests`, `beautifulsoup4`, `pandas`.

## Configuration

By default all data lives in `~/Desktop/持仓`. To use a different folder, set the `PORTFOLIO_DIR` environment variable; every script honors it and falls back to the default when unset:

```bash
export PORTFOLIO_DIR=/path/to/your/portfolio
```

Throughout this README, `~/Desktop/持仓` refers to this portfolio folder (its default location).

## Scope & Disclaimer

- **Markets:** US / HK / A-share stocks, plus funds priced in CNY / HKD / USD. Quotes come from `akshare` (China-accessible sources), KGI, and Stock Events — so the skill is geared toward investors holding A-share / HK / US assets.
- **Retirement projection assumes China urban social security** (default parameters tuned to Beijing: social-average wage, 个人账户计发月数, 缴费指数). It is a **planning-grade estimate** — by default it does not model investment returns or inflation, and it is **not** an actuarial calculation.
- This tool is for personal bookkeeping and planning only. It is **not financial advice**, and it never places trades or moves money.

## Files

- `SKILL.md`: skill instructions and workflow
- `requirements.txt`: Python dependencies
- `scripts/init_portfolio.py`: creates the skeleton CSV / optional files in the portfolio folder
- `scripts/fetch_prices.py`: fetches prices and computes the valuation JSON (the core script)
- `scripts/profile_manager.py`: saves / shows the retirement `profile.json`
- `scripts/retirement_projection.py`: estimates the earliest stop-working age/month and pension
- `scripts/archive_reports.py`: archives calculation outputs into the portfolio folder
- `scripts/hk_ipo_ytd.py`: fetches HK IPO year-to-date performance and calculates one-lot returns
- `tests/test_core.py`: hermetic unit tests for the core calculations (no network, no real data)

## Init

First-time setup can start from natural language plus screenshots.

Typical ways to initialize:

- Send a message like `持仓` / `帮我初始化持仓`
- Paste stock or fund account screenshots
- Describe holdings in natural language, such as buy price, shares, fund market value, or cash balance

The skill will extract the information and normalize it into the local CSV.

If you want to initialize manually, only one required file is needed:

```bash
mkdir -p ~/Desktop/持仓
```

Create `~/Desktop/持仓/明细.csv` with:

```csv
account,category,name,code,market,currency,shares,cost_price,cost_total,last_market_value,last_pnl,last_updated,note
```

Common values:

- `account`: any free-text label — e.g. `富途` / `支付宝` / `腾讯理财通` / `LTI`. The `LTI` account is treated specially: its market value counts toward net worth, but its P&L is excluded from return statistics.
- `category`: `股票` / `基金` / `活期` / `存款` / `应收款` / `社保` / `加密货币`
- `market`: `US` / `HK` / `CN` / `FUND_USD` / `FUND_HKD` / `FUND_CNY`

Other files are optional and can be added later:

- `~/Desktop/持仓/总额.csv` — daily total-asset history (written automatically by each valuation run)
- `~/Desktop/持仓/未来现金流.csv` — future cash flows (annuity income, premiums) used by the retirement projection
- `~/Desktop/持仓/profile.json` — retirement-planning inputs
- `~/Desktop/持仓/已实现盈亏.csv` — **reserved**: a realized-P&L ledger skeleton created by `--with-optional-files`, not yet consumed by any report

## Retirement Profile

When you ask something like `我什么时候可以不上班`, the skill can save or reuse these profile fields:

- `birth_year_month`
- `years_worked`
- `annual_spending_cny`
- `annual_savings_cny`

Optional planning fields:

- `retirement_age`
- `lifespan_age`
- `current_social_security_personal_account_cny`
- `historical_contribution_multiple`
- `future_contribution_multiple`
- `social_avg_monthly_salary_cny_today` — local social-average monthly wage used for the pension base (default `12000`, tuned to Beijing; **non-Beijing users should set their own city's value**), updatable with `--social-avg-monthly-salary-cny-today`. The legacy key `beijing_average_monthly_salary_cny_today` is still read for backward compatibility.
- `personal_account_interest_rate` — annual interest rate credited to the social-security personal account (default `0.03`)

You can let the skill save them from natural language, or update them manually with:

```bash
python3 scripts/profile_manager.py --birth-year-month YYYY-MM --years-worked N \
  --annual-spending-cny ANNUAL_SPENDING --annual-savings-cny ANNUAL_SAVINGS
python3 scripts/profile_manager.py --show
```

Typical natural-language inputs:

- `我是90后，出生年月是YYYY-MM`
- `我大概工作了十几年`
- `我每年大致消费几十万`
- `我每年能攒几十万`
- `我的社保个人账户现在大约有一笔余额`
- `之前按较高档位交，未来计划继续按更高档位交`

## Run

```bash
python3 scripts/init_portfolio.py
python3 scripts/init_portfolio.py --with-optional-files
python3 scripts/profile_manager.py --show
python3 scripts/retirement_projection.py
python3 scripts/retirement_projection.py --stop-age 46
python3 scripts/fetch_prices.py
python3 scripts/fetch_prices.py --fund-mode official
python3 scripts/fetch_prices.py --fund-mode estimate --write-back-funds
python3 scripts/archive_reports.py --from-tmp
python3 scripts/hk_ipo_ytd.py --year 2026
```

Notes:

- `FUND_CNY` funds prefer intraday estimated NAV, then fall back to official NAV.
- `FUND_HKD` and `FUND_USD` funds now try to fetch latest NAV from KGI fund-detail pages first, then Stock Events `.FUND` pages.
- If an offshore fund cannot be resolved from the online source, the skill falls back to the local snapshot stored in `明细.csv`.
- Use `--write-back-funds` when you want successful fund refreshes saved back into `~/Desktop/持仓/明细.csv` for future runs.
- Each valuation run writes or replaces the same-day row in `~/Desktop/持仓/总额.csv`; use `--no-write-history` for dry debug runs.
- Use `scripts/archive_reports.py` to persist temporary valuation, retirement, or IPO outputs under `~/Desktop/持仓/测算归档`.
- Use `scripts/hk_ipo_ytd.py` for HK IPO one-lot first-day returns, hold-to-now returns, win/loss counts, and "稳中一手" follow-up analysis.

## Use

Typical prompts:

- `持仓`
- `帮我根据截图初始化持仓`
- `刷新持仓估值`
- `完整持仓估值报告`
- `累计盈亏`
- `可立即动用的现金有多少`
- `LTI 年初 vs 现在`
- `今年港股打新清单`
- `港股打新全中一手收益`
- `我什么时候可以不上班`
- `如果我在某个年龄不上班，退休时能领多少退休金`

Workflow:

1. Initialize from natural language or screenshots, or update `~/Desktop/持仓/明细.csv`
2. Run the script or ask for valuation in chat
3. Review holdings, P&L, cash, LTI summaries, and the appended retirement countdown

For retirement planning:

1. Save or update `~/Desktop/持仓/profile.json`
2. Keep `~/Desktop/持仓/总额.csv` and `~/Desktop/持仓/未来现金流.csv` updated
3. Run `scripts/retirement_projection.py` or ask in chat

When the prompt is a broad portfolio request such as `持仓`, `持仓总值`, `持仓分析`, or `完整持仓估值报告`, the skill should also run:

```bash
python3 scripts/retirement_projection.py --current-total-assets-cny CURRENT_TOTAL_ASSETS
```

This appends a short retirement countdown based on the freshly calculated portfolio total instead of an older `总额.csv` snapshot.

## Calculation Archives

Persist temporary outputs into the local portfolio folder:

```bash
python3 scripts/archive_reports.py --from-tmp
python3 scripts/archive_reports.py --valuation-json /path/to/valuation.json
python3 scripts/archive_reports.py --retirement-json /path/to/retirement.json
python3 scripts/archive_reports.py --ipo-csv /path/to/hk_ipo.csv
```

Archive root:

```text
~/Desktop/持仓/测算归档
```

Files are grouped into `估值快照`, `退休测算`, and `港股IPO`.

## HK IPO Analysis

Fetch the current year's listed HK IPO table and calculate one-lot returns:

```bash
python3 scripts/hk_ipo_ytd.py --year 2026
```

The script writes:

```text
~/Desktop/持仓/测算归档/港股IPO/hk_ipo_ytd.csv
```

It includes listing date, ticker, one-lot size, listing price, first-day return, estimated first-day close, one-lot first-day P&L, latest price, one-lot current P&L, and current return.

## Retirement Countdown

Typical prompts:

- `距离退休还有多久？`
- `退休倒计时版`
- `什么时候可以不上班？`
- `距离不上班还差多少钱，预计还需要多久？`
- `做一个每年还差多少钱的倒计时表`

CLI usage:

```bash
python3 scripts/retirement_projection.py
python3 scripts/retirement_projection.py --stop-age TARGET_AGE
python3 scripts/retirement_projection.py --stop-month YYYY-MM
python3 scripts/retirement_projection.py --current-total-assets-cny CURRENT_TOTAL_ASSETS
```

What the output includes:

- `current_age_years`: your current age
- `current_total_assets_cny`: the asset base used in the calculation
- `bridge_assets_excluding_social_security_cny`: current assets excluding the social-security account used as future pension source
- `earliest_no_work_projection`: the earliest stop-working age that still keeps assets above zero through the target lifespan
- `earliest_no_work_month_projection`: the earliest stop-working month using monthly cash-flow simulation
- `nearby_month_scenarios`: boundary months around the earliest feasible month
- `scenario_table`: reference scenarios such as several stop-working ages and retirement age
- `annual_pension_cny`: estimated annual pension at retirement
- `monthly_pension_cny`: estimated monthly pension at retirement
- `projected_social_security_account_cny`: projected personal social-security account balance at retirement
- `yearly_projection`: yearly gap and ending assets after stopping work

Example summary in chat:

```text
最早大约在某个年龄可以不上班
距离不上班还差一段资产缺口
预计还需要若干年
退休后每年需要动用一部分存款
```

Example CLI output shape:

```json
{
  "current_date": "YYYY-MM-DD",
  "current_age_years": 30.0,
  "current_total_assets_cny": 3500000.0,
  "bridge_assets_excluding_social_security_cny": 3200000.0,
  "earliest_no_work_projection": {
    "stop_age": 50,
    "assets_at_stop_cny": 9000000.0,
    "ending_assets_cny": 500000.0,
    "annual_pension_cny": 150000.0
  },
  "earliest_no_work_month_projection": {
    "stop_month": "YYYY-MM",
    "stop_age": "50岁0个月",
    "months_until_stop": 120,
    "assets_at_stop_cny": 9000000.0,
    "ending_assets_cny": 500000.0,
    "monthly_pension_cny": 12500.0
  }
}
```

## Tests

Hermetic unit tests (no network, no real data) cover the core calculations — bucket classification, currency aggregation, after-tax option intrinsic value, fund-name matching, gold detection, and the pension / age formulas:

```bash
python3 -m unittest discover -s tests
```

## Privacy & Security

- This skill is designed for local personal finance workflows. Source data stays in local files such as `~/Desktop/持仓/明细.csv` (or `$PORTFOLIO_DIR`).
- **Filesystem:** scripts read and write only inside the portfolio folder (`~/Desktop/持仓` by default, or `$PORTFOLIO_DIR`).
- **Network:** scripts make outbound HTTPS requests purely to fetch market data — akshare upstreams (Sina / EastMoney / SGE), `kgi.com.hk`, `stockevents.app`, `open.er-api.com`, and `aastocks.com`. No portfolio data is uploaded; requests carry only public tickers / fund codes.
- **No secrets, no telemetry:** the skill stores no API keys or credentials and sends no analytics.
- Example screenshots in this repository are privacy-obfuscated mockups, not real holdings data.
- Do not commit real portfolio CSV files, screenshots, `profile.json`, or personal identifiers into a public repository — `.gitignore` already excludes these.

## Examples

Privacy-obfuscated bookkeeping mockup:

![Privacy-obfuscated bookkeeping mockup](assets/privacy-obfuscated-bookkeeping-mockup.png)

Privacy-obfuscated valuation report mockup:

![Privacy-obfuscated valuation report mockup](assets/privacy-obfuscated-valuation-report.png)
