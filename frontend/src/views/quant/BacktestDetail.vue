<template>
  <PageContainer>
    <!-- 页头：策略名 + 状态行 + 操作 -->
    <PageHeader :title="result?.name || '回测详情'">
      <template #actions>
        <el-button size="small" text @click="goList">← {{ detailSource ? '返回策略库' : '返回策略列表' }}</el-button>
        <span class="jq-tab">编辑策略</span>
        <span class="jq-tab jq-tab--on">回测详情</span>
      </template>
    </PageHeader>
    <div v-if="result" class="detail-meta">
      <span>设置：<b>{{ result.start_date }} 到 {{ result.end_date }}</b>，<b>¥{{ fmtCapital }}</b>，<b>{{ modeLabel }}</b></span>
      <span>状态：<i class="ok">✓</i> <b>回测完成</b></span>
      <span class="pill" v-if="result.backend">{{ result.backend }}</span>
      <el-button v-if="!detailSource" size="small" @click="openRerunPrefill">调整参数重跑</el-button>
      <el-button v-if="!detailSource" size="small" :loading="mcLoading" @click="runMonteCarlo">蒙特卡罗模拟</el-button>
      <el-button v-if="canUseStrategyApi" size="small" :loading="reportLoading" @click="runPortfolioReport">组合报告</el-button>
      <el-button v-if="canUseStrategyApi" size="small" :loading="reviewLoading" @click="runAiReview">AI 评审</el-button>
      <el-button size="small" @click="exportJson">导出</el-button>
      <el-button size="small" type="danger" plain @click="onDelete">删除回测</el-button>
    </div>

    <div v-loading="loading" class="jq-body">
      <template v-if="result">
        <!-- 左侧章节菜单 -->
        <aside class="jq-side">
          <span
            v-for="s in sections"
            :key="s.id"
            class="jq-side__item"
            :class="{ 'jq-side__item--on': activeSection === s.id }"
            @click="scrollTo(s.id)"
            >{{ s.label }}</span
          >
        </aside>

        <!-- 主区 -->
        <div class="jq-main">
          <!-- 收益概述：22 指标 6×2 -->
          <SectionCard id="sec-overview" class="detail-card" title="收益概述">
            <div class="jq-grid">
              <div v-for="m in metricCells" :key="m.label" class="jq-grid__item">
                <span class="jq-grid__label">{{ m.label }}</span>
                <b class="jq-grid__value" :class="m.cls">{{ m.value }}</b>
              </div>
            </div>
          </SectionCard>

          <!-- 主图：净值（策略/超额/基准） -->
          <SectionCard id="sec-nav" class="detail-card" title="净值走势">
            <template #extra>
              <div class="jq-zoom">
                <span>缩放：</span>
                <span
                  v-for="z in zoomOptions"
                  :key="z.label"
                  class="jq-zoom__btn"
                  :class="{ 'jq-zoom__btn--on': zoom === z.value }"
                  @click="zoom = z.value"
                  >{{ z.label }}</span
                >
              </div>
              <span
                class="jq-zoom__btn"
                :class="{ 'jq-zoom__btn--on': logAxis }"
                @click="logAxis = !logAxis"
                >对数轴</span
              >
            </template>
            <VChart :option="navOption" autoresize class="jq-chart" />
          </SectionCard>

          <!-- 副图：每日盈亏 -->
          <SectionCard id="sec-daily" class="detail-card" title="每日盈亏">
            <VChart :option="pnlOption" autoresize class="jq-chart jq-chart--sub" />
          </SectionCard>

          <!-- 策略 K 线（仅规则策略回测结果带 indicator 时渲染） -->
          <SectionCard v-if="hasIndicator" id="sec-kline" class="detail-card" title="策略 K 线">
            <BacktestKLinePanel :result="result" />
          </SectionCard>

          <!-- 副图：持仓量 -->
          <SectionCard id="sec-hold" class="detail-card" title="持仓量（由成交明细还原）">
            <VChart :option="holdingsOption" autoresize class="jq-chart jq-chart--sub" />
          </SectionCard>

          <!-- 归因分析 -->
          <SectionCard id="sec-attribution" class="detail-card" title="归因分析 · 个股盈亏贡献">
            <p class="jq-note" v-if="!attributionRows.length">暂无成交明细，无法归因（本回测 trades 为空）。</p>
            <template v-else>
              <p class="jq-note">FIFO 配对完整买卖回合，含交易成本；按个股净盈亏降序。</p>
              <VChart :option="attributionOption" autoresize class="jq-chart jq-chart--sub" />
              <el-table :data="attributionRows.slice(0, 20)" size="small" max-height="320">
                <el-table-column prop="code" label="代码" width="110" />
                <el-table-column label="回合数" width="80" align="center">
                  <template #default="{ row }">{{ row.rounds }}</template>
                </el-table-column>
                <el-table-column label="净盈亏" width="130" align="right">
                  <template #default="{ row }">
                    <span :class="pnlClass(row.net)">{{ fmtMoney(row.net) }}</span>
                  </template>
                </el-table-column>
                <el-table-column label="收益率" width="100" align="right">
                  <template #default="{ row }">
                    <span :class="pnlClass(row.ret)">{{ fmtPct2(row.ret) }}</span>
                  </template>
                </el-table-column>
                <el-table-column label="贡献占比" min-width="180">
                  <template #default="{ row }">
                    <div class="jq-bar">
                      <i
                        class="jq-bar__fill"
                        :class="row.net >= 0 ? 'jq-bar__fill--up' : 'jq-bar__fill--down'"
                        :style="{ width: barWidth(row) + '%' }"
                      />
                      <span class="jq-bar__pct">{{ fmtPct2(row.share) }}</span>
                    </div>
                  </template>
                </el-table-column>
              </el-table>
            </template>
          </SectionCard>

          <!-- 交易详情 -->
          <SectionCard id="sec-trades" class="detail-card" title="交易详情（共 {{ trades.length }} 笔）">
            <el-alert
              v-if="isReconstructed && trades.length"
              type="warning"
              :closable="false"
              show-icon
              title="成交明细由持仓快照差分重构估算（成本按固定费率），非交易所级订单"
              class="jq-reconstruct-alert"
            />
            <el-table v-if="trades.length" :data="pagedTrades" size="small" max-height="360">
              <el-table-column prop="date" label="日期" width="110" />
              <el-table-column prop="code" label="代码" width="110" />
              <el-table-column label="方向" width="80" align="center">
                <template #default="{ row }">
                  <span :class="row.action === 'BUY' ? 'num-up' : 'num-down'">
                    {{ row.action === 'BUY' ? '买入' : '卖出' }}
                  </span>
                </template>
              </el-table-column>
              <el-table-column prop="price" label="价格" width="100" align="right" />
              <el-table-column prop="quantity" label="数量" width="100" align="right" />
              <el-table-column prop="total" label="金额" width="120" align="right" />
              <el-table-column prop="cost" label="成本" width="100" align="right" />
            </el-table>
            <p class="jq-note" v-else>本回测无成交明细。</p>
            <el-pagination
              v-if="trades.length > tradePageSize"
              v-model:current-page="tradePage"
              :page-size="tradePageSize"
              :total="trades.length"
              layout="prev, pager, next, total"
              class="jq-pager"
            />
          </SectionCard>

          <!-- 蒙特卡罗 -->
          <SectionCard v-if="!detailSource" id="sec-mc" class="detail-card" title="蒙特卡罗模拟">
            <p class="jq-note" v-if="!mcData">点击页头「蒙特卡罗模拟」运行 Stationary Bootstrap 置信区间估计。</p>
            <template v-else>
              <p class="jq-note">
                Stationary Bootstrap ×{{ mcData.n_iter }}，块长 {{ mcData.block }} 交易日，
                {{ Math.round(Number(mcData.ci_level) * 100) }}% 置信区间
              </p>
              <div class="jq-mc-grid">
                <div v-for="m in mcCells" :key="m.label" class="jq-mc-grid__item">
                  <span class="jq-grid__label">{{ m.label }}</span>
                  <b class="jq-grid__value">{{ m.point }}</b>
                  <span class="jq-grid__ci">CI [{{ m.lo }}, {{ m.hi }}]</span>
                </div>
              </div>
            </template>
          </SectionCard>

          <!-- 组合绩效报告（quantstats） -->
          <SectionCard v-if="canUseStrategyApi" id="sec-report" class="detail-card" title="组合报告">
            <p class="jq-note" v-if="!reportData">
              点击页头「组合报告」生成 quantstats 绩效指标（不做 HTML tear-sheet）。
            </p>
            <template v-else>
              <p class="jq-note">
                样本 {{ reportData.n_obs ?? '--' }} 个交易日（{{ reportData.start_date || '--' }} ~ {{ reportData.end_date || '--' }}）
              </p>
              <div class="jq-grid">
                <div v-for="m in reportCells" :key="m.label" class="jq-grid__item">
                  <span class="jq-grid__label">{{ m.label }}</span>
                  <b class="jq-grid__value">{{ m.value }}</b>
                </div>
              </div>
            </template>
          </SectionCard>

          <!-- AI 评审 -->
          <SectionCard v-if="canUseStrategyApi" id="sec-review" class="detail-card" title="AI 评审">
            <p class="jq-note" v-if="!reviewData">点击页头「AI 评审」让 AI 解读本次回测结果。</p>
            <template v-else>
              <div v-if="reviewKeyEvents.length" class="jq-review-events">
                <el-tag v-for="(e, i) in reviewKeyEvents" :key="i" size="small" effect="plain" class="jq-review-event">
                  {{ typeof e === 'string' ? e : JSON.stringify(e) }}
                </el-tag>
              </div>
              <pre class="jq-review-pre">{{ reviewText }}</pre>
            </template>
          </SectionCard>

          <!-- 回测参数 -->
          <SectionCard id="sec-params" class="detail-card" title="回测参数与执行口径">
            <div class="jq-params">
              <div v-for="p in paramCells" :key="p.label" class="jq-params__item">
                <span>{{ p.label }}</span>
                <b>{{ p.value }}</b>
              </div>
            </div>
          </SectionCard>
        </div>
      </template>
    </div>

    <!-- 调整参数重跑弹窗 -->
    <el-dialog v-model="rerunOpen" title="调整参数重跑" width="420px">
      <el-form label-width="110px">
        <el-form-item label="开始日期">
          <el-date-picker v-model="rerunForm.start" type="date" value-format="YYYY-MM-DD" />
        </el-form-item>
        <el-form-item label="结束日期">
          <el-date-picker v-model="rerunForm.end" type="date" value-format="YYYY-MM-DD" />
        </el-form-item>
        <el-form-item label="topk">
          <el-input-number v-model="rerunForm.topk" :min="1" :max="100" />
        </el-form-item>
        <el-form-item label="n_drop">
          <el-input-number v-model="rerunForm.n_drop" :min="0" :max="50" />
        </el-form-item>
        <el-form-item label="调仓频率">
          <el-select v-model="rerunForm.rebalance_freq" style="width: 160px">
            <el-option label="每天" value="day" />
            <el-option label="每周" value="week" />
            <el-option label="每月" value="month" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="rerunOpen = false">取消</el-button>
        <el-button type="primary" :loading="rerunning" @click="onRerun">开始回测</el-button>
      </template>
    </el-dialog>
  </PageContainer>
