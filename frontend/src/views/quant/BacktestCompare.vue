<template>
  <PageContainer>
    <header class="page-header">
      <div class="page-header__lead">
        <h1 class="page-header__title">回测对比</h1>
        <p class="page-header__subtitle">对比多个回测结果的关键指标与净值曲线</p>
      </div>
      <div class="page-header__actions">
        <el-button @click="goBack">返回策略</el-button>
        <span v-if="resultIds.length" class="page-header__count">共 {{ resultIds.length }} 个回测结果</span>
      </div>
    </header>

    <!-- 始终显示的回测结果选择器：页面主要交互入口 -->
    <SectionCard title="选择回测结果" subtitle="至少选择 2 个回测结果进行对比，支持搜索筛选">
      <template #extra>
        <div class="selector-extra">
          <span v-if="selectedIds.length" class="selector-count">已选 {{ selectedIds.length }} 个</span>
          <el-button text size="small" @click="selectorCollapsed = !selectorCollapsed">
            {{ selectorCollapsed ? '展开 ▼' : '收起 ▲' }}
          </el-button>
        </div>
      </template>

      <div v-show="!selectorCollapsed" class="selector-area">
        <el-select
          v-model="selectedIds"
          multiple
          filterable
          clearable
          collapse-tags
          collapse-tags-tooltip
          placeholder="搜索并选择要对比的回测结果（策略 / 日期）"
          style="width: 100%"
          :loading="resultsLoading"
        >
          <el-option
            v-for="item in allResults"
            :key="item.id"
            :label="formatResultLabel(item)"
            :value="item.id"
          >
            <div class="result-option">
              <span class="result-option__name">策略 #{{ item.strategy_id }}</span>
              <span class="result-option__meta">
                <span v-if="item.start_date && item.end_date" class="meta-date">{{ item.start_date }}~{{ item.end_date }}</span>
                <span v-if="item.annual_return != null" class="num" :class="numClass(item.annual_return)">年化 {{ fmt(item.annual_return, 2, '%') }}</span>
                <span v-if="item.sharpe != null" class="num">夏普 {{ fmt(item.sharpe, 3) }}</span>
              </span>
            </div>
          </el-option>
        </el-select>
        <div class="selector-actions">
          <el-button
            type="primary"
            :disabled="selectedIds.length < 2 || comparing"
            :loading="comparing"
            @click="startCompare"
          >
            开始对比
          </el-button>
          <el-button :disabled="!selectedIds.length" @click="clearSelection">清空</el-button>
          <el-button @click="loadAllResults">刷新列表</el-button>
        </div>
        <el-empty
          v-if="!resultsLoading && allResults.length === 0"
          description="暂无回测结果，请先执行策略回测"
          :image-size="80"
        >
          <el-button type="primary" @click="goBack">前往策略回测</el-button>
        </el-empty>
      </div>

      <!-- 折叠时显示已选摘要 -->
      <div v-if="selectorCollapsed && selectedIds.length" class="selector-summary">
        已选 {{ selectedIds.length }} 个回测：{{ selectedNames }}
      </div>
      <div v-else-if="selectorCollapsed && !selectedIds.length" class="selector-summary selector-summary--muted">
        尚未选择回测结果，点击“展开”进行选择
      </div>
    </SectionCard>

    <el-skeleton v-if="loading" :rows="10" animated />

    <!-- 对比前提示 -->
    <SectionCard v-else-if="!resultIds.length" title="对比结果" subtitle="回测指标与净值曲线">
      <el-empty description="请选择 2 个或以上回测结果进行对比" :image-size="120" />
    </SectionCard>

    <template v-else>
      <!-- 指标对比表格 -->
      <SectionCard title="回测指标对比" subtitle="高亮显示各指标最优值">
        <el-table :data="resultList" stripe :cell-class-name="cellClass">
          <el-table-column prop="name" label="策略名称" min-width="160">
            <template #default="{ row }">
              <span class="cell-name">{{ row.name }}</span>
            </template>
          </el-table-column>
          <el-table-column prop="annual_return" label="年化收益" width="120" align="right">
            <template #default="{ row }">
              <span class="num" :class="numClass(row.annual_return)">{{ fmt(row.annual_return, 2, '%') }}</span>
            </template>
          </el-table-column>
          <el-table-column prop="sharpe" label="夏普比率" width="110" align="right">
            <template #default="{ row }">
              <span class="num">{{ fmt(row.sharpe, 3) }}</span>
            </template>
          </el-table-column>
          <el-table-column prop="max_drawdown" label="最大回撤" width="120" align="right">
            <template #default="{ row }">
              <span class="num is-negative">{{ fmt(row.max_drawdown, 2, '%') }}</span>
            </template>
          </el-table-column>
          <el-table-column prop="volatility" label="波动率" width="110" align="right">
            <template #default="{ row }">
              <span class="num">{{ fmt(row.volatility, 2, '%') }}</span>
            </template>
          </el-table-column>
          <el-table-column prop="calmar" label="Calmar" width="110" align="right">
            <template #default="{ row }">
              <span class="num">{{ fmt(row.calmar, 3) }}</span>
            </template>
          </el-table-column>
          <el-table-column prop="win_rate" label="胜率" width="100" align="right">
            <template #default="{ row }">
              <span class="num">{{ fmt(row.win_rate, 2, '%') }}</span>
            </template>
          </el-table-column>
          <template #empty>
            <el-empty description="暂无回测数据" :image-size="80" />
          </template>
        </el-table>
      </SectionCard>

      <!-- 净值曲线叠加图 -->
      <SectionCard title="净值曲线对比" subtitle="各回测净值走势叠加">
        <div class="chart-area">
          <el-empty v-if="curveEmpty" description="暂无净值数据" :image-size="80" />
          <v-chart v-else class="chart" :option="curveOption" autoresize />
        </div>
      </SectionCard>

      <!-- 月度收益分布 -->
      <SectionCard title="月度收益分布" subtitle="各回测月度收益率对比">
        <div class="chart-area">
          <el-empty v-if="curveEmpty" description="暂无数据" :image-size="80" />
          <v-chart v-else class="chart" :option="monthlyOption" autoresize />
        </div>
      </SectionCard>

      <!-- 回撤对比 -->
      <SectionCard title="回撤对比" subtitle="各回测回撤曲线">
        <div class="chart-area">
          <el-empty v-if="curveEmpty" description="暂无数据" :image-size="80" />
          <v-chart v-else class="chart" :option="drawdownOption" autoresize />
        </div>
      </SectionCard>

      <!-- 风险指标雷达图 -->
      <SectionCard title="风险指标雷达图" subtitle="多维度风险指标对比">
        <div class="chart-area">
          <el-empty v-if="!resultList.length" description="暂无数据" :image-size="80" />
          <v-chart v-else class="chart" :option="radarOption" autoresize />
        </div>
      </SectionCard>
    </template>
  </PageContainer>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import VChart from 'vue-echarts'
