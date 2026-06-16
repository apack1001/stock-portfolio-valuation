# stock-portfolio-valuation

> 语言：中文 | [English](README.en.md)

一个用于个人持仓估值的 Claude Code skill。它能从自然语言或截图初始化持仓，归一化为本地 CSV，获取市场行情，并生成覆盖美元、港元、人民币资产的估值、盈亏、现金与 LTI 汇总。
它还支持退休规划类提问（如 `我什么时候可以不上班`），并可将个人规划参数持久化到本地 `profile.json`。

## 环境要求

- Python 3.9+
- 安装依赖：

```bash
pip3 install -r requirements.txt
```

依赖：`akshare`（A股 / 港股 / 美股行情、基金净值、汇率、上金所黄金）、`requests`、`beautifulsoup4`、`pandas`。

## 配置

默认所有数据都存放在 `~/Desktop/持仓`。如需使用其他目录，设置环境变量 `PORTFOLIO_DIR`，所有脚本都会读取它，未设置时回退到默认目录：

```bash
export PORTFOLIO_DIR=/path/to/your/portfolio
```

本文中的 `~/Desktop/持仓` 均指这个持仓数据目录（其默认位置）。

## 适用范围与免责声明

- **市场**：美股 / 港股 / A股股票，以及以人民币 / 港元 / 美元计价的基金。行情来自 `akshare`（中国大陆可访问的数据源）、凯基（KGI）和 Stock Events——因此本 skill 面向持有 A股 / 港股 / 美股资产的投资者。
- **退休测算基于中国城镇职工社保**（默认参数按北京调校：社会平均工资、个人账户计发月数、缴费指数）。它是**规划级估算**——默认不建模投资收益或通胀，**不是**精算结果。
- 本工具仅用于个人记账与规划，**不构成投资建议**，也绝不执行交易或转移资金。

## 文件说明

- `SKILL.md`：skill 指令与工作流
- `requirements.txt`：Python 依赖
- `scripts/init_portfolio.py`：在持仓目录创建骨架 CSV / 可选文件
- `scripts/fetch_prices.py`：获取行情并计算估值 JSON（核心脚本）
- `scripts/profile_manager.py`：保存 / 查看退休 `profile.json`
- `scripts/retirement_projection.py`：测算最早可停工的年龄/月份及养老金
- `scripts/archive_reports.py`：将测算结果归档到持仓目录
- `scripts/hk_ipo_ytd.py`：获取港股年内打新表现并计算一手收益
- `tests/test_core.py`：核心计算的隔离单元测试（无网络、无真实数据）

## 初始化

首次使用可以从自然语言加截图开始。

常见初始化方式：

- 发送类似 `持仓` / `帮我初始化持仓` 的消息
- 粘贴股票或基金账户截图
- 用自然语言描述持仓，例如买入价、股数、基金市值或现金余额

skill 会提取信息并归一化为本地 CSV。

如果想手动初始化，只需一个必需文件：

```bash
mkdir -p ~/Desktop/持仓
```

创建 `~/Desktop/持仓/明细.csv`，表头为：

```csv
account,category,name,code,market,currency,shares,cost_price,cost_total,last_market_value,last_pnl,last_updated,note
```

常用取值：

- `account`：任意自由文本标签——如 `富途` / `支付宝` / `腾讯理财通` / `LTI`。`LTI` 账户会被特殊处理：其市值计入总身家，但盈亏不计入收益统计。
- `category`：`股票` / `基金` / `活期` / `存款` / `应收款` / `社保` / `加密货币`
- `market`：`US` / `HK` / `CN` / `FUND_USD` / `FUND_HKD` / `FUND_CNY`

其他文件均为可选，可后续添加：