</template>

<script setup>
import { ref, computed, onMounted, onBeforeUnmount, nextTick } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus/es/components/message/index'
import { ElMessageBox } from 'element-plus/es/components/message-box/index'
import VChart from 'vue-echarts'
import '@/utils/echarts'
import PageContainer from '@/components/common/PageContainer.vue'
import PageHeader from '@/components/common/PageHeader.vue'
import SectionCard from '@/components/common/SectionCard.vue'
import BacktestKLinePanel from '@/components/quant/BacktestKLinePanel.vue'
import {
  getBacktestResult,
  deleteBacktestResult,
  runBacktest,
  getMonteCarlo,
  getAllBacktestStatuses,
  listBacktestResults,
  getPortfolioReport,
  aiReviewBacktest,
} from '@/api/strategy'
import { getStrategyHistoryDetail, deleteStrategyHistory } from '@/api/strategyLibrary'
import { getClassicHistoryDetail, deleteClassicHistory } from '@/api/classicStrategy'
import { fmtNum } from '@/utils/format'
import { useThemeRev } from '@/composables/useChartTheme'

defineOptions({ name: 'BacktestDetail' })

const route = useRoute()
const router = useRouter()
const isDark = useThemeRev()

const loading = ref(true)
const result = ref(null)
const mcData = ref(null)
const mcLoading = ref(false)

