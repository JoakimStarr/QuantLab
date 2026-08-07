<template>
  <PageContainer>
    <PageHeader title="因子对比" subtitle="对比多个因子的评价指标与 IC 衰减特征">
      <template #actions>
        <el-button @click="goBack">返回因子库</el-button>
        <span v-if="factorIds.length" class="page-header__count">共 {{ factorIds.length }} 个因子</span>
      </template>
    </PageHeader>

    <!-- 始终显示的因子选择器：页面主要交互入口 -->
    <SectionCard title="选择因子" subtitle="至少选择 2 个因子进行对比，支持搜索筛选">
      <template #extra>
        <div class="selector-extra">
          <span v-if="selectedFactorIds.length" class="selector-count">已选 {{ selectedFactorIds.length }} 个</span>
          <el-button text size="small" @click="selectorCollapsed = !selectorCollapsed">
            {{ selectorCollapsed ? '展开 ▼' : '收起 ▲' }}
          </el-button>
        </div>
      </template>

      <div v-show="!selectorCollapsed" class="selector-area">
        <el-select
          v-model="selectedFactorIds"
          multiple
          filterable
          clearable
          collapse-tags
          collapse-tags-tooltip
          placeholder="搜索并选择要对比的因子（名称 / 类别）"
          style="width: 100%"
          :loading="factorsLoading"
        >
          <el-option v-for="item in allFactors" :key="item.id" :label="formatFactorLabel(item)" :value="item.id">
            <div class="factor-option">
              <span class="factor-option__name">{{ item.name }}</span>
              <span class="factor-option__meta">
                <span class="badge" :class="categoryBadgeClass(item.category)">{{ categoryLabel(item.category) }}</span>
                <span class="num" :class="numClass(item.ic)">IC {{ fmt(item.ic, 4) }}</span>
              </span>
            </div>
          </el-option>
        </el-select>
        <div class="selector-actions">
          <el-button
            type="primary"
            :disabled="selectedFactorIds.length < 2 || comparing"
            :loading="comparing"
            @click="startCompare"
          >
            开始对比
          </el-button>
          <el-button :disabled="!selectedFactorIds.length" @click="clearSelection">清空</el-button>
          <el-button @click="loadAllFactors">刷新列表</el-button>
        </div>
        <el-empty
          v-if="!factorsLoading && allFactors.length === 0"
          description="暂无因子，请先在因子库创建"
          :image-size="80"
        >
          <el-button type="primary" @click="goBack">前往因子库</el-button>
        </el-empty>
      </div>

      <!-- 折叠时显示已选摘要 -->
      <div v-if="selectorCollapsed && selectedFactorIds.length" class="selector-summary">
        已选 {{ selectedFactorIds.length }} 个因子：{{ selectedNames }}
      </div>
      <div v-else-if="selectorCollapsed && !selectedFactorIds.length" class="selector-summary selector-summary--muted">
        尚未选择因子，点击“展开”进行选择
      </div>
    </SectionCard>

    <el-skeleton v-if="loading" :rows="10" animated />

    <!-- 对比前提示 -->
    <SectionCard v-else-if="!factorIds.length" title="对比结果" subtitle="因子指标与 IC 特征">
      <el-empty description="请选择 2 个或以上因子进行对比" :image-size="120" />
    </SectionCard>

    <template v-else>
      <!-- 指标对比表格 -->
      <SectionCard title="因子指标对比" subtitle="IC / RankIC / ICIR / 换手率">
        <el-table :data="factorList" stripe>
          <el-table-column prop="name" label="因子名称" min-width="160">
            <template #default="{ row }">
              <span class="cell-name">{{ row.name }}</span>
            </template>
          </el-table-column>
          <el-table-column prop="category" label="类别" width="100" align="center">
            <template #default="{ row }">
              <span class="badge" :class="categoryBadgeClass(row.category)">{{ categoryLabel(row.category) }}</span>
            </template>
          </el-table-column>
          <el-table-column prop="ic" label="IC" width="110" align="right">
            <template #default="{ row }">
              <span class="num" :class="numClass(row.ic)">{{ fmt(row.ic, 4) }}</span>
            </template>
          </el-table-column>
          <el-table-column prop="rank_ic" label="RankIC" width="110" align="right">
            <template #default="{ row }">
              <span class="num" :class="numClass(row.rank_ic)">{{ fmt(row.rank_ic, 4) }}</span>
            </template>
          </el-table-column>
          <el-table-column prop="icir" label="ICIR" width="100" align="right">
            <template #default="{ row }">
              <span class="num">{{ fmt(row.icir, 3) }}</span>
            </template>
          </el-table-column>
          <el-table-column prop="turnover" label="换手率" width="100" align="right">
            <template #default="{ row }">
              <span class="num">{{ fmt(row.turnover, 3) }}</span>
            </template>
          </el-table-column>
          <template #empty>
            <el-empty description="暂无因子数据" :image-size="80" />
          </template>
        </el-table>
      </SectionCard>

      <!-- IC 衰减曲线对比 -->
      <SectionCard title="IC 衰减曲线对比" subtitle="不同 lag 下的 IC 值变化趋势">
        <div class="chart-area">
          <el-empty v-if="decayEmpty" description="暂无衰减数据" :image-size="80" />
          <v-chart v-else class="chart" :option="decayOption" autoresize />
        </div>
      </SectionCard>

      <!-- IC 时序对比 -->
      <SectionCard title="IC 时序对比" subtitle="各因子 IC 随时间变化">
        <div class="chart-area">
          <el-empty v-if="seriesEmpty" description="暂无时序数据" :image-size="80" />
          <v-chart v-else class="chart" :option="seriesOption" autoresize />
        </div>
      </SectionCard>
    </template>
  </PageContainer>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus/es/components/message/index'
