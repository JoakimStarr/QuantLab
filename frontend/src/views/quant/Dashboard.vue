<template>
  <PageContainer>
    <header class="page-header">
      <div class="page-header__lead">
        <h1 class="page-header__title">量化研究看板</h1>
        <p class="page-header__subtitle">量化研究全景概览</p>
      </div>
      <div class="page-header__actions">
        <span v-if="lastTradeDate" class="page-header__count">最近行情 {{ lastTradeDate }}</span>
        <el-button @click="refreshAll" :loading="loading" size="small">刷新</el-button>
      </div>
    </header>

    <!-- 加载失败的非阻塞提示条 -->
    <el-alert
      v-for="err in loadErrors"
      :key="err"
      class="dashboard-alert"
      :title="err"
      type="error"
      :closable="true"
      show-icon
      @close="loadErrors = loadErrors.filter((e) => e !== err)"
    />

    <!-- 初始加载骨架屏 -->
    <template v-if="initialLoading">
      <SkeletonLoader :rows="3" />
      <el-row :gutter="16" style="margin-top: 16px">
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
        :loading="overviewLoading"
        @update:selected="onSelectIndex"
        @run-stock-kline="runStockKline"
      />

      <KLineChart
        :kline-items="klineItems"
        :indices="indices"
        :selected-index="selectedIndex"
        :stock-target="stockTarget"
        :selected-period="selectedPeriod"
        :active-indicators="activeIndicators"
        :periods="periods"
        :kline-loading="klineLoading"
        :time-range="timeRange"
        :custom-range="customRange"
        @update:selected-index="onSelectIndex"
        @update:selected-period="selectedPeriod = $event"
        @update:active-indicators="activeIndicators = $event"
        @update:time-range="timeRange = $event"
        @update:custom-range="customRange = $event"
        @run-stock-kline="runStockKline"
        @select-stock="onSelectStock"
        @clear-stock="onClearStock"
      />

      <!-- 以下为折叠区：滚动到视口附近才加载（按需懒加载，减少首屏请求） -->
      <LazySection @activate="activateMacro">
        <MacroSnapshot :items="macroItems" :loading="macroLoading" />
      </LazySection>

      <el-row :gutter="16">
        <el-col :xs="24" :sm="12" class="dashboard-col">
          <LazySection @activate="activateFactorStats">
            <FactorStats :total="factorTotal" :by-source="factorBySource" />
          </LazySection>
        </el-col>
        <el-col :xs="24" :sm="12" class="dashboard-col">
          <LazySection>
            <MiningTasks :tasks="recentMining" :loading="loading" />
          </LazySection>
        </el-col>
      </el-row>

      <LazySection>
        <DecayAlert />
      </LazySection>

      <LazySection>
        <BacktestList :backtests="recentBacktests" :loading="loading" />
      </LazySection>

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
import MacroSnapshot from '@/components/dashboard/MacroSnapshot.vue'
import DecayAlert from '@/components/dashboard/DecayAlert.vue'
import Guide from '@/components/common/Guide.vue'
import LazySection from '@/components/common/LazySection.vue'
import { listFactors } from '@/api/factor'
import { listStrategies, listAllBacktestResults } from '@/api/strategy'
import { listMiningTasks } from '@/api/mining'
import { getQuantDataStatus } from '@/api/quant'
import { listIndices, getIndexKline, getMarketOverview } from '@/api/market'
import { getMacroIndicators } from '@/api/macro'
import { searchStocks } from '@/api/quant'
import { getCached, setCache } from '@/utils/ttlCache'

const loading = ref(true)
const initialLoading = ref(true)
const guideVisible = ref(false)
const router = useRouter()
const overviewLoading = ref(false)
const macroLoading = ref(false)
const loadErrors = ref([])
let klineReqId = 0

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
const stockTarget = ref(null)
const selectedPeriod = ref('1d')
const klineItems = ref([])
const overviewItems = ref([])
const macroItems = ref([])
const klineLoading = ref(false)
const activeIndicators = ref(['MA'])
// 时间范围：1月/3月/6月/1年/2年/全部/自定义，默认 2 年（用户明确要求）
const timeRange = ref('2Y')
const customRange = ref(null)

const periods = [
  { key: '1d', label: '日线' },
  { key: '1w', label: '周线' },
  { key: '1M', label: '月线' },
]

