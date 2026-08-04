<template>
  <PageContainer narrow>
    <div class="page-header mb-6">
      <h2 class="page-title">策略库</h2>
      <p class="page-desc">内置规则/信号型策略模板，选模板配参数即可回测（v1 结果不保存）</p>
    </div>

    <!-- 模板卡片 -->
    <div class="tpl-grid">
      <el-card v-for="t in templates" :key="t.key" class="tpl-card" shadow="hover">
        <div class="tpl-head">
          <span class="tpl-name">{{ t.name }}</span>
          <el-tag size="small" :type="catType(t.category)">{{ t.category }}</el-tag>
        </div>
        <p class="tpl-desc">{{ t.description }}</p>
        <div class="tpl-foot">
          <span class="tpl-kind">{{ t.kind === 'pairs' ? '双标的' : '单标的' }}</span>
          <el-button size="small" type="primary" @click="openConfig(t)">配置并回测</el-button>
        </div>
      </el-card>
    </div>

    <!-- 配置弹窗 -->
    <el-dialog v-model="dialogVisible" :title="`配置策略：${currentTpl?.name || ''}`" width="520px" :close-on-click-modal="false">
      <el-form label-width="90px" label-position="left">
        <el-form-item v-for="p in currentTpl?.params || []" :key="p.key" :label="p.label">
          <el-input-number v-model="formParams[p.key]" :min="p.min" :max="p.max" :step="p.step || 1" style="width: 100%" />
        </el-form-item>
        <el-form-item label="标的">
          <div v-if="currentTpl?.kind === 'pairs'" class="sym-pair">
            <el-input v-model="formSymbols[0]" placeholder="如 sh600000 / 600000" />
            <span class="sym-sep">vs</span>
            <el-input v-model="formSymbols[1]" placeholder="如 sh600004 / 600004" />
          </div>
          <el-input v-else v-model="formSymbols[0]" placeholder="如 sh600000 / 600000" />
        </el-form-item>
        <el-form-item label="日期区间">
          <el-date-picker v-model="formDates" type="daterange" range-separator="至" start-placeholder="开始" end-placeholder="结束" value-format="YYYY-MM-DD" style="width: 100%" />
        </el-form-item>
        <el-form-item label="基准">
          <el-select v-model="formBenchmark" style="width: 100%">
            <el-option label="沪深300 (SH000300)" value="SH000300" />
            <el-option label="中证500 (SH000905)" value="SH000905" />
            <el-option label="上证指数 (SH000001)" value="SH000001" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="running" @click="runBacktest">{{ running ? '回测中...' : '运行回测' }}</el-button>
      </template>
    </el-dialog>

    <!-- 回测结果 -->
    <SectionCard v-if="result" class="mt-6">
      <div class="result-head">
        <h3 class="result-title">{{ result.name }} 回测结果</h3>
        <span class="result-meta">
          {{ (result.symbols || []).join(' / ') }} · 基准 {{ result.benchmark }} · {{ result.n_trades || 0 }} 笔交易
        </span>
      </div>
      <div class="metrics-grid">
        <div v-for="m in metricList" :key="m.label" class="metric-card">
          <div class="metric-label">{{ m.label }}</div>
          <div class="metric-value" :class="m.tone">{{ m.value }}</div>
        </div>
      </div>
      <v-chart v-if="hasChart" :option="chartOption" class="chart-body" autoresize />
      <el-empty v-else description="净值曲线为空（区间数据可能不足）" :image-size="60" />

      <!-- 交易明细 -->
      <div v-if="result?.trades?.length" class="trades-section">
        <div class="trades-head">
          <span class="trades-title">交易明细</span>
          <span class="trades-count">{{ result.trades.length }} 笔</span>
        </div>
        <el-table :data="result.trades" size="small" stripe max-height="320">
          <el-table-column label="日期" width="120">
            <template #default="{ row }">
              <span class="time">{{ (row.date || '').slice(0, 10) }}</span>
            </template>
          </el-table-column>
          <el-table-column label="方向" width="80" align="center">
            <template #default="{ row }">
              <el-tag :type="row.action === 'BUY' ? 'success' : 'danger'" size="small">
                {{ row.action === 'BUY' ? '买入' : '卖出' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="价格" align="right">
            <template #default="{ row }">{{ row.price != null ? Number(row.price).toFixed(3) : '--' }}</template>
          </el-table-column>
          <el-table-column label="数量" align="right">
            <template #default="{ row }">{{ row.quantity != null ? row.quantity : '--' }}</template>
          </el-table-column>
          <el-table-column label="金额" align="right">
            <template #default="{ row }">{{ row.total != null ? Number(row.total).toFixed(2) : '--' }}</template>
          </el-table-column>
          <el-table-column label="费用" align="right">
            <template #default="{ row }">{{ row.cost != null ? Number(row.cost).toFixed(4) : '--' }}</template>
          </el-table-column>
          <el-table-column v-if="hasTradeNote" label="说明" min-width="100" show-overflow-tooltip>
            <template #default="{ row }">{{ row.note || '--' }}</template>
          </el-table-column>
        </el-table>
      </div>
    </SectionCard>
  </PageContainer>
</template>

<script setup>
defineOptions({ name: 'QuantStrategyLibrary' })
import { ref, reactive, computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus/es/components/message/index'
import PageContainer from '@/components/common/PageContainer.vue'
import SectionCard from '@/components/common/SectionCard.vue'
import VChart from 'vue-echarts'
import { getStrategyTemplates, runStrategyLibraryBacktest } from '@/api/strategyLibrary'
import { chartTheme, withAlpha } from '@/utils/chartTheme'
import { useThemeRev } from '@/composables/useChartTheme'

const themeRev = useThemeRev()

const templates = ref([])
const dialogVisible = ref(false)
const currentTpl = ref(null)
const formParams = reactive({})
const formSymbols = reactive(['', ''])
const formDates = ref([])
const formBenchmark = ref('SH000300')
const running = ref(false)
const result = ref(null)

const catType = (c) => ({ 均值回归: 'success', 趋势: 'primary', 统计套利: 'warning' }[c] || 'info')

function openConfig(t) {
  currentTpl.value = t
  for (const k of Object.keys(formParams)) delete formParams[k]
  for (const p of t.params || []) formParams[p.key] = p.default
  formSymbols[0] = ''
  formSymbols[1] = ''
  formDates.value = []
  dialogVisible.value = true
}

async function runBacktest() {
  if (!currentTpl.value) return
  const kind = currentTpl.value.kind
  const syms = kind === 'pairs' ? [formSymbols[0], formSymbols[1]] : [formSymbols[0]]
  if (!syms[0] || (kind === 'pairs' && !syms[1])) {
    ElMessage.warning('请填写标的代码')
    return
  }
  let [start, end] = formDates.value || []
  if (!start || !end) {
    const now = new Date()
    end = now.toISOString().slice(0, 10)
    const s = new Date(now)
    s.setFullYear(now.getFullYear() - 1)
    start = s.toISOString().slice(0, 10)
  }
  running.value = true
  try {
    result.value = await runStrategyLibraryBacktest({
      template: currentTpl.value.key,
      params: { ...formParams },
      symbols: syms,
      start,
      end,
      benchmark: formBenchmark.value,
    })
    dialogVisible.value = false
  } catch (e) {
    if (e !== 'cancel') ElMessage.error('回测失败: ' + (e?.message || e))
  } finally {
    running.value = false
  }
}

function fmtPct(v, digits = 2) {
  if (v == null || v === '') return '--'
  return (v * 100).toFixed(digits) + '%'
}
function fmtNum(v, digits = 3) {
  if (v == null || v === '') return '--'
  return Number(v).toFixed(digits)
}

const metricList = computed(() => {
  const m = result.value?.metrics || {}
  const ar = m.annual_return
  const er = m.excess_return
  return [
    { label: '年化收益', value: fmtPct(ar), tone: ar > 0 ? 'tone-success' : ar < 0 ? 'tone-danger' : '' },
    { label: '年化波动', value: fmtPct(m.annual_volatility), tone: '' },
    { label: '夏普比率', value: fmtNum(m.sharpe), tone: '' },
    { label: '索提诺', value: fmtNum(m.sortino), tone: '' },
    { label: '最大回撤', value: fmtPct(m.max_drawdown), tone: 'tone-danger' },
    { label: '卡玛比率', value: fmtNum(m.calmar), tone: '' },
    { label: '胜率', value: fmtPct(m.win_rate, 1), tone: '' },
    { label: '超额收益', value: fmtPct(er), tone: er > 0 ? 'tone-success' : er < 0 ? 'tone-danger' : '' },
  ]
})

const hasChart = computed(() => {
  const c = result.value?.nav_curve
  return !!(c && c.dates && c.portfolio)
})

// 交易明细是否有"说明"列（配对交易含开/平仓说明）
const hasTradeNote = computed(() => (result.value?.trades || []).some(t => t.note))

const chartOption = computed(() => {
  void themeRev.value
  const c = result.value?.nav_curve || {}
  return {
    grid: { top: 20, right: 24, bottom: 30, left: 50 },
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'line', snap: true },
      backgroundColor: chartTheme.bgCard(),
      borderColor: chartTheme.border(),
      textStyle: { color: chartTheme.textPrimary() },
      formatter: (params) => {
        const lines = params.map(p => `${p.marker} ${p.seriesName}: <b>${Number(p.value).toFixed(3)}</b>`)
        return `${params[0].axisValue}<br/>${lines.join('<br/>')}`
      },
    },
    xAxis: {
      type: 'category',
      data: c.dates || [],
      boundaryGap: false,
      axisLine: { lineStyle: { color: chartTheme.border() } },
      axisTick: { show: false },
      axisLabel: { color: chartTheme.axisText(), fontSize: 11, hideOverlap: true },
    },
    yAxis: {
      type: 'value',
      scale: true,
      axisLine: { show: false },
      axisTick: { show: false },
      axisLabel: { color: chartTheme.axisText(), fontSize: 11, formatter: v => Number(v).toFixed(2) },
      splitLine: { lineStyle: { color: chartTheme.border(), type: 'dashed' } },
    },
    series: [
      {
        name: '策略净值',
        data: c.portfolio || [],
        type: 'line',
        smooth: true,
        showSymbol: false,
        connectNulls: true,
        emphasis: { disabled: true },
        lineStyle: { color: chartTheme.primary(), width: 2 },
        areaStyle: { color: withAlpha(chartTheme.primary(), 0.08) },
        itemStyle: { color: chartTheme.primary() },
      },
      {
        name: '基准净值',
        data: c.benchmark || [],
        type: 'line',
        smooth: true,
        showSymbol: false,
        connectNulls: true,
        emphasis: { disabled: true },
        lineStyle: { color: chartTheme.axisText(), width: 1.5, type: 'dashed' },
        itemStyle: { color: chartTheme.axisText() },
      },
    ],
  }
})

onMounted(async () => {
  try {
    const data = await getStrategyTemplates()
    templates.value = data?.items || []
  } catch (e) {
    if (e !== 'cancel') ElMessage.error('加载策略模板失败')
  }
})
</script>

<style scoped lang="scss">
.page-header { animation: fadeInUp 0.5s var(--ease-out-expo); }
.page-title { font-size: var(--font-size-2xl); font-weight: 700; color: var(--text-primary); }
.page-desc { font-size: var(--font-size-sm); color: var(--text-secondary); margin-top: 4px; }

.tpl-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 16px;
}
.tpl-card {
  :deep(.el-card__body) { display: flex; flex-direction: column; height: 100%; }
}
.tpl-head { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.tpl-name { font-size: 16px; font-weight: 600; color: var(--text-primary); }
.tpl-desc {
  font-size: 13px; color: var(--text-secondary); line-height: 1.6;
  margin: 10px 0 14px; min-height: 42px;
}
.tpl-foot { display: flex; align-items: center; justify-content: space-between; margin-top: auto; }
.tpl-kind { font-size: 12px; color: var(--text-tertiary); }

.sym-pair { display: flex; align-items: center; gap: 8px; width: 100%; }
.sym-sep { color: var(--text-tertiary); font-size: 12px; flex-shrink: 0; }

.result-head { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; flex-wrap: wrap; margin-bottom: 14px; }
.result-title { font-size: 15px; font-weight: 600; color: var(--text-primary); margin: 0; }
.result-meta { font-size: 12px; color: var(--text-tertiary); }

.metrics-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
  gap: 12px;
  margin-bottom: 16px;
}
.metric-card {
  padding: 12px 14px;
  background: var(--bg-tertiary, #f5f7fa);
  border-radius: 8px;
}
.metric-label { font-size: 12px; color: var(--text-tertiary); }
.metric-value { font-size: 18px; font-weight: 700; color: var(--text-primary); margin-top: 4px; font-variant-numeric: tabular-nums; }
.tone-success { color: var(--success); }
.tone-danger { color: var(--danger); }

.chart-body { height: 360px; }

.trades-section { margin-top: 18px; }
.trades-head { display: flex; align-items: baseline; justify-content: space-between; margin-bottom: 10px; }
.trades-title { font-size: 14px; font-weight: 600; color: var(--text-primary); }
.trades-count { font-size: 12px; color: var(--text-tertiary); }
.time { font-size: 12px; color: var(--text-secondary); font-variant-numeric: tabular-nums; }
.mt-6 { margin-top: 24px; }
.mb-6 { margin-bottom: 24px; }

@keyframes fadeInUp {
  from { opacity: 0; transform: translateY(8px); }
  to { opacity: 1; transform: translateY(0); }
}
</style>