import PageContainer from '@/components/common/PageContainer.vue'
import SectionCard from '@/components/common/SectionCard.vue'
import { compareBacktests } from '@/api/quant'
import { listAllBacktestResults } from '@/api/strategy'


const route = useRoute()
const router = useRouter()
const loading = ref(true)
const resultIds = ref([])
const resultList = ref([])
// 净值数据：{ resultId: [{ date, nav }, ...] }
const curveData = ref({})

// 选择器相关状态
const allResults = ref([])
const selectedIds = ref([])
const resultsLoading = ref(false)
const selectorCollapsed = ref(false)
const comparing = ref(false)

// 配色
const colors = ['#1f4ba0', '#1f9d6b', '#d24545', '#c8801c', '#2f7dc2', '#9333ea', '#0891b2', '#be185d']

const curveEmpty = computed(() => Object.keys(curveData.value).length === 0)

// 已选回测名称摘要（折叠时展示）
const selectedNames = computed(() => {
  return selectedIds.value
    .map(id => {
      const item = allResults.value.find(r => r.id === id)
      return item ? `策略 #${item.strategy_id}` : `#${id}`
    })
    .join('、')
})

// 最优值计算
function bestValue(field, isMax = true) {
  const vals = resultList.value
    .map(r => Number(r[field]))
    .filter(v => !Number.isNaN(v))
  if (vals.length === 0) return null
  return isMax ? Math.max(...vals) : Math.min(...vals)
}

// 各指标最优值（年化收益、夏普、Calmar、胜率越大越好；最大回撤、波动率越小越好）
const best = computed(() => ({
  annual_return: bestValue('annual_return', true),
  sharpe: bestValue('sharpe', true),
  max_drawdown: bestValue('max_drawdown', false),
  volatility: bestValue('volatility', false),
  calmar: bestValue('calmar', true),
  win_rate: bestValue('win_rate', true)
}))