import VChart from 'vue-echarts'
import '@/utils/echarts'
import PageContainer from '@/components/common/PageContainer.vue'
import PageHeader from '@/components/common/PageHeader.vue'
import SectionCard from '@/components/common/SectionCard.vue'
import { chartTheme } from '@/utils/chartTheme'
import { fmt, numClass } from '@/utils/format'
import { useThemeRev } from '@/composables/useChartTheme'

const themeRev = useThemeRev()
import { compareFactors, getFactorDecay } from '@/api/quant'
import { useFactorStore } from '@/stores/factor'

const route = useRoute()
const router = useRouter()
const factorStore = useFactorStore()
const loading = ref(true)
const factorIds = ref([])
const factorList = ref([])
// IC 衰减数据：{ factorId: [{ lag, ic }, ...] }
const decayData = ref({})
// IC 时序数据：{ factorId: [{ date, ic }, ...] }
const seriesData = ref({})

// 配色（深靛蓝为主色）
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

const decayEmpty = computed(() => Object.keys(decayData.value).length === 0)
const seriesEmpty = computed(() => Object.keys(seriesData.value).length === 0)

// 选择器相关状态
const allFactors = ref([])
const selectedFactorIds = ref([])
const factorsLoading = ref(false)
const selectorCollapsed = ref(false)
const comparing = ref(false)

// 已选因子名称摘要（折叠时展示）
const selectedNames = computed(() => {
  return selectedFactorIds.value.map((id) => allFactors.value.find((f) => f.id === id)?.name || `#${id}`).join('、')
})

// 类别映射
const categoryMap = {
  builtin: { label: '内置', badge: 'primary' },
  llm: { label: 'LLM', badge: 'success' },
  symbolic: { label: '符号', badge: 'warning' },
  text: { label: '文本', badge: 'info' },
  automl: { label: 'AutoML', badge: 'danger' },
}
const categoryLabel = (c) => categoryMap[c]?.label || c || '—'
const categoryBadgeClass = (c) => `badge--${categoryMap[c]?.badge || 'muted'}`

