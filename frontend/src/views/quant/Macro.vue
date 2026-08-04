<template>
  <PageContainer narrow>
    <div class="page-header mb-6">
      <h2 class="page-title">宏观指标</h2>
      <p class="page-desc">东财宏观数据（PMI/CPI/PPI/GDP），同步后广播为 qlib 因子字段（$pmi/$cpi/$ppi/$gdp）</p>
    </div>

    <!-- 操作区 -->
    <SectionCard class="mb-6">
      <div class="macro-toolbar">
        <div class="toolbar-left">
          <el-radio-group v-model="selectedField" size="small" @change="loadSeries">
            <el-radio-button v-for="f in fieldOptions" :key="f.key" :value="f.key">{{ f.label }}</el-radio-button>
          </el-radio-group>
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
    </SectionCard>

    <!-- 走势图 -->
    <SectionCard :title="currentLabel" class="mb-6">
      <div v-if="seriesLoading" class="chart-wrap">
        <el-skeleton :rows="8" animated />
      </div>
      <v-chart v-else-if="seriesData.length" :option="chartOption" class="chart-macro" autoresize />
      <el-empty v-else description="暂无数据，请先同步宏观指标" />
    </SectionCard>

    <!-- 数据状态 -->
    <SectionCard title="同步状态" class="mb-6">
      <el-table :data="statusItems" size="small" stripe empty-text="暂无数据">
        <el-table-column prop="indicator" label="指标" width="120" align="center" />
        <el-table-column prop="field_name" label="字段" width="120" align="center" />
        <el-table-column prop="count" label="记录数" width="100" align="right" />
        <el-table-column prop="latest_date" label="最新可用日" align="center" />
      </el-table>
    </SectionCard>

    <!-- 最新值快照 -->
    <SectionCard v-if="snapshotItems.length" title="最新值">
      <div class="snapshot-grid">
        <div v-for="it in snapshotItems" :key="it.indicator + '-' + it.field_name" class="snapshot-cell">
          <div class="snapshot-label">{{ it.label }}</div>
          <div class="snapshot-value">{{ it.value != null ? it.value : '--' }}<span v-if="it.unit" class="snapshot-unit">{{ it.unit }}</span></div>
          <div class="snapshot-date">{{ it.available_date }}</div>
        </div>
      </div>
    </SectionCard>
  </PageContainer>
</template>

<script setup>
defineOptions({ name: 'QuantMacro' })
import { ref, computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus/es/components/message/index'
import PageContainer from '@/components/common/PageContainer.vue'
import SectionCard from '@/components/common/SectionCard.vue'
import VChart from 'vue-echarts'
import { syncMacro, getMacroIndicators, getMacroStatus } from '@/api/macro'

// 指标选项（与后端 MACRO_INDICATORS 一致）
// 一个选项可包含多个字段序列，会在同一张图里叠加展示（如制造/非制造 PMI）
const fieldOptions = [
  {
    key: 'pmi',
    indicator: 'PMI',
    label: 'PMI',
    markLine: 50,
    fields: [
      { field: 'pmi', name: '制造业PMI', color: '#5470c6' },
      { field: 'pmi_nm', name: '非制造业PMI', color: '#fa8c16' },
    ],
  },
  { key: 'cpi', indicator: 'CPI', label: 'CPI同比(%)', fields: [{ field: 'cpi', name: 'CPI同比', color: '#722ed1' }] },
  { key: 'ppi', indicator: 'PPI', label: 'PPI同比(%)', fields: [{ field: 'ppi', name: 'PPI同比', color: '#13c2c2' }] },
  { key: 'gdp', indicator: 'GDP', label: 'GDP同比(%)', fields: [{ field: 'gdp', name: 'GDP同比', color: '#52c41a' }] },
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
const seriesData = ref([])
const statusItems = ref([])
const snapshotItems = ref([])

const currentLabel = computed(() => {
  const f = fieldOptions.find(x => x.key === selectedField.value)
  return f ? `${f.label} 走势` : '走势'
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
        value: rising ? '突破荣枯线' : '跌破荣枯线',
        symbol: 'pin',
        symbolSize: 20,
        itemStyle: { color: rising ? '#f5222d' : '#52c41a' },
        label: { show: true, fontSize: 10, fontFamily: 'sans-serif', color: rising ? '#f5222d' : '#52c41a' },
      })
    }
  }
  return pts
}

