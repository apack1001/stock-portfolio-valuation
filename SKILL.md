---
name: stock-portfolio-valuation
description: 从持仓CSV或本地截图读取持仓数据，支持首次初始化、实时获取最新行情，自动计算总估值、盈亏金额和盈亏比例，生成完整持仓报告。支持美股、港股、A股、基金（支付宝/腾讯理财通/富途理财）。也支持退休测算，当用户提到"我什么时候可以不上班"、"几岁可以退休"时触发。
license: MIT
metadata:
  author: chenyuan1
  version: 1.0.0
---
# 股票持仓实时估值

## 脚本路径约定

本 skill 的所有脚本位于其基目录下的 `scripts/`。基目录在加载时以「Base directory for this skill: <绝对路径>」给出；作为插件（plugin）安装时即环境变量 `CLAUDE_PLUGIN_ROOT` 指向的目录。

**下文命令中的 `${SKILL_DIR}` 是占位符，执行前必须替换为上述基目录的真实绝对路径**（个人安装通常为 `~/.claude/skills/stock-portfolio-valuation`，插件安装为 `CLAUDE_PLUGIN_ROOT`）。不要把 `${SKILL_DIR}` 原样传给 shell。

## 数据源
持仓数据的**唯一可信来源**是 `~/Desktop/持仓/明细.csv`。
- 未来收入/支出计划记录在 `~/Desktop/持仓/未来现金流.csv`，例如年金保险领取、保费支出、未来确定性款项；这类现金流**不计入当前持仓市值**，只在退休/长期现金流测算中使用
- 退休测算画像记录在 `~/Desktop/持仓/profile.json`，例如出生年月、工作年限、每年支出、每年可新增储蓄、社保相关参数
- 交易纪律记录在 `~/Desktop/持仓/交易纪律.json`，例如减仓优先级、触发价位、每档卖出股数和风险规则；该文件只记录策略，不代表已执行交易
- 测算归档保存在 `~/Desktop/持仓/测算归档/`，用于保存估值快照、退休测算、港股IPO清单等历史测算结果，避免只落在 `/tmp`
- **非必要不从图片重新导入**：CSV 存在则直接加载
- **每次买入/卖出/基金变动后立即更新 CSV**，再生成报告
- 基金市值优先用脚本刷新最新净值；截图只作为自动源失败时的补录来源
---
## 富途持仓自动同步（可选）
当用户说"同步富途持仓"、"从富途拉持仓/对账"、"富途账户对一下账"时触发。需用户本地已运行 Futu OpenD 网关并登录、且已 `pip3 install futu-api`；否则脚本会优雅降级并提示，不影响其它功能。

```bash
python3 ${SKILL_DIR}/scripts/fetch_futu_positions.py            # 只读对账，输出 JSON
python3 ${SKILL_DIR}/scripts/fetch_futu_positions.py --summary  # 人类可读对账表
python3 ${SKILL_DIR}/scripts/fetch_futu_positions.py --write-back  # 保守写回 明细.csv
```

要点：
- **实时数据优先**：脚本输出 `account_summary`（accinfo：总资产/证券/基金/现金，HKD 视图）与 `today_pl_by_currency`（来自富途 `today_pl_val`，与富途 App 的"今日盈亏"同源）。**富途持仓的今日盈亏、实时价、账户总额一律以此为准，不要再用 akshare 日线估算**（日线滞后、口径不符）。账户级基金市值也能从 accinfo 实时拿到（但拿不到逐只场外基金净值）。
- **只读**：仅查询持仓/资金，绝不下单或解锁交易；符合"绝不交易"原则。
- **仅覆盖证券**（股票/ETF场内/期权/期货）的逐笔持仓，**不提供逐只场外公募基金净值**——基金逐只仍走盘中估算/官方净值，T+1 是基金固有属性。
- `--write-back` 保守：同一代码若在 LTI 等其它账户也持有（普通股+激励双重持仓），跳过自动写、仅标记，避免错并 Futu 合计股数；CSV 有而 Futu 查不到的富途行只标记疑似清仓，绝不自动删。
- 同步后可再跑 `${SKILL_DIR}/scripts/fetch_prices.py --report` 生成最新估值。