// 单元格样式：高亮最优值
function cellClass({ column, row }) {
  const field = column.property
  if (!field) return ''
  const bv = best.value[field]
  if (bv === null || bv === undefined) return ''
  const rv = Number(row[field])
  if (Number.isNaN(rv)) return ''
  return Math.abs(rv - bv) < 1e-9 ? 'best-cell' : ''
}

// 净值曲线图配置
const curveOption = computed(() => {
  const results = resultList.value
  // 收集所有日期并排序
  const allDates = new Set()
  Object.values(curveData.value).forEach(points => {
    points.forEach(p => allDates.add(p.date))
  })
  const dates = [...allDates].sort()

  const series = Object.entries(curveData.value).map(([rid, points], idx) => {
    const result = results.find(r => String(r.id) === String(rid))
    const color = colors[idx % colors.length]
    const map = new Map(points.map(p => [p.date, Number(p.nav)]))
    return {
      name: result?.name || `回测${rid}`,
      type: 'line',
      smooth: false,
      showSymbol: false,
      lineStyle: { width: 1.5, color },
      itemStyle: { color },
      connectNulls: true,
      data: dates.map(d => {
        const v = map.get(d)
        return v === undefined ? null : v
      })
    }
  })

  return {
    tooltip: { trigger: 'axis' },
    legend: { top: 0, textStyle: { color: '#5b6b85' } },
    grid: { left: '3%', right: '4%', bottom: '12%', top: 40, containLabel: true },
    xAxis: { type: 'category', data: dates, boundaryGap: false },
    yAxis: { type: 'value', name: '净值', scale: true, nameLocation: 'middle', nameGap: 40 },
    dataZoom: [{ type: 'inside' }, { type: 'slider', height: 20 }],
    series
  }
})

// 月度收益分布图配置
const monthlyOption = computed(() => {
  const allMonths = new Set()
  const perResult = []
  Object.entries(curveData.value).forEach(([rid, points], idx) => {
    const result = resultList.value.find(r => String(r.id) === String(rid))
    const name = result?.name || `回测${rid}`
    const monthly = {}
    points.forEach(p => {
      const month = String(p.date).slice(0, 7)
      if (!monthly[month]) monthly[month] = { first: Number(p.nav), last: Number(p.nav) }
      monthly[month].last = Number(p.nav)
    })
    const months = Object.keys(monthly).sort()
    months.forEach(m => allMonths.add(m))
    const returns = months.map(m => (monthly[m].last / monthly[m].first - 1) * 100)
    perResult.push({ name, color: colors[idx % colors.length], months, returns })
  })
  const sortedMonths = [...allMonths].sort()
  const series = perResult.map(r => ({
    name: r.name,
    type: 'bar',
    data: sortedMonths.map(m => {
      const idx = r.months.indexOf(m)
      return idx >= 0 ? Number(r.returns[idx].toFixed(2)) : null
    }),
    itemStyle: { color: r.color },
  }))
  return {
    tooltip: {
      trigger: 'axis',
      formatter: params => {
        if (!params.length) return ''
        let s = params[0].axisValue + '<br/>'
        params.forEach(p => {
          s += `${p.marker}${p.seriesName}: ${p.value != null ? p.value + '%' : '—'}<br/>`
        })
        return s
      }
    },
    legend: { top: 0, textStyle: { color: '#5b6b85' } },
    grid: { left: '3%', right: '4%', bottom: '12%', top: 40, containLabel: true },
    xAxis: { type: 'category', data: sortedMonths },
    yAxis: { type: 'value', name: '收益率%', axisLabel: { formatter: '{value}%' } },
    series,
  }
})

// 回撤对比图配置
const drawdownOption = computed(() => {
  const allDates = new Set()
  Object.values(curveData.value).forEach(points => {
    points.forEach(p => allDates.add(p.date))
  })
  const dates = [...allDates].sort()
  const series = Object.entries(curveData.value).map(([rid, points], idx) => {
    const result = resultList.value.find(r => String(r.id) === String(rid))
    const name = result?.name || `回测${rid}`
    const color = colors[idx % colors.length]
    const map = new Map(points.map(p => [p.date, Number(p.nav)]))
    let cummax = -Infinity
    const drawdown = dates.map(d => {
      const v = map.get(d)
      if (v === undefined || v === null) return null
      if (v > cummax) cummax = v
      return Number(((v - cummax) / cummax * 100).toFixed(2))
    })
    return {
      name,
      type: 'line',
      showSymbol: false,
      lineStyle: { width: 1.5, color },
      itemStyle: { color },
      connectNulls: true,
      data: drawdown,
      areaStyle: { opacity: 0.1, color },
    }
  })
  return {
    tooltip: {
      trigger: 'axis',
      formatter: params => {
        if (!params.length) return ''
        let s = params[0].axisValue + '<br/>'
        params.forEach(p => {
          s += `${p.marker}${p.seriesName}: ${p.value != null ? p.value + '%' : '—'}<br/>`
        })
        return s
      }
    },
    legend: { top: 0, textStyle: { color: '#5b6b85' } },
    grid: { left: '3%', right: '4%', bottom: '12%', top: 40, containLabel: true },
    xAxis: { type: 'category', data: dates, boundaryGap: false },
    yAxis: { type: 'value', name: '回撤%', axisLabel: { formatter: '{value}%' } },
    dataZoom: [{ type: 'inside' }, { type: 'slider', height: 20 }],
    series,
  }
})

// 风险指标雷达图配置
const radarOption = computed(() => {
  const indicators = [
    { name: '年化收益', max: 100 },
    { name: '夏普', max: 3 },
    { name: 'Calmar', max: 5 },
    { name: '胜率', max: 100 },
    { name: '低回撤', max: 100 },
    { name: '低波动', max: 100 },
  ]
  const data = resultList.value.map((r, idx) => {
    const color = colors[idx % colors.length]
    const annualReturn = Number(r.annual_return) * 100
    const sharpe = Number(r.sharpe)
    const calmar = Number(r.calmar)
    const winRate = Number(r.win_rate) * 100
    const maxDD = Math.abs(Number(r.max_drawdown)) * 100
    const volatility = Number(r.volatility) * 100
    return {
      name: r.name,
      value: [
        Math.max(0, annualReturn),
        Math.max(0, sharpe),
        Math.max(0, calmar),
        Math.max(0, winRate),
        Math.max(0, 100 - maxDD),
        Math.max(0, 100 - volatility),
      ],
      lineStyle: { color },
      itemStyle: { color },
      areaStyle: { opacity: 0.1, color },
    }
  })
  return {
    tooltip: { trigger: 'item' },
    legend: { top: 0, textStyle: { color: '#5b6b85' }, data: resultList.value.map(r => r.name) },
    radar: {
      indicator: indicators,
      shape: 'polygon',
      splitNumber: 5,
      splitArea: { areaStyle: { color: ['rgba(31,75,160,0.02)', 'rgba(31,75,160,0.04)'] } },
    },
    series: [{ type: 'radar', data }],
  }
})