// 规则策略回测结果带 indicator（K 线面板）；qlib/vbt 回测没有
const hasIndicator = computed(() => !!result.value?.indicator?.lines?.length)
// 成交明细由持仓快照差分重构时后端会带 reconstructed 标记
const isReconstructed = computed(() => result.value?.reconstructed === true)
// 组合报告/AI 评审挂在 /strategies/{id}/ 下，需要策略 id（策略库历史详情没有）
const canUseStrategyApi = computed(() => !detailSource.value && !!result.value?.strategy_id)

const zoom = ref(1) // 1=全部；0.25=近1年；0.08=近3月；0.03=近1月（近似占比）
const zoomOptions = [
  { label: '1个月', value: 0.03 },
  { label: '3个月', value: 0.08 },
  { label: '1年', value: 0.25 },
  { label: '全部', value: 1 },
]
const logAxis = ref(false)

const activeSection = ref('sec-overview')
const allSections = [
  { id: 'sec-overview', label: '收益概述' },
  { id: 'sec-nav', label: '净值走势' },
  { id: 'sec-daily', label: '每日盈亏' },
  { id: 'sec-kline', label: '策略K线' },
  { id: 'sec-hold', label: '持仓量' },
  { id: 'sec-attribution', label: '归因分析' },
  { id: 'sec-trades', label: '交易详情' },
  { id: 'sec-mc', label: '蒙特卡罗' },
  { id: 'sec-report', label: '组合报告' },
  { id: 'sec-review', label: 'AI 评审' },
  { id: 'sec-params', label: '回测参数' },
]
// 策略库历史无蒙特卡罗接口（依赖因子回测结果 id），隐藏该章节；
// 组合报告/AI 评审依赖 strategy_id，仅因子策略回测详情展示；
// 策略K线仅当结果带 indicator（规则策略回测）时展示。
const sections = computed(() =>
  allSections.filter((s) => {
    if (s.id === 'sec-mc' || s.id === 'sec-report' || s.id === 'sec-review') return !detailSource.value
    if (s.id === 'sec-kline') return hasIndicator.value
    return true
  })
)

let sectionObserver = null

// 数据源：默认因子策略回测结果；?source=rule / classic 为策略库历史详情
const detailSource = computed(() => {
  const s = route.query.source
  return s === 'rule' || s === 'classic' ? s : null
})

async function loadResult() {
  if (detailSource.value === 'rule') return await getStrategyHistoryDetail(route.params.id)
  if (detailSource.value === 'classic') return await getClassicHistoryDetail(route.params.id)
  return await getBacktestResult(route.params.id)
}

onMounted(async () => {
  try {
    result.value = await loadResult()
    if (detailSource.value && result.value && !result.value.name) {
      result.value.name = result.value.template_name || '策略回测'
    }
  } catch {
    // 拦截器已弹错误提示
  } finally {
    loading.value = false
    await nextTick()
    setupSectionObserver()
  }
})

// 滚动联动：section 进入视口时同步左侧菜单高亮
function setupSectionObserver() {
  if (typeof IntersectionObserver === 'undefined') return
  sectionObserver = new IntersectionObserver(
    (entries) => {
      for (const e of entries) {
        if (e.isIntersecting) activeSection.value = e.target.id
      }
    },
    { rootMargin: '-15% 0px -75% 0px' }
  )
  for (const s of sections.value) {
    const el = document.getElementById(s.id)
    if (el) sectionObserver.observe(el)
  }
}