// IC 衰减曲线图配置
const decayOption = computed(() => {
  void themeRev.value
  const factors = factorList.value
  const series = Object.entries(decayData.value).map(([fid, points], idx) => {
    const factor = factors.find((f) => String(f.id) === String(fid))
    const color = colors[idx % colors.length]
    return {
      name: factor?.name || `因子${fid}`,
      type: 'line',
      smooth: true,
      showSymbol: true,
      symbolSize: 7,
      lineStyle: { width: 2, color },
      itemStyle: { color },
      data: points.map((p) => [p.lag, Number(p.ic)]),
    }
  })
  return {
    tooltip: {
      trigger: 'axis',
      formatter: (params) => {
        let html = `Lag: ${params[0]?.axisValue}<br/>`
        params.forEach((p) => {
          html += `${p.marker} ${p.seriesName}: ${Number(p.value[1]).toFixed(2)}<br/>`
        })
        return html
      },
    },
    legend: { top: 0, textStyle: { color: chartTheme.axisText() } },
    textStyle: { color: chartTheme.axisText() },
    grid: { left: '3%', right: '4%', bottom: '3%', top: 40, containLabel: true },
    xAxis: {
      type: 'value',
      name: 'Lag',
      nameLocation: 'middle',
      nameGap: 30,
      axisLabel: { color: chartTheme.axisText() },
    },
    yAxis: {
      type: 'value',
      name: 'IC',
      nameLocation: 'middle',
      nameGap: 40,
      axisLabel: { color: chartTheme.axisText() },
    },
    series,
  }
})

// IC 时序图配置
const seriesOption = computed(() => {
  void themeRev.value
  const factors = factorList.value
  // 收集所有日期并排序
  const allDates = new Set()
  Object.values(seriesData.value).forEach((points) => {
    points.forEach((p) => allDates.add(p.date))
  })
  const dates = [...allDates].sort()

  const series = Object.entries(seriesData.value).map(([fid, points], idx) => {
    const factor = factors.find((f) => String(f.id) === String(fid))
    const color = colors[idx % colors.length]
    const map = new Map(points.map((p) => [p.date, Number(p.ic)]))
    return {
      name: factor?.name || `因子${fid}`,
      type: 'line',
      smooth: false,
      showSymbol: false,
      lineStyle: { width: 1.5, color },
      itemStyle: { color },
      connectNulls: true,
      data: dates.map((d) => {
        const v = map.get(d)
        return v === undefined ? null : v
      }),
    }
  })

  return {
    tooltip: { trigger: 'axis' },
    textStyle: { color: chartTheme.axisText() },
    legend: { top: 0, textStyle: { color: chartTheme.axisText() } },
    grid: { left: '3%', right: '4%', bottom: '12%', top: 40, containLabel: true },
    xAxis: { type: 'category', data: dates, boundaryGap: false, axisLabel: { color: chartTheme.axisText() } },
    yAxis: {
      type: 'value',
      name: 'IC',
      nameLocation: 'middle',
      nameGap: 40,
      axisLabel: { color: chartTheme.axisText() },
    },
    dataZoom: [{ type: 'inside' }, { type: 'slider', height: 20, textStyle: { color: chartTheme.axisText() } }],
    series,
  }
})

// 标准化衰减数据
function normalizeDecay(data) {
  if (Array.isArray(data)) return data
  if (data?.decay) return data.decay
  if (data?.points) return data.points
  if (data?.items) return data.items
  return []
}

// 标准化 IC 时序数据
function normalizeSeries(data) {
  // 后端返回 ic_timeseries: [{ date, factor_id, ic }]
  // 转换为 { factorId: [{ date, ic }] }
  const ts = data?.ic_timeseries || data?.ic_series || data?.series
  if (Array.isArray(ts)) {
    const map = {}
    ts.forEach((item) => {
      const fid = String(item.factor_id)
      if (!map[fid]) map[fid] = []
      map[fid].push({ date: item.date, ic: Number(item.ic) })
    })
    return map
  }
  if (ts && typeof ts === 'object') return ts
  return {}
}

// 格式化因子选项标签（用于 el-select 已选 tag 展示）
function formatFactorLabel(item) {
  const parts = [`#${item.id}`, item.name]
  if (item.category) parts.push(categoryLabel(item.category))
  if (item.ic != null) parts.push(`IC=${Number(item.ic).toFixed(2)}`)
  return parts.join(' | ')
}

// 加载所有因子（用于选择器）
async function loadAllFactors() {
  factorsLoading.value = true
  try {
    await factorStore.fetchList()
    allFactors.value = factorStore.factors
  } catch (e) {
    ElMessage.error('加载因子列表失败')
    allFactors.value = []
  } finally {
    factorsLoading.value = false
  }
}