// 数值格式化（suffix='%' 时将小数转为百分比）
function fmt(val, digits = 3, suffix = '') {
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

// 标准化净值数据
function normalizeNavSeries(data) {
  // 后端返回 nav_curves: [{ result_id, curve: [1.0, 1.01, ...] }]
  // 转换为 { resultId: [{ date, nav }] }（用索引作为日期占位）
  const curves = data?.nav_curves || data?.nav_series || data?.curve_series || data?.series
  if (Array.isArray(curves)) {
    const map = {}
    curves.forEach(item => {
      const rid = String(item.result_id)
      const arr = item.curve || item.nav || []
      map[rid] = arr.map((v, i) => ({ date: `D${i + 1}`, nav: Number(v) }))
    })
    return map
  }
  if (curves && typeof curves === 'object') return curves
  return {}
}

// 格式化回测结果选项标签（用于 el-select 已选 tag 展示）
function formatResultLabel(item) {
  const parts = [`#${item.id}`, `策略#${item.strategy_id}`]
  if (item.start_date && item.end_date) parts.push(`${item.start_date}~${item.end_date}`)
  if (item.annual_return != null) {
    parts.push(`年化${(Number(item.annual_return) * 100).toFixed(2)}%`)
  }
  if (item.sharpe != null) parts.push(`夏普${Number(item.sharpe).toFixed(3)}`)
  if (item.created_at) parts.push(item.created_at.slice(0, 10))
  return parts.join(' | ')
}

// 加载所有回测结果（用于选择器）
async function loadAllResults() {
  resultsLoading.value = true
  try {
    const res = await listAllBacktestResults({ limit: 100 })
    // 后端返回 { items: [...], total: N }
    allResults.value = res?.items || []
  } catch (e) {
    ElMessage.error('加载回测结果列表失败')
    allResults.value = []
  } finally {
    resultsLoading.value = false
  }
}

// 同步 URL ids → 选择器（保证从分享链接进入时选择器已预选）
function syncSelectionFromIds(ids) {
  if (!ids || !ids.length) return
  const nums = ids.map(id => Number(id)).filter(n => !Number.isNaN(n))
  const validIds = nums.filter(n => allResults.value.some(r => r.id === n))
  selectedIds.value = validIds.length ? validIds : nums
}

// 清空选择
function clearSelection() {
  selectedIds.value = []
}

// 用选中的 ID 加载对比数据，并同步 URL
async function startCompare() {
  if (selectedIds.value.length < 2) {
    ElMessage.warning('请至少选择 2 个回测结果进行对比')
    return
  }
  resultIds.value = [...selectedIds.value]
  // 同步到 URL，方便分享
  router.replace({ query: { ...route.query, ids: resultIds.value.join(',') } })
  comparing.value = true
  loading.value = true
  try {
    const result = await compareBacktests(selectedIds.value)
    if (result?.comparison) {
      resultList.value = result.comparison
    } else if (Array.isArray(result)) {
      resultList.value = result
    }
    curveData.value = normalizeNavSeries(result)
    // 对比成功后自动收起选择器，聚焦结果
    selectorCollapsed.value = true
  } catch (e) {
    ElMessage.error('加载对比数据失败')
  } finally {
    loading.value = false
    comparing.value = false
  }
}

function goBack() {
  router.push('/quant/strategy')
}

// 加载对比数据（从 URL ids 进入时）
async function loadCompareData(ids) {
  loading.value = true
  try {
    const result = await compareBacktests(ids)
    if (result?.comparison) {
      resultList.value = result.comparison
    } else if (Array.isArray(result)) {
      resultList.value = result
    }
    curveData.value = normalizeNavSeries(result)
  } catch (e) {
    ElMessage.error('加载对比数据失败')
  } finally {
    loading.value = false
  }
}

async function loadData() {
  // 始终加载回测结果列表供选择器使用
  await loadAllResults()

  const idsParam = route.query.ids
  const ids = idsParam
    ? String(idsParam).split(',').map(s => s.trim()).filter(Boolean)
    : []
  if (ids.length === 0) {
    loading.value = false
    return
  }
  resultIds.value = ids
  syncSelectionFromIds(ids)
  await loadCompareData(ids)
}

onMounted(loadData)
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

  &__count {
    font-size: var(--font-size-sm);
    color: var(--text-tertiary);
    white-space: nowrap;
  }
}

.chart-area {
  height: 400px;
  width: 100%;
}

.chart {
  width: 100%;
  height: 100%;
}

.cell-name {
  font-weight: 500;
  color: var(--text-primary);
}

:deep(.best-cell) {
  background-color: rgba(31, 75, 160, 0.08) !important;
  font-weight: 600;
}

.selector-extra {
  display: flex;
  align-items: center;
  gap: var(--space-md);
}

.selector-count {
  font-size: var(--font-size-sm);
  color: var(--primary);
  font-weight: 600;
}

.selector-area {
  display: flex;
  flex-direction: column;
  gap: var(--space-md);
}

.selector-actions {
  display: flex;
  gap: var(--space-sm);
  flex-wrap: wrap;
}

.selector-summary {
  font-size: var(--font-size-sm);
  color: var(--text-secondary);
  line-height: 1.6;

  &--muted {
    color: var(--text-tertiary);
  }
}

.result-option {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-md);
  padding: 2px 0;

  &__name {
    font-weight: 500;
    color: var(--text-primary);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  &__meta {
    display: flex;
    align-items: center;
    gap: var(--space-sm);
    flex-shrink: 0;
  }
}

.meta-date {
  font-size: var(--font-size-xs);
  color: var(--text-tertiary);
  font-family: var(--font-mono);
}

.num {
  font-family: var(--font-mono);

  &.is-positive {
    color: var(--success);
  }

  &.is-negative {
    color: var(--danger);
  }
}
</style>
