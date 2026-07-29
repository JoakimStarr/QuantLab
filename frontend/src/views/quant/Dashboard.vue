<template>
  <div class="dashboard">
    <!-- 页面头 -->
    <header class="dashboard__header">
      <div class="dashboard__header-text">
        <h1 class="dashboard__title">研究首页</h1>
        <p class="dashboard__subtitle">量化研究全景概览</p>
      </div>
    </header>

    <!-- KPI 统计卡行 -->
    <section class="kpi-grid">
      <template v-if="loading">
        <div class="kpi-card" v-for="i in 4" :key="i">
          <el-skeleton :rows="2" animated />
        </div>
      </template>
      <template v-else>
        <div class="kpi-card" v-for="card in kpiCards" :key="card.key">
          <div class="kpi-card__label">{{ card.label }}</div>
          <div class="kpi-card__value">{{ card.value }}</div>
          <div class="kpi-card__sub">{{ card.sub }}</div>
          <el-icon class="kpi-card__icon"><component :is="card.icon" /></el-icon>
        </div>
      </template>
    </section>

    <!-- 市场概览卡片：多指数实时行情 -->
    <section class="market-overview-grid" v-if="overviewItems.length">
      <div
        class="market-overview-card"
        v-for="item in overviewItems"
        :key="item.code"
        :class="{ 'is-active': item.code === selectedIndex }"
        @click="selectedIndex = item.code"
      >
        <div class="market-overview-card__name">{{ item.name }}</div>
        <div class="market-overview-card__price">{{ item.price }}</div>
        <div
          class="market-overview-card__pct"
          :class="item.pct_change >= 0 ? 'is-up' : 'is-down'"
        >
          {{ item.pct_change >= 0 ? '+' : '' }}{{ item.pct_change }}%
        </div>
      </div>
    </section>

    <!-- K线图卡 -->
    <section class="chart-card">
      <div class="chart-card__header">
        <h2 class="chart-card__title">市场概览</h2>
        <div class="chart-card__controls">
          <el-select
            v-model="selectedIndex"
            size="small"
            class="chart-card__index-select"
            placeholder="选择指数"
          >
            <el-option
              v-for="idx in indices"
              :key="idx.code"
              :label="idx.name"
              :value="idx.code"
            />
          </el-select>
          <div class="chart-card__range">
            <button
              v-for="p in periods"
              :key="p.key"
              class="chart-card__range-btn"
              :class="{ 'is-active': selectedPeriod === p.key }"
              @click="selectedPeriod = p.key"
            >{{ p.label }}</button>
          </div>
          <el-checkbox-group v-model="activeIndicators" size="small" class="chart-card__indicators">
            <el-checkbox-button label="MA">MA</el-checkbox-button>
            <el-checkbox-button label="EMA">EMA</el-checkbox-button>
            <el-checkbox-button label="MACD">MACD</el-checkbox-button>
            <el-checkbox-button label="KDJ">KDJ</el-checkbox-button>
          </el-checkbox-group>
        </div>
      </div>
      <el-skeleton v-if="klineLoading" :rows="8" animated />
      <v-chart
        v-else-if="klineItems.length"
        :option="klineOption"
        :style="{ height: klineChartHeight + 'px' }"
        class="chart-card__chart chart-card__chart--kline"
        autoresize
      />
      <el-empty v-else description="暂无K线数据" />
    </section>

    <!-- 双列区：最近挖掘任务 + 最近回测结果 -->
    <section class="dual-grid">
      <!-- 最近挖掘任务 -->
      <div class="table-card">
        <div class="table-card__header">
          <h2 class="table-card__title">最近挖掘任务</h2>
          <router-link to="/quant/mining" class="table-card__link">查看全部</router-link>
        </div>
        <el-skeleton v-if="loading" :rows="5" animated />
        <el-table
          v-else
          :data="recentMining"
          class="dashboard-table"
          empty-text="暂无任务"
          size="default"
        >
          <el-table-column label="类型" width="90" align="center">
            <template #default="{ row }">
              <span class="badge" :class="`badge--${typeBadgeClass(row.type)}`">{{ typeLabel[row.type] || row.type }}</span>
            </template>
          </el-table-column>
          <el-table-column label="状态" width="80" align="center">
            <template #default="{ row }">
              <span class="badge" :class="`badge--${statusBadgeClass(row.status)}`">{{ statusLabel[row.status] || row.status }}</span>
            </template>
          </el-table-column>
          <el-table-column label="候选" width="70" align="right">
            <template #default="{ row }"><span class="num">{{ row.candidates_generated ?? 0 }}</span></template>
          </el-table-column>
          <el-table-column label="通过" width="70" align="right">
            <template #default="{ row }"><span class="num">{{ row.candidates_passed ?? 0 }}</span></template>
          </el-table-column>
          <el-table-column label="最佳IC" width="90" align="right">
            <template #default="{ row }"><span class="num">{{ formatIc(row.best_ic) }}</span></template>
          </el-table-column>
          <el-table-column label="时间" min-width="140">
            <template #default="{ row }">{{ (row.finished_at || row.created_at || '').slice(0, 16) }}</template>
          </el-table-column>
        </el-table>
      </div>

      <!-- 最近回测结果 -->
      <div class="table-card">
        <div class="table-card__header">
          <h2 class="table-card__title">最近回测结果</h2>
          <router-link to="/quant/strategy" class="table-card__link">查看全部</router-link>
        </div>
        <el-skeleton v-if="loading" :rows="5" animated />
        <el-table
          v-else
          :data="recentBacktests"
          class="dashboard-table"
          empty-text="暂无回测"
          size="default"
        >
          <el-table-column label="策略" min-width="100">
            <template #default="{ row }">{{ row.strategy_name || row.strategy_id }}</template>
          </el-table-column>
          <el-table-column label="夏普" width="80" align="right">
            <template #default="{ row }">
              <span class="num" :class="numClass(row.sharpe)">{{ formatNum(row.sharpe) }}</span>
            </template>
          </el-table-column>
          <el-table-column label="年化" width="80" align="right">
            <template #default="{ row }">
              <span class="num" :class="numClass(row.annual_return)">{{ formatPercent(row.annual_return) }}</span>
            </template>
          </el-table-column>
          <el-table-column label="回撤" width="80" align="right">
            <template #default="{ row }">
              <span class="num num--danger">{{ formatPercent(row.max_drawdown) }}</span>
            </template>
          </el-table-column>
          <el-table-column label="卡玛" width="70" align="right">
            <template #default="{ row }"><span class="num">{{ formatNum(row.calmar) }}</span></template>
          </el-table-column>
          <el-table-column label="区间" min-width="160">
            <template #default="{ row }">{{ row.start_date }}~{{ row.end_date }}</template>
          </el-table-column>
        </el-table>
      </div>
    </section>

    <!-- 操作引导 -->
    <Guide v-model:visible="guideVisible" />
  </div>
