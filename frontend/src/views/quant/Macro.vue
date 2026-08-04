<template>
  <PageContainer narrow>
    <div class="page-header mb-6">
      <h2 class="page-title">宏观指标</h2>
      <p class="page-desc">东财 + akshare 宏观数据（PMI/CPI/PPI/GDP/国债/Shibor/汇率等），同步后广播为 qlib 因子字段</p>
    </div>

    <!-- 操作区 -->
    <SectionCard class="mb-6">
      <div class="macro-toolbar">
        <div class="toolbar-left">
          <el-select v-model="selectedField" size="small" style="width: 170px" @change="loadSeries">
            <el-option-group v-for="g in fieldGroups" :key="g.label" :label="g.label">
              <el-option v-for="f in g.options" :key="f.key" :value="f.key" :label="f.label" />
            </el-option-group>
          </el-select>
          <el-radio-group v-model="timeRange" size="small" @change="loadSeries">
            <el-radio-button v-for="r in timeOptions" :key="r.key" :value="r.key">{{ r.label }}</el-radio-button>
          </el-radio-group>
        </div>
        <div class="toolbar-right">
          <el-button size="small" @click="loadSeries" :loading="loading">刷新</el-button>
          <el-button size="small" type="primary" @click="doSync" :loading="syncing">
            {{ syncing ? '同步中...' : '同步宏观数据' }}
          </el-button>
        </div>
      </div>
      <div v-if="syncMessage" class="sync-message">{{ syncMessage }}</div>
      <div v-if="syncProgress" class="sync-progress">
        <div class="progress-header">
          <span class="progress-status">{{ syncProgress.message || '同步中...' }}</span>
          <span class="progress-pct">{{ (syncProgress.progress_pct || 0).toFixed(1) }}%</span>
        </div>
        <el-progress
          :percentage="syncProgress.progress_pct || 0"
          :status="syncProgress.status === 'failed' ? 'exception' : syncProgress.status === 'done' ? 'success' : ''"
          :stroke-width="12"
          :show-text="false"
        />
      </div>
    </SectionCard>

    <!-- 走势图 -->
    <SectionCard :title="currentLabel" class="mb-6">
      <div v-if="seriesLoading" class="chart-wrap">
        <el-skeleton :rows="8" animated />
      </div>
      <v-chart v-else-if="seriesData.length" :option="chartOption" class="chart-macro" autoresize />
      <el-empty v-else description="暂无数据，请先同步宏观指标" />
    </SectionCard>

    <!-- 最新值快照 -->
    <SectionCard v-if="snapshotItems.length" title="最新值" class="mb-6">
      <div class="snapshot-grid">
        <div v-for="it in snapshotItems" :key="it.indicator + '-' + it.field_name" class="snapshot-cell" :title="it.available_date + (it.prevDate ? '，较 ' + it.prevDate : '')">
          <div class="snapshot-label">{{ it.label }}</div>
          <div class="snapshot-value" :class="trendClass(it.change)">{{ formatValue(it.value) }}<span v-if="it.unit" class="snapshot-unit">{{ it.unit }}</span></div>
          <div v-if="hasChange(it.change)" class="snapshot-trend" :class="trendClass(it.change)">
            <el-icon v-if="it.change > 0"><CaretTop /></el-icon>
            <el-icon v-else><CaretBottom /></el-icon>
            <span>{{ fmtChange(it.change) }}</span>
          </div>
          <div class="snapshot-date">{{ it.available_date }}</div>
        </div>
      </div>
    </SectionCard>

    <!-- 同步状态 -->
    <SectionCard title="同步状态">
      <el-table :data="statusItems" size="small" stripe empty-text="暂无数据">
        <el-table-column prop="indicator" label="指标" width="120" align="center" />
        <el-table-column prop="field_name" label="字段" width="120" align="center" />
        <el-table-column prop="count" label="记录数" width="100" align="right" />
        <el-table-column prop="latest_date" label="最新可用日" align="center" />
      </el-table>
    </SectionCard>
  </PageContainer>
</template>