onBeforeUnmount(() => {
  sectionObserver?.disconnect()
  sectionObserver = null
})

const metrics = computed(() => result.value?.metrics || {})
const trades = computed(() => result.value?.trades || [])
const navCurve = computed(() => result.value?.nav_curve || null)

const fmtCapital = computed(() => {
  const c = result.value?.initial_capital
  return c != null ? Number(c).toLocaleString('zh-CN') : '--'
})

const rebalanceLabel = computed(() => {
  const map = { day: '每天', week: '每周', month: '每月' }
  return map[result.value?.rebalance_freq] || result.value?.rebalance_freq || '--'
})

// 策略库历史：以策略形态替代调仓频率展示
const modeLabel = computed(() => {
  if (!detailSource.value) return rebalanceLabel.value
  const kind = result.value?.kind
  const map = { pairs: '配对交易', factor: '截面因子', single: '单标的' }
  return map[kind] || '规则策略'
})

/* ============ 22 指标（按需显示：缺失显示 '--'） ============ */
function pct(v, digits = 2) {
  return v == null ? '--' : (v * 100).toFixed(digits) + '%'
}
function num(v, digits = 3) {
  return v == null ? '--' : Number(v).toFixed(digits)
}
function pctSigned(v, digits = 2) {
  if (v == null) return '--'
  return (v >= 0 ? '▲ ' : '▼ ') + Math.abs(v * 100).toFixed(digits) + '%'
}
function posNeg(v) {
  if (v == null) return ''
  return v >= 0 ? 'num-up' : 'num-down'
}

// 交易口径胜率（FIFO 回合）；无 trades 时退化为日胜率
const roundTripWinRate = computed(() => {
  const rows = attributionRows.value
  if (!rows.length) return null
  const wins = rows.filter((r) => r.net > 0).length
  return wins / rows.length
})

const metricCells = computed(() => {
  const m = metrics.value
  const tradeWr = roundTripWinRate.value
  return [
    { label: '策略收益', value: pctSigned(m.total_return), cls: posNeg(m.total_return) },
    { label: '策略年化收益', value: pctSigned(m.annual_return), cls: posNeg(m.annual_return) },
    { label: '超额收益', value: pctSigned(m.excess_return), cls: posNeg(m.excess_return) },
    { label: '基准收益', value: pctSigned(m.benchmark_return), cls: posNeg(m.benchmark_return) },
    { label: '阿尔法', value: num(m.alpha), cls: posNeg(m.alpha) },
    { label: '贝塔', value: num(m.beta) },
    { label: '夏普比率', value: num(m.sharpe) },
    { label: '胜率', value: pct(tradeWr != null ? tradeWr : m.win_rate) },
    { label: '盈亏比', value: num(m.profit_loss_ratio) },
    { label: '最大回撤', value: pct(m.max_drawdown), cls: 'num-down' },
    { label: '索提诺比率', value: num(m.sortino) },
    { label: '卡玛比率', value: num(m.calmar) },
    { label: '日均超额收益', value: pctSigned(m.daily_mean_excess, 3), cls: posNeg(m.daily_mean_excess) },
    { label: '超额收益最大回撤', value: pct(m.excess_max_drawdown), cls: 'num-down' },
    { label: '超额收益夏普比率', value: num(m.excess_sharpe) },
    { label: '日胜率', value: pct(m.win_rate) },
    { label: '盈利次数', value: m.win_count != null ? String(m.win_count) : '--' },
    { label: '亏损次数', value: m.loss_count != null ? String(m.loss_count) : '--' },
    { label: '信息比率', value: num(m.information_ratio) },
    { label: '策略波动率', value: pct(m.annual_volatility) },
    { label: '基准波动率', value: pct(m.benchmark_volatility) },
    { label: '最大回撤区间', value: m.max_drawdown_period || '--' },
  ]
})

/* ============ 图表主题色（token 化，暗色自动生效） ============ */
function cssVar(name, fallback) {
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim()
  return v || fallback
}
const C = computed(() => ({
  // 读取 isDark 建立响应依赖：主题切换时重算取新 token 值
  _theme: isDark.value,
  text: cssVar('--text-secondary', '#52525b'),
  split: cssVar('--border-light', '#eef2f7'),
  strategy: cssVar('--chart-p1', '#5470c6'),
  excess: cssVar('--chart-up', '#ef232a'),
  bench: cssVar('--chart-p2', '#fa8c16'),
  up: cssVar('--chart-up', '#ef232a'),
  down: cssVar('--chart-down', '#14b143'),
}))

function dataZoomCfg() {
  if (zoom.value >= 1) return []
  return [{ type: 'inside', start: (1 - zoom.value) * 100, end: 100 }]
}

