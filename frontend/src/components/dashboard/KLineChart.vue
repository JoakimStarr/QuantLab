<template>
  <SectionCard title="K线走势">
    <template #extra>
      <div class="chart-controls">
        <el-select
          :model-value="selectedIndex"
          size="small"
          class="chart-index-select"
          placeholder="选择指数"
          @update:model-value="$emit('update:selected-index', $event)"
        >
          <el-option
            v-for="idx in indices"
            :key="idx.code"
            :label="idx.name"
            :value="idx.code"
          />
        </el-select>
        <div class="chart-range">
          <button
            v-for="p in periods"
            :key="p.key"
            class="chart-range-btn"
            :class="{ 'is-active': selectedPeriod === p.key }"
            @click="$emit('update:selected-period', p.key)"
          >{{ p.label }}</button>
        </div>
        <el-checkbox-group
          :model-value="activeIndicators"
          size="small"
          class="chart-indicators"
          @update:model-value="$emit('update:active-indicators', $event)"
        >
          <el-checkbox-button label="MA">MA</el-checkbox-button>
          <el-checkbox-button label="EMA">EMA</el-checkbox-button>
          <el-checkbox-button label="MACD">MACD</el-checkbox-button>
          <el-checkbox-button label="KDJ">KDJ</el-checkbox-button>
        </el-checkbox-group>
      </div>
    </template>
    <el-skeleton v-if="klineLoading" :rows="8" animated />
    <v-chart
      v-else-if="klineItems.length"
      :option="klineOption"
      :style="{ height: klineChartHeight + 'px' }"
      class="chart-kline"
      autoresize
    />
    <el-empty v-else description="暂无K线数据" />
  </SectionCard>
</template>

<script setup>
import { computed } from 'vue'
import VChart from 'vue-echarts'
import SectionCard from '@/components/common/SectionCard.vue'

const props = defineProps({
  klineItems: { type: Array, default: () => [] },
  indices: { type: Array, default: () => [] },
  selectedIndex: { type: String, default: '' },
  selectedPeriod: { type: String, default: '1d' },
  activeIndicators: { type: Array, default: () => ['MA'] },
  periods: { type: Array, default: () => [] },
  klineLoading: { type: Boolean, default: false }
})

defineEmits(['update:selected-index', 'update:selected-period', 'update:active-indicators'])

const klineDates = computed(() => props.klineItems.map(k => k.date))
const klineOhlc = computed(() => props.klineItems.map(k => [k.open, k.close, k.low, k.high]))
const klineVolumes = computed(() => props.klineItems.map(k => k.volume))

function calcMA(data, period) {
  const result = []
  for (let i = 0; i < data.length; i++) {
    if (i < period - 1) { result.push(null); continue }
    let sum = 0
    for (let j = 0; j < period; j++) sum += data[i - j].close
    result.push(sum / period)
  }
  return result
}