<script setup>
defineOptions({ name: 'QuantMacro' })
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'
import { ElMessage } from 'element-plus/es/components/message/index'
import PageContainer from '@/components/common/PageContainer.vue'
import SectionCard from '@/components/common/SectionCard.vue'
import VChart from 'vue-echarts'
import { syncMacro, getMacroIndicators, getMacroStatus } from '@/api/macro'
import { getSyncProgress } from '@/api/quant'
import { chartTheme, echartPalette as C } from '@/utils/chartTheme'
import { useThemeRev } from '@/composables/useChartTheme'

const themeRev = useThemeRev()

// 指标选项（与后端 MACRO_INDICATORS / AKSHARE_INDICATORS 一致）
// 一个选项可包含多个字段序列，会在同一张图里叠加展示（如制造/非制造 PMI）
const fieldOptions = [
  // 景气/价格
  {
    key: 'pmi',
    indicator: 'PMI',
    label: 'PMI',
    group: '景气/价格',
    markLine: 50,
    fields: [
      { field: 'pmi', name: '制造业PMI', color: C.blue },
      { field: 'pmi_nm', name: '非制造业PMI', color: C.orangeAlt },
    ],
  },
  { key: 'cpi', indicator: 'CPI', label: 'CPI同比(%)', group: '景气/价格', fields: [{ field: 'cpi', name: 'CPI同比', color: C.purple }] },
  { key: 'ppi', indicator: 'PPI', label: 'PPI同比(%)', group: '景气/价格', fields: [{ field: 'ppi', name: 'PPI同比', color: C.teal }] },
  { key: 'gdp', indicator: 'GDP', label: 'GDP同比(%)', group: '景气/价格', fields: [{ field: 'gdp', name: 'GDP同比', color: C.grass }] },
  // 利率
  {
    key: 'treasury',
    indicator: 'TREASURY',
    label: '国债收益率',
    group: '利率',
    fields: [
      { field: 'trsy2y', name: '中债2Y', color: C.blue },
      { field: 'trsy5y', name: '中债5Y', color: C.green },
      { field: 'trsy10y', name: '中债10Y', color: C.gold },
      { field: 'trsy30y', name: '中债30Y', color: C.red },
      { field: 'trsy_spread_10y2y', name: '期限利差10Y-2Y', color: C.cyan },
      { field: 'us_trsy10y', name: '美债10Y', color: C.forest },
    ],
  },
  {
    key: 'shibor',
    indicator: 'SHIBOR',
    label: 'Shibor',
    group: '利率',
    fields: [
      { field: 'shibor_on', name: '隔夜', color: C.blue },
      { field: 'shibor_1w', name: '1周', color: C.green },
      { field: 'shibor_3m', name: '3月', color: C.gold },
      { field: 'shibor_1y', name: '1年', color: C.red },
    ],
  },
  {
    key: 'lpr',
    indicator: 'LPR',
    label: 'LPR',
    group: '利率',
    fields: [
      { field: 'lpr1y', name: 'LPR 1Y', color: C.blue },
      { field: 'lpr5y', name: 'LPR 5Y', color: C.orangeAlt },
    ],
  },
  {
    key: 'repofr',
    indicator: 'REPO_FR',
    label: '回购定盘利率',
    group: '利率',
    fields: [
      { field: 'fr001', name: 'FR001', color: C.blue },
      { field: 'fr007', name: 'FR007(≈R007)', color: C.orangeAlt },
      { field: 'fr014', name: 'FR014', color: C.teal },
    ],
  },
  {
    key: 'repofdr',
    indicator: 'REPO_FDR',
    label: '银银间回购',
    group: '利率',
    fields: [
      { field: 'fdr001', name: 'FDR001', color: C.blue },
      { field: 'fdr007', name: 'FDR007(≈DR007)', color: C.orangeAlt },
      { field: 'fdr014', name: 'FDR014', color: C.teal },
    ],
  },
  // 商品/汇率
  { key: 'commodity', indicator: 'COMMODITY', label: '大宗商品指数', group: '商品/汇率', fields: [{ field: 'commodity_idx', name: '商品价格指数', color: C.blue }] },
  { key: 'copper', indicator: 'COPPER', label: '沪铜', group: '商品/汇率', fields: [{ field: 'copper_close', name: '沪铜主力收盘价', color: C.red }] },
  { key: 'crude', indicator: 'CRUDE_OIL', label: '原油', group: '商品/汇率', fields: [{ field: 'crude_close', name: '原油SC主力', color: C.orange }] },
  { key: 'fx', indicator: 'FX', label: '人民币汇率', group: '商品/汇率', fields: [{ field: 'usdcny_mid', name: '美元中间价', color: C.blue }] },
  // 风险/情绪
  { key: 'ivix', indicator: 'IVIX', label: '波指iVIX', group: '风险/情绪', fields: [{ field: 'ivix', name: '50ETF波动率指数', color: C.red }] },
  {
    key: 'futif',
    indicator: 'FUTURES_IF',
    label: '股指期货IF',
    group: '风险/情绪',
    fields: [
      { field: 'if_close', name: 'IF主力收盘价', color: C.blue },
      { field: 'if_hold', name: 'IF持仓量', color: C.orangeAlt },
    ],
  },
  {
    key: 'futic',
    indicator: 'FUTURES_IC',
    label: '中证500期货',
    group: '风险/情绪',
    fields: [
      { field: 'ic_close', name: 'IC主力收盘价', color: C.blue },
      { field: 'ic_hold', name: 'IC持仓量', color: C.orangeAlt },
    ],
  },
  { key: 'futtf', indicator: 'FUTURES_TF', label: '国债期货', group: '风险/情绪', fields: [{ field: 'tf_close', name: 'TF主力收盘价', color: C.cyan }] },
  { key: 'gold', indicator: 'GOLD', label: '沪金', group: '风险/情绪', fields: [{ field: 'au_close', name: '沪金AU主力', color: C.gold }] },
  // 货币/信贷
  {
    key: 'moneysupply',
    indicator: 'MONEY_SUPPLY',
    label: '货币供应',
    group: '货币/信贷',
    fields: [
      { field: 'm0_yoy', name: 'M0同比', color: C.blue },
      { field: 'm1_yoy', name: 'M1同比', color: C.orangeAlt },
      { field: 'm2_yoy', name: 'M2同比', color: C.teal },
    ],
  },
  {
    key: 'socialfinance',
    indicator: 'SOCIAL_FINANCE',
    label: '社会融资',
    group: '货币/信贷',
    fields: [
      { field: 'social_finance', name: '社融增量', color: C.blue },
      { field: 'sf_rmb_loan', name: '社融-人民币贷款', color: C.orangeAlt },
    ],
  },
  {
    key: 'loan',
    indicator: 'LOAN',
    label: '新增贷款',
    group: '货币/信贷',
    fields: [
      { field: 'new_loan', name: '新增贷款', color: C.blue },
      { field: 'new_loan_yoy', name: '新增贷款同比', color: C.orangeAlt },
    ],
  },
  {
    key: 'margin',
    indicator: 'MARGIN',
    label: '两融余额',
    group: '货币/信贷',
    fields: [
      { field: 'margin_balance', name: '沪市两融余额', color: C.blue },
      { field: 'margin_balance_sz', name: '深市两融余额', color: C.orangeAlt },
    ],
  },
]