/* ============ 主图：净值 ============ */
const navOption = computed(() => {
  const nc = navCurve.value
  if (!nc || !nc.dates?.length) return {}
  const dates = nc.dates
  const portfolio = nc.portfolio || []
  const benchmark = nc.benchmark || null
  const excess = benchmark ? portfolio.map((v, i) => (v != null && benchmark[i] != null ? Number((v - benchmark[i]).toFixed(4)) : null)) : null

  const series = [
    { name: '策略收益', type: 'line', data: portfolio, showSymbol: false, lineWidth: 2, lineStyle: { width: 2, color: C.value.strategy }, itemStyle: { color: C.value.strategy } },
  ]
  if (excess) {
    series.push({ name: '超额收益', type: 'line', data: excess, showSymbol: false, lineStyle: { width: 1.5, color: C.value.excess }, itemStyle: { color: C.value.excess }, yAxisIndex: 1 })
  }
  if (benchmark) {
    series.push({ name: '基准收益', type: 'line', data: benchmark, showSymbol: false, lineStyle: { width: 1.5, color: C.value.bench }, itemStyle: { color: C.value.bench } })
  }

  const y1 = { type: logAxis.value ? 'log' : 'value', scale: true, splitLine: { lineStyle: { color: C.value.split } }, axisLabel: { color: C.value.text, formatter: (v) => v.toFixed(2) } }
  const y2 = { type: 'value', scale: true, splitLine: { show: false }, axisLabel: { color: C.value.excess, formatter: (v) => v.toFixed(2) } }

  return {
    backgroundColor: 'transparent',
    tooltip: { trigger: 'axis' },
    legend: { top: 0, textStyle: { color: C.value.text } },
    grid: { left: 50, right: excess ? 50 : 20, top: 30, bottom: 28 },
    xAxis: { type: 'category', data: dates, axisLabel: { color: C.value.text } },
    yAxis: excess ? [y1, y2] : y1,
    dataZoom: dataZoomCfg(),
    series,
  }
})

/* ============ 副图：每日盈亏 ============ */
const dailyPnl = computed(() => {
  const nc = navCurve.value
  if (!nc?.portfolio?.length) return []
  return nc.portfolio.map((v, i) => (i === 0 ? 0 : Number(((v / nc.portfolio[i - 1]) - 1).toFixed(6))))
})

const pnlOption = computed(() => {
  if (!navCurve.value?.dates?.length) return {}
  return {
    backgroundColor: 'transparent',
    tooltip: { trigger: 'axis', valueFormatter: (v) => (v * 100).toFixed(2) + '%' },
    grid: { left: 50, right: 20, top: 12, bottom: 24 },
    xAxis: { type: 'category', data: navCurve.value.dates, axisLabel: { color: C.value.text } },
    yAxis: { type: 'value', axisLabel: { color: C.value.text, formatter: (v) => (v * 100).toFixed(1) + '%' }, splitLine: { lineStyle: { color: C.value.split } } },
    dataZoom: dataZoomCfg(),
    series: [
      {
        type: 'bar',
        data: dailyPnl.value.map((v) => ({ value: v, itemStyle: { color: v >= 0 ? C.value.up : C.value.down } })),
      },
    ],
  }
})

/* ============ 副图：持仓量 ============ */
const holdingsSeries = computed(() => {
  const nc = navCurve.value
  if (!nc?.dates?.length || !trades.value.length) return null
  const byDate = new Map()
  for (const t of trades.value) {
    const d = String(t.date).slice(0, 10)
    const delta = t.action === 'BUY' ? Number(t.quantity) : -Number(t.quantity)
    byDate.set(d, (byDate.get(d) || 0) + delta)
  }
  let cum = 0
  const out = []
  for (const d of nc.dates) {
    cum += byDate.get(String(d).slice(0, 10)) || 0
    out.push(Math.max(0, Math.round(cum)))
  }
  return out
})

const holdingsOption = computed(() => {
  const dates = navCurve.value?.dates
  const data = holdingsSeries.value
  if (!dates || !data) return {}
  return {
    backgroundColor: 'transparent',
    tooltip: { trigger: 'axis' },
    grid: { left: 50, right: 20, top: 12, bottom: 24 },
    xAxis: { type: 'category', data: dates, axisLabel: { color: C.value.text } },
    yAxis: { type: 'value', axisLabel: { color: C.value.text }, splitLine: { lineStyle: { color: C.value.split } } },
    dataZoom: dataZoomCfg(),
    series: [{ type: 'bar', data, itemStyle: { color: C.value.strategy } }],
  }
})

/* ============ 归因分析：FIFO 回合配对 ============ */
const attributionRows = computed(() => {
  const tradesArr = trades.value
  if (!tradesArr.length) return []
  const queues = new Map() // code -> [{price, qty, cost}]
  const rounds = new Map() // code -> {rounds, net, cost basis}
  const sorted = [...tradesArr].sort((a, b) => (a.date < b.date ? -1 : 1))
  for (const t of sorted) {
    const code = t.code
    if (!queues.has(code)) queues.set(code, [])
    if (!rounds.has(code)) rounds.set(code, { rounds: 0, net: 0, invested: 0 })
    const q = queues.get(code)
    const rec = rounds.get(code)
    if (t.action === 'BUY') {
      q.push({ price: Number(t.price), qty: Number(t.quantity), cost: Number(t.cost || 0) })
    } else {
      let remain = Number(t.quantity)
      while (remain > 1e-9 && q.length) {
        const head = q[0]
        const take = Math.min(head.qty, remain)
        const buyAmt = take * head.price
        const sellAmt = take * Number(t.price)
        rec.net += sellAmt - buyAmt - Number(t.cost || 0) * (take / Number(t.quantity)) - head.cost * (take / head.qty)
        rec.invested += buyAmt
        rec.rounds += take / Number(t.quantity) >= 0.999 ? 1 : 0
        head.qty -= take
        remain -= take
        if (head.qty <= 1e-9) q.shift()
      }
    }
  }
  const rows = []
  for (const [code, rec] of rounds) {
    if (rec.invested > 0) {
      rows.push({ code, rounds: rec.rounds || 1, net: Number(rec.net.toFixed(2)), ret: rec.net / rec.invested })
    }
  }
  const totalAbs = rows.reduce((s, r) => s + Math.abs(r.net), 0) || 1
  for (const r of rows) r.share = r.net / totalAbs
  rows.sort((a, b) => b.net - a.net)
  return rows
})

