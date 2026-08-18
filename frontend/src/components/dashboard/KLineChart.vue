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
          <el-option v-for="idx in indices" :key="idx.code" :label="idx.name" :value="idx.code" />
        </el-select>
        <div class="chart-range">
          <button
            v-for="p in periods"
            :key="p.key"
            class="chart-range-btn"
            :class="{ 'is-active': selectedPeriod === p.key }"
            @click="$emit('update:selected-period', p.key)"
          >
            {{ p.label }}
          </button>
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
import '@/utils/echarts'
import { ElMessage } from 'element-plus/es/components/message/index'
import SectionCard from '@/components/common/SectionCard.vue'
import { klineChartOption } from '@/utils/klineChart'
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
    cb(
      (res?.items ?? []).map((s) => ({
        value: `${s.name} ${s.code}`,
        code: s.qlib_code || s.code,
        name: s.name,
        initials: s.initials || '',
      }))
    )
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

// 个股成交量单位按万股显示，指数按亿股显示（图内统一由 klineChartOption 工厂处理）
const isStock = computed(() => !!props.stockTarget)
const volumeUnit = computed(() => (isStock.value ? '万股' : '亿股'))

const klineChartHeight = computed(() => {
  let h = 420
  if (props.activeIndicators.includes('MACD')) h += 120
  if (props.activeIndicators.includes('KDJ')) h += 120
  return h
})

// ECharts option 统一由 klineChartOption 工厂生成（与回测 K 线面板共用，保证口径一致）
const klineOption = computed(() => {
  void themeRev.value
  return klineChartOption({
    items: props.klineItems,
    activeIndicators: props.activeIndicators,
    volumeUnit: volumeUnit.value,
  })
})

</script>

<style scoped lang="scss">
.chart-controls {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
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
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
}
.stock-suggestion__name {
  color: var(--text-primary);
}
.stock-suggestion__code {
  color: var(--text-tertiary);
  font-family: var(--font-mono);
  font-size: 12px;
}
.stock-suggestion__initials {
  margin-left: auto;
  color: var(--text-tertiary);
  font-size: 12px;
}
.chart-stock-tag {
  margin-left: 4px;
}
.chart-index-select {
  width: 130px;
}
.chart-range {
  display: flex;
  gap: 4px;
}
.chart-timerange {
  margin-left: 4px;
}
.chart-daterange {
  width: 240px !important;
  margin-left: 4px;
}
.chart-indicators {
  margin-left: 4px;
}
.chart-range-btn {
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
    color: var(--text-inverse);
  }
  &:hover:not(.is-active) {
    color: var(--text-primary);
    background: var(--bg-hover);
  }
}
.chart-kline {
  width: 100%;
}
</style>