</template>

<script setup>
import { ref, computed, onMounted, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { Coin, TrendCharts, MagicStick, DataAnalysis } from '@element-plus/icons-vue'
import VChart from 'vue-echarts'
import { listFactors } from '@/api/factor'
import { listStrategies, listAllBacktestResults } from '@/api/strategy'
import { listMiningTasks } from '@/api/mining'
import { getQuantDataStatus } from '@/api/quant'
import { listIndices, getIndexKline, getMarketOverview } from '@/api/market'
import Guide from '@/components/common/Guide.vue'

const loading = ref(true)
const guideVisible = ref(false)

// 基础数据
const factorTotal = ref(0)
const factorBySource = ref({ builtin: 0, llm: 0, symbolic: 0, text: 0, automl: 0 })
const strategies = ref([])
const recentMining = ref([])
const miningTotal = ref(0)
const recentBacktests = ref([])
const backtestTotal = ref(0)

// 类型与状态标签映射
const typeLabel = { llm: 'LLM', symbolic: '符号', text: '文本', automl: 'AutoML' }
const typeBadgeClass = (t) => ({ llm: 'primary', symbolic: 'warning', text: 'info', automl: 'danger' }[t] || 'info')
const statusLabel = { pending: '等待', running: '运行中', done: '完成', failed: '失败' }
const statusBadgeClass = (s) => ({ pending: 'info', running: 'warning', done: 'success', failed: 'danger' }[s] || 'info')

// KPI 卡片数据
const kpiCards = computed(() => {
  // 策略分类：活跃 / 归档
  const activeStrategies = strategies.value.filter(s => !s.archived && s.status !== 'archived').length
  const archivedStrategies = strategies.value.length - activeStrategies
  // 挖掘任务：今日 / 运行中
  const todayMining = recentMining.value.filter(t => isToday(t.created_at)).length
  const runningMining = recentMining.value.filter(t => t.status === 'running').length
  // 回测记录：近7日
  const last7dBacktests = recentBacktests.value.filter(b => isWithinDays(b.created_at || b.end_date, 7)).length

  return [
    {
      key: 'factor',
      label: '因子总数',
      value: factorTotal.value,
      sub: `内置 ${factorBySource.value.builtin} / LLM ${factorBySource.value.llm} / 符号 ${factorBySource.value.symbolic}`,
      icon: Coin
    },
    {
      key: 'strategy',
      label: '策略数量',
      value: strategies.value.length,
      sub: `活跃 ${activeStrategies} / 归档 ${archivedStrategies}`,
      icon: TrendCharts
    },
    {
      key: 'mining',
      label: '挖掘任务',
      value: miningTotal.value,
      sub: `今日 ${todayMining} / 运行中 ${runningMining}`,
      icon: MagicStick
    },
    {
      key: 'backtest',
      label: '回测记录',
      value: backtestTotal.value,
      sub: `近7日 ${last7dBacktests}`,
      icon: DataAnalysis
    }
  ]
})

// ===== 市场概览：多指数选择 + K线图 =====
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

// K线数据拆分
const klineDates = computed(() => klineItems.value.map(k => k.date))
const klineOhlc = computed(() => klineItems.value.map(k => [k.open, k.close, k.low, k.high]))
const klineVolumes = computed(() => klineItems.value.map(k => k.volume))

// 技术指标计算函数
function calcMA(data, period) {
  const result = []
  for (let i = 0; i < data.length; i++) {
    if (i < period - 1) {
      result.push(null)
    } else {
      let sum = 0
      for (let j = 0; j < period; j++) sum += data[i - j].close
      result.push(sum / period)
    }
  }
  return result
}

function calcEMA(data, period) {
  const result = []
  const k = 2 / (period + 1)
  let ema = null
  for (let i = 0; i < data.length; i++) {
    if (i === 0) {
      ema = data[i].close
    } else {
      ema = data[i].close * k + ema * (1 - k)
    }
    result.push(ema)
  }
  return result
}

function calcMACD(data, short = 12, long = 26, signal = 9) {
  const emaShort = calcEMA(data, short)
  const emaLong = calcEMA(data, long)
  const dif = data.map((_, i) => emaShort[i] - emaLong[i])
  const dea = []
  const k = 2 / (signal + 1)
  let prev = 0
  for (let i = 0; i < dif.length; i++) {
    if (i === 0) prev = dif[0]
    else prev = dif[i] * k + prev * (1 - k)
    dea.push(prev)
  }
  const macd = dif.map((d, i) => (d - dea[i]) * 2)
  return { dif, dea, macd }
}

function calcKDJ(data, n = 9, m1 = 3, m2 = 3) {
  let prevK = 50, prevD = 50
  const k = [], d = [], j = []
  for (let i = 0; i < data.length; i++) {
    let hn = -Infinity, ln = Infinity
    for (let p = Math.max(0, i - n + 1); p <= i; p++) {
      if (data[p].high > hn) hn = data[p].high
      if (data[p].low < ln) ln = data[p].low
    }
    const rsv = hn === ln ? 0 : (data[i].close - ln) / (hn - ln) * 100
    const curK = (m1 - 1) / m1 * prevK + 1 / m1 * rsv
    const curD = (m2 - 1) / m2 * prevD + 1 / m2 * curK
    const curJ = 3 * curK - 2 * curD
    k.push(curK); d.push(curD); j.push(curJ)
    prevK = curK; prevD = curD
  }
  return { k, d, j }
}

// K线图动态高度：启用 MACD/KDJ 时增加副图空间
const klineChartHeight = computed(() => {
  let h = 420
  if (activeIndicators.value.includes('MACD')) h += 120
  if (activeIndicators.value.includes('KDJ')) h += 120
  return h
})

// K线图配置（红涨绿跌：中国股市习惯）
const klineOption = computed(() => {
  const inds = activeIndicators.value
  const showMA = inds.includes('MA')
  const showEMA = inds.includes('EMA')
  const showMACD = inds.includes('MACD')
  const showKDJ = inds.includes('KDJ')

  const data = klineItems.value
  const dates = klineDates.value

  // 根据副图数量动态计算 grid 布局
  const extraSubs = (showMACD ? 1 : 0) + (showKDJ ? 1 : 0)
  let grids, xAxes, yAxes, zoomIndices, dataZoomTop
  if (extraSubs === 0) {
    grids = [
      { left: '8%', right: '4%', top: '8%', height: '55%' },
      { left: '8%', right: '4%', top: '70%', height: '18%' },
    ]
    xAxes = [0, 1].map(i => ({
      type: 'category', gridIndex: i, data: dates, scale: true,
      boundaryGap: false, axisLine: { onZero: false },
      splitLine: { show: false }, min: 'dataMin', max: 'dataMax',
    }))
    yAxes = [
      { scale: true, splitArea: { show: true } },
      { gridIndex: 1, splitNumber: 2 },
    ]
    zoomIndices = [0, 1]
    dataZoomTop = '92%'
  } else if (extraSubs === 1) {
    grids = [
      { left: '8%', right: '4%', top: '6%', height: '46%' },
      { left: '8%', right: '4%', top: '56%', height: '14%' },
      { left: '8%', right: '4%', top: '74%', height: '14%' },
    ]
    xAxes = [0, 1, 2].map(i => ({
      type: 'category', gridIndex: i, data: dates, scale: true,
      boundaryGap: false, axisLine: { onZero: false },
      splitLine: { show: false }, min: 'dataMin', max: 'dataMax',
    }))
    yAxes = [
      { scale: true, splitArea: { show: true } },
      { gridIndex: 1, splitNumber: 2 },
      { gridIndex: 2, splitNumber: 2 },
    ]
    zoomIndices = [0, 1, 2]
    dataZoomTop = '91%'
  } else {
    grids = [
      { left: '8%', right: '4%', top: '5%', height: '38%' },
      { left: '8%', right: '4%', top: '47%', height: '12%' },
      { left: '8%', right: '4%', top: '63%', height: '12%' },
      { left: '8%', right: '4%', top: '79%', height: '12%' },
    ]
    xAxes = [0, 1, 2, 3].map(i => ({
      type: 'category', gridIndex: i, data: dates, scale: true,
      boundaryGap: false, axisLine: { onZero: false },
      splitLine: { show: false }, min: 'dataMin', max: 'dataMax',
    }))
    yAxes = [
      { scale: true, splitArea: { show: true } },
      { gridIndex: 1, splitNumber: 2 },
      { gridIndex: 2, splitNumber: 2 },
      { gridIndex: 3, splitNumber: 2 },
    ]
    zoomIndices = [0, 1, 2, 3]
    dataZoomTop = '93%'
  }

  const legendData = ['日K', '成交量']
  const series = [
    {
      name: '日K',
      type: 'candlestick',
      data: klineOhlc.value,
      itemStyle: {
        color: '#ef232a',
        color0: '#14b143',
        borderColor: '#ef232a',
        borderColor0: '#14b143',
      },
    },
    {
      name: '成交量',
      type: 'bar',
      xAxisIndex: 1,
      yAxisIndex: 1,
      data: klineVolumes.value,
      itemStyle: { color: '#7fbbea' },
    },
  ]

  // MA 均线叠加在主图
  if (showMA && data.length) {
    const maColors = { MA5: '#ffaa00', MA10: '#ff55ff', MA20: '#00bfff', MA60: '#cccccc' }
    const maData = { MA5: calcMA(data, 5), MA10: calcMA(data, 10), MA20: calcMA(data, 20), MA60: calcMA(data, 60) }
    Object.entries(maData).forEach(([key, vals]) => {
      legendData.push(key)
      series.push({
        name: key,
        type: 'line',
        data: vals,
        smooth: true,
        showSymbol: false,
        lineStyle: { width: 1, color: maColors[key] },
        itemStyle: { color: maColors[key] },
      })
    })
  }

  // EMA 均线叠加在主图
  if (showEMA && data.length) {
    const emaColors = { EMA12: '#e6a23c', EMA26: '#a23ce6' }
    const emaData = { EMA12: calcEMA(data, 12), EMA26: calcEMA(data, 26) }
    Object.entries(emaData).forEach(([key, vals]) => {
      legendData.push(key)
      series.push({
        name: key,
        type: 'line',
        data: vals,
        smooth: true,
        showSymbol: false,
        lineStyle: { width: 1, color: emaColors[key] },
        itemStyle: { color: emaColors[key] },
      })
    })
  }

  // MACD 副图
  if (showMACD && data.length) {
    const gi = 2
    const { dif, dea, macd } = calcMACD(data)
    legendData.push('DIF', 'DEA', 'MACD')
    series.push(
      {
        name: 'DIF', type: 'line', xAxisIndex: gi, yAxisIndex: gi,
        data: dif, showSymbol: false,
        lineStyle: { width: 1, color: '#ffaa00' },
        itemStyle: { color: '#ffaa00' },
      },
      {
        name: 'DEA', type: 'line', xAxisIndex: gi, yAxisIndex: gi,
        data: dea, showSymbol: false,
        lineStyle: { width: 1, color: '#ff55ff' },
        itemStyle: { color: '#ff55ff' },
      },
      {
        name: 'MACD', type: 'bar', xAxisIndex: gi, yAxisIndex: gi,
        data: macd.map(v => ({
          value: v,
          itemStyle: { color: v >= 0 ? '#ef232a' : '#14b143' },
        })),
      }
    )
  }

  // KDJ 副图
  if (showKDJ && data.length) {
    const gi = showMACD ? 3 : 2
    const { k, d, j } = calcKDJ(data)
    legendData.push('K', 'D', 'J')
    series.push(
      {
        name: 'K', type: 'line', xAxisIndex: gi, yAxisIndex: gi,
        data: k, showSymbol: false,
        lineStyle: { width: 1, color: '#ffaa00' },
        itemStyle: { color: '#ffaa00' },
      },
      {
        name: 'D', type: 'line', xAxisIndex: gi, yAxisIndex: gi,
        data: d, showSymbol: false,
        lineStyle: { width: 1, color: '#ff55ff' },
        itemStyle: { color: '#ff55ff' },
      },
      {
        name: 'J', type: 'line', xAxisIndex: gi, yAxisIndex: gi,
        data: j, showSymbol: false,
        lineStyle: { width: 1, color: '#00bfff' },
        itemStyle: { color: '#00bfff' },
      }
    )
  }

  return {
    animation: true,
    legend: { data: legendData, top: 0 },
    tooltip: { trigger: 'axis', axisPointer: { type: 'cross' } },
    axisPointer: { link: { xAxisIndex: 'all' } },
    grid: grids,
    xAxis: xAxes,
    yAxis: yAxes,
    dataZoom: [
      { type: 'inside', xAxisIndex: zoomIndices, start: 60, end: 100 },
      { show: true, type: 'slider', xAxisIndex: zoomIndices, top: dataZoomTop, start: 60, end: 100 },
    ],
    series,
  }
})

async function loadKline() {
  klineLoading.value = true
  try {
    const res = await getIndexKline(selectedIndex.value, {
      period: selectedPeriod.value,
      limit: 120,
    })
    klineItems.value = res?.items ?? []
  } catch (e) {
    klineItems.value = []
  } finally {
    klineLoading.value = false
  }
}

async function loadOverview() {
  try {
    const res = await getMarketOverview()
    overviewItems.value = res?.items ?? []
  } catch (e) {
    overviewItems.value = []
  }
}

// 切换指数或周期时重新加载K线
watch([selectedIndex, selectedPeriod], () => {
  loadKline()
})

// 工具函数
function isToday(dateStr) {
  if (!dateStr) return false
  const d = new Date(dateStr)
  const today = new Date()
  return d.toDateString() === today.toDateString()
}
function isWithinDays(dateStr, days) {
  if (!dateStr) return false
  const d = new Date(dateStr)
  const cutoff = new Date()
  cutoff.setDate(cutoff.getDate() - days)
  return d >= cutoff
}
function formatIc(v) {
  if (v == null || v === '') return '--'
  return Number(v).toFixed(4)
}
function formatNum(v) {
  if (v == null || v === '') return '--'
  return Number(v).toFixed(2)
}
function formatPercent(v) {
  if (v == null || v === '') return '--'
  return (Number(v) * 100).toFixed(1) + '%'
}
function numClass(v) {
  if (v == null || v === '') return ''
  return Number(v) >= 0 ? 'num--success' : 'num--danger'
}

// 并行加载所有数据
async function loadAll() {
  loading.value = true
  try {
    const [factors, factorsAll, strategiesData, mining, backtests, dataStatus, indicesData] = await Promise.all([
      listFactors({ limit: 1 }),          // 设计稿要求：从 total 获取因子总数
      listFactors({ limit: 500 }),        // 额外调用：取 items 用于按来源分类统计副说明
      listStrategies(),                   // 策略列表（用于活跃/归档分类）
      listMiningTasks({ limit: 5 }),      // 最近挖掘任务
      listAllBacktestResults({ limit: 5 }),// 最近回测结果
      getQuantDataStatus(),                // 数据状态
      listIndices(),                       // 指数列表
    ])
    // 因子总数
    factorTotal.value = factors?.total ?? 0
    // 因子按来源分类
    const bySource = { builtin: 0, llm: 0, symbolic: 0, text: 0, automl: 0 }
    const factorItems = factorsAll?.items ?? []
    factorItems.forEach(f => {
      const s = (f.source || f.type || '').toLowerCase()
      if (bySource[s] != null) bySource[s]++
    })
    factorBySource.value = bySource
    // 策略
    strategies.value = strategiesData?.items ?? []
    // 挖掘任务
    recentMining.value = mining?.items ?? []
    miningTotal.value = mining?.total ?? 0
    // 回测结果
    recentBacktests.value = backtests?.items ?? []
    backtestTotal.value = backtests?.total ?? 0
    // 指数列表
    indices.value = indicesData?.items ?? []
  } catch (e) {
    if (e !== 'cancel') ElMessage.error('加载首页数据失败')
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  loadAll()
  loadOverview()
  loadKline()
  // 检查是否首次访问
  if (!localStorage.getItem('quantlab_guide_seen')) {
    guideVisible.value = true
  }
})
</script>

<style scoped lang="scss">
// Dashboard 页面容器
.dashboard {
  max-width: 1400px;
  margin: 0 auto;
  width: 100%;
  padding: 24px;
  display: flex;
  flex-direction: column;
  gap: 24px;
}

// 页面头
.dashboard__header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
}
.dashboard__title {
  font-size: 24px;
  font-weight: 600;
  color: var(--text-primary);
  margin: 0;
  line-height: 1.3;
}
.dashboard__subtitle {
  font-size: 14px;
  color: var(--text-tertiary);
  margin-top: 4px;
}