当用户说"补历史已实现盈亏"、"审计过往买卖"、"导入富途成交"时，用历史成交脚本：

```bash
python3 ${SKILL_DIR}/scripts/fetch_futu_deals.py --start 2024-01-01 --summary  # 只读：FIFO 算每笔已实现盈亏
python3 ${SKILL_DIR}/scripts/fetch_futu_deals.py --start 2024-01-01 --write-ledger  # 写入 已实现盈亏.csv（按 日期+代码+金额 去重）
```

成交脚本要点：
- `history_deal_list_query` **限频每30秒10次**，脚本已内置节流（每次间隔 3.2s），全历史会跑约 1 分钟。
- 富途**历史成交仅可回溯约 2 年**；更早的买入查不到，对应卖出会标 `needs_manual`（不臆造成本），需人工补。
- **仅证券成交**：不含转仓（如股票转户）、基金申赎、支付宝/腾讯交易。
- USD/HKD 等不同币种**分开合计**，不可混加。
- 写入台账前按 日期+代码+金额去重；但若已有记录是"按标的累计"口径（与逐笔金额对不上），仍可能语义重复，需人工判断。
---
## 执行步骤
### Step 1：确定数据来源
**A. 首次使用，CSV 不存在**：
- 先运行初始化脚本创建骨架文件：
```bash
python3 ${SKILL_DIR}/scripts/init_portfolio.py
```
- 然后优先通过**自然语言或截图**补齐持仓
  - 示例自然语言：`富途有100股AAPL，成本150；招行现金10万`
  - 示例截图：股票持仓页、基金持仓页、自由现金页
- 用 Read 工具读取图片，识别字段
  - 股票：名称、代码、市场、持仓数量、成本均价
  - 基金：名称、当前市值（持有金额）、持仓收益
  - 现金：账户名、币种、余额
- 按格式写入/更新 CSV，保存后进入 Step 2
**B. CSV 已存在（正常路径）**：直接进入 Step 2，跳过重新初始化。
**C. 用户提供了新截图，需要补录或覆盖部分数据**：
- 用 Read 工具读取图片，识别字段
- 只更新截图对应的资产，不重置整份 CSV

### Step 1.5：退休测算画像（当用户问“什么时候可以不上班”时）
- 若 `~/Desktop/持仓/profile.json` 不存在，或缺少以下任一字段，则优先从用户输入中提取并保存：
  - `birth_year_month`
  - `years_worked`
  - `annual_spending_cny`
  - `annual_savings_cny`
- 若用户还提供了这些信息，也一并保存：
  - `retirement_age`
  - `lifespan_age`
  - `current_social_security_personal_account_cny`
  - `historical_contribution_multiple`
  - `future_contribution_multiple`
- 保存方式：
```bash
python3 ${SKILL_DIR}/scripts/profile_manager.py --birth-year-month 1990-01 --years-worked 10 --annual-spending-cny 300000 --annual-savings-cny 200000
```
- 如需跑退休测算：
```bash
python3 ${SKILL_DIR}/scripts/retirement_projection.py
```
- 如需只看某个停工年龄：
```bash
python3 ${SKILL_DIR}/scripts/retirement_projection.py --stop-age 46
```
- 如需精确到某个停工月份：
```bash
python3 ${SKILL_DIR}/scripts/retirement_projection.py --stop-month 2038-02
```
- 当用户问“精确到月”“倒计时到月”时，优先使用输出里的 `earliest_no_work_month_projection` 和 `nearby_month_scenarios`；年度口径 `earliest_no_work_projection` 只作为粗略参考。
- 退休测算默认优先读取：
  - `总额.csv` 最新 `total_cny`
  - `未来现金流.csv`
  - `profile.json`
