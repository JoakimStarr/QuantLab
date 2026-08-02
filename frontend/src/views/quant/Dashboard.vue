<template>
  <PageContainer>
    <header class="dashboard-header">
      <div class="dashboard-header__lead">
        <h2>量化研究看板</h2>
        <p>量化研究全景概览</p>
      </div>
      <el-button @click="refreshAll" :loading="loading" size="small">刷新</el-button>
    </header>

    <!-- 初始加载骨架屏 -->
    <template v-if="initialLoading">
      <SkeletonLoader :rows="3" />
      <el-row :gutter="16" style="margin-top: 16px;">
        <el-col :xs="24" :sm="12">
          <SkeletonLoader :rows="4" />
        </el-col>
        <el-col :xs="24" :sm="12">
          <SkeletonLoader :rows="4" />
        </el-col>
      </el-row>
      <SkeletonLoader :rows="6" />
    </template>

    <template v-else>
      <KpiCards :stats="dashboardStats" :loading="loading" :data-status="dataStatus" />

      <MarketOverview
        :items="overviewItems"
        :selected="selectedIndex"
        @update:selected="selectedIndex = $event"
        @run-stock-kline="runStockKline"
      />

      <KLineChart
        :kline-items="klineItems"
        :indices="indices"
        :selected-index="selectedIndex"
        :selected-period="selectedPeriod"
        :active-indicators="activeIndicators"
        :periods="periods"
        :kline-loading="klineLoading"
        :time-range="timeRange"
        :custom-range="customRange"
        @update:selected-index="selectedIndex = $event"
        @update:selected-period="selectedPeriod = $event"
        @update:active-indicators="activeIndicators = $event"
        @update:time-range="timeRange = $event"
        @update:custom-range="customRange = $event"
        @run-stock-kline="runStockKline"
      />

      <el-row :gutter="16">
        <el-col :xs="24" :sm="12">
          <FactorStats :total="factorTotal" :by-source="factorBySource" />
        </el-col>
        <el-col :xs="24" :sm="12">
          <MiningTasks :tasks="recentMining" :loading="loading" />
        </el-col>
      </el-row>

      <DecayAlert />

      <BacktestList :backtests="recentBacktests" :loading="loading" />

      <Guide v-model:visible="guideVisible" />
    </template>
  </PageContainer>
</template>

