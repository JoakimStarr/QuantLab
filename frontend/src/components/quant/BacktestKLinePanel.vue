<template>
  <div class="bt-kline">
    <!-- 标的选择与图例 -->
    <div class="bt-kline__head">
      <div class="bt-kline__pick">
        <el-tag v-if="selectedSymbol" size="small" effect="plain" class="bt-kline__tag">
          {{ selectedSymbol }}
        </el-tag>
        <SymbolSearchSelect
          v-model="selectedSymbol"
          :seed-options="seedOptions"
          placeholder="搜索股票 / 指数切换K线标的"
          :limit="10"
          style="width: 240px"
        />
      </div>
      <div class="bt-kline__legend">
        <span class="legend-item"> <span class="legend-dot legend-dot--buy"></span>买入 </span>
        <span class="legend-item"> <span class="legend-dot legend-dot--sell"></span>卖出 </span>
      </div>
      <el-checkbox-group v-model="activeIndicators" size="small" class="bt-kline__ind">
        <el-checkbox-button value="MA">MA</el-checkbox-button>
        <el-checkbox-button value="EMA">EMA</el-checkbox-button>
        <el-checkbox-button value="MACD">MACD</el-checkbox-button>
        <el-checkbox-button value="KDJ">KDJ</el-checkbox-button>
      </el-checkbox-group>
    </div>

    <el-skeleton v-if="klineLoading" :rows="8" animated />
    <v-chart
      v-else-if="klineItems.length"
      :option="klineOption"
      :style="{ height: chartHeight + 'px' }"
      class="bt-kline__chart"
      autoresize
    />
    <el-empty v-else description="该标的在回测区间无K线数据" :image-size="64" />
  </div>
</template>

<script setup>
import { ref, computed, watch } from 'vue'
import VChart from 'vue-echarts'
import '@/utils/echarts'
import { ElMessage } from 'element-plus/es/components/message/index'
import SymbolSearchSelect from '@/components/common/SymbolSearchSelect.vue'
import { getIndexKline } from '@/api/market'
import { klineChartOption, buildTradeMarks } from '@/utils/klineChart'

const props = defineProps({
  result: { type: Object, default: null },
})

// 手动指标默认全不勾选：仅显示策略自动叠加的指标线（result.indicator），
// MA/EMA/MACD/KDJ 由用户按需勾选
const activeIndicators = ref([])
const selectedSymbol = ref('')
const klineItems = ref([])
const klineLoading = ref(false)
let loadSeq = 0

// 回测区间（供 K 线拉取；缺失时兜底）
function klinePeriod() {
  const s = String(props.result?.start_date || '')
  const e = String(props.result?.end_date || '')
  const today = new Date().toISOString().slice(0, 10)
  return { start_date: s || '2019-01-01', end_date: e || today }
}

async function loadKline() {
  const code = selectedSymbol.value
  if (!code) {
    klineItems.value = []
    return
  }
  const seq = ++loadSeq
  klineLoading.value = true
  try {
    const { start_date, end_date } = klinePeriod()
    const res = await getIndexKline(code, { start_date, end_date, period: '1d' })
    if (seq !== loadSeq) return
    klineItems.value = res?.items ?? []
  } catch {
    if (seq === loadSeq) {
      klineItems.value = []
      ElMessage.error('K线数据加载失败')
    }
  } finally {
    if (seq === loadSeq) klineLoading.value = false
  }
}

// 从成交记录中提取标的（用于默认选中回显）
const tradeBySymbol = computed(() => {
  const trades = props.result?.trades || []
  const map = new Map()
  for (const t of trades) {
    const code = String(t.code || '')
    if (!code) continue
    const rec = map.get(code) || { code, total: 0, count: 0 }
    rec.total += Number(t.total) || 0
    rec.count++
    map.set(code, rec)
  }
  return [...map.entries()].map(([, v]) => v)
})

// 初始标的：优先回测标的（result.symbols），其次成交记录中累计成交额最大的，
// 最后回退基准。规则策略旧历史 trades 可能无 code，symbols 兜底保证默认选中回测标的。
const defaultSymbol = computed(() => {
  const best = [...tradeBySymbol.value].sort((a, b) => b.total - a.total)[0]
  const syms = props.result?.symbols || []
  const bm = props.result?.benchmark
  if (best?.code) return String(best.code).toUpperCase()
  if (syms.length) return String(syms[0]).toUpperCase()
  return bm ? String(bm).toUpperCase() : ''
})