---
### Step 2：处理交易（若用户报告了新买卖）
**先更新 CSV，再生成报告：**
| 操作 | CSV 更新方式 |
|------|-------------|
| 买入新股 | 新增一行，填写 account/category/name/code/market/currency/shares/cost_price/cost_total |
| 加仓已有股 | 重新计算加权均价：`(原shares×原cost + 新shares×新价) / 总shares`，更新 shares 和 cost_price |
| 卖出部分 | 减少 shares，cost_price 不变 |
| 清仓 | 删除该行 |
| 基金市值更新 | 更新 last_market_value、last_pnl、last_updated |
| 基金份额/成本更新 | 更新 shares、cost_price，重新计算 cost_total = shares × cost_price；同步修正 last_pnl = last_market_value - cost_total |
| 新增未行权期权 | 新增一行（market=US，category=股票），cost_price 填行权价，在 note 字段写 `税率X%`（如 `税率20%`），脚本自动按税后内在价值计算市值 |
---
### Step 2.5：读取交易纪律（若用户问“交易纪律/减仓策略/哪些该卖”）
- 优先读取 `~/Desktop/持仓/交易纪律.json`
- 将纪律与当前持仓、最新价格结合，输出：
  - 当前是否触发减仓
  - 下一档触发价
  - 触发后卖出股数
  - 不应继续加仓的标的
  - LTI 与普通股重复暴露的风险
- 若用户确认“已卖出/已买入”，再按 Step 2 更新 `明细.csv`；仅记录纪律时不要改动真实持仓明细
---
### Step 3：运行估值脚本 + 获取汇率

**持仓分析/估值报告：优先用 `--report`，一条命令拿到全部所需数据，避免手写内联聚合代码：**
```bash
python3 ${SKILL_DIR}/scripts/fetch_prices.py --report
```
`--report` 直接返回报告所需的全部结构，**不要再手写任何 FX 获取或分类汇总的 Python**：
- `fx`：`usd_cny` / `hkd_cny` / `usd_hkd` / `date`（含 akshare→er-api fallback）
- `summary.buckets`：四口径已折算汇总（`invest` / `lti` / `cash` / `restricted`），每个含 `by_currency`（分币种 mv/pnl/cost）与 `total_cny`（`mv_cny` / `pnl_cny` / `cost_cny` / `ret_pct`）；LTI 的 pnl/cost/ret 已按规则置 null（只看市值）
- `summary.grand_total_cny` / `grand_total_usd`：总身家
- `compare`：昨日 vs 今日（`yesterday_cny` / `today_cny` / `delta_cny` / `delta_pct`），首日无对比时为 null
- `results`：逐项明细，用于填各分区表格

只需调一次 `--report`，即可直接套用 Step 4 报告模板，无需再调 fetch_prices、无需手算口径、无需手取汇率、无需 tail 总额.csv。

调试或仅需逐项明细时可用不带 `--report` 的原始命令：
```bash
python3 ${SKILL_DIR}/scripts/fetch_prices.py
```
脚本自动：
- 读取 CSV
- 对 `market=US/HK/CN` 的股票实时获取最新价格（akshare）
- 对 `FUND_CNY` 且有 `code + shares` 的基金，默认优先使用**盘中估算净值**；失败时回退正式净值/CSV 快照
- 对 `market=FUND_USD/FUND_HKD` 的基金，优先尝试 KGI 最新净值；KGI 不覆盖时尝试 Stock Events 的 `.FUND` 页面；仍失败时回退 CSV 快照
- 默认将本次总资产汇总按日期 upsert 到 `~/Desktop/持仓/总额.csv`，同一天重复执行会覆盖当天记录，不会重复追加；如只想调试输出可加 `--no-write-history`
- 当用户要求“基金全部更新/刷新基金净值/下次也用最新值”时，运行：
```bash
python3 ${SKILL_DIR}/scripts/fetch_prices.py --fund-mode estimate --write-back-funds
```
  该命令会把成功获取到的基金 `last_market_value`、`last_pnl`、`last_updated` 和净值来源写回 `~/Desktop/持仓/明细.csv`；未获取成功的基金保留原快照
- **若 note 中含 `税率X%`**（未行权期权），按税后内在价值计算市值：
  `market_value = max(0, 现价 - 行权价) × 持仓数 × (1 - 税率)`
  价外时（现价 ≤ 行权价）市值自动为 0；输出中额外包含 `tax_rate` 和 `intrinsic_gross`（税前内在价值）供参考
