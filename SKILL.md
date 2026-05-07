---
name: stock-portfolio-valuation
description: 从持仓CSV或本地截图读取持仓数据，支持首次初始化、实时获取最新行情，自动计算总估值、盈亏金额和盈亏比例，生成完整持仓报告。支持美股、港股、A股、基金（支付宝/腾讯理财通/富途理财）。也支持退休测算，当用户提到"我什么时候可以不上班"、"几岁可以退休"时触发。
---
# 股票持仓实时估值
## 数据源
持仓数据的**唯一可信来源**是 `~/Desktop/持仓/明细.csv`。
- 未来收入/支出计划记录在 `~/Desktop/持仓/未来现金流.csv`，例如年金保险领取、保费支出、未来确定性款项；这类现金流**不计入当前持仓市值**，只在退休/长期现金流测算中使用
- 退休测算画像记录在 `~/Desktop/持仓/profile.json`，例如出生年月、工作年限、每年支出、每年可新增储蓄、社保相关参数
- **非必要不从图片重新导入**：CSV 存在则直接加载
- **每次买入/卖出/基金变动后立即更新 CSV**，再生成报告
- 基金市值建议每周从 App 截图更新一次
---
## 执行步骤
### Step 1：确定数据来源
**A. 首次使用，CSV 不存在**：
- 先运行初始化脚本创建骨架文件：
```bash
python3 ~/.claude/skills/stock-portfolio-valuation/scripts/init_portfolio.py
```
- 然后优先通过**自然语言或截图**补齐持仓
  - 示例自然语言：`富途有300股YINN，成本37.153；招行现金60万`
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
python3 ~/.claude/skills/stock-portfolio-valuation/scripts/profile_manager.py --birth-year-month 1989-07 --years-worked 14.5 --annual-spending-cny 400000 --annual-savings-cny 500000
```
- 如需跑退休测算：
```bash
python3 ~/.claude/skills/stock-portfolio-valuation/scripts/retirement_projection.py
```
- 如需只看某个停工年龄：
```bash
python3 ~/.claude/skills/stock-portfolio-valuation/scripts/retirement_projection.py --stop-age 46
```
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
### Step 3：运行估值脚本 + 获取汇率
```bash
python3 ~/.claude/skills/stock-portfolio-valuation/scripts/fetch_prices.py
```
脚本自动：
- 读取 CSV
- 对 `market=US/HK/CN` 的股票实时获取最新价格（akshare）
- 对 `FUND_CNY` 且有 `code + shares` 的基金，默认优先使用**盘中估算净值**；失败时回退正式净值/CSV 快照
- 对 `market=FUND_USD/FUND_HKD` 的基金，当前仍以 CSV 存储的 `last_market_value` 为主
- **若 note 中含 `税率X%`**（未行权期权），按税后内在价值计算市值：
  `market_value = max(0, 现价 - 行权价) × 持仓数 × (1 - 税率)`
  价外时（现价 ≤ 行权价）市值自动为 0；输出中额外包含 `tax_rate` 和 `intrinsic_gross`（税前内在价值）供参考
- 输出 JSON 结果
如需切回正式净值口径：
```bash
python3 ~/.claude/skills/stock-portfolio-valuation/scripts/fetch_prices.py --fund-mode official
```
**同步获取实时汇率（含 fallback）：**
```python
import akshare as ak, requests, math
def get_fx_rates():
    try:
        df = ak.fx_spot_quote()
        usd_row = df[df['货币对']=='USD/CNY'][['买报价','卖报价']].iloc[0]
        hkd_row = df[df['货币对']=='HKD/CNY'][['买报价','卖报价']].iloc[0]
        usd_cny = (float(usd_row['买报价']) + float(usd_row['卖报价'])) / 2
        hkd_cny = (float(hkd_row['买报价']) + float(hkd_row['卖报价'])) / 2
        if math.isnan(usd_cny) or math.isnan(hkd_cny):
            raise ValueError("NaN from akshare")
    except Exception:
        r = requests.get('https://open.er-api.com/v6/latest/USD', timeout=5)
        d = r.json()['rates']
        usd_cny = d['CNY']
        hkd_cny = usd_cny / d['HKD']
    return usd_cny, hkd_cny, usd_cny / hkd_cny
usd_cny, hkd_cny, usd_hkd = get_fx_rates()
```
---
### Step 4：生成报告
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
### Step 5：保存日历史 + 对比昨日
每次生成报告后，将当日汇总数据 **upsert** 到 `~/Desktop/持仓/总额.csv`（按 date 去重，同一天覆盖）：
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
