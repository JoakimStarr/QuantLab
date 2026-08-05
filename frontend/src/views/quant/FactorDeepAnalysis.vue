<template>
  <PageContainer>
    <header class="page-header">
      <div class="page-header__lead">
        <h1 class="page-header__title">因子深度分析 — {{ factorName }}</h1>
        <p class="page-header__subtitle">IC 时序 · 分层收益 · 换手率 · IC 衰减多维度评估</p>
      </div>
      <div class="page-header__actions">
        <el-button @click="goBack">返回因子库</el-button>
      </div>
    </header>

    <!-- 参数栏 -->
    <SectionCard title="分析参数" subtitle="选择日期范围、预测周期与分组数">
      <div class="param-bar">
        <el-date-picker
          v-model="dateRange"
          type="daterange"
          range-separator="至"
          start-placeholder="开始日期"
          end-placeholder="结束日期"
          value-format="YYYY-MM-DD"
          :clearable="false"
          style="width: 320px"
        />
        <el-select v-model="horizon" style="width: 140px">
          <el-option label="horizon = 1" :value="1" />
          <el-option label="horizon = 5" :value="5" />
          <el-option label="horizon = 20" :value="20" />
        </el-select>
        <el-select v-model="nGroups" style="width: 140px">
          <el-option label="5 分组" :value="5" />
          <el-option label="10 分组" :value="10" />
        </el-select>
        <div class="param-bar__spacer" />
        <el-button type="primary" :loading="loading" :disabled="!factorId" @click="runAnalysis">开始分析</el-button>
      </div>
    </SectionCard>

    <el-empty v-if="!factorId" description="缺少 factor_id 参数，请从因子库进入" :image-size="120" />

    <template v-else>
      <!-- 数值卡片行 -->
      <div v-loading="loading" class="stat-row">
        <el-card v-for="card in statCards" :key="card.key" class="stat-card" shadow="hover">
          <div class="stat-card__label">{{ card.label }}</div>
          <div class="stat-card__value" :class="card.cls">
            {{ card.value }}<span v-if="card.suffix" class="stat-card__suffix">{{ card.suffix }}</span>
          </div>
          <div v-if="card.note" class="stat-card__note">{{ card.note }}</div>
        </el-card>
      </div>

      <!-- IC 时序 & IC 分布 -->
      <el-row :gutter="20">
        <el-col :span="12">
          <SectionCard title="IC 时序" subtitle="日 IC（浅色）与 60 日均线（深色），虚线为 0 轴">
            <div class="chart-area">
              <el-empty v-if="!hasIcTs" description="暂无数据" :image-size="80" />
              <v-chart v-else class="chart" :option="icTimeseriesOption" autoresize />
            </div>
          </SectionCard>
        </el-col>
        <el-col :span="12">
          <SectionCard title="IC 分布" subtitle="直方图，虚线为 IC 均值">
            <div class="chart-area">
              <el-empty v-if="!hasIcDist" description="暂无数据" :image-size="80" />
              <v-chart v-else class="chart" :option="icDistOption" autoresize />
            </div>
          </SectionCard>
        </el-col>
      </el-row>

      <!-- 分层累计收益 -->
      <SectionCard title="分层累计收益" subtitle="Q1（红）→ Q5（绿）分组净值与多空曲线（黑色粗线）">
        <div class="chart-area chart-area--tall">
          <el-empty v-if="!hasQuantile" description="暂无数据" :image-size="80" />
          <v-chart v-else class="chart" :option="quantileOption" autoresize />
        </div>
      </SectionCard>

      <!-- 换手率 & IC 衰减 -->
      <el-row :gutter="20">
        <el-col :span="12">
          <SectionCard title="换手率曲线" subtitle="虚线为平均换手率">
            <div class="chart-area">
              <el-empty v-if="!hasTurnover" description="暂无数据" :image-size="80" />
              <v-chart v-else class="chart" :option="turnoverOption" autoresize />
            </div>
          </SectionCard>
        </el-col>
        <el-col :span="12">
          <SectionCard title="IC 衰减曲线" subtitle="阴影区为 IC > 0.03 的有效区间">
            <div class="chart-area">
              <el-empty v-if="!hasDecay" description="暂无数据" :image-size="80" />
              <v-chart v-else class="chart" :option="decayOption" autoresize />
            </div>
          </SectionCard>
        </el-col>
      </el-row>
    </template>
  </PageContainer>