- 输出 JSON 结果
若用户要求“把测算数据沉淀/归档/别只放在/tmp”，运行：
```bash
python3 ${SKILL_DIR}/scripts/archive_reports.py --from-tmp
```
或按文件类型精确归档：
```bash
python3 ${SKILL_DIR}/scripts/archive_reports.py --valuation-json /path/to/valuation.json
python3 ${SKILL_DIR}/scripts/archive_reports.py --retirement-json /path/to/retirement.json
python3 ${SKILL_DIR}/scripts/archive_reports.py --ipo-csv /path/to/hk_ipo.csv
```
如需切回正式净值口径：
```bash
python3 ${SKILL_DIR}/scripts/fetch_prices.py --fund-mode official
```
**实时汇率**：已由 `--report` 的 `fx` 字段提供（akshare→er-api fallback），直接取用，不要再写汇率获取代码。
---
### Step 3.5：港股IPO打新测算（若用户问“今年港股打新清单/全中一手收益/首日卖出收益/持有到现在收益”）
运行：
```bash
python3 ${SKILL_DIR}/scripts/hk_ipo_ytd.py --year 2026
```
脚本会生成：
```text
~/Desktop/持仓/测算归档/港股IPO/hk_ipo_ytd.csv
```
输出字段包括：
- 上市日期、代号、名称、每手股数、上市价
- 首日表现、估算首日收盘价、一手首日盈亏
- 现价、一手当前盈亏、当前收益率
- 超额倍数、稳中一手、中签率

回答时优先用该 CSV 做：
- 每只都中1手并首日卖出的总收益
- 每只都中1手并持有到现在的收益率
- 当前亏损股列表及其首日表现
- 稳中一手资金需求
---
### Step 4：生成报告

> 各口径折算市值/本金/盈亏/收益率**直接取自 `--report` 的 `summary.buckets`**，无需自行分类或折算；下列规则仅为口径释义（已固化在脚本 `classify_bucket` 中）。

**盈亏统计规则：**
- **LTI 账户**：市值计入总资产，但**不计入盈亏统计**（成本为零，不反映真实投资损益）
- **投资资产口径**：只统计普通投资资产，用来衡量组合收益率；包含 `股票`、普通 `基金`、`加密货币`，但排除 `LTI` 账户，排除 `活期`、`存款`、`应收款`、`社保`，也排除备注为货币基金/类现金的基金
- **LTI 口径**：只统计 `LTI` 账户市值，单独展示；期权按税后内在价值估值，LTI 不进入投资资产收益率
- **可立即动用现金口径**：统计 `活期`、`存款`、券商自由现金、货币基金/类现金资产等可快速支配资产；用于看真实现金安全垫和待配置资金
- **应收/限制类资产口径**：统计 `应收款`、`社保` 等低波动但流动性受限或不可立即动用的资产；用于看完整稳态资产但不混入现金安全垫
- **总身家口径**：统计全部资产；包含普通投资资产、LTI、现金/活期、存款、应收款、社保、USDT 等
- **未来现金流**：年金保险领取、保费支出等单独展示，不计入当前总身家；做退休规划时，正数按领取年份抵扣当年支出，负数按支付年份加入生活费之外的独立开销
- 现金/活期、存款、应收款、社保市值计入总身家，但不计入投资资产收益率；其中应收款和社保不计入可立即动用现金
- 其他普通投资账户（富途股票/基金、支付宝、腾讯理财通、币安等）正常计入投资盈亏
**报告格式（总资产速览放在最后）：**
---
## 📊 持仓估值报告
> 更新时间：{当前时间}
> 数据更新时间：逐项展示在明细表的“更新时间/来源”列；股票通常为本次拉取时间，基金/现金可能为净值日期或快照日期
### 🇺🇸 美元资产（USD）
**富途 · 股票**
| 名称 | 代码 | 持仓 | 成本价 | 现价 | 市值 | 盈亏 | 盈亏% | 更新时间/来源 |
|------|------|------|--------|------|------|------|-------|--------------|
| ... |
**小计（不含LTI）：市值 $X ｜ 盈亏 +/-$X（X%）**
**长期激励账户（LTI · 仅统计市值）**
| 名称 | 代码 | 持仓 | 现价 | 市值 | 更新时间/来源 | 备注 |
|------|------|------|------|------|--------------|------|
| ... |
**LTI 小计：市值 $X**
**富途 · 美元基金**（存储值，上次更新：{date}）
| 名称 | 市值 | 盈亏 | 盈亏% | 更新时间/来源 |
|------|------|------|-------|--------------|
| ... |
**美元合计：市值 $X（含LTI $X）｜ 盈亏 +/-$X（不含LTI）**
---
### 🇭🇰 港元资产（HKD）
**富途 · 港股**
| 名称 | 代码 | 持仓 | 成本价 | 现价 | 市值 | 盈亏 | 盈亏% | 更新时间/来源 |
|------|------|------|--------|------|------|------|-------|--------------|
| ... |
**富途 · 港元基金**（存储值，上次更新：{date}）
| 名称 | 市值 | 盈亏 | 盈亏% | 更新时间/来源 |
|------|------|------|-------|--------------|
| ... |
**港元合计：市值 HK$X ｜ 盈亏 +/-HK$X（X%）**
---
### 🇨🇳 人民币资产（CNY）
**支付宝**
| 名称 | 市值 | 盈亏 | 盈亏% | 更新时间/来源 |
|------|------|------|-------|--------------|
| ... |
**腾讯理财通**
| 名称 | 市值 | 盈亏 | 盈亏% | 更新时间/来源 |
|------|------|------|-------|--------------|
| ... |
**CNY 合计：市值 ¥X ｜ 盈亏 +/-¥X（X%）**
---
### 📈 总资产速览
> 参考汇率（{日期}）：1 USD = {X} CNY = {X} HKD ｜ 1 HKD = {X} CNY
**投资资产口径**（排除 LTI、活期、存款、应收款、社保、货币基金/类现金）
| 口径 | 折算市值 | 投资本金 | 投资盈亏 | 收益率 |
|------|---------:|---------:|---------:|------:|
| **USD** | **$X** | $X | +/-$X | X% |
| **HKD** | **HK$X** | HK$X | +/-HK$X | X% |
| **CNY** | **¥X** | ¥X | +/-¥X | X% |

**LTI 口径**（仅 LTI，市值单列，盈亏不进入投资收益率）
| 口径 | 折算市值 | 备注 |
|------|---------:|------|
| **USD** | **$X** | 已归属股票 + 期权税后内在价值 |
| **HKD** | **HK$X** | 已归属股票 + 期权税后内在价值 |
| **CNY** | **¥X** | 已归属股票 + 期权税后内在价值 |

**可立即动用现金口径**（活期、存款、券商自由现金、货币基金/类现金）
| 口径 | 折算市值 | 构成 |
|------|---------:|------|
| **USD** | **$X** | 现金/活期 + 存款 + 券商自由现金 + 货币基金/类现金 |
| **HKD** | **HK$X** | 现金/活期 + 存款 + 券商自由现金 + 货币基金/类现金 |
| **CNY** | **¥X** | 现金/活期 + 存款 + 券商自由现金 + 货币基金/类现金 |

**应收/限制类资产口径**（应收款、社保）
| 口径 | 折算市值 | 构成 |
|------|---------:|------|
| **USD** | **$X** | 应收款 + 社保 |
| **HKD** | **HK$X** | 应收款 + 社保 |
| **CNY** | **¥X** | 应收款 + 社保 |

**总身家口径**（包含全部资产，LTI 计市值但不计盈亏）
| 口径 | 原始构成 | 折算总市值 | 折算总盈亏（不含LTI） |
|------|---------|-----------:|----------------------:|
| **USD** | $X(USD) + HK$X÷{率} + ¥X÷{率} | **$X** | **+/-$X** |
| **HKD** | $X×{率} + HK$X + ¥X÷{率} | **HK$X** | **+/-HK$X** |
| **CNY** | $X×{率} + HK$X×{率} + ¥X | **¥X** | **+/-¥X** |
> 投资资产口径用于看组合表现；LTI 口径用于看激励资产；可立即动用现金用于看现金安全垫；应收/限制类资产用于看低波动但流动性较弱的资产；总身家口径用于看完整资产规模。
> 年金保险等未来现金流不进入上述当前资产口径，但在退休测算中作为未来收入/支出项处理；负数现金流应作为生活费之外的独立开销。
---
### Step 4.5：持仓报告自动追加退休倒计时
当用户询问 `持仓`、`持仓总值`、`持仓分析`、`持仓明细`、`估值`、`完整版持仓估值报告` 等资产估值类问题时，完成 Step 3/4 后一并运行退休测算：
```bash
python3 ${SKILL_DIR}/scripts/retirement_projection.py --current-total-assets-cny {本次估值总资产CNY}
```
- 使用本次刚算出的总资产作为 `--current-total-assets-cny`，不要只依赖 `总额.csv` 的旧值
- 若 `profile.json` 缺失必要字段，提示用户补充出生年月、已工作年限、每年支出、每年可攒金额；不要阻塞持仓报告本身
- 报告末尾追加简短「退休倒计时」摘要：
  - 当前年龄
  - 最早可不上班月份/年龄
  - 距离不上班还需多久
  - 到停工时预计资产
  - 退休后预计月养老金
  - 79岁或 profile 中目标寿命时的结余