// 时间范围（默认最近 5 年）
const timeOptions = [
  { key: '1Y', label: '1年' },
  { key: '3Y', label: '3年' },
  { key: '5Y', label: '5年' },
  { key: 'ALL', label: '全部' },
]

const selectedField = ref('pmi')
const timeRange = ref('5Y')
const loading = ref(false)
const syncing = ref(false)
const seriesLoading = ref(false)
const syncMessage = ref('')
const syncProgress = ref(null)
let progressTimer = null
const seriesData = ref([])
const statusItems = ref([])
const snapshotItems = ref([])

const currentLabel = computed(() => {
  const f = fieldOptions.find(x => x.key === selectedField.value)
  return f ? `${f.label} 走势` : '走势'
})

// 按 group 分组的下拉选项（景气/价格、利率、商品/汇率、货币/信贷）
const fieldGroups = computed(() => {
  const groups = []
  const order = ['景气/价格', '利率', '商品/汇率', '风险/情绪', '货币/信贷']
  const byGroup = {}
  for (const f of fieldOptions) {
    const g = f.group || '其他'
    ;(byGroup[g] = byGroup[g] || []).push(f)
  }
  for (const g of order) {
    if (byGroup[g]) groups.push({ label: g, options: byGroup[g] })
  }
  return groups
})

