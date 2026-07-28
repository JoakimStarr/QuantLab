<template>
  <PageContainer>
    <div class="page-header mb-16">
      <h2 class="page-title">策略回测</h2>
      <div class="page-actions">
        <el-button @click="loadStrategies" :icon="Refresh">刷新</el-button>
        <el-button type="primary" @click="openCreate" :icon="Plus">新建策略</el-button>
      </div>
    </div>

    <SectionCard title="策略列表">
      <el-table :data="strategies" size="small" stripe empty-text="暂无策略" max-height="300">
        <el-table-column prop="id" label="ID" width="60" align="center" />
        <el-table-column prop="name" label="策略名称" width="180" />
        <el-table-column label="因子" min-width="160">
          <template #default="{row}">{{ row.factor_ids?.join(', ') || '--' }}</template>
        </el-table-column>
        <el-table-column prop="combination_method" label="组合方式" width="110" align="center" />
        <el-table-column label="topk/n_drop" width="100" align="center">
          <template #default="{row}">{{ row.topk }}/{{ row.n_drop }}</template>
        </el-table-column>
        <el-table-column prop="benchmark" label="基准" width="110" align="center" />
        <el-table-column label="操作" width="220" align="center" fixed="right">
          <template #default="{row}">
            <el-button size="small" link type="primary" @click="triggerBacktest(row.id)" :disabled="!qlibAvailable">回测</el-button>
            <el-button size="small" link type="success" @click="viewResults(row.id)">结果</el-button>
            <el-button size="small" link type="danger" @click="archive(row.id)">归档</el-button>
          </template>
        </el-table-column>
      </el-table>
    </SectionCard>

    <el-dialog v-model="showCreate" title="新建策略" width="560px">
      <el-form label-width="90px">
        <el-form-item label="策略名称">
          <el-input v-model="form.name" placeholder="如 多因子动量策略" />
        </el-form-item>
        <el-form-item label="选择因子">
          <el-select v-model="form.factor_ids" multiple filterable placeholder="选择因子" style="width:100%">
            <el-option v-for="f in factorOptions" :key="f.id"
              :label="`${f.name} (IC=${f.ic ?? '--'})`" :value="f.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="组合方式">
          <el-select v-model="form.combination_method" style="width:180px">
            <el-option label="等权" value="equal_weight" />
            <el-option label="IC加权" value="ic_weight" />
          </el-select>
        </el-form-item>
        <el-form-item label="topk">
          <el-input-number v-model="form.topk" :min="5" :max="300" />
        </el-form-item>
        <el-form-item label="n_drop">
          <el-input-number v-model="form.n_drop" :min="1" :max="50" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showCreate = false">取消</el-button>
        <el-button type="primary" @click="doCreate" :loading="creating">创建</el-button>
      </template>
    </el-dialog>

    <el-drawer v-model="showResults" title="回测结果" size="70%">
      <div v-if="currentResult" class="result-content">
        <div class="metrics-grid">
          <div class="metric-card" v-for="m in metricList" :key="m.label">
            <div class="metric-label">{{ m.label }}</div>
            <div class="metric-value">{{ m.value }}</div>
          </div>
        </div>
        <div ref="chartRef" class="nav-chart"></div>
        <el-table :data="resultsList" size="small" stripe max-height="240" class="mt-16">
          <el-table-column prop="start_date" label="开始" width="110" align="center" />
          <el-table-column prop="end_date" label="结束" width="110" align="center" />
          <el-table-column prop="sharpe" label="夏普" width="80" align="center" />
          <el-table-column prop="max_drawdown" label="最大回撤" width="100" align="center">
            <template #default="{row}">{{ row.max_drawdown != null ? (row.max_drawdown * 100).toFixed(2) + '%' : '--' }}</template>
          </el-table-column>
          <el-table-column prop="annual_return" label="年化" width="90" align="center">
            <template #default="{row}">{{ row.annual_return != null ? (row.annual_return * 100).toFixed(2) + '%' : '--' }}</template>
          </el-table-column>
          <el-table-column prop="calmar" label="卡玛" width="80" align="center" />
          <el-table-column prop="win_rate" label="胜率" width="80" align="center">
            <template #default="{row}">{{ row.win_rate != null ? (row.win_rate * 100).toFixed(1) + '%' : '--' }}</template>
          </el-table-column>
        </el-table>
      </div>
      <el-empty v-else description="暂无回测结果" />
    </el-drawer>
  </PageContainer>
</template>

<script setup>
import { ref, reactive, nextTick, onMounted, onBeforeUnmount } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Refresh } from '@element-plus/icons-vue'
import PageContainer from '@/components/common/PageContainer.vue'
import SectionCard from '@/components/common/SectionCard.vue'
import { listStrategies, createStrategy, archiveStrategy, runBacktest, listBacktestResults, getBacktestResult } from '@/api/strategy'
import { listFactors } from '@/api/factor'
import { getQlibStatus } from '@/api/quant'

const strategies = ref([])
const factorOptions = ref([])
const qlibAvailable = ref(false)
const showCreate = ref(false)
const creating = ref(false)
const showResults = ref(false)
const resultsList = ref([])
const currentResult = ref(null)
const chartRef = ref(null)
let chartInstance = null