const attributionOption = computed(() => {
  const rows = attributionRows.value.slice(0, 15)
  if (!rows.length) return {}
  return {
    backgroundColor: 'transparent',
    tooltip: { trigger: 'axis' },
    grid: { left: 70, right: 20, top: 12, bottom: 24 },
    xAxis: { type: 'value', axisLabel: { color: C.value.text }, splitLine: { lineStyle: { color: C.value.split } } },
    yAxis: { type: 'category', data: rows.map((r) => r.code).reverse(), axisLabel: { color: C.value.text } },
    series: [
      {
        type: 'bar',
        data: rows.map((r) => ({ value: r.net, itemStyle: { color: r.net >= 0 ? C.value.up : C.value.down } })).reverse(),
      },
    ],
  }
})

function barWidth(row) {
  return Math.min(100, Math.abs(row.share) * 100)
}
function pnlClass(v) {
  return v >= 0 ? 'num-up' : 'num-down'
}
function fmtMoney(v) {
  return Number(v).toLocaleString('zh-CN', { maximumFractionDigits: 0 })
}
function fmtPct2(v) {
  if (v == null) return '--'
  return (v * 100).toFixed(2) + '%'
}

/* ============ 交易明细分页 ============ */
const tradePage = ref(1)
const tradePageSize = 50
const pagedTrades = computed(() => {
  const start = (tradePage.value - 1) * tradePageSize
  return trades.value.slice(start, start + tradePageSize)
})

/* ============ 蒙特卡罗 ============ */
async function runMonteCarlo() {
  mcLoading.value = true
  try {
    mcData.value = await getMonteCarlo(result.value.id, {})
  } catch {
    // 拦截器已弹错误提示
  } finally {
    mcLoading.value = false
  }
}

const mcCells = computed(() => {
  const m = mcData.value?.metrics || {}
  const cells = []
  for (const [key, label] of [['annual_return', '年化收益'], ['sharpe', '夏普比率'], ['max_drawdown', '最大回撤']]) {
    const it = m[key] || {}
    if (it.point != null) {
      cells.push({ label, point: fmtNum(it.point, 3), lo: fmtNum(it.lo, 3), hi: fmtNum(it.hi, 3) })
    }
  }
  return cells
})

/* ============ 组合报告（quantstats） ============ */
const reportLoading = ref(false)
const reportData = ref(null)

async function runPortfolioReport() {
  reportLoading.value = true
  try {
    reportData.value = await getPortfolioReport(result.value.strategy_id, {
      result_id: result.value.id,
      generate_html: false,
    })
    await nextTick()
    document.getElementById('sec-report')?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  } catch {
    // 拦截器已弹错误提示
  } finally {
    reportLoading.value = false
  }
}

const REPORT_PCT_KEYS = new Set([
  'cagr', 'annual_volatility', 'max_drawdown', 'win_rate', 'avg_return',
  'expected_daily_return', 'best_day', 'worst_day', 'best_week', 'worst_week',
  'best_month', 'worst_month', 'var_95', 'cvar_95',
])
const REPORT_LABELS = {
  sharpe: '夏普比率', sortino: '索提诺比率', calmar: '卡玛比率', cagr: '年化收益',
  annual_volatility: '年化波动率', max_drawdown: '最大回撤', skew: '偏度',
  kurtosis: '峰度', var_95: 'VaR (95%)', cvar_95: 'CVaR (95%)', tail_ratio: '尾部比率',
  win_rate: '日胜率', avg_return: '日均收益', expected_daily_return: '预期日收益',
  best_day: '最佳单日', worst_day: '最差单日', best_week: '最佳单周', worst_week: '最差单周',
  best_month: '最佳单月', worst_month: '最差单月', consecutive_wins: '最长连盈',
  consecutive_losses: '最长连亏', beta: '贝塔', alpha: '阿尔法', rsq: 'R²',
  correlation: '相关系数',
}

const reportCells = computed(() => {
  const m = reportData.value?.metrics || {}
  const cells = []
  for (const [k, v] of Object.entries(m)) {
    if (v == null) continue
    const n = Number(v)
    let value
    if (REPORT_PCT_KEYS.has(k)) value = (n * 100).toFixed(2) + '%'
    else if (k.startsWith('consecutive_')) value = String(Math.round(n))
    else if (Number.isNaN(n)) value = String(v)
    else value = n.toFixed(3)
    cells.push({ label: REPORT_LABELS[k] || k, value })
  }
  return cells
})

/* ============ AI 评审 ============ */
const reviewLoading = ref(false)
const reviewData = ref(null)

async function runAiReview() {
  reviewLoading.value = true
  try {
    reviewData.value = await aiReviewBacktest(result.value.strategy_id, result.value.id)
    await nextTick()
    document.getElementById('sec-review')?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  } catch {
    // 拦截器已弹错误提示
  } finally {
    reviewLoading.value = false
  }
}