function rangeStartDate() {
  if (timeRange.value === 'ALL') return null
  const start = new Date()
  switch (timeRange.value) {
    case '1Y': start.setFullYear(start.getFullYear() - 1); break
    case '3Y': start.setFullYear(start.getFullYear() - 3); break
    case '5Y': start.setFullYear(start.getFullYear() - 5); break
    default: return null
  }
  return start.toISOString().slice(0, 10)
}

// 数值显示：千分位 + 最多 2 位小数（大数如两融余额不再一长串）
function formatValue(v) {
  if (v == null) return '--'
  const n = Number(v)
  if (!Number.isFinite(n)) return '--'
  return n.toLocaleString('zh-CN', { maximumFractionDigits: 2 })
}

// 荣枯线穿越点图标（向上/向下箭头）
const RISING_ICON = 'path://M448 96 L832 512 L608 512 L608 928 L608 928 L288 928 L288 512 L64 512 Z'
const FALLING_ICON = 'path://M448 896 L832 480 L608 480 L608 64 L608 64 L288 64 L288 480 L64 480 Z'

// 计算某序列相对基准线的穿越点（越过 50 的拐点）
function crossingPoints(points, base) {
  const pts = []
  for (let i = 1; i < points.length; i++) {
    const p0 = points[i - 1]
    const p1 = points[i]
    if (p0.value == null || p1.value == null) continue
    const d0 = p0.value - base
    const d1 = p1.value - base
    if (d0 === 0 || d1 === 0) continue
    if (d0 * d1 < 0) {
      const rising = d1 > 0
      pts.push({
        coord: [p1.date, base],
        symbol: rising ? RISING_ICON : FALLING_ICON,
        symbolSize: 10,
        symbolOffset: [0, rising ? 8 : -8],
        itemStyle: { color: rising ? chartTheme.up() : chartTheme.down() },
        label: { show: false },
      })
    }
  }
  return pts
}

const chartOption = computed(() => {
  void themeRev.value
  // 合并所有序列的日期作为 x 轴（同一指标内多字段通常同日发布）
  const dateSet = new Set()
  for (const s of seriesData.value) {
    for (const p of s.points) dateSet.add(p.date)
  }
  const dates = [...dateSet].sort()
  const opt = fieldOptions.find(x => x.key === selectedField.value)

  // PMI 荣枯线（50）与扩张/收缩背景分区：上方淡绿、下方淡红
  const markArea = opt?.markLine != null
    ? (() => {
        const vals = seriesData.value.flatMap(s => s.points.map(p => p.value)).filter(v => v != null)
        const lo = Math.min(opt.markLine, ...(vals.length ? vals : [opt.markLine]))
        const hi = Math.max(opt.markLine, ...(vals.length ? vals : [opt.markLine]))
        return {
          silent: true,
          data: [
            [{ yAxis: opt.markLine, itemStyle: { color: chartTheme.areaAbove() } }, { yAxis: hi, itemStyle: { color: chartTheme.areaAbove() } }],
            [{ yAxis: lo, itemStyle: { color: chartTheme.areaBelow() } }, { yAxis: opt.markLine, itemStyle: { color: chartTheme.areaBelow() } }],
          ],
        }
      })()
    : undefined

  const series = seriesData.value.map(s => {
    const valByDate = new Map(s.points.map(p => [p.date, p.value]))
    const cfg = {
      name: s.name,
      type: 'line',
      smooth: true,
      symbol: 'circle',
      symbolSize: 5,
      data: dates.map(d => (valByDate.has(d) ? valByDate.get(d) : null)),
      lineStyle: { width: 2, color: s.color },
      itemStyle: { color: s.color },
    }
    if (seriesData.value.length === 1) cfg.areaStyle = { opacity: 0.12, color: s.color }
    const crosses = opt?.markLine != null ? crossingPoints(s.points, opt.markLine) : []
    if (crosses.length) cfg.markPoint = { symbol: 'pin', data: crosses, silent: true }
    return cfg
  })

  // 荣枯线作为独立序列展示在图例（可开关），不再在线上绘制文字
  if (opt?.markLine != null) {
    series.push({
      name: `荣枯线 ${opt.markLine}`,
      type: 'line',
      symbol: 'none',
      data: dates.map(() => opt.markLine),
      lineStyle: { type: 'dashed', color: chartTheme.baseline(), width: 2 },
      itemStyle: { color: chartTheme.baseline() },
      tooltip: { show: false },
      silent: true,
      z: 0,
      markArea: JSON.parse(JSON.stringify(markArea)),
    })
  }

  return {
    tooltip: { trigger: 'axis' },
    textStyle: { color: chartTheme.axisText() },
    legend: { top: 0, left: 8, textStyle: { color: chartTheme.axisText() } },
    grid: { left: 48, right: 24, top: 40, bottom: 40 },
    xAxis: { type: 'category', data: dates, axisLabel: { fontSize: 11, color: chartTheme.axisText() } },
    yAxis: { type: 'value', scale: true, axisLabel: { color: chartTheme.axisText() } },
    series,
  }
})