<script setup>
defineOptions({ name: 'Dashboard' })
import { ref, computed, onMounted, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus/es/components/message/index'
import PageContainer from '@/components/common/PageContainer.vue'
import SkeletonLoader from '@/components/SkeletonLoader.vue'
import KpiCards from '@/components/dashboard/KpiCards.vue'
import MarketOverview from '@/components/dashboard/MarketOverview.vue'
import KLineChart from '@/components/dashboard/KLineChart.vue'
import FactorStats from '@/components/dashboard/FactorStats.vue'
import MiningTasks from '@/components/dashboard/MiningTasks.vue'
import BacktestList from '@/components/dashboard/BacktestList.vue'
import DecayAlert from '@/components/dashboard/DecayAlert.vue'
import Guide from '@/components/common/Guide.vue'
import { listFactors } from '@/api/factor'
import { listStrategies, listAllBacktestResults } from '@/api/strategy'
import { listMiningTasks } from '@/api/mining'
import { getQuantDataStatus } from '@/api/quant'
import { listIndices, getIndexKline, getMarketOverview } from '@/api/market'
import { searchStocks } from '@/api/quant'

const loading = ref(true)
const initialLoading = ref(true)
const guideVisible = ref(false)
const router = useRouter()

const factorTotal = ref(0)
const factorBySource = ref({ builtin: 0, llm: 0, symbolic: 0, text: 0, automl: 0 })
const strategies = ref([])
const recentMining = ref([])
const miningTotal = ref(0)
const recentBacktests = ref([])
const backtestTotal = ref(0)
const dataStatus = ref({})

const indices = ref([])
const selectedIndex = ref('SH000300')
const selectedPeriod = ref('1d')
const klineItems = ref([])
const overviewItems = ref([])
const klineLoading = ref(false)
const activeIndicators = ref(['MA'])
// 时间范围：1月/3月/6月/1年/2年/全部/自定义，默认 2 年（用户明确要求）
const timeRange = ref('2Y')
const customRange = ref(null)

const periods = [
  { key: '1d', label: '日线' },
  { key: '1w', label: '周线' },
  { key: '1M', label: '月线' }
]

const dashboardStats = computed(() => ({
  factorTotal: factorTotal.value,
  factorBySource: factorBySource.value,
  strategies: strategies.value,
  recentMining: recentMining.value,
  miningTotal: miningTotal.value,
  recentBacktests: recentBacktests.value,
  backtestTotal: backtestTotal.value
}))

function formatDate(d) {
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${y}-${m}-${day}`
}

// 时间范围 → 实际起止日期；ALL/custom 未选完整区间时 start/end 为 null，交给后端默认
function rangeToDates(range, custom) {
  if (range === 'custom') {
    if (Array.isArray(custom) && custom.length === 2 && custom[0] && custom[1]) {
      return { start: custom[0], end: custom[1] }
    }
    return { start: null, end: null }
  }
  if (range === 'ALL') return { start: null, end: null }
  const end = new Date()
  const start = new Date()
  switch (range) {
    case '1M': start.setMonth(start.getMonth() - 1); break
    case '3M': start.setMonth(start.getMonth() - 3); break
    case '6M': start.setMonth(start.getMonth() - 6); break
    case '1Y': start.setFullYear(start.getFullYear() - 1); break
    case '2Y': start.setFullYear(start.getFullYear() - 2); break
    default: return { start: null, end: null }
  }
  return { start: formatDate(start), end: formatDate(end) }
}

async function loadKline() {
  klineLoading.value = true
  try {
    const { start, end } = rangeToDates(timeRange.value, customRange.value)
    // 后端 limit 上限 500，2 年日线 ≈ 486 条刚好覆盖；ALL 时不传 start_date，由后端按 limit*2 天回溯
    const params = { period: selectedPeriod.value, limit: 500 }
    if (start) params.start_date = start
    if (end) params.end_date = end
    const res = await getIndexKline(selectedIndex.value, params)
    klineItems.value = res?.items ?? []
  } catch {
    klineItems.value = []
  } finally {
    klineLoading.value = false
  }
}

async function loadOverview() {
  try {
    const res = await getMarketOverview()
    overviewItems.value = res?.items ?? []
  } catch {
    overviewItems.value = []
  }
}

async function loadAll() {
  loading.value = true
  try {
    const [factors, factorsAll, strategiesData, mining, backtests, status, indicesData] = await Promise.all([
      listFactors({ limit: 1 }),
      listFactors({ limit: 500 }),
      listStrategies(),
      listMiningTasks({ limit: 5 }),
      listAllBacktestResults({ limit: 5 }),
      getQuantDataStatus(),
      listIndices()
    ])
    factorTotal.value = factors?.total ?? 0
    const bySource = { builtin: 0, llm: 0, symbolic: 0, text: 0, automl: 0 }
    ;(factorsAll?.items ?? []).forEach(f => {
      const s = (f.source || f.category || f.type || '').toLowerCase()
      if (bySource[s] != null) bySource[s]++
    })
    factorBySource.value = bySource
    strategies.value = strategiesData?.items ?? []
    recentMining.value = mining?.items ?? []
    miningTotal.value = mining?.total ?? 0
    recentBacktests.value = backtests?.items ?? []
    backtestTotal.value = backtests?.total ?? 0
    dataStatus.value = status ?? {}
    indices.value = indicesData?.items ?? []
  } catch {
    ElMessage.error('加载首页数据失败')
  } finally {
    loading.value = false
    initialLoading.value = false
  }
}

function refreshAll() {
  loadAll()
  loadOverview()
  loadKline()
}

async function runStockKline(query) {
  const stockQuery = String(query || '').trim()
  if (!stockQuery) {
    ElMessage.warning('请输入股票代码')
    return
  }

  try {
    const res = await searchStocks(stockQuery, 1)
    const match = res?.items?.[0]
    if (!match?.code) {
      ElMessage.warning('未找到匹配的个股')
      return
    }
    router.push({ name: 'QuantData', query: { preview: match.code } })
  } catch {
    ElMessage.error('个股搜索失败')
  }
}

watch([selectedIndex, selectedPeriod, timeRange, customRange], () => loadKline())

onMounted(() => {
  loadAll()
  loadOverview()
  loadKline()
  if (!localStorage.getItem('quantlab_guide_seen')) guideVisible.value = true
})
</script>

<style scoped lang="scss">
.dashboard-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  margin-bottom: 16px;
  &__lead {
    h2 { margin: 0; font-size: 24px; font-weight: 600; color: var(--text-primary); }
    p { margin: 4px 0 0; font-size: 14px; color: var(--text-tertiary); }
  }
}
</style>