// KPI 统计卡行
.kpi-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 16px;

  @media (max-width: 768px) {
    grid-template-columns: repeat(2, 1fr);
  }
}

// KPI 统计卡
.kpi-card {
  position: relative;
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 20px;
}
.kpi-card__label {
  font-size: 14px;
  color: var(--text-tertiary);
}
.kpi-card__value {
  font-size: 32px;
  font-weight: 600;
  color: var(--text-primary);
  font-variant-numeric: tabular-nums;
  line-height: 1.2;
  margin-top: 8px;
}
.kpi-card__sub {
  font-size: 12px;
  color: var(--text-tertiary);
  margin-top: 6px;
}
.kpi-card__icon {
  position: absolute;
  top: 20px;
  right: 20px;
  font-size: 20px;
  color: var(--text-tertiary);
}

// 市场概览卡片：多指数实时行情
.market-overview-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(140px, 1fr));
  gap: 12px;
}
.market-overview-card {
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 14px 16px;
  cursor: pointer;
  transition: all var(--duration-fast) var(--ease-in-out);

  &:hover {
    border-color: var(--primary);
  }
  &.is-active {
    border-color: var(--primary);
    box-shadow: 0 0 0 1px var(--primary);
  }
}
.market-overview-card__name {
  font-size: 13px;
  color: var(--text-tertiary);
}
.market-overview-card__price {
  font-size: 20px;
  font-weight: 600;
  color: var(--text-primary);
  font-variant-numeric: tabular-nums;
  margin-top: 4px;
}
.market-overview-card__pct {
  font-size: 13px;
  font-weight: 500;
  font-variant-numeric: tabular-nums;
  margin-top: 2px;

  &.is-up {
    color: #ef232a;
  }
  &.is-down {
    color: #14b143;
  }
}

// 市场概览图表卡
.chart-card {
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 20px;
}
.chart-card__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 16px;
  flex-wrap: wrap;
  gap: 12px;
}
.chart-card__title {
  font-size: 18px;
  font-weight: 600;
  color: var(--text-primary);
  margin: 0;
}
.chart-card__controls {
  display: flex;
  align-items: center;
  gap: 12px;
}
.chart-card__index-select {
  width: 130px;
}
.chart-card__range {
  display: flex;
  gap: 4px;
}
.chart-card__indicators {
  margin-left: 4px;
}
.chart-card__range-btn {
  border: none;
  background: transparent;
  color: var(--text-tertiary);
  font-size: 13px;
  padding: 4px 12px;
  border-radius: 4px;
  cursor: pointer;
  transition: all var(--duration-fast) var(--ease-in-out);
  font-family: var(--font-family);

  &.is-active {
    background: var(--primary);
    color: #fff;
  }

  &:hover:not(.is-active) {
    color: var(--text-primary);
    background: var(--bg-hover);
  }
}
.chart-card__chart {
  width: 100%;
  height: 300px;
}
.chart-card__chart--kline {
  height: 420px;
}

// 双列区
.dual-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 24px;

  @media (max-width: 992px) {
    grid-template-columns: 1fr;
  }
}