- 用户只问单项资产、单只股票、单只基金时，不需要自动追加退休倒计时
---
### Step 5：保存日历史 + 对比昨日
`fetch_prices.py` 默认会将当日汇总数据 **upsert** 到 `~/Desktop/持仓/总额.csv`（按 date 去重，同一天覆盖）：
| 字段 | 说明 |
|------|------|
| date | YYYY-MM-DD |
| total_usd | 总市值（USD 口径，含 LTI） |
| total_cny | 总市值（CNY 口径，含 LTI） |
| usd_mv | 美元原始市值（含 LTI） |
| hkd_mv | 港元原始市值 |
| cny_mv | 人民币原始市值 |
| usd_cny_rate | 当日 USD/CNY |
| hkd_cny_rate | 当日 HKD/CNY |
| pnl_usd_excl_lti | 不含 LTI 总盈亏（USD 口径） |
| pnl_cny_excl_lti | 不含 LTI 总盈亏（CNY 口径） |
**对比展示（在最后的总资产速览下方追加一行）：**
```
📅 昨日（YYYY-MM-DD）：¥X / $X　→　今日：¥X / $X　变动：▲/▼¥X（X%）
```
若 CSV 只有一行（首次记录）则跳过对比。
---
## CSV 字段说明
| 字段 | 说明 |
|------|------|
| account | 账户（富途/LTI/支付宝/腾讯理财通）|
| category | 类别（股票/基金/活期）|
| name | 名称 |
| code | 股票 ticker 或基金代码（基金可留空）|
| market | `US`/`HK`/`CN`（股票）或 `FUND_USD`/`FUND_HKD`/`FUND_CNY`（基金）|
| currency | USD/HKD/CNY |
| shares | 持仓股数/份额（股票必填；基金若知道份额也可填，方便计算盈亏%）|
| cost_price | 持仓均价（股票必填；期权填行权价；基金若知道也可填）|
| cost_total | 总成本（基金必填，股票可留空由脚本计算）|
| last_market_value | 最近市值（基金必填，定期从截图更新）|
| last_pnl | 最近持仓盈亏（基金必填）|
| last_updated | 最后更新日期 |
| note | 备注；**未行权期权须写 `税率X%`**（如 `未行权期权，行权价$3.06，税率20%（财产转让所得）`），脚本据此自动切换为税后内在价值估值模式 |
## 注意事项
- 恒大(45757)等已退市股票获取价格会失败，属正常现象，市值计为 0
- **LTI 账户市值计入总资产，盈亏一律不计入统计**（在总览表和各分区小计中均排除）
- 多次买入同一股票时，使用加权平均成本，不要单独记录每笔
- 港股 akshare 偶发连接失败，失败时使用 CSV 中 `last_market_value` 快照值并注明 *
- 退休测算属于**规划级估算**，不是社保局精算结果；如果用户问到“什么时候可以不上班”，应明确说明关键假设：是否考虑投资收益、通胀、未来养老金/年金、以及 `profile.json` 是否完整