const dashboardStats = computed(() => ({
  factorTotal: factorTotal.value,
  factorBySource: factorBySource.value,
  strategies: strategies.value,
  recentMining: recentMining.value,
  miningTotal: miningTotal.value,
  recentBacktests: recentBacktests.value,
  backtestTotal: backtestTotal.value,
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
    case '1M':
      start.setMonth(start.getMonth() - 1)
      break
    case '3M':
      start.setMonth(start.getMonth() - 3)
      break
    case '6M':
      start.setMonth(start.getMonth() - 6)
      break
    case '1Y':
      start.setFullYear(start.getFullYear() - 1)
      break
    case '2Y':
      start.setFullYear(start.getFullYear() - 2)
      break
    default:
      return { start: null, end: null }
  }
  return { start: formatDate(start), end: formatDate(end) }
}

async function loadKline() {
  const reqId = ++klineReqId
  klineLoading.value = true
  try {
    const { start, end } = rangeToDates(timeRange.value, customRange.value)
    // 后端 limit 上限 500，2 年日线 ≈ 486 条刚好覆盖；ALL 时不传 start_date，由后端按 limit*2 天回溯
    const params = { period: selectedPeriod.value, limit: 500 }
    if (start) params.start_date = start
    if (end) params.end_date = end
    const res = await getIndexKline(klineCode.value, params)
    if (reqId !== klineReqId) return
    klineItems.value = res?.items ?? []
  } catch {
    if (reqId !== klineReqId) return
    klineItems.value = []
    pushError('行情数据加载失败')
  } finally {
    if (reqId === klineReqId) klineLoading.value = false
  }
}

function pushError(msg) {
  if (!loadErrors.value.includes(msg)) loadErrors.value.push(msg)
}

async function loadOverview() {
  overviewLoading.value = true
  try {
    const res = await getMarketOverview()
    overviewItems.value = res?.items ?? []
  } catch {
    overviewItems.value = []
    pushError('市场行情数据加载失败')
  } finally {
    overviewLoading.value = false
  }
}

// 宏观指标最新值快照（Dashboard 卡片），附环比变化（最新值 - 上一条值）
async function loadMacroSnapshot() {
  macroLoading.value = true
  try {
    const res = await getMacroIndicators()
    const items = res?.items ?? []
    // 每个 indicator-field_name 保留最新两条，用于计算环比变化
    const labelMap = { pmi: '制造业PMI', pmi_nm: '非制造业PMI', cpi: 'CPI同比', ppi: 'PPI同比', gdp: 'GDP同比' }
    const series = {}
    for (const it of items) {
      const k = `${it.indicator}-${it.field_name}`
      if (!series[k]) series[k] = []
      series[k].push(it)
    }
    macroItems.value = Object.values(series).map((arr) => {
      arr.sort((a, b) => String(a.available_date).localeCompare(String(b.available_date)))
      const latest = arr[arr.length - 1]
      const prev = arr[arr.length - 2]
      const latestVal = Number(latest.value)
      const prevVal = prev ? Number(prev.value) : null
      const change =
        prev != null && prevVal != null && latestVal != null && !Number.isNaN(latestVal) && !Number.isNaN(prevVal)
          ? latestVal - prevVal
          : null
      return {
        ...latest,
        label: labelMap[latest.field_name] || latest.field_name,
        change,
        prevDate: prev?.available_date ?? null,
      }
    })
  } catch {
    macroItems.value = []
    pushError('宏观数据加载失败')
  } finally {
    macroLoading.value = false
  }
}

// 首页统计缓存：5 分钟内复用，进入时先渲染缓存再后台刷新
const DASH_STATS_KEY = 'dashboard_stats'
const DASH_STATS_TTL = 5 * 60 * 1000

function applyStats(s) {
  factorTotal.value = s.factorTotal ?? 0
  strategies.value = s.strategies ?? []
  recentMining.value = s.recentMining ?? []
  miningTotal.value = s.miningTotal ?? 0
  recentBacktests.value = s.recentBacktests ?? []
  backtestTotal.value = s.backtestTotal ?? 0
  dataStatus.value = s.dataStatus ?? {}
  indices.value = s.indices ?? []
}

function computeFactorBySource(items) {
  const bySource = { builtin: 0, llm: 0, symbolic: 0, text: 0, automl: 0 }
  items.forEach((f) => {
    const k = (f.source || f.category || f.type || '').toLowerCase()
    if (bySource[k] != null) bySource[k]++
  })
  return bySource
}

// 首屏紧加载核心统计（不含重量级的「因子逐一全量列表」），保证首屏尽快渲染
async function fetchKpi() {
  try {
    const [factors, strategiesData, mining, backtests, status, indicesData] = await Promise.all([
      listFactors({ limit: 1 }),
      listStrategies(),
      listMiningTasks({ limit: 5 }),
      listAllBacktestResults({ limit: 5 }),
      getQuantDataStatus(),
      listIndices(),
    ])
    const stats = {
      factorTotal: factors?.total ?? 0,
      strategies: strategiesData?.items ?? [],
      recentMining: mining?.items ?? [],
      miningTotal: mining?.total ?? 0,
      recentBacktests: backtests?.items ?? [],
      backtestTotal: backtests?.total ?? 0,
      dataStatus: status ?? {},
      indices: indicesData?.items ?? [],
    }
    applyStats(stats)
    setCache(DASH_STATS_KEY, stats, DASH_STATS_TTL)
    return true
  } catch {
    ElMessage.error('加载首页数据失败')
    return false
  }
}

async function loadAll(force = false) {
  loading.value = true
  const cached = !force ? getCached(DASH_STATS_KEY) : null
  if (cached) {
    applyStats(cached)
    loading.value = false
    // 命中缓存：快速展示，后台静默刷新（不阻塞首屏）
    fetchKpi().finally(() => {
      loading.value = false
    })
    return
  }
  await fetchKpi()
  loading.value = false
}

// 因子来源分布：依赖全量因子列表（最重的请求），滚动到该区块才加载
const FACTOR_BREAKDOWN_KEY = 'dashboard_factor_breakdown'
const FACTOR_BREAKDOWN_TTL = 5 * 60 * 1000
let factorBreakdownLoaded = false

async function fetchFactorBreakdown() {
  if (factorBreakdownLoaded) return
  try {
    const res = await listFactors({ limit: 500 })
    factorBySource.value = computeFactorBySource(res?.items ?? [])
    factorBreakdownLoaded = true
    setCache(FACTOR_BREAKDOWN_KEY, factorBySource.value, FACTOR_BREAKDOWN_TTL)
  } catch {
    // 静默失败，区块不展示也未阻塞页面
  }
}

function activateFactorStats() {
  const cached = getCached(FACTOR_BREAKDOWN_KEY)
  if (cached) {
    factorBySource.value = cached
    factorBreakdownLoaded = true
    return
  }
  fetchFactorBreakdown()
}

// 宏观快照：滚动到该区块才拉取（含环比计算）
let macroLoaded = false
async function activateMacro() {
  if (macroLoaded) return
  macroLoaded = true
  await loadMacroSnapshot()
}

function refreshAll() {
  loadAll(true)
  loadOverview()
  loadKline()
  // 因子分布已加载过则连同一起刷新（未滚动到则保持懒加载）
  if (factorBreakdownLoaded) {
    factorBreakdownLoaded = false
    fetchFactorBreakdown()
  }
}

function onSelectIndex(code) {
  selectedIndex.value = code
  stockTarget.value = null
  const item = overviewItems.value.find((i) => i.code === code)
  if (item) ElMessage.success(`已切换指数：${item.name}`)
}

function onSelectStock(stock) {
  if (!stock?.code) return
  stockTarget.value = { code: stock.code, name: stock.name }
  ElMessage.success(`已加载个股：${stock.name}`)
}

function onClearStock() {
  stockTarget.value = null
}

const klineCode = computed(() => stockTarget.value?.code || selectedIndex.value)

const lastTradeDate = computed(() => {
  const arr = klineItems.value
  return arr.length ? arr[arr.length - 1].date : null
})

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

watch([klineCode, selectedPeriod, timeRange, customRange], () => loadKline())

onMounted(async () => {
  if (!localStorage.getItem('quantlab_guide_seen')) guideVisible.value = true
  await Promise.all([loadAll(), loadOverview(), loadKline()])
  initialLoading.value = false
})
</script>

<style scoped lang="scss">
.dashboard-col {
  display: flex;
}
.dashboard-col > .section-card {
  flex: 1;
}
.page-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: var(--space-md);
  margin-bottom: var(--space-lg);

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
  &__count {
    font-size: var(--font-size-sm);
    color: var(--text-tertiary);
    font-variant-numeric: tabular-nums;
    white-space: nowrap;
  }
}
.dashboard-alert {
  margin-bottom: 16px;
}
</style>