// 表格卡
.table-card {
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 20px;
}
.table-card__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 16px;
}
.table-card__title {
  font-size: 18px;
  font-weight: 600;
  color: var(--text-primary);
  margin: 0;
}
.table-card__link {
  color: var(--primary);
  font-size: 13px;
  text-decoration: none;

  &:hover {
    text-decoration: underline;
  }
}

// Badge 通用样式
.badge {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 4px;
  font-size: 12px;
  font-weight: 500;
  line-height: 1.5;

  &--primary {
    color: var(--primary);
    background: rgba(31, 75, 160, 0.1);
  }
  &--success {
    color: var(--success);
    background: rgba(31, 157, 107, 0.1);
  }
  &--warning {
    color: var(--warning);
    background: rgba(200, 128, 28, 0.1);
  }
  &--danger {
    color: var(--danger);
    background: rgba(210, 69, 69, 0.1);
  }
  &--info {
    color: var(--info);
    background: rgba(47, 125, 194, 0.1);
  }
}

// 数字列：等宽字体 + 表格数字
.num {
  font-family: var(--font-mono);
  font-variant-numeric: tabular-nums;

  &--success {
    color: var(--success);
  }
  &--danger {
    color: var(--danger);
  }
}

// el-table 样式覆盖
.dashboard-table {
  :deep(.el-table) {
    --el-table-bg-color: transparent;
    --el-table-tr-bg-color: transparent;
    --el-table-header-bg-color: var(--bg-tertiary);
    --el-table-border-color: var(--border);
    --el-table-border: 1px solid var(--border);
    --el-table-text-color: var(--text-primary);
    --el-table-header-text-color: var(--text-tertiary);
    --el-table-row-hover-bg-color: var(--bg-hover);
    background: transparent;
  }

  :deep(.el-table__header-wrapper) {
    th.el-table__cell {
      font-size: 12px;
      color: var(--text-tertiary);
      background: var(--bg-tertiary);
      font-weight: 500;
    }
  }

  :deep(.el-table__row) {
    td.el-table__cell {
      font-size: 13px;
      color: var(--text-primary);
    }

    &:hover > td.el-table__cell {
      background: var(--bg-hover) !important;
    }
  }

  :deep(.el-table__inner-wrapper::before),
  :deep(.el-table__border-left-patch) {
    display: none;
  }
}
</style>