const chartOption = computed(() => {
  // 合并所有序列的日期作为 x 轴（同一指标内多字段通常同日发布）
  const dateSet = new Set()
  for (const s of seriesData.value) {
    for (const p of s.points) dateSet.add(p.date)
  }
  const dates = [...dateSet].sort()
  const opt = fieldOptions.find(x => x.key === selectedField.value)

  // PMI 荣枯线（50）与扩张/收缩背景分区：上方淡绿、下方淡红
  const markLine = opt?.markLine != null
    ? {
        silent: true,
        symbol: 'none',
        data: [{ yAxis: opt.markLine }],
        lineStyle: { type: 'dashed', color: '#d4380d', width: 2 },
        label: {
          formatter: `荣枯线 ${opt.markLine}`,
          position: 'end',
          color: '#d4380d',
          fontSize: 12,
          fontWeight: 'bold',
        },
      }
    : undefined
  const markArea = opt?.markLine != null
    ? {
        silent: true,
        data: [
          [{ yAxis: opt.markLine, itemStyle: { color: 'rgba(82,196,26,0.08)' } }, { itemStyle: { color: 'rgba(82,196,26,0.08)' } }],
          [{ yAxis: 'min', itemStyle: { color: 'rgba(245,34,45,0.05)' } }, { yAxis: opt.markLine, itemStyle: { color: 'rgba(245,34,45,0.05)' } }],
        ],
      }
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
    if (markLine) cfg.markLine = JSON.parse(JSON.stringify(markLine))
    if (markArea && seriesData.value.indexOf(s) === 0) cfg.markArea = JSON.parse(JSON.stringify(markArea))
    const crosses = opt?.markLine != null ? crossingPoints(s.points, opt.markLine) : []
    if (crosses.length) cfg.markPoint = { symbol: 'pin', data: crosses }
    return cfg
  })

  return {
    tooltip: { trigger: 'axis' },
    legend: { top: 0, left: 8 },
    grid: { left: 48, right: 24, top: 40, bottom: 40 },
    xAxis: { type: 'category', data: dates, axisLabel: { fontSize: 11 } },
    yAxis: { type: 'value', scale: true },
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
    // 快照：每个指标字段的最新一条
    const all = await getMacroIndicators()
    const items = all?.items ?? []
    const latestByField = {}
    for (const it of items) {
      const k = `${it.indicator}-${it.field_name}`
      if (!latestByField[k] || it.available_date > latestByField[k].available_date) {
        latestByField[k] = it
      }
    }
    const labelMap = {}
    for (const opt of fieldOptions) {
      for (const f of opt.fields) {
        labelMap[f.field] = f.name
      }
    }
    snapshotItems.value = Object.values(latestByField).map(it => ({
      ...it,
      label: labelMap[it.field_name] || it.field_name
    }))
  } catch {
    statusItems.value = []
    snapshotItems.value = []
  }
}

async function doSync() {
  syncing.value = true
  syncMessage.value = ''
  try {
    await syncMacro()
    syncMessage.value = '同步已提交（后台执行）。若日历尚未就绪，广播写 bin 可能为空，稍后在数据管理页确认回填完成后重试。'
    ElMessage.success('宏观同步已提交')
    // 稍后轮询状态
    setTimeout(() => { loadStatus(); loadSeries() }, 3000)
  } catch (e) {
    if (e !== 'cancel') ElMessage.error('宏观同步提交失败: ' + (e?.message || e))
  } finally {
    syncing.value = false
  }
}

onMounted(() => {
  loadSeries()
  loadStatus()
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

.chart-wrap { min-height: 200px; }
.chart-macro { height: 420px; }

.snapshot-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
  gap: 12px;
}
.snapshot-cell {
  padding: 14px 16px;
  background: var(--bg-tertiary, #f5f7fa);
  border-radius: 8px;
}
.snapshot-label { font-size: 12px; color: var(--text-tertiary); }
.snapshot-value { font-size: 22px; font-weight: 700; color: var(--text-primary); margin-top: 4px; font-variant-numeric: tabular-nums; }
.snapshot-unit { font-size: 12px; font-weight: 400; color: var(--text-tertiary); margin-left: 2px; }
.snapshot-date { font-size: 12px; color: var(--text-tertiary); margin-top: 4px; }

.mb-6 { margin-bottom: 24px; }

@keyframes fadeInUp {
  from { opacity: 0; transform: translateY(8px); }
  to { opacity: 1; transform: translateY(0); }
}
</style>