const form = reactive({ name: '', factor_ids: [], combination_method: 'equal_weight', topk: 50, n_drop: 5 })

const metricList = ref([])

async function loadStrategies() {
  try {
    const data = await listStrategies()
    strategies.value = data?.items || []
  } catch {}
}

async function loadFactors() {
  try {
    const data = await listFactors({ status: 'active', limit: 200 })
    factorOptions.value = data?.items || []
  } catch {}
}

async function loadQlib() {
  try { const data = await getQlibStatus(); qlibAvailable.value = data?.available || false } catch {}
}

function openCreate() {
  form.name = ''; form.factor_ids = []; form.combination_method = 'equal_weight'; form.topk = 50; form.n_drop = 5
  showCreate.value = true
}

async function doCreate() {
  if (!form.name || !form.factor_ids.length) { ElMessage.warning('请填写名称并选择因子'); return }
  creating.value = true
  try {
    await createStrategy(form)
    ElMessage.success('策略已创建')
    showCreate.value = false
    loadStrategies()
  } catch {} finally { creating.value = false }
}

async function triggerBacktest(id) {
  try {
    await runBacktest(id)
    ElMessage.success('回测已提交（后台执行），稍后查看结果')
  } catch {}
}

async function viewResults(strategyId) {
  try {
    const data = await listBacktestResults(strategyId, { limit: 20 })
    resultsList.value = data?.items || []
    showResults.value = true
    if (resultsList.value.length) {
      await loadResultDetail(resultsList.value[0].id)
    } else {
      currentResult.value = null
    }
  } catch {}
}

async function loadResultDetail(resultId) {
  try {
    const data = await getBacktestResult(resultId)
    currentResult.value = data
    const m = data || {}
    metricList.value = [
      { label: '年化收益', value: m.annual_return != null ? (m.annual_return * 100).toFixed(2) + '%' : '--' },
      { label: '年化波动', value: m.annual_volatility != null ? (m.annual_volatility * 100).toFixed(2) + '%' : '--' },
      { label: '夏普比率', value: m.sharpe?.toFixed(3) ?? '--' },
      { label: '索提诺', value: m.sortino?.toFixed(3) ?? '--' },
      { label: '最大回撤', value: m.max_drawdown != null ? (m.max_drawdown * 100).toFixed(2) + '%' : '--' },
      { label: '卡玛比率', value: m.calmar?.toFixed(3) ?? '--' },
      { label: '胜率', value: m.win_rate != null ? (m.win_rate * 100).toFixed(1) + '%' : '--' },
      { label: '超额收益', value: m.excess_return != null ? (m.excess_return * 100).toFixed(2) + '%' : '--' },
    ]
    nextTick(() => initChart(m.nav_curve))
  } catch {}
}

function initChart(curve) {
  if (!curve || !chartRef.value) return
  import('echarts').then(echarts => {
    if (chartInstance) chartInstance.dispose()
    chartInstance = echarts.init(chartRef.value)
    const series = [{ name: '策略净值', type: 'line', data: curve.portfolio, smooth: true, lineStyle: { width: 2 }, itemStyle: { color: '#409EFF' } }]
    if (curve.benchmark) series.push({ name: '基准净值', type: 'line', data: curve.benchmark, smooth: true, lineStyle: { width: 1.5, type: 'dashed' }, itemStyle: { color: '#909399' } })
    chartInstance.setOption({
      tooltip: { trigger: 'axis' },
      legend: { data: ['策略净值', '基准净值'], bottom: 0 },
      grid: { left: '3%', right: '4%', bottom: '15%', containLabel: true },
      xAxis: { type: 'category', data: curve.dates, axisLabel: { rotate: 30, fontSize: 10 } },
      yAxis: { type: 'value' },
      series
    })
  })
}

async function archive(id) {
  try {
    await ElMessageBox.confirm('确定归档该策略？', '提示', { type: 'warning' })
    await archiveStrategy(id)
    ElMessage.success('已归档')
    loadStrategies()
  } catch {}
}

onMounted(() => { loadStrategies(); loadFactors(); loadQlib() })
onBeforeUnmount(() => { if (chartInstance) chartInstance.dispose() })
</script>

<style scoped lang="scss">
.page-header { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: var(--space-md); animation: fadeInUp 0.5s var(--ease-out-expo); }
.page-title { font-size: var(--font-size-2xl); font-weight: 700; }
.page-actions { display: flex; align-items: center; gap: var(--space-sm); }
.result-content { padding: 0 var(--space-md); }
.metrics-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: var(--space-sm); margin-bottom: var(--space-md); }
.metric-card { background: var(--bg-card, #f5f7fa); border-radius: var(--radius-sm); padding: var(--space-sm) var(--space-md); }
.metric-label { font-size: var(--font-size-xs); color: var(--text-secondary); }
.metric-value { font-size: var(--font-size-lg); font-weight: 700; margin-top: 4px; }
.nav-chart { width: 100%; height: 320px; }
.mt-16 { margin-top: var(--space-md); }
</style>
