<template>
  <SectionCard title="K线走势" collapsible>
    <template #extra>
      <div class="chart-controls">
        <div class="chart-stock-search">
          <el-autocomplete
            v-model="stockQuery"
            class="chart-stock-search__input"
            :fetch-suggestions="querySearch"
            placeholder="搜索个股：名称 / 首字母 / 代码"
            clearable
            size="small"
            :debounce="300"
            value-key="value"
            @select="onStockSelect"
          >
            <template #default="{ item }">
              <div class="stock-suggestion">
                <span class="stock-suggestion__name">{{ item.name }}</span>
                <span class="stock-suggestion__code">{{ item.code }}</span>
                <span v-if="item.initials" class="stock-suggestion__initials">{{ item.initials }}</span>
              </div>
            </template>
          </el-autocomplete>
          <el-button size="small" type="primary" :disabled="!stockQuery.trim()" @click="searchFirst">
            个股K线
          </el-button>
        </div>
        <el-tag v-if="stockTarget" closable size="small" class="chart-stock-tag" @close="$emit('clear-stock')">
          {{ stockTarget.name }}
        </el-tag>
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
        <el-radio-group
          :model-value="timeRange"
          size="small"
          class="chart-timerange"
          @update:model-value="$emit('update:time-range', $event)"
        >
          <el-radio-button value="1M">1月</el-radio-button>
          <el-radio-button value="3M">3月</el-radio-button>
          <el-radio-button value="6M">6月</el-radio-button>
          <el-radio-button value="1Y">1年</el-radio-button>
          <el-radio-button value="2Y">2年</el-radio-button>
          <el-radio-button value="ALL">全部</el-radio-button>
          <el-radio-button value="custom">自定义</el-radio-button>
        </el-radio-group>
        <el-date-picker
          v-if="timeRange === 'custom'"
          :model-value="customRange"
          type="daterange"
          start-placeholder="开始日期"
          end-placeholder="结束日期"
          format="YYYY-MM-DD"
          value-format="YYYY-MM-DD"
          size="small"
          class="chart-daterange"
          @update:model-value="$emit('update:custom-range', $event)"
        />
        <el-checkbox-group
          :model-value="activeIndicators"
          size="small"
          class="chart-indicators"
          @update:model-value="$emit('update:active-indicators', $event)"
        >
          <el-checkbox-button value="MA">MA</el-checkbox-button>
          <el-checkbox-button value="EMA">EMA</el-checkbox-button>
          <el-checkbox-button value="MACD">MACD</el-checkbox-button>
          <el-checkbox-button value="KDJ">KDJ</el-checkbox-button>
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
import { computed, ref } from 'vue'
import VChart from 'vue-echarts'
import { ElMessage } from 'element-plus/es/components/message/index'
import SectionCard from '@/components/common/SectionCard.vue'
import { chartTheme } from '@/utils/chartTheme'
import { useThemeRev } from '@/composables/useChartTheme'
import { searchStocks } from '@/api/quant'

const themeRev = useThemeRev()

const props = defineProps({
  klineItems: { type: Array, default: () => [] },
  indices: { type: Array, default: () => [] },
  selectedIndex: { type: String, default: '' },
  selectedPeriod: { type: String, default: '1d' },
  activeIndicators: { type: Array, default: () => ['MA'] },
  periods: { type: Array, default: () => [] },
  klineLoading: { type: Boolean, default: false },
  timeRange: { type: String, default: '2Y' },
  customRange: { type: Array, default: () => null },
  stockTarget: { type: Object, default: null },
})

const emit = defineEmits([
  'update:selected-index',
  'update:selected-period',
  'update:active-indicators',
  'update:time-range',
  'update:custom-range',
  'run-stock-kline',
  'select-stock',
  'clear-stock',
])

const stockQuery = ref('')

async function querySearch(query, cb) {
  const q = (query || '').trim()
  if (!q) return cb([])
  try {
    const res = await searchStocks(q, 10)
    cb((res?.items ?? []).map(s => ({
      value: `${s.name} ${s.code}`,
      code: s.qlib_code || s.code,
      name: s.name,
      initials: s.initials || '',
    })))
  } catch {
    cb([])
  }
}

function onStockSelect(item) {
  if (!item?.code) return
  emit('select-stock', { code: item.code, name: item.name })
  stockQuery.value = ''
}

async function searchFirst() {
  const q = stockQuery.value.trim()
  if (!q) return
  try {
    const res = await searchStocks(q, 1)
    const s = res?.items?.[0]
    if (s?.qlib_code) {
      emit('select-stock', { code: s.qlib_code, name: s.name })
      stockQuery.value = ''
    } else {
      ElMessage.warning('未找到匹配个股')
    }
  } catch {
    ElMessage.error('个股搜索失败')
  }
}