const seedOptions = computed(() => {
  const opts = tradeBySymbol.value.map((t) => ({
    value: String(t.code).toUpperCase(),
    label: `${t.code} (${t.count}笔)`,
    type: '',
  }))
  const bm = props.result?.benchmark
  if (bm) opts.unshift({ value: String(bm).toUpperCase(), label: `${bm} 基准`, type: '' })
  return opts
})

// 标的类型：基准/指数按亿股，个股按万股
const isBenchmark = computed(() => {
  const bm = String(props.result?.benchmark || '').toUpperCase()
  return !!bm && String(selectedSymbol.value).toUpperCase() === bm
})
const volumeUnit = computed(() => (isBenchmark.value ? '亿股' : '万股'))

// 当前标的的买卖点
const currentMarks = computed(() => {
  if (isBenchmark.value) return { buy: [], sell: [] }
  const code = String(selectedSymbol.value).toUpperCase()
  const syms = props.result?.symbols || []
  // 旧历史 trades 可能无 code：单标的策略（symbols 唯一）下视为回测标的的交易
  const isTarget = (t) => {
    const tc = String(t.code || '')
    if (tc) return tc.toUpperCase() === code
    return syms.length === 1 && String(syms[0]).toUpperCase() === code
  }
  const trades = (props.result?.trades || []).filter(isTarget)
  return buildTradeMarks(klineItems.value, trades)
})

// 策略指标线（后端随回测结果返回）：按日期对齐 K 线数据，供图表叠加。
// grid=main 价格域叠主图，grid=sub 振荡域占一个副图。
// 策略指标线配色：统一从 chartTheme.palette 派生（token 化，暗色模式自动适配）
const STRATEGY_COLORS = Array.from({ length: 8 }, (_, i) => chartTheme.palette(i + 1))

const strategyIndicator = computed(() => {
  const ind = props.result?.indicator
  if (!ind?.lines?.length || !klineItems.value.length) return null
  const idx = new Map((ind.dates || []).map((d, i) => [String(d).slice(0, 10), i]))
  return {
    name: ind.name,
    lines: ind.lines.map((l, li) => ({
      key: l.key,
      name: l.name,
      grid: l.grid,
      color: STRATEGY_COLORS[li % STRATEGY_COLORS.length],
      data: klineItems.value.map((k) => {
        const i = idx.get(String(k.date).slice(0, 10))
        return i == null ? null : l.values?.[i] ?? null
      }),
    })),
  }
})

const chartHeight = computed(() => {
  let h = 420
  if (activeIndicators.value.includes('MACD')) h += 120
  if (activeIndicators.value.includes('KDJ')) h += 120
  if (strategyIndicator.value?.lines.some((l) => l.grid === 'sub')) h += 120
  return h
})

const klineOption = computed(() =>
  klineChartOption({
    items: klineItems.value,
    activeIndicators: activeIndicators.value,
    buyPoints: currentMarks.value.buy,
    sellPoints: currentMarks.value.sell,
    volumeUnit: volumeUnit.value,
    strategyIndicator: strategyIndicator.value,
  })
)

// 注意：selectedSymbol 的 watch 必须注册在 props.result 的 immediate watch 之前。
// immediate watch 在 setup 同步阶段就会给 selectedSymbol 赋值，若它后注册，
// 会错过这次赋值导致首次挂载不触发 loadKline（表现为"需先切换一次标的才显示"）。
watch(selectedSymbol, () => loadKline())

// 回测结果变化：重置默认标的（selectedSymbol 变化会触发上面的 watch 重新加载）
watch(
  () => props.result,
  () => {
    selectedSymbol.value = defaultSymbol.value
  },
  { immediate: true }
)
</script>

<style scoped lang="scss">
.bt-kline {
  &__head {
    display: flex;
    align-items: center;
    gap: 14px;
    flex-wrap: wrap;
    margin-bottom: 8px;
  }
  &__pick {
    display: flex;
    align-items: center;
    gap: 8px;
  }
  &__tag {
    font-family: var(--font-mono);
  }
  &__ind {
    margin-left: auto;
  }
  &__chart {
    width: 100%;
  }
  &__legend {
    display: flex;
    align-items: center;
    gap: 14px;
    font-size: 12px;
    color: var(--text-tertiary);
  }
}
.legend-item {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
.legend-dot {
  display: inline-block;
  width: 9px;
  height: 9px;
  border-radius: 2px;
  border: 1px solid #fff;
  &--buy {
    background: rgba(210, 69, 69, 0.95);
  }
  &--sell {
    background: rgba(31, 157, 107, 0.95);
  }
}
</style>