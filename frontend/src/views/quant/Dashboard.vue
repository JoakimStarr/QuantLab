<template>
  <PageContainer>
    <header class="dashboard-header">
      <div class="dashboard-header__lead">
        <h2>量化研究看板</h2>
        <p>量化研究全景概览</p>
      </div>
      <el-button @click="refreshAll" :loading="loading" size="small">刷新</el-button>
    </header>

    <KpiCards :stats="dashboardStats" :loading="loading" :data-status="dataStatus" />

    <MarketOverview
      :items="overviewItems"
      :selected="selectedIndex"
      @update:selected="selectedIndex = $event"
    />

    <KLineChart
      :kline-items="klineItems"
      :indices="indices"
      :selected-index="selectedIndex"
      :selected-period="selectedPeriod"
      :active-indicators="activeIndicators"
      :periods="periods"
      :kline-loading="klineLoading"
      @update:selected-index="selectedIndex = $event"
      @update:selected-period="selectedPeriod = $event"
      @update:active-indicators="activeIndicators = $event"
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
  </PageContainer>
</template>

<script setup>
defineOptions({ name: 'Dashboard' })
import { ref, computed, onMounted, watch } from 'vue'
import { ElMessage } from 'element-plus'
import PageContainer from '@/components/common/PageContainer.vue'
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

const loading = ref(true)
const guideVisible = ref(false)

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

async function loadKline() {
  klineLoading.value = true
  try {
    const res = await getIndexKline(selectedIndex.value, { period: selectedPeriod.value, limit: 120 })
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
  }
}

function refreshAll() {
  loadAll()
  loadOverview()
  loadKline()
}

watch([selectedIndex, selectedPeriod], () => loadKline())

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