- `~/Desktop/持仓/总额.csv` — 每日总资产历史（每次估值运行自动写入）
- `~/Desktop/持仓/未来现金流.csv` — 退休测算使用的未来现金流（年金领取、保费）
- `~/Desktop/持仓/profile.json` — 退休规划参数
- `~/Desktop/持仓/已实现盈亏.csv` — **预留**：由 `--with-optional-files` 创建的已实现盈亏台账骨架，目前尚未被任何报告使用

## 退休画像

当你问类似 `我什么时候可以不上班` 时，skill 可保存或复用以下画像字段：

- `birth_year_month`
- `years_worked`
- `annual_spending_cny`
- `annual_savings_cny`

可选规划字段：

- `retirement_age`
- `lifespan_age`
- `current_social_security_personal_account_cny`
- `historical_contribution_multiple`
- `future_contribution_multiple`
- `social_avg_monthly_salary_cny_today` — 用于养老金基数的当地社会平均月工资（默认 `12000`，按北京调校；**非北京用户应设置本城市的数值**），可用 `--social-avg-monthly-salary-cny-today` 更新。旧字段名 `beijing_average_monthly_salary_cny_today` 仍向后兼容读取。
- `personal_account_interest_rate` — 社保个人账户的年记账利率（默认 `0.03`）

你可以让 skill 从自然语言中保存这些字段，或手动更新：

```bash
python3 scripts/profile_manager.py --birth-year-month YYYY-MM --years-worked N \
  --annual-spending-cny ANNUAL_SPENDING --annual-savings-cny ANNUAL_SAVINGS
python3 scripts/profile_manager.py --show
```

常见自然语言输入：

- `我是90后，出生年月是YYYY-MM`
- `我大概工作了十几年`
- `我每年大致消费几十万`
- `我每年能攒几十万`
- `我的社保个人账户现在大约有一笔余额`
- `之前按较高档位交，未来计划继续按更高档位交`

## 运行

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

说明：

- `FUND_CNY` 基金优先使用盘中估算净值，再回退到正式净值。
- `FUND_HKD` 和 `FUND_USD` 基金现在先尝试从凯基（KGI）基金详情页获取最新净值，再尝试 Stock Events 的 `.FUND` 页面。
- 若离岸基金无法从在线数据源解析，skill 回退到 `明细.csv` 中存储的本地快照。
- 当你希望把成功刷新的基金净值写回 `~/Desktop/持仓/明细.csv` 供后续运行使用时，加 `--write-back-funds`。
- 每次估值运行会写入或覆盖 `~/Desktop/持仓/总额.csv` 中当天那一行；调试空跑用 `--no-write-history`。
- 用 `scripts/archive_reports.py` 把临时的估值、退休或 IPO 结果持久化到 `~/Desktop/持仓/测算归档`。
- 用 `scripts/hk_ipo_ytd.py` 计算港股打新一手首日收益、持有至今收益、盈亏家数，以及“稳中一手”跟投分析。

## 使用

常见提问：

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

工作流：

1. 从自然语言或截图初始化，或更新 `~/Desktop/持仓/明细.csv`
2. 运行脚本或在对话中请求估值
3. 查看持仓、盈亏、现金、LTI 汇总，以及附加的退休倒计时

退休规划：

1. 保存或更新 `~/Desktop/持仓/profile.json`
2. 保持 `~/Desktop/持仓/总额.csv` 与 `~/Desktop/持仓/未来现金流.csv` 更新
3. 运行 `scripts/retirement_projection.py` 或在对话中询问

当提问是宽泛的持仓请求（如 `持仓`、`持仓总值`、`持仓分析`、`完整持仓估值报告`）时，skill 还应运行：

```bash
python3 scripts/retirement_projection.py --current-total-assets-cny CURRENT_TOTAL_ASSETS
```

这会基于刚算出的组合总额（而非较旧的 `总额.csv` 快照）附加一段简短的退休倒计时。

## 测算归档

把临时结果持久化到本地持仓目录：

```bash
python3 scripts/archive_reports.py --from-tmp
python3 scripts/archive_reports.py --valuation-json /path/to/valuation.json
python3 scripts/archive_reports.py --retirement-json /path/to/retirement.json
python3 scripts/archive_reports.py --ipo-csv /path/to/hk_ipo.csv
```