const reviewText = computed(() => {
  const r = reviewData.value?.review
  if (r == null) return ''
  if (typeof r === 'string') return r
  try {
    return JSON.stringify(r, null, 2)
  } catch {
    return String(r)
  }
})

const reviewKeyEvents = computed(() => {
  const e = reviewData.value?.key_events
  return Array.isArray(e) ? e : []
})

/* ============ 参数 ============ */
const paramCells = computed(() => {
  const r = result.value || {}
  if (detailSource.value) {
    const paramsTxt = r.params && Object.keys(r.params).length
      ? Object.entries(r.params).map(([k, v]) => `${k}=${v}`).join('，')
      : '--'
    return [
      { label: '策略模板', value: r.template_name || '--' },
      { label: '标的', value: (r.symbols || []).join(' / ') || '--' },
      { label: '区间', value: `${r.start_date || '--'} ~ ${r.end_date || '--'}` },
      { label: '初始资金', value: '¥' + fmtCapital.value },
      { label: '基准', value: r.benchmark || '--' },
      { label: '模板参数', value: paramsTxt },
      { label: '回测时间', value: r.created_at ? String(r.created_at).replace('T', ' ').slice(0, 16) : '--' },
    ]
  }
  return [
    { label: '区间', value: `${r.start_date || '--'} ~ ${r.end_date || '--'}` },
    { label: '初始资金', value: '¥' + fmtCapital.value },
    { label: '调仓频率', value: rebalanceLabel.value },
    { label: 'topk / n_drop', value: `${r.topk ?? '--'} / ${r.n_drop ?? '--'}` },
    { label: '组合方式', value: combinationLabel(r.combination_method) },
    { label: '基准', value: r.benchmark || '--' },
    { label: '后端', value: r.backend || '--' },
    { label: '换手率', value: fmtNum(r.turnover, 3) },
  ]
})
function combinationLabel(m) {
  const map = { ic_weight: 'IC 加权', ir_weight: 'IR 加权' }
  return map[m] || '等权'
}

/* ============ 调整参数重跑 ============ */
const rerunOpen = ref(false)
const rerunning = ref(false)
const rerunForm = ref({ start: '', end: '', topk: 10, n_drop: 5, rebalance_freq: 'day' })

// 打开弹窗时用当前结果预填（watch 时机简化：computed 不行，用 open 时赋值）
function openRerunPrefill() {
  const r = result.value
  if (r) rerunForm.value = { start: r.start_date, end: r.end_date, topk: r.topk, n_drop: r.n_drop, rebalance_freq: r.rebalance_freq || 'day' }
  rerunOpen.value = true
}

async function onRerun() {
  rerunning.value = true
  try {
    const f = rerunForm.value
    await runBacktest(result.value.strategy_id, {
      start: f.start, end: f.end, topk: f.topk, n_drop: f.n_drop, rebalance_freq: f.rebalance_freq,
    })
    ElMessage.success('回测已提交，等待执行完成…')
    rerunOpen.value = false
    await waitAndJump()
  } catch {
    // 拦截器已弹业务/网络错误提示
    rerunning.value = false
  }
}

// 回测为独立子进程异步执行：单状态轮询直到完成，然后取最新结果跳转。
// 每 3s 一个请求（原实现每 2s 打状态+列表两个请求，最多 600 轮 ≈1200 请求），
// 改为仅在状态发生终态跳变时才拉一次列表，最多 300 轮（15min）兜底。
async function waitAndJump() {
  const sid = result.value.strategy_id
  for (let i = 0; i < 300; i++) {
    await new Promise((r) => setTimeout(r, 3000))
    let st = null
    try {
      const res = await getAllBacktestStatuses()
      st = (res?.items || {})[sid] || { status: 'idle' }
    } catch {
      // 轮询失败继续重试
      continue
    }
    if (st.status === 'running') continue
    if (st.status === 'failed') {
      ElMessage.error('回测失败：' + (st.error || st.message || '详见日志'))
      break
    }
    // completed / idle：拉最新结果
    try {
      const list = await listBacktestResults(sid, { limit: 1 })
      const latest = Array.isArray(list) ? list[0] : list?.items?.[0]
      if (latest?.id && String(latest.id) !== String(route.params.id)) {
        router.replace(`/quant/backtest/${latest.id}`)
      } else {
        await reload()
      }
      break
    } catch {
      // 结果可能尚未落库，下一轮重试
    }
  }
  rerunning.value = false
}

async function reload() {
  loading.value = true
  try {
    result.value = await loadResult()
    if (detailSource.value && result.value && !result.value.name) {
      result.value.name = result.value.template_name || '策略回测'
    }
  } finally {
    loading.value = false
  }
}

async function onDelete() {
  try {
    await ElMessageBox.confirm('确认删除该回测结果？删除后不可恢复。', '删除确认', { type: 'warning' })
  } catch {
    return
  }
  try {
    if (detailSource.value === 'rule') await deleteStrategyHistory(result.value.history_id)
    else if (detailSource.value === 'classic') await deleteClassicHistory(result.value.history_id)
    else await deleteBacktestResult(result.value.id)
    ElMessage.success('已删除')
    goList()
  } catch {
    // 拦截器已弹错误提示
  }
}