async function loadSeries() {
  seriesLoading.value = true
  try {
    const opt = fieldOptions.find(x => x.key === selectedField.value)
    const startDate = rangeStartDate()
    const series = []
    for (const f of opt?.fields ?? []) {
      const res = await getMacroIndicators({ indicator: opt.indicator, field: f.field })
      const items = res?.items ?? []
      series.push({
        field: f.field,
        name: f.name,
        color: f.color,
        points: items
          .filter(d => !startDate || d.available_date >= startDate)
          .map(d => ({ date: d.available_date, value: d.value })),
      })
    }
    seriesData.value = series
  } catch {
    seriesData.value = []
  } finally {
    seriesLoading.value = false
  }
}

async function loadStatus() {
  try {
    const res = await getMacroStatus()
    statusItems.value = res?.items ?? []
    // 快照：每个指标字段的最新一条 + 环比变化（最新值 - 上一条值）
    const all = await getMacroIndicators()
    const items = all?.items ?? []
    const labelMap = {}
    for (const opt of fieldOptions) {
      for (const f of opt.fields) {
        labelMap[f.field] = f.name
      }
    }
    const series = {}
    for (const it of items) {
      const k = `${it.indicator}-${it.field_name}`
      if (!series[k]) series[k] = []
      series[k].push(it)
    }
    snapshotItems.value = Object.values(series).map(arr => {
      arr.sort((a, b) => String(a.available_date).localeCompare(String(b.available_date)))
      const latest = arr[arr.length - 1]
      const prev = arr[arr.length - 2]
      const latestVal = Number(latest.value)
      const prevVal = prev ? Number(prev.value) : null
      const change = prev != null && prevVal != null && latestVal != null &&
        !Number.isNaN(latestVal) && !Number.isNaN(prevVal)
        ? latestVal - prevVal
        : null
      return {
        ...latest,
        label: labelMap[latest.field_name] || latest.field_name,
        change,
        prevDate: prev?.available_date ?? null
      }
    })
  } catch {
    statusItems.value = []
    snapshotItems.value = []
  }
}

function hasChange(v) {
  return v !== null && v !== undefined && Number(v) !== 0
}

function trendClass(v) {
  const n = Number(v)
  if (n === null || n === undefined || Number.isNaN(n) || n === 0) return ''
  return n > 0 ? 'is-up' : 'is-down'
}

function fmtChange(v) {
  const n = Number(v)
  if (Number.isNaN(n)) return ''
  return `${n > 0 ? '+' : ''}${Number(n.toFixed(4))}`
}