// 同步 URL ids → 选择器（保证从分享链接进入时选择器已预选）
function syncSelectionFromIds(ids) {
  if (!ids || !ids.length) return
  const nums = ids.map((id) => Number(id)).filter((n) => !Number.isNaN(n))
  // 只保留列表中存在的 id，避免显示空白 tag
  const validIds = nums.filter((n) => allFactors.value.some((f) => f.id === n))
  selectedFactorIds.value = validIds.length ? validIds : nums
}

// 清空选择
function clearSelection() {
  selectedFactorIds.value = []
}

// 用选中的 ID 加载对比数据，并同步 URL
async function startCompare() {
  if (selectedFactorIds.value.length < 2) {
    ElMessage.warning('请至少选择 2 个因子进行对比')
    return
  }
  factorIds.value = selectedFactorIds.value.map(String)
  // 同步到 URL，方便分享
  router.replace({ query: { ...route.query, ids: factorIds.value.join(',') } })
  comparing.value = true
  loading.value = true
  try {
    const compareResult = await compareFactors(factorIds.value)
    if (compareResult?.factors) {
      factorList.value = compareResult.factors
    } else if (Array.isArray(compareResult)) {
      factorList.value = compareResult
    }
    seriesData.value = normalizeSeries(compareResult)
    const decayPromises = factorIds.value.map((id) =>
      getFactorDecay(id)
        .then((res) => [id, normalizeDecay(res)])
        .catch(() => [id, []])
    )
    const decayResults = await Promise.all(decayPromises)
    const decayMap = {}
    decayResults.forEach(([id, points]) => {
      if (Array.isArray(points) && points.length > 0) {
        decayMap[id] = points
      }
    })
    decayData.value = decayMap
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
  router.push('/quant/factors')
}

// 加载对比数据（从 URL ids 进入时）
async function loadCompareData(ids) {
  loading.value = true
  try {
    const compareResult = await compareFactors(ids)
    if (compareResult?.factors) {
      factorList.value = compareResult.factors
    } else if (Array.isArray(compareResult)) {
      factorList.value = compareResult
    }
    seriesData.value = normalizeSeries(compareResult)
    const decayPromises = ids.map((id) =>
      getFactorDecay(id)
        .then((res) => [id, normalizeDecay(res)])
        .catch(() => [id, []])
    )
    const decayResults = await Promise.all(decayPromises)
    const decayMap = {}
    decayResults.forEach(([id, points]) => {
      if (Array.isArray(points) && points.length > 0) {
        decayMap[id] = points
      }
    })
    decayData.value = decayMap
  } catch (e) {
    ElMessage.error('加载对比数据失败')
  } finally {
    loading.value = false
  }
}

async function loadData() {
  // 始终加载因子列表供选择器使用
  await loadAllFactors()

  const idsParam = route.query.ids
  const ids = idsParam
    ? String(idsParam)
        .split(',')
        .map((s) => s.trim())
        .filter(Boolean)
    : []
  if (ids.length === 0) {
    loading.value = false
    return
  }
  factorIds.value = ids
  syncSelectionFromIds(ids)
  await loadCompareData(ids)
}

onMounted(loadData)
</script>

<style scoped lang="scss">
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

.factor-option {
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

.num {
  font-family: var(--font-mono);
}

.badge {
  display: inline-block;
  padding: 2px 10px;
  border-radius: var(--radius-full);
  font-size: var(--font-size-xs);
  font-weight: 500;
  line-height: 1.6;

  &--primary {
    background: var(--primary-soft);
    color: var(--primary);
  }
  &--success {
    background: var(--success-soft);
    color: var(--success);
  }
  &--warning {
    background: var(--warning-soft);
    color: var(--warning);
  }
  &--info {
    background: var(--info-soft);
    color: var(--info);
  }
  &--danger {
    background: var(--danger-soft);
    color: var(--danger);
  }
  &--muted {
    background: var(--bg-tertiary);
    color: var(--text-tertiary);
  }
}
</style>