function calcEMA(data, period) {
  const result = []
  const k = 2 / (period + 1)
  let ema = null
  for (let i = 0; i < data.length; i++) {
    ema = i === 0 ? data[i].close : data[i].close * k + ema * (1 - k)
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
    prev = i === 0 ? dif[0] : dif[i] * k + prev * (1 - k)
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

const klineChartHeight = computed(() => {
  let h = 420
  if (props.activeIndicators.includes('MACD')) h += 120
  if (props.activeIndicators.includes('KDJ')) h += 120
  return h
})

const klineOption = computed(() => {
  const inds = props.activeIndicators
  const showMA = inds.includes('MA')
  const showEMA = inds.includes('EMA')
  const showMACD = inds.includes('MACD')
  const showKDJ = inds.includes('KDJ')

  const data = props.klineItems
  const dates = klineDates.value
  if (!data.length) return {}

  const extraSubs = (showMACD ? 1 : 0) + (showKDJ ? 1 : 0)
  let grids, xAxes, yAxes, zoomIndices, dataZoomTop
  if (extraSubs === 0) {
    grids = [
      { left: '8%', right: '4%', top: '8%', height: '55%' },
      { left: '8%', right: '4%', top: '70%', height: '18%' },
    ]
    xAxes = [0, 1].map(i => ({ type: 'category', gridIndex: i, data: dates, scale: true, boundaryGap: false, axisLine: { onZero: false }, splitLine: { show: false }, min: 'dataMin', max: 'dataMax' }))
    yAxes = [{ scale: true, splitArea: { show: true } }, { gridIndex: 1, splitNumber: 2 }]
    zoomIndices = [0, 1]; dataZoomTop = '92%'
  } else if (extraSubs === 1) {
    grids = [
      { left: '8%', right: '4%', top: '6%', height: '46%' },
      { left: '8%', right: '4%', top: '56%', height: '14%' },
      { left: '8%', right: '4%', top: '74%', height: '14%' },
    ]
    xAxes = [0, 1, 2].map(i => ({ type: 'category', gridIndex: i, data: dates, scale: true, boundaryGap: false, axisLine: { onZero: false }, splitLine: { show: false }, min: 'dataMin', max: 'dataMax' }))
    yAxes = [{ scale: true, splitArea: { show: true } }, { gridIndex: 1, splitNumber: 2 }, { gridIndex: 2, splitNumber: 2 }]
    zoomIndices = [0, 1, 2]; dataZoomTop = '91%'
  } else {
    grids = [
      { left: '8%', right: '4%', top: '5%', height: '38%' },
      { left: '8%', right: '4%', top: '47%', height: '12%' },
      { left: '8%', right: '4%', top: '63%', height: '12%' },
      { left: '8%', right: '4%', top: '79%', height: '12%' },
    ]
    xAxes = [0, 1, 2, 3].map(i => ({ type: 'category', gridIndex: i, data: dates, scale: true, boundaryGap: false, axisLine: { onZero: false }, splitLine: { show: false }, min: 'dataMin', max: 'dataMax' }))
    yAxes = [{ scale: true, splitArea: { show: true } }, { gridIndex: 1, splitNumber: 2 }, { gridIndex: 2, splitNumber: 2 }, { gridIndex: 3, splitNumber: 2 }]
    zoomIndices = [0, 1, 2, 3]; dataZoomTop = '93%'
  }

  const legendData = ['日K', '成交量']
  const series = [
    {
      name: '日K', type: 'candlestick', data: klineOhlc.value,
      itemStyle: { color: '#ef232a', color0: '#14b143', borderColor: '#ef232a', borderColor0: '#14b143' },
    },
    { name: '成交量', type: 'bar', xAxisIndex: 1, yAxisIndex: 1, data: klineVolumes.value, itemStyle: { color: '#7fbbea' } },
  ]

  if (showMA) {
    const maColors = { MA5: '#ffaa00', MA10: '#ff55ff', MA20: '#00bfff', MA60: '#cccccc' }
    const maData = { MA5: calcMA(data, 5), MA10: calcMA(data, 10), MA20: calcMA(data, 20), MA60: calcMA(data, 60) }
    Object.entries(maData).forEach(([key, vals]) => {
      legendData.push(key)
      series.push({ name: key, type: 'line', data: vals, smooth: true, showSymbol: false, lineStyle: { width: 1, color: maColors[key] }, itemStyle: { color: maColors[key] } })
    })
  }

  if (showEMA) {
    const emaColors = { EMA12: '#e6a23c', EMA26: '#a23ce6' }
    const emaData = { EMA12: calcEMA(data, 12), EMA26: calcEMA(data, 26) }
    Object.entries(emaData).forEach(([key, vals]) => {
      legendData.push(key)
      series.push({ name: key, type: 'line', data: vals, smooth: true, showSymbol: false, lineStyle: { width: 1, color: emaColors[key] }, itemStyle: { color: emaColors[key] } })
    })
  }

  if (showMACD) {
    const gi = 2
    const { dif, dea, macd } = calcMACD(data)
    legendData.push('DIF', 'DEA', 'MACD')
    series.push(
      { name: 'DIF', type: 'line', xAxisIndex: gi, yAxisIndex: gi, data: dif, showSymbol: false, lineStyle: { width: 1, color: '#ffaa00' }, itemStyle: { color: '#ffaa00' } },
      { name: 'DEA', type: 'line', xAxisIndex: gi, yAxisIndex: gi, data: dea, showSymbol: false, lineStyle: { width: 1, color: '#ff55ff' }, itemStyle: { color: '#ff55ff' } },
      { name: 'MACD', type: 'bar', xAxisIndex: gi, yAxisIndex: gi, data: macd.map(v => ({ value: v, itemStyle: { color: v >= 0 ? '#ef232a' : '#14b143' } })) }
    )
  }

  if (showKDJ) {
    const gi = showMACD ? 3 : 2
    const { k, d, j } = calcKDJ(data)
    legendData.push('K', 'D', 'J')
    series.push(
      { name: 'K', type: 'line', xAxisIndex: gi, yAxisIndex: gi, data: k, showSymbol: false, lineStyle: { width: 1, color: '#ffaa00' }, itemStyle: { color: '#ffaa00' } },
      { name: 'D', type: 'line', xAxisIndex: gi, yAxisIndex: gi, data: d, showSymbol: false, lineStyle: { width: 1, color: '#ff55ff' }, itemStyle: { color: '#ff55ff' } },
      { name: 'J', type: 'line', xAxisIndex: gi, yAxisIndex: gi, data: j, showSymbol: false, lineStyle: { width: 1, color: '#00bfff' }, itemStyle: { color: '#00bfff' } }
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
</script>

<style scoped lang="scss">
.chart-controls {
  display: flex; align-items: center; gap: 12px; flex-wrap: wrap;
}
.chart-index-select { width: 130px; }
.chart-range { display: flex; gap: 4px; }
.chart-indicators { margin-left: 4px; }
.chart-range-btn {
  border: none; background: transparent; color: var(--text-tertiary);
  font-size: 13px; padding: 4px 12px; border-radius: 4px; cursor: pointer;
  transition: all var(--duration-fast) var(--ease-in-out); font-family: var(--font-family);
  &.is-active { background: var(--primary); color: #fff; }
  &:hover:not(.is-active) { color: var(--text-primary); background: var(--bg-hover); }
}
.chart-kline { width: 100%; }
</style>