async function doSync() {
  syncing.value = true
  syncMessage.value = ''
  try {
    await syncMacro()
    syncMessage.value = '同步已提交（后台执行），进度见下方。'
    ElMessage.success('宏观同步已提交')
    startMacroProgressPolling()
    // 兜底：即使进度未显示，也刷新一次状态
    setTimeout(() => { loadStatus(); loadSeries() }, 5000)
  } catch (e) {
    if (e !== 'cancel') ElMessage.error('宏观同步提交失败: ' + (e?.message || e))
  } finally {
    syncing.value = false
  }
}

// 轮询宏观同步进度（共享 /quant/data/sync-progress，data_source=eastmoney）
function startMacroProgressPolling() {
  if (progressTimer) clearInterval(progressTimer)
  syncProgress.value = null
  let nullCount = 0
  const poll = async () => {
    try {
      const data = await getSyncProgress()
      if (data && data.data_source === 'eastmoney') {
        nullCount = 0
        syncProgress.value = data
        if (data.status === 'done' || data.status === 'failed') {
          clearInterval(progressTimer)
          progressTimer = null
          if (data.status === 'done') ElMessage.success('宏观同步完成')
          else ElMessage.error('宏观同步失败: ' + (data.error || '未知错误'))
          setTimeout(() => { syncProgress.value = null }, 2000)
          loadStatus()
          loadSeries()
        }
      } else if (data) {
        // 其他任务（如 baostock）占用进度文件，非本页关注
        nullCount = 0
        syncProgress.value = null
      } else {
        nullCount += 1
        if (nullCount > 20) {
          clearInterval(progressTimer)
          progressTimer = null
          syncProgress.value = null
        }
      }
    } catch (e) {
      // 忽略瞬时错误，继续轮询
    }
  }
  poll()
  progressTimer = setInterval(poll, 1000)
}

onMounted(() => {
  loadSeries()
  loadStatus()
})

onBeforeUnmount(() => {
  if (progressTimer) clearInterval(progressTimer)
})
</script>

<style scoped lang="scss">
.page-header { animation: fadeInUp 0.5s var(--ease-out-expo); }
.page-title { font-size: var(--font-size-2xl); font-weight: 700; color: var(--text-primary); }
.page-desc { font-size: var(--font-size-sm); color: var(--text-secondary); margin-top: 4px; }

.macro-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}
.toolbar-left { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.toolbar-right { display: flex; gap: 8px; }
.sync-message { margin-top: 12px; font-size: 13px; color: var(--text-secondary); }

.sync-progress {
  margin-top: 14px;
  padding: 12px 14px;
  background: var(--bg-tertiary, #f5f7fa);
  border-radius: 8px;
}
.progress-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }
.progress-status { font-size: 13px; color: var(--text-primary); }
.progress-pct { font-size: 13px; font-weight: 600; color: var(--primary); font-variant-numeric: tabular-nums; }

.chart-wrap { min-height: 200px; }
.chart-macro { height: 420px; }

.snapshot-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
  gap: 12px;
}
.snapshot-cell {
  padding: 14px 16px;
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: 10px;
}
.snapshot-label { font-size: 12px; color: var(--text-tertiary); }
.snapshot-value { font-size: 22px; font-weight: 700; color: var(--text-primary); margin-top: 4px; font-variant-numeric: tabular-nums; }
.snapshot-unit { font-size: 12px; font-weight: 400; color: var(--text-tertiary); margin-left: 2px; }
.snapshot-value.is-up { color: var(--chart-up); }
.snapshot-value.is-down { color: var(--chart-down); }
.snapshot-trend {
  display: flex; align-items: center; gap: 2px;
  margin-top: 4px; font-size: 12px; font-weight: 500;
  font-variant-numeric: tabular-nums;
}
.snapshot-trend .el-icon { font-size: 13px; }
.snapshot-trend.is-up { color: var(--chart-up); }
.snapshot-trend.is-down { color: var(--chart-down); }
.snapshot-date { font-size: 12px; color: var(--text-tertiary); margin-top: 4px; }

.mb-6 { margin-bottom: 24px; }

@keyframes fadeInUp {
  from { opacity: 0; transform: translateY(8px); }
  to { opacity: 1; transform: translateY(0); }
}
</style>