</template>
<script setup>
defineOptions({ name: 'FactorDeepAnalysis' })
import { ref, computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus/es/components/message/index'
import VChart from 'vue-echarts'
import '@/utils/echarts'
import PageContainer from '@/components/common/PageContainer.vue'
import SectionCard from '@/components/common/SectionCard.vue'
import { deepAnalysis } from '@/api/quant'
import { chartTheme, quantileGradient } from '@/utils/chartTheme'
import { useThemeRev } from '@/composables/useChartTheme'

const themeRev = useThemeRev()

const route = useRoute()
const router = useRouter()

const factorId = computed(() => route.query.factor_id)
const factorName = computed(() => route.query.factor_name || '因子')

// 参数
const dateRange = ref(['2020-01-01', '2024-12-31'])
const horizon = ref(5)
const nGroups = ref(5)
const icWindow = 60

const loading = ref(false)
const result = ref(null)

// 配色（语义色走 CSS token，随主题切换；后续为扩展调色板）
const colors = [
  chartTheme.primary(),
  chartTheme.success(),
  chartTheme.danger(),
  chartTheme.warning(),
  chartTheme.info(),
  chartTheme.palette(6),
  chartTheme.palette(7),
  chartTheme.palette(8),
]
// Q1（红）→ Q5（绿）渐变，最多支持 10 分组
const quantileColors = quantileGradient

// === 数据解析（兼容多种后端结构） ===
const summary = computed(() => result.value?.summary || {})

const icTimeseries = computed(() => {
  const d = result.value?.ic_timeseries
  if (!d) return { dates: [], ic: [] }
  if (Array.isArray(d)) {
    return {
      dates: d.map((x) => x.date || x.ts || x.time || ''),
      ic: d.map((x) => Number(x.ic ?? x.value)),
    }
  }
  const dates = d.dates || d.x || []
  const ic = d.ic || d.values || d.y || []
  return { dates, ic: ic.map(Number) }
})

const icDistribution = computed(() => {
  const d = result.value?.ic_distribution
  if (!d) return { bins: [], counts: [] }
  if (Array.isArray(d)) {
    return {
      bins: d.map((x) => x.bin ?? x.label ?? x.x ?? ''),
      counts: d.map((x) => Number(x.count ?? x.freq ?? x.y ?? 0)),
    }
  }
  const bins = d.bins || d.edges || d.labels || d.x || []
  const counts = d.counts || d.freq || d.frequencies || d.y || []
  return { bins, counts: counts.map(Number) }
})

const quantileReturns = computed(() => {
  const d = result.value?.quantile_returns
  if (!d) return { dates: [], groups: {}, longShort: [] }
  const dates = d.dates || d.x || []
  const groups = d.group_nav || d.groups || d.quantiles || d.nav || {}
  const longShort = d.long_short_nav || d.long_short || d.ls_nav || []
  return { dates, groups, longShort: longShort.map(Number) }
})

const turnoverCurve = computed(() => {
  const d = result.value?.turnover_curve
  if (!d) return { dates: [], turnover: [] }
  if (Array.isArray(d)) {
    return {
      dates: d.map((x) => x.date || x.ts || ''),
      turnover: d.map((x) => Number(x.turnover ?? x.value ?? 0)),
    }
  }
  const dates = d.dates || d.x || []
  const turnover = d.turnover || d.values || d.y || []
  return { dates, turnover: turnover.map(Number) }
})

const decay = computed(() => {
  const d = result.value?.decay
  if (!d) return { lags: [], ic: [] }
  if (Array.isArray(d)) {
    return {
      lags: d.map((x) => Number(x.lag ?? x.lag_days ?? x.x ?? 0)),
      ic: d.map((x) => Number(x.ic ?? x.value ?? x.y ?? 0)),
    }
  }
  const lags = d.lags || d.lag || d.x || []
  const ic = d.ic || d.values || d.y || []
  return { lags: lags.map(Number), ic: ic.map(Number) }
})

const hasIcTs = computed(() => icTimeseries.value.ic.length > 0)
const hasIcDist = computed(() => icDistribution.value.counts.length > 0)
const hasQuantile = computed(() => quantileReturns.value.dates.length > 0)
const hasTurnover = computed(() => turnoverCurve.value.turnover.length > 0)
const hasDecay = computed(() => decay.value.ic.length > 0)
// === 数值格式化 ===
function fmtNum(val, digits = 3, suffix = '') {
  if (val === null || val === undefined || val === '') return '—'
  const n = Number(val)
  if (Number.isNaN(n)) return '—'
  const display = suffix === '%' ? n * 100 : n
  return display.toFixed(digits) + suffix
}

function numClass(val) {
  const n = Number(val)
  if (Number.isNaN(n) || n === 0) return ''
  return n > 0 ? 'is-positive' : 'is-negative'
}

// 提取分箱中心（兼容数值 / "[-0.1,0.0)" / "-0.1~0.0" 等格式）
function binCenter(b) {
  if (typeof b === 'number') return b
  const nums = String(b).match(/-?\d+\.?\d*/g)
  if (nums && nums.length >= 2) return (Number(nums[0]) + Number(nums[1])) / 2
  if (nums && nums.length === 1) return Number(nums[0])
  return NaN
}

// === 数值卡片 ===
const statCards = computed(() => {
  const s = summary.value
  const hasT = s.t_stat != null && s.t_stat !== '' && !Number.isNaN(Number(s.t_stat))
  const tStat = hasT ? Number(s.t_stat) : null
  const tSig = hasT ? Math.abs(tStat) >= 2 : null
  const hasP = s.p_value != null && s.p_value !== '' && !Number.isNaN(Number(s.p_value))
  const pValue = hasP ? Number(s.p_value) : null
  return [
    { key: 'ic_mean', label: 'IC 均值', value: fmtNum(s.ic_mean, 4), cls: numClass(s.ic_mean) },
    { key: 'icir', label: 'ICIR', value: fmtNum(s.icir, 3), cls: numClass(s.icir) },
    {
      key: 't_stat',
      label: 't-stat',
      value: fmtNum(s.t_stat, 3),
      cls: tSig === null ? '' : tSig ? 'is-positive' : 'is-negative',
      note: tSig === null ? '' : tSig ? '显著' : '不显著',
    },
    {
      key: 'p_value',
      label: 'p-value',
      value: fmtNum(s.p_value, 4) + (hasP && pValue < 0.05 ? ' ★' : ''),
      cls: hasP && pValue < 0.05 ? 'is-positive' : '',
    },
    { key: 'annual_turnover', label: '年化换手', value: fmtNum(s.annual_turnover, 2, '%'), cls: '' },
    {
      key: 'long_short_annual',
      label: '多空年化',
      value: fmtNum(s.long_short_annual, 2, '%'),
      cls: numClass(s.long_short_annual),
    },
  ]
})
// === 图表配置 ===
// IC 时序：日 IC 浅色 + 60 日均线深色 + 0 轴参考线
const icTimeseriesOption = computed(() => {
  void themeRev.value
  const { dates, ic } = icTimeseries.value
  const win = icWindow
  const ma = []
  for (let i = 0; i < ic.length; i++) {
    const start = Math.max(0, i - win + 1)
    const slice = ic.slice(start, i + 1).filter((v) => !Number.isNaN(v))
    ma.push(slice.length ? Number((slice.reduce((a, b) => a + b, 0) / slice.length).toFixed(6)) : null)
  }
  return {
    tooltip: { trigger: 'axis' },
    textStyle: { color: chartTheme.axisText() },
    legend: { top: 0, data: ['日 IC', win + '日均线'], textStyle: { color: chartTheme.axisText() } },
    grid: { left: '3%', right: '4%', bottom: '12%', top: 40, containLabel: true },
    xAxis: {
      type: 'category',
      data: dates,
      boundaryGap: false,
      axisLabel: { hideOverlap: true, color: chartTheme.axisText() },
    },
    yAxis: { type: 'value', name: 'IC', scale: true, axisLabel: { color: chartTheme.axisText() } },
    dataZoom: [{ type: 'inside' }, { type: 'slider', height: 20, textStyle: { color: chartTheme.axisText() } }],
    series: [
      {
        name: '日 IC',
        type: 'line',
        data: ic,
        showSymbol: false,
        lineStyle: { width: 1, color: chartTheme.line() },
        itemStyle: { color: chartTheme.line() },
        markLine: {
          silent: true,
          symbol: 'none',
          lineStyle: { color: chartTheme.neutral(), type: 'dashed' },
          data: [{ yAxis: 0 }],
        },
      },
      {
        name: win + '日均线',
        type: 'line',
        data: ma,
        showSymbol: false,
        smooth: true,
        lineStyle: { width: 2, color: colors[0] },
        itemStyle: { color: colors[0] },
      },
    ],
  }
})

// IC 分布直方图：markLine 标均值
const icDistOption = computed(() => {
  void themeRev.value
  const { bins, counts } = icDistribution.value
  const centers = bins.map(binCenter)
  const allNumeric = centers.length > 0 && centers.every((c) => !Number.isNaN(c))
  const icMean = Number(summary.value.ic_mean)
  const base = { type: 'bar', barCategoryGap: '0%', itemStyle: { color: colors[4] } }
  if (allNumeric) {
    const data = centers.map((c, i) => [c, counts[i]])
    return {
      tooltip: { trigger: 'axis' },
      textStyle: { color: chartTheme.axisText() },
      grid: { left: '3%', right: '4%', bottom: '10%', top: 30, containLabel: true },
      xAxis: { type: 'value', name: 'IC', axisLabel: { color: chartTheme.axisText() } },
      yAxis: { type: 'value', name: '频次', axisLabel: { color: chartTheme.axisText() } },
      series: [
        {
          ...base,
          data,
          markLine: !Number.isNaN(icMean)
            ? {
                silent: true,
                symbol: 'none',
                lineStyle: { color: colors[2], type: 'dashed', width: 2 },
                data: [{ xAxis: icMean, label: { formatter: '均值 ' + icMean.toFixed(4) } }],
              }
            : undefined,
        },
      ],
    }
  }
  return {
    tooltip: { trigger: 'axis' },
    textStyle: { color: chartTheme.axisText() },
    grid: { left: '3%', right: '4%', bottom: '10%', top: 30, containLabel: true },
    xAxis: { type: 'category', data: bins.map(String), axisLabel: { color: chartTheme.axisText() } },
    yAxis: { type: 'value', name: '频次', axisLabel: { color: chartTheme.axisText() } },
    series: [{ ...base, data: counts }],
  }
})

// 分层累计收益：Q1-Q5 渐变 + 多空黑色粗线，tooltip 联动
const quantileOption = computed(() => {
  void themeRev.value
  const { dates, groups, longShort } = quantileReturns.value
  const n = nGroups.value
  const series = []
  for (let g = 1; g <= n; g++) {
    const data = (groups[String(g)] || []).map(Number)
    const color = quantileColors[(g - 1) % quantileColors.length]
    series.push({
      name: 'Q' + g,
      type: 'line',
      data,
      showSymbol: false,
      smooth: false,
      lineStyle: { width: 1.5, color },
      itemStyle: { color },
    })
  }
  series.push({
    name: '多空',
    type: 'line',
    data: longShort,
    showSymbol: false,
    lineStyle: { width: 2.5, color: chartTheme.textPrimary() },
    itemStyle: { color: chartTheme.textPrimary() },
  })
  return {
    tooltip: { trigger: 'axis', axisPointer: { type: 'cross' } },
    textStyle: { color: chartTheme.axisText() },
    legend: { top: 0, textStyle: { color: chartTheme.axisText() } },
    grid: { left: '3%', right: '4%', bottom: '12%', top: 40, containLabel: true },
    xAxis: {
      type: 'category',
      data: dates,
      boundaryGap: false,
      axisLabel: { hideOverlap: true, color: chartTheme.axisText() },
    },
    yAxis: { type: 'value', name: '净值', scale: true, axisLabel: { color: chartTheme.axisText() } },
    dataZoom: [{ type: 'inside' }, { type: 'slider', height: 20, textStyle: { color: chartTheme.axisText() } }],
    series,
  }
})
// 换手率曲线：bar + markLine 平均换手率
const turnoverOption = computed(() => {
  void themeRev.value
  const { dates, turnover } = turnoverCurve.value
  const valid = turnover.filter((v) => !Number.isNaN(v))
  const avg = valid.length ? valid.reduce((a, b) => a + b, 0) / valid.length : 0
  // 假设换手率为小数，展示为百分比
  const data = turnover.map((v) => (Number.isNaN(v) ? null : Number((v * 100).toFixed(4))))
  return {
    tooltip: {
      trigger: 'axis',
      formatter: (params) => {
        if (!params.length) return ''
        let s = params[0].axisValue + '<br/>'
        params.forEach((p) => {
          s += p.marker + p.seriesName + ': ' + (p.value != null ? p.value + '%' : '—') + '<br/>'
        })
        return s
      },
    },
    grid: { left: '3%', right: '4%', bottom: '12%', top: 30, containLabel: true },
    xAxis: { type: 'category', data: dates, axisLabel: { hideOverlap: true, color: chartTheme.axisText() } },
    yAxis: { type: 'value', name: '换手率%', axisLabel: { formatter: '{value}%', color: chartTheme.axisText() } },
    dataZoom: [{ type: 'inside' }, { type: 'slider', height: 20, textStyle: { color: chartTheme.axisText() } }],
    series: [
      {
        name: '换手率',
        type: 'bar',
        data,
        itemStyle: { color: colors[3] },
        markLine: {
          silent: true,
          symbol: 'none',
          lineStyle: { color: colors[2], type: 'dashed' },
          data: [
            { yAxis: Number((avg * 100).toFixed(4)), label: { formatter: '均值 ' + (avg * 100).toFixed(2) + '%' } },
          ],
        },
      },
    ],
  }
})

// IC 衰减曲线：折线 + markArea 标 IC>0.03 区间
const decayOption = computed(() => {
  void themeRev.value
  const { lags, ic } = decay.value
  const data = lags.map((l, i) => [l, ic[i]])
  const areas = []
  let start = null
  for (let i = 0; i < lags.length; i++) {
    if (ic[i] > 0.03) {
      if (start === null) start = lags[i]
    } else if (start !== null) {
      areas.push([{ xAxis: start }, { xAxis: lags[i - 1] }])
      start = null
    }
  }
  if (start !== null && lags.length) {
    areas.push([{ xAxis: start }, { xAxis: lags[lags.length - 1] }])
  }
  return {
    tooltip: { trigger: 'axis' },
    grid: { left: '3%', right: '4%', bottom: '10%', top: 30, containLabel: true },
    xAxis: { type: 'value', name: 'lag', minInterval: 1 },
    yAxis: { type: 'value', name: 'IC' },
    series: [
      {
        name: 'IC',
        type: 'line',
        data,
        smooth: true,
        showSymbol: true,
        lineStyle: { width: 2, color: colors[0] },
        itemStyle: { color: colors[0] },
        markLine: {
          silent: true,
          symbol: 'none',
          data: [
            { yAxis: 0, lineStyle: { color: chartTheme.neutral(), type: 'dashed' } },
            { yAxis: 0.03, lineStyle: { color: colors[1], type: 'dashed' }, label: { formatter: 'IC=0.03' } },
          ],
        },
        markArea: {
          silent: true,
          itemStyle: { color: chartTheme.successSoft() },
          data: areas,
        },
      },
    ],
  }
})

// === 数据获取 ===
async function runAnalysis() {
  if (!factorId.value) {
    ElMessage.warning('缺少因子 ID')
    return
  }
  if (!dateRange.value || dateRange.value.length !== 2) {
    ElMessage.warning('请选择日期范围')
    return
  }
  loading.value = true
  try {
    const data = await deepAnalysis(factorId.value, {
      start_date: dateRange.value[0],
      end_date: dateRange.value[1],
      horizon: horizon.value,
      n_groups: nGroups.value,
      ic_window: icWindow,
    })
    result.value = data || {}
    if (!data) ElMessage.warning('未返回分析数据')
  } catch (e) {
    const msg = e?.message || e?.code || '未知错误'
    ElMessage.error('因子深度分析失败: ' + msg)
    result.value = null
  } finally {
    loading.value = false
  }
}

function goBack() {
  router.push('/quant/factors')
}

onMounted(() => {
  if (factorId.value) runAnalysis()
})
</script>
<style scoped lang="scss">
.page-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--space-md);
  margin-bottom: var(--space-lg);
  flex-wrap: wrap;

  &__lead {
    flex: 1;
    min-width: 0;
  }

  &__title {
    font-size: var(--font-size-2xl);
    font-weight: 700;
    color: var(--text-primary);
    margin: 0 0 var(--space-xs);
  }

  &__subtitle {
    font-size: var(--font-size-sm);
    color: var(--text-secondary);
  }

  &__actions {
    display: flex;
    align-items: center;
    gap: var(--space-sm);
  }
}

.param-bar {
  display: flex;
  align-items: center;
  gap: var(--space-md);
  flex-wrap: wrap;

  &__spacer {
    flex: 1;
  }
}

.stat-row {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: var(--space-md);
  margin-bottom: var(--space-md);
}

.stat-card {
  :deep(.el-card__body) {
    padding: var(--space-md) var(--space-lg);
  }

  &__label {
    font-size: var(--font-size-sm);
    color: var(--text-tertiary);
    margin-bottom: 6px;
  }

  &__value {
    font-size: var(--font-size-xl);
    font-weight: 700;
    font-family: var(--font-mono);
    color: var(--text-primary);
    line-height: 1.2;
  }

  &__suffix {
    font-size: var(--font-size-sm);
    font-weight: 500;
    margin-left: 4px;
  }

  &__note {
    font-size: var(--font-size-xs);
    margin-top: 4px;
    color: var(--text-tertiary);
  }
}

.is-positive {
  color: var(--success);
}

.is-negative {
  color: var(--danger);
}

.chart-area {
  height: 360px;
  width: 100%;

  &--tall {
    height: 440px;
  }
}

.chart {
  width: 100%;
  height: 100%;
}
</style>