function exportJson() {
  const blob = new Blob([JSON.stringify(result.value, null, 2)], { type: 'application/json' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `backtest-${result.value.id ?? result.value.history_id}.json`
  a.click()
  URL.revokeObjectURL(url)
}

function goList() {
  router.push(detailSource.value ? '/quant/strategy-library' : '/quant/strategy')
}

function scrollTo(id) {
  activeSection.value = id
  document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}
</script>

<style scoped>
.detail-meta {
  display: flex;
  align-items: center;
  gap: var(--space-12) var(--space-md);
  flex-wrap: wrap;
  margin: calc(-1 * var(--space-sm)) 0 var(--space-md);
  padding: var(--space-sm) var(--space-md);
  background: var(--bg-secondary);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  font-size: var(--font-size-base);
  color: var(--text-secondary);
}
.detail-meta b {
  color: var(--text-primary);
  font-weight: var(--font-weight-medium);
}
.detail-meta .ok {
  color: var(--success);
  font-style: normal;
}
.detail-meta .pill {
  background: var(--primary-soft);
  color: var(--primary);
  border-radius: var(--radius-full);
  padding: var(--space-2xs) var(--space-sm);
  font-size: var(--font-size-sm);
}
.detail-card {
  scroll-margin-top: var(--space-12);
}
.jq-tab {
  font-size: var(--font-size-lg);
  color: var(--text-secondary);
  padding-bottom: var(--space-xs);
  cursor: pointer;
  border-bottom: 2px solid transparent;
}
.jq-tab--on {
  color: var(--primary);
  border-bottom-color: var(--primary);
  font-weight: var(--font-weight-medium);
}
.jq-body {
  display: grid;
  grid-template-columns: 150px 1fr;
  gap: var(--space-12);
  min-height: 400px;
}
.jq-side {
  position: sticky;
  top: var(--space-12);
  align-self: start;
  display: flex;
  flex-direction: column;
  gap: var(--space-2xs);
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  padding: var(--space-sm);
}
.jq-side__item {
  font-size: var(--font-size-base);
  color: var(--text-secondary);
  padding: var(--space-sm) var(--space-12);
  border-radius: var(--radius-sm);
  cursor: pointer;
}
.jq-side__item:hover {
  background: var(--bg-hover);
}
.jq-side__item--on {
  background: var(--primary);
  color: var(--text-inverse);
  font-weight: var(--font-weight-medium);
}
.jq-main {
  display: flex;
  flex-direction: column;
  gap: 0;
  min-width: 0;
}
.jq-zoom {
  display: flex;
  align-items: center;
  gap: var(--space-xs);
  font-size: var(--font-size-sm);
  color: var(--text-secondary);
}
.jq-zoom__btn {
  padding: var(--space-2xs) var(--space-sm);
  border-radius: var(--radius-full);
  cursor: pointer;
  font-size: var(--font-size-sm);
}
.jq-zoom__btn:hover {
  color: var(--primary);
}
.jq-zoom__btn--on {
  background: var(--primary);
  color: var(--text-inverse);
}
.jq-switch {
  margin-left: auto;
}
.jq-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: var(--space-sm) var(--space-md);
}
.jq-grid__item {
  display: flex;
  flex-direction: column;
  gap: 3px;
}
.jq-grid__label {
  font-size: var(--font-size-sm);
  color: var(--text-secondary);
}
.jq-grid__value {
  font-size: var(--font-size-lg);
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}
.jq-grid__ci {
  font-size: var(--font-size-sm);
  color: var(--text-secondary);
}
.jq-chart {
  width: 100%;
  height: 360px;
}
.jq-chart--sub {
  height: 180px;
}
.jq-note {
  font-size: var(--font-size-sm);
  color: var(--text-secondary);
  margin: 0 0 var(--space-sm);
}
.jq-mc-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: var(--space-12);
}
.jq-params {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: var(--space-sm) var(--space-md);
}
.jq-params__item {
  display: flex;
  justify-content: space-between;
  font-size: var(--font-size-base);
  color: var(--text-secondary);
  border-bottom: 1px dashed var(--border);
  padding: var(--space-sm) 0;
}
.jq-params__item b {
  color: var(--text-primary);
  font-weight: 500;
}
.jq-bar {
  display: flex;
  align-items: center;
  gap: var(--space-sm);
}
.jq-bar__fill {
  display: inline-block;
  height: 8px;
  border-radius: var(--radius-sm);
  min-width: 2px;
}
.jq-bar__fill--up {
  background: var(--chart-up);
}
.jq-bar__fill--down {
  background: var(--chart-down);
}
.jq-bar__pct {
  font-size: var(--font-size-sm);
  color: var(--text-secondary);
  white-space: nowrap;
}
.jq-pager {
  margin-top: var(--space-sm);
  justify-content: flex-end;
}
.jq-reconstruct-alert {
  margin-bottom: var(--space-sm);
}
.jq-review-events {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-xs);
  margin-bottom: var(--space-sm);
}
.jq-review-event {
  white-space: normal;
  height: auto;
}
.jq-review-pre {
  margin: 0;
  padding: var(--space-sm) var(--space-12);
  background: var(--bg-tertiary);
  border-radius: var(--radius-sm);
  font-family: inherit;
  font-size: var(--font-size-base);
  line-height: 1.7;
  white-space: pre-wrap;
  word-break: break-word;
  color: var(--text-primary);
  max-height: 480px;
  overflow: auto;
}

@media (max-width: 1100px) {
  .jq-grid,
  .jq-params {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .jq-body {
    grid-template-columns: 1fr;
  }
  .jq-side {
    position: static;
    flex-direction: row;
    flex-wrap: wrap;
  }
}
</style>