归档根目录：

```text
~/Desktop/持仓/测算归档
```

文件按 `估值快照`、`退休测算`、`港股IPO` 分组。

## 港股打新分析

获取当年已上市港股打新表并计算一手收益：

```bash
python3 scripts/hk_ipo_ytd.py --year 2026
```

脚本写入：

```text
~/Desktop/持仓/测算归档/港股IPO/hk_ipo_ytd.csv
```

包含上市日期、代号、每手股数、上市价、首日涨跌、估算首日收盘价、一手首日盈亏、最新价、一手当前盈亏、当前收益率。

## 退休倒计时

常见提问：

- `距离退休还有多久？`
- `退休倒计时版`
- `什么时候可以不上班？`
- `距离不上班还差多少钱，预计还需要多久？`
- `做一个每年还差多少钱的倒计时表`

CLI 用法：

```bash
python3 scripts/retirement_projection.py
python3 scripts/retirement_projection.py --stop-age TARGET_AGE
python3 scripts/retirement_projection.py --stop-month YYYY-MM
python3 scripts/retirement_projection.py --current-total-assets-cny CURRENT_TOTAL_ASSETS
```

输出包含：

- `current_age_years`：你的当前年龄
- `current_total_assets_cny`：计算所用的资产基数
- `bridge_assets_excluding_social_security_cny`：剔除作为未来养老金来源的社保账户后的当前资产
- `earliest_no_work_projection`：在目标寿命内仍能保持资产为正的最早停工年龄
- `earliest_no_work_month_projection`：用按月现金流模拟得出的最早停工月份
- `nearby_month_scenarios`：最早可行月份附近的边界月份
- `scenario_table`：参考情景，如若干停工年龄与法定退休年龄
- `annual_pension_cny`：退休时的预计年养老金
- `monthly_pension_cny`：退休时的预计月养老金
- `projected_social_security_account_cny`：退休时预计的社保个人账户余额
- `yearly_projection`：停工后逐年的缺口与年末资产

对话中的示例摘要：

```text
最早大约在某个年龄可以不上班
距离不上班还差一段资产缺口
预计还需要若干年
退休后每年需要动用一部分存款
```

CLI 输出示例结构：

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

## 测试

隔离单元测试（无网络、无真实数据）覆盖核心计算——口径分类、币种汇总、期权税后内在价值、基金名称匹配、黄金识别，以及养老金 / 年龄公式：

```bash
python3 -m unittest discover -s tests
```

## 隐私与安全

- 本 skill 面向本地个人理财工作流设计。源数据保留在本地文件中，如 `~/Desktop/持仓/明细.csv`（或 `$PORTFOLIO_DIR`）。
- **文件系统**：脚本仅在持仓目录内读写（默认 `~/Desktop/持仓`，或 `$PORTFOLIO_DIR`）。
- **网络**：脚本发起的出站 HTTPS 请求仅用于获取市场数据——akshare 上游（新浪 / 东方财富 / 上金所）、`kgi.com.hk`、`stockevents.app`、`open.er-api.com` 和 `aastocks.com`。不上传任何持仓数据；请求只携带公开的股票代码 / 基金代码。
- **无密钥、无遥测**：本 skill 不存储任何 API 密钥或凭证，也不发送任何分析数据。
- 本仓库中的示例截图为隐私脱敏的样例，并非真实持仓数据。
- 不要把真实的持仓 CSV、截图、`profile.json` 或个人标识提交到公开仓库——`.gitignore` 已排除这些。

## 示例

隐私脱敏的记账样例：

![隐私脱敏的记账样例](assets/privacy-obfuscated-bookkeeping-mockup.png)

隐私脱敏的估值报告样例：

![隐私脱敏的估值报告样例](assets/privacy-obfuscated-valuation-report.png)