const klineDates = computed(() => props.klineItems.map(k => k.date))
const klineOhlc = computed(() => props.klineItems.map(k => [k.open, k.close, k.low, k.high]))
// 个股成交量单位按万股显示，指数按亿股显示
const isStock = computed(() => !!props.stockTarget)
const volumeUnit = computed(() => (isStock.value ? '万股' : '亿股'))
const klineVolumes = computed(() => props.klineItems.map(k => (Number(k.volume) || 0) / (isStock.value ? 10000 : 100000000)))

function formatVolume(value) {
  const num = Number(value) || 0
  return `${num.toFixed(num >= 100 ? 0 : 2)}${volumeUnit.value}`
}

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
  void themeRev.value
  const inds = props.activeIndicators
  const showMA = inds.includes('MA')
  const showEMA = inds.includes('EMA')
  const showMACD = inds.includes('MACD')
  const showKDJ = inds.includes('KDJ')

  const data = props.klineItems
  const dates = klineDates.value
  if (!data.length) return {}

  const extraSubs = (showMACD ? 1 : 0) + (showKDJ ? 1 : 0)
  // 数据量少时（如 1 月）默认显示全部，避免被 dataZoom 裁成尾部一小段
  const zoomStart = data.length <= 80 ? 0 : 60
  let grids, xAxes, yAxes, zoomIndices
  if (extraSubs === 0) {
    grids = [
      { left: '10%', right: '4%', top: '8%', height: '55%' },
      { left: '10%', right: '4%', top: '70%', height: '18%' },
    ]
    xAxes = [0, 1].map(i => ({ type: 'category', gridIndex: i, data: dates, scale: true, boundaryGap: true, axisLine: { onZero: false }, splitLine: { show: false }, min: 'dataMin', max: 'dataMax' }))
    yAxes = [{ scale: true, splitArea: { show: true } }, { gridIndex: 1, splitNumber: 2 }]
    zoomIndices = [0, 1]
  } else if (extraSubs === 1) {
    grids = [
      { left: '10%', right: '4%', top: '6%', height: '46%' },
      { left: '10%', right: '4%', top: '56%', height: '14%' },
      { left: '10%', right: '4%', top: '74%', height: '14%' },
    ]
    xAxes = [0, 1, 2].map(i => ({ type: 'category', gridIndex: i, data: dates, scale: true, boundaryGap: true, axisLine: { onZero: false }, splitLine: { show: false }, min: 'dataMin', max: 'dataMax' }))
    yAxes = [{ scale: true, splitArea: { show: true } }, { gridIndex: 1, splitNumber: 2 }, { gridIndex: 2, splitNumber: 2 }]
    zoomIndices = [0, 1, 2]
  } else {
    grids = [
      { left: '10%', right: '4%', top: '5%', height: '38%' },
      { left: '10%', right: '4%', top: '47%', height: '12%' },
      { left: '10%', right: '4%', top: '63%', height: '12%' },
      { left: '10%', right: '4%', top: '79%', height: '12%' },
    ]
    xAxes = [0, 1, 2, 3].map(i => ({ type: 'category', gridIndex: i, data: dates, scale: true, boundaryGap: true, axisLine: { onZero: false }, splitLine: { show: false }, min: 'dataMin', max: 'dataMax' }))
    yAxes = [{ scale: true, splitArea: { show: true } }, { gridIndex: 1, splitNumber: 2 }, { gridIndex: 2, splitNumber: 2 }, { gridIndex: 3, splitNumber: 2 }]
    zoomIndices = [0, 1, 2, 3]
  }

  const legendData = [`日K`, `成交量(${volumeUnit.value})`]
  const series = [
    {
      name: '日K', type: 'candlestick', data: klineOhlc.value,
      itemStyle: { color: chartTheme.up(), color0: chartTheme.down(), borderColor: chartTheme.up(), borderColor0: chartTheme.down() },
    },
    { name: `成交量(${volumeUnit.value})`, type: 'bar', xAxisIndex: 1, yAxisIndex: 1, data: klineVolumes.value, itemStyle: { color: chartTheme.volume() } },
  ]

  if (showMA) {
    const maColors = { MA5: chartTheme.ma5(), MA10: chartTheme.ma10(), MA20: chartTheme.ma20(), MA60: chartTheme.ma60() }
    const maData = { MA5: calcMA(data, 5), MA10: calcMA(data, 10), MA20: calcMA(data, 20), MA60: calcMA(data, 60) }
    Object.entries(maData).forEach(([key, vals]) => {
      legendData.push(key)
      series.push({ name: key, type: 'line', data: vals, smooth: true, showSymbol: false, lineStyle: { width: 1, color: maColors[key] }, itemStyle: { color: maColors[key] } })
    })
  }

  if (showEMA) {
    const emaColors = { EMA12: chartTheme.ema12(), EMA26: chartTheme.ema26() }
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
      { name: 'DIF', type: 'line', xAxisIndex: gi, yAxisIndex: gi, data: dif, showSymbol: false, lineStyle: { width: 1, color: chartTheme.dif() }, itemStyle: { color: chartTheme.dif() } },
      { name: 'DEA', type: 'line', xAxisIndex: gi, yAxisIndex: gi, data: dea, showSymbol: false, lineStyle: { width: 1, color: chartTheme.dea() }, itemStyle: { color: chartTheme.dea() } },
      { name: 'MACD', type: 'bar', xAxisIndex: gi, yAxisIndex: gi, data: macd.map(v => ({ value: v, itemStyle: { color: v >= 0 ? chartTheme.up() : chartTheme.down() } })) }
    )
  }

  if (showKDJ) {
    const gi = showMACD ? 3 : 2
    const { k, d, j } = calcKDJ(data)
    legendData.push('K', 'D', 'J')
    series.push(
      { name: 'K', type: 'line', xAxisIndex: gi, yAxisIndex: gi, data: k, showSymbol: false, lineStyle: { width: 1, color: chartTheme.k() }, itemStyle: { color: chartTheme.k() } },
      { name: 'D', type: 'line', xAxisIndex: gi, yAxisIndex: gi, data: d, showSymbol: false, lineStyle: { width: 1, color: chartTheme.d() }, itemStyle: { color: chartTheme.d() } },
      { name: 'J', type: 'line', xAxisIndex: gi, yAxisIndex: gi, data: j, showSymbol: false, lineStyle: { width: 1, color: chartTheme.j() }, itemStyle: { color: chartTheme.j() } }
    )
  }

  return {
    animation: true,
    textStyle: { color: chartTheme.axisText() },
    legend: { data: legendData, top: 0, type: 'scroll', width: '100%', textStyle: { color: chartTheme.axisText() } },
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'cross' },
      formatter(params) {
        const items = Array.isArray(params) ? params : [params]
        if (!items.length) return ''

        const lines = [items[0].axisValueLabel || '']
        for (const item of items) {
          if (item.seriesName === '日K' && Array.isArray(item.data)) {
            const [open, close, low, high] = item.data
            const idx = item.dataIndex
            const prev = props.klineItems[idx - 1]
            const cur = props.klineItems[idx]
            const pct = prev && cur ? ((Number(cur.close) - Number(prev.close)) / Number(prev.close) * 100) : null
            const pctTxt = pct !== null ? `（${pct >= 0 ? '+' : ''}${pct.toFixed(2)}%）` : ''
            lines.push(`${item.marker}${item.seriesName}：开 ${open}，收 ${close}，低 ${low}，高 ${high}${pctTxt}`)
            continue
          }
          if (item.seriesName.startsWith('成交量(')) {
            lines.push(`${item.marker}${item.seriesName}：${formatVolume(item.data)}`)
            continue
          }
          lines.push(`${item.marker}${item.seriesName}：${item.data}`)
        }
        return lines.join('<br/>')
      },
    },
    axisPointer: { link: { xAxisIndex: 'all' } },
    grid: grids,
    xAxis: xAxes,
    yAxis: yAxes.map((axis, index) => (
      index === 1
        ? { ...axis, axisLabel: { formatter: (value) => `${value}${volumeUnit.value}` } }
        : axis
    )),
    dataZoom: [
      { type: 'inside', xAxisIndex: zoomIndices, start: zoomStart, end: 100 },
      { show: true, type: 'slider', xAxisIndex: zoomIndices, bottom: 6, height: 20, start: zoomStart, end: 100, textStyle: { color: chartTheme.axisText() } },
    ],
    series,
  }
})
</script>

<style scoped lang="scss">
.chart-controls {
  display: flex; align-items: center; gap: 12px; flex-wrap: wrap;
}
.chart-stock-search {
  display: flex;
  align-items: center;
  gap: 8px;
}
.chart-stock-search__input {
  width: 230px;
}
.stock-suggestion {
  display: flex; align-items: center; gap: 10px; width: 100%;
}
.stock-suggestion__name { color: var(--text-primary); }
.stock-suggestion__code { color: var(--text-tertiary); font-family: var(--font-mono); font-size: 12px; }
.stock-suggestion__initials {
  margin-left: auto; color: var(--text-tertiary); font-size: 12px;
}
.chart-stock-tag { margin-left: 4px; }
.chart-index-select { width: 130px; }
.chart-range { display: flex; gap: 4px; }
.chart-timerange { margin-left: 4px; }
.chart-daterange { width: 240px !important; margin-left: 4px; }
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
