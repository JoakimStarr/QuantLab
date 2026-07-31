<template>
  <PageContainer>
    <!-- 页面头 -->
    <header class="page-header">
      <div class="page-header__text">
        <h1 class="page-header__title">策略回测</h1>
        <p class="page-header__subtitle">多因子策略构建与回测分析</p>
      </div>
      <div class="page-header__actions">
        <el-button :icon="Refresh" @click="loadStrategies">刷新</el-button>
        <el-button type="primary" :disabled="selectedResults.length < 2" :loading="comparing" @click="compareResults">对比选中策略 ({{ selectedResults.length }})</el-button>
        <el-button type="primary" :icon="Plus" @click="openCreate">新建策略</el-button>
      </div>
    </header>

    <!-- 策略列表表格 -->
    <div class="table-card" v-loading="listLoading">
      <el-table
        v-if="strategies.length"
        :data="strategies"
        :row-class-name="rowClassName"
        @row-click="onRowClick"
        @selection-change="handleResultSelectionChange"
        size="default"
      >
        <el-table-column type="selection" width="48" />
        <el-table-column prop="id" label="ID" width="60" align="center">
          <template #default="{ row }"><span class="cell-mono">{{ row.id }}</span></template>
        </el-table-column>
        <el-table-column prop="name" label="策略名称" min-width="160">
          <template #default="{ row }"><span class="cell-name">{{ row.name }}</span></template>
        </el-table-column>
        <el-table-column label="因子" min-width="200">
          <template #default="{ row }">
            <span class="cell-factors" :title="row.factor_ids?.join(', ')">
              {{ row.factor_ids?.join(', ') || '--' }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="组合方式" width="110" align="center">
          <template #default="{ row }">
            <span v-if="row.combination_method === 'ic_weight'" class="pill pill--primary">IC加权</span>
            <span v-else-if="row.combination_method === 'ir_weight'" class="pill pill--primary">IR加权</span>
            <span v-else class="pill pill--muted">等权</span>
          </template>
        </el-table-column>
        <el-table-column label="topk/n_drop" width="110" align="center">
          <template #default="{ row }">
            <span class="cell-mono cell-tnum">{{ row.topk }}/{{ row.n_drop }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="benchmark" label="基准" width="120" align="center">
          <template #default="{ row }"><span class="cell-mono cell-sm">{{ row.benchmark || '--' }}</span></template>
        </el-table-column>
        <el-table-column label="回测状态" width="120" align="center">
          <template #default="{row}">
            <span class="status-badge" :class="getBacktestStatusClass(row.id)">{{ getBacktestStatusText(row.id) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="创建时间" width="180" align="center">
          <template #default="{row}">
            <span class="time">{{ formatTime(row.created_at) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="180" align="center" fixed="right">
          <template #default="{ row }">
            <div class="row-actions">
              <a class="link link--primary" @click.stop="triggerBacktest(row)">回测</a>
              <a class="link link--success" @click.stop="viewResults(row)">结果</a>
              <a class="link link--warning" @click.stop="openWalkForward(row)">Walk-forward</a>
              <a class="link link--danger" @click.stop="archive(row)">归档</a>
            </div>
          </template>
        </el-table-column>
      </el-table>
      <el-empty v-else description="暂无策略" />
    </div>

    <!-- 回测指标 + 净值曲线（选中策略后显示） -->
    <template v-if="selectedStrategy">
      <!-- 指标卡组 -->
      <section ref="metricsRef" class="metrics-section">
        <div class="metrics-header">
          <h2 class="metrics-title">最新回测结果 — {{ selectedStrategy.name }}</h2>
          <span v-if="currentResult" class="metrics-range">
            {{ currentResult.start_date }} ~ {{ currentResult.end_date }}
          </span>
        </div>
        <div class="metrics-grid" v-loading="resultLoading">
          <div class="metric-card" v-for="m in metricList" :key="m.label">
            <div class="metric-label">{{ m.label }}</div>
            <div class="metric-value" :class="m.tone">{{ m.value }}</div>
          </div>
        </div>
      </section>

      <!-- 净值曲线 -->
      <section class="chart-card">
        <div class="chart-header">
          <h2 class="chart-title">净值曲线</h2>
          <div class="chart-legend">
            <span class="legend-item">
              <span class="legend-line legend-line--solid"></span>策略净值
            </span>
            <span class="legend-item">
              <span class="legend-line legend-line--dashed"></span>基准净值
            </span>
          </div>
        </div>
        <v-chart v-if="hasChart" :option="chartOption" class="chart-body" autoresize />
        <el-empty v-else description="暂无净值数据" :image-size="64" />
      </section>
    </template>

    <!-- 新建策略对话框 -->
    <el-dialog v-model="showCreate" title="新建策略" width="560px">
      <el-form label-width="96px" :model="form">
        <el-form-item label="策略名称">
          <el-input v-model="form.name" placeholder="如 多因子动量策略" />
        </el-form-item>
        <el-form-item label="选择因子">
          <el-select v-model="form.factor_ids" multiple filterable placeholder="选择因子" style="width:100%">
            <el-option
              v-for="f in factorOptions"
              :key="f.id"
              :label="`${f.name} (IC=${f.ic ?? '--'})`"
              :value="f.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="组合方式">
          <el-select v-model="form.combination_method" style="width:180px">
            <el-option label="等权" value="equal_weight" />
            <el-option label="IC加权" value="ic_weight" />
            <el-option label="IR加权" value="ir_weight" />
          </el-select>
        </el-form-item>
        <el-form-item label="因子正交化">
          <el-switch v-model="form.orthogonalize" :active-value="1" :inactive-value="0" />
          <span style="margin-left:12px;color:var(--text-tertiary);font-size:var(--font-size-sm)">启用后按 IC 排序做 Gram-Schmidt 截面正交化，降低共线性</span>
        </el-form-item>
        <el-form-item label="topk">
          <el-input-number v-model="form.topk" :min="5" :max="300" />
        </el-form-item>
        <el-form-item label="n_drop">
          <el-input-number v-model="form.n_drop" :min="1" :max="50" />
        </el-form-item>
        <el-form-item label="调仓频率">
          <el-select v-model="form.rebalance_freq" style="width:180px">
            <el-option label="每日" value="day" />
            <el-option label="每周" value="week" />
            <el-option label="每月" value="month" />
          </el-select>
        </el-form-item>
        <el-form-item label="基准">
          <el-select v-model="form.benchmark" filterable allow-create placeholder="选择基准" style="width:240px">
            <el-option label="沪深300 (SH000300)" value="SH000300" />
            <el-option label="中证500 (SH000905)" value="SH000905" />
            <el-option label="中证1000 (SH000852)" value="SH000852" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showCreate = false">取消</el-button>
        <el-button type="primary" :loading="creating" @click="doCreate">创建</el-button>
      </template>
    </el-dialog>

    <!-- Walk-forward 滚动回测对话框（添加14） -->
    <el-dialog v-model="wfDialog.visible" title="Walk-forward 滚动回测" width="760px" :close-on-click-modal="false">
      <el-form label-position="top" v-if="wfDialog.result?.status !== 'done'">
        <div style="display:flex; gap:12px; flex-wrap:wrap;">
          <el-form-item label="训练窗口" style="flex:1; min-width:180px;">
            <el-input v-model="wfDialog.form.trainWindow" placeholder="如 730D" />
          </el-form-item>
          <el-form-item label="测试窗口" style="flex:1; min-width:180px;">
            <el-input v-model="wfDialog.form.testWindow" placeholder="如 180D" />
          </el-form-item>
          <el-form-item label="滚动步长" style="flex:1; min-width:180px;">
            <el-input v-model="wfDialog.form.step" placeholder="如 180D" />
          </el-form-item>
        </div>
        <div style="display:flex; gap:12px; flex-wrap:wrap;">
          <el-form-item label="每期剔除数" style="flex:1; min-width:180px;">
            <el-input-number v-model="wfDialog.form.nDrop" :min="0" :max="20" controls-position="right" />
          </el-form-item>
          <el-form-item label="调仓频率" style="flex:1; min-width:180px;">
            <el-select v-model="wfDialog.form.rebalance" style="width:100%;">
              <el-option label="每日" value="day" />
              <el-option label="每周" value="week" />
              <el-option label="每月" value="month" />
            </el-select>
          </el-form-item>
        </div>
      </el-form>
      <div class="wf-result" v-if="wfDialog.result">
        <el-alert v-if="wfDialog.result.status === 'running'" type="info" :closable="false" title="回测进行中，请稍候..." />
        <el-alert v-else-if="wfDialog.result.status === 'failed'" type="error" :closable="false" :title="String(wfDialog.result.error || '回测失败')" />
        <template v-else-if="wfDialog.result.status === 'done' && wfDialog.result.result">
          <h4 class="wf-section-title">样本外整体指标</h4>
          <div class="wf-metrics" v-if="wfDialog.result.result.oos_metrics">
            <div class="wf-metric-card" v-for="(val, key) in wfDialog.result.result.oos_metrics" :key="key">
              <div class="wf-metric-label">{{ wfLabel(key) }}</div>
              <div class="wf-metric-value">{{ wfFmt(key, val) }}</div>
            </div>
          </div>
          <h4 class="wf-section-title" v-if="wfDialog.result.result.consistency">跨窗一致性</h4>
          <div class="wf-metrics" v-if="wfDialog.result.result.consistency">
            <div class="wf-metric-card" v-for="(val, key) in wfDialog.result.result.consistency" :key="key">
              <div class="wf-metric-label">{{ wfLabel(key) }}</div>
              <div class="wf-metric-value">{{ wfFmt(key, val) }}</div>
            </div>
          </div>
          <h4 class="wf-section-title">各窗口明细 ({{ wfDialog.result.result.n_windows || 0 }} 窗)</h4>
          <el-table :data="wfDialog.result.result.windows || []" size="small" max-height="320">
            <el-table-column prop="window_idx" label="#" width="50" />
            <el-table-column label="测试期" min-width="170">
              <template #default="{ row }">{{ row.test_start }} ~ {{ row.test_end }}</template>
            </el-table-column>
            <el-table-column prop="best_topk" label="topk" width="70" />
            <el-table-column label="训练夏普" width="90">
              <template #default="{ row }">{{ wfFmt('sharpe', row.train_sharpe) }}</template>
            </el-table-column>
            <el-table-column label="测试夏普" width="90">
              <template #default="{ row }">{{ wfFmt('sharpe', row.test_sharpe) }}</template>
            </el-table-column>
            <el-table-column label="年化" width="90">
              <template #default="{ row }">{{ wfFmt('annual_return', row.test_annual_return) }}</template>
            </el-table-column>
            <el-table-column label="最大回撤" width="100">
              <template #default="{ row }">{{ wfFmt('max_drawdown', row.test_max_dd) }}</template>
            </el-table-column>
          </el-table>
        </template>
      </div>
      <template #footer>
        <el-button @click="closeWalkForward">关闭</el-button>
        <el-button v-if="wfDialog.result?.status !== 'done'" type="primary" :loading="wfDialog.submitting" @click="submitWalkForward">开始回测</el-button>
      </template>
    </el-dialog>
  </PageContainer>
</template>

<script setup>
defineOptions({ name: 'QuantStrategy' })
import { ref, reactive, computed, nextTick, onMounted, onBeforeUnmount } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Plus, Refresh } from '@element-plus/icons-vue'
import VChart from 'vue-echarts'
import PageContainer from '@/components/common/PageContainer.vue'
import {
  listStrategies, createStrategy, runBacktest,
  listBacktestResults, getBacktestResult,
  getAllBacktestStatuses,
  runWalkForward, getWalkForwardResults
} from '@/api/strategy'
import { useFactorStore } from '@/stores/factor'

const router = useRouter()
const factorStore = useFactorStore()

// === 策略列表与选中 ===
const strategies = ref([])
const selectedStrategy = ref(null)
const listLoading = ref(false)

// === 回测状态 ===
const backtestStatuses = ref({})

// === 选中的策略（用于对比回测结果） ===
const selectedResults = ref([])
const comparing = ref(false)
function handleResultSelectionChange(val) {
  selectedResults.value = val
}
async function compareResults() {
  if (selectedResults.value.length < 2) {
    ElMessage.warning('请至少选择 2 个策略')
    return
  }
  comparing.value = true
  try {
    // 获取每个选中策略的最新回测结果 ID
    const promises = selectedResults.value.map(s =>
      listBacktestResults(s.id, { limit: 1 }).then(data => data?.items?.[0]?.id).catch(() => null)
    )
    const ids = (await Promise.all(promises)).filter(id => id != null)
    if (ids.length < 2) {
      ElMessage.warning('选中策略的有效回测结果不足 2 个，无法对比')
      return
    }
    router.push(`/quant/backtest-compare?ids=${ids.join(',')}`)
  } catch (e) {
    ElMessage.error('获取回测结果失败')
  } finally {
    comparing.value = false
  }
}

// === 回测结果 ===
const currentResult = ref(null)
const resultLoading = ref(false)
const metricsRef = ref(null)

// === 新建策略对话框 ===
const showCreate = ref(false)
const creating = ref(false)
const factorOptions = ref([])
const form = reactive({
  name: '',
  factor_ids: [],
  combination_method: 'equal_weight',
  topk: 50,
  n_drop: 5,
  rebalance_freq: 'week',
  benchmark: 'SH000300',
  orthogonalize: 0
})

// === 轮询控制 ===
let pollTimer = null
let statusPollTimer = null

// === 时间格式化 ===
function formatTime(ts) {
  if (!ts) return '--'
  return ts.replace('T', ' ').slice(0, 19)
}

// === 数值格式化 ===
function fmtPct(v, digits = 2) {
  if (v == null || v === '') return '--'
  return (v * 100).toFixed(digits) + '%'
}
function fmtNum(v, digits = 3) {
  if (v == null || v === '') return '--'
  return Number(v).toFixed(digits)
}

// === 指标卡（8张，含语义色） ===
const metricList = computed(() => {
  const m = currentResult.value || {}
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
    { label: '超额收益', value: fmtPct(er), tone: er > 0 ? 'tone-success' : er < 0 ? 'tone-danger' : '' }
  ]
})

// === 净值曲线数据 ===
const hasChart = computed(() => {
  const c = currentResult.value?.nav_curve
  return !!(c && c.dates && c.portfolio)
})

const chartOption = computed(() => {
  const c = currentResult.value?.nav_curve || {}
  return {
    grid: { top: 20, right: 24, bottom: 30, left: 50 },
    tooltip: {
      trigger: 'axis',
      backgroundColor: 'var(--bg-card)',
      borderColor: 'var(--border)',
      textStyle: { color: 'var(--text-primary)' },
      formatter: (params) => {
        const lines = params.map(p => `${p.marker} ${p.seriesName}: <b>${Number(p.value).toFixed(3)}</b>`)
        return `${params[0].axisValue}<br/>${lines.join('<br/>')}`
      }
    },
    xAxis: {
      type: 'category',
      data: c.dates || [],
      boundaryGap: false,
      axisLine: { lineStyle: { color: 'var(--border)' } },
      axisTick: { show: false },
      axisLabel: { color: 'var(--text-tertiary)', fontSize: 11, hideOverlap: true }
    },
    yAxis: {
      type: 'value',
      scale: true,
      axisLine: { show: false },
      axisTick: { show: false },
      axisLabel: {
        color: 'var(--text-tertiary)',
        fontSize: 11,
        formatter: v => Number(v).toFixed(1)
      },
      splitLine: { lineStyle: { color: 'var(--border)', type: 'dashed' } }
    },
    series: [
      {
        name: '策略净值',
        data: c.portfolio || [],
        type: 'line',
        smooth: true,
        showSymbol: false,
        lineStyle: { color: 'var(--primary)', width: 2 },
        areaStyle: { color: 'rgba(31, 75, 160, 0.08)' },
        itemStyle: { color: 'var(--primary)' }
      },
      {
        name: '基准净值',
        data: c.benchmark || [],
        type: 'line',
        smooth: true,
        showSymbol: false,
        lineStyle: { color: 'var(--text-tertiary)', width: 1.5, type: 'dashed' },
        itemStyle: { color: 'var(--text-tertiary)' }
      }
    ]
  }
})

// === 选中行样式 ===
function rowClassName({ row }) {
  return selectedStrategy.value?.id === row.id ? 'is-selected' : ''
}

// === 回测状态显示 ===
function getBacktestStatusClass(strategyId) {
  const s = backtestStatuses.value[strategyId]?.status || 'idle'
  return 'status-badge--' + s
}

function getBacktestStatusText(strategyId) {
  const s = backtestStatuses.value[strategyId]?.status || 'idle'
  const map = { idle: '空闲', running: '运行中', completed: '已完成', failed: '失败' }
  return map[s] || s
}

// === 加载回测状态 ===
async function loadBacktestStatuses() {
  try {
    const data = await getAllBacktestStatuses()
    backtestStatuses.value = data?.items || {}
  } catch (e) {
    // 静默失败，不阻塞主流程
  }
}

function hasRunningStatus() {
  return Object.values(backtestStatuses.value).some(s => s?.status === 'running')
}

// === 状态轮询（每 3s 刷新，无 running 时自动停止） ===
function startStatusPolling() {
  stopStatusPolling()
  statusPollTimer = setInterval(async () => {
    await loadBacktestStatuses()
    if (!hasRunningStatus()) {
      stopStatusPolling()
    }
  }, 3000)
}

function stopStatusPolling() {
  if (statusPollTimer) {
    clearInterval(statusPollTimer)
    statusPollTimer = null
  }
}

// === 加载策略列表 ===
async function loadStrategies() {
  listLoading.value = true
  try {
    const data = await listStrategies()
    strategies.value = data?.items || []
    await loadBacktestStatuses()
    // 如果有运行中的回测，启动状态轮询
    if (hasRunningStatus()) {
      startStatusPolling()
    }
  } catch (e) {
    if (e !== 'cancel') ElMessage.error('加载策略列表失败')
  } finally {
    listLoading.value = false
  }
}

// === 加载因子选项（供新建对话框选择） ===
async function loadFactors() {
  try {
    await factorStore.fetchList()
    factorOptions.value = factorStore.activeFactors
  } catch (e) {
    // 静默失败，不阻塞主流程
  }
}

// === 点击行选中策略并加载回测结果 ===
function onRowClick(row) {
  if (!row || selectedStrategy.value?.id === row.id) return
  selectStrategy(row)
}

async function selectStrategy(row, scroll = false) {
  selectedStrategy.value = row
  currentResult.value = null
  resultLoading.value = true
  try {
    const data = await listBacktestResults(row.id, { limit: 1 })
    const items = data?.items || []
    if (items.length && items[0].id != null) {
      currentResult.value = await getBacktestResult(items[0].id)
    }
  } catch (e) {
    if (e !== 'cancel') ElMessage.error('加载回测结果失败')
  } finally {
    resultLoading.value = false
    if (scroll) {
      nextTick(() => metricsRef.value?.scrollIntoView({ behavior: 'smooth', block: 'start' }))
    }
  }
}

// === 触发回测 + 轮询结果 ===
async function triggerBacktest(row) {
  selectedStrategy.value = row
  // 记录回测前的最新结果 id，用于判断新结果是否产生
  let prevId = null
  try {
    const data = await listBacktestResults(row.id, { limit: 1 })
    const items = data?.items || []
    prevId = items.length ? items[0].id : null
  } catch (e) { /* ignore */ }

  try {
    const today = new Date().toISOString().slice(0, 10)
    await runBacktest(row.id, { start_date: '2020-01-01', end_date: today })
    ElMessage.success('回测已启动')
    // 立即刷新状态并启动轮询
    await loadBacktestStatuses()
    startStatusPolling()
    startPolling(row, prevId)
  } catch (e) {
    if (e !== 'cancel') ElMessage.error('回测启动失败')
  }
}

// === 轮询回测结果（每 3s 检查一次，最多 40 次） ===
function startPolling(row, prevId) {
  stopPolling()
  let attempts = 0
  const maxAttempts = 40
  resultLoading.value = true
  pollTimer = setInterval(async () => {
    attempts++
    try {
      const data = await listBacktestResults(row.id, { limit: 1 })
      const latest = data?.items?.[0]
      // 出现新的已完成结果（id 变化且指标已填充）
      if (latest && latest.id !== prevId && latest.annual_return != null) {
        currentResult.value = await getBacktestResult(latest.id)
        ElMessage.success('回测完成')
        stopPolling()
      } else if (attempts >= maxAttempts) {
        ElMessage.warning('回测仍在进行中，请稍后点击"结果"查看')
        stopPolling()
      }
    } catch (e) {
      if (attempts >= maxAttempts) stopPolling()
    }
  }, 3000)
}

function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
  resultLoading.value = false
}

// === "结果"链接：选中策略并滚动到指标区 ===
function viewResults(row) {
  selectStrategy(row, true)
}

// === "归档"链接：仅提示 ===
function archive(row) {
  ElMessage.info(`归档策略「${row.name}」(id=${row.id})`)
}

// === 新建策略对话框 ===
function openCreate() {
  Object.assign(form, {
    name: '',
    factor_ids: [],
    combination_method: 'equal_weight',
    topk: 50,
    n_drop: 5,
    rebalance_freq: 'week',
    benchmark: 'SH000300',
    orthogonalize: 0
  })
  showCreate.value = true
}

// === Walk-forward 滚动回测（添加14） ===
const wfDialog = reactive({
  visible: false,
  submitting: false,
  strategyId: null,
  result: null,
  form: { trainWindow: '730D', testWindow: '180D', step: '180D', nDrop: 5, rebalance: 'day' },
})
let wfTimer = null

function openWalkForward(row) {
  wfDialog.strategyId = row.id
  wfDialog.visible = true
  wfDialog.submitting = false
  wfDialog.result = null
  Object.assign(wfDialog.form, { trainWindow: '730D', testWindow: '180D', step: '180D', nDrop: 5, rebalance: 'day' })
}

function closeWalkForward() {
  wfDialog.visible = false
  stopWfPolling()
}

async function submitWalkForward() {
  if (!wfDialog.strategyId) return
  wfDialog.submitting = true
  try {
    await runWalkForward(wfDialog.strategyId, {
      train_window: wfDialog.form.trainWindow,
      test_window: wfDialog.form.testWindow,
      step: wfDialog.form.step,
      n_drop: wfDialog.form.nDrop,
      rebalance: wfDialog.form.rebalance,
    })
    wfDialog.result = { status: 'running' }
    ElMessage.success('Walk-forward 回测已启动')
    startWfPolling()
  } catch (e) {
    ElMessage.error('Walk-forward 启动失败')
  } finally {
    wfDialog.submitting = false
  }
}

function startWfPolling() {
  if (wfTimer) clearInterval(wfTimer)
  let attempts = 0
  wfTimer = setInterval(async () => {
    attempts++
    if (attempts > 120) { stopWfPolling(); return }
    try {
      const data = await getWalkForwardResults(wfDialog.strategyId)
      if (data && data.status && data.status !== 'running') {
        wfDialog.result = data
        stopWfPolling()
      } else if (data) {
        wfDialog.result = data
      }
    } catch (e) { /* ignore */ }
  }, 3000)
}

function stopWfPolling() {
  if (wfTimer) { clearInterval(wfTimer); wfTimer = null }
}

const _WF_LABELS = {
  total_return: '总收益', annual_return: '年化收益', annual_volatility: '年化波动',
  sharpe: '夏普', max_drawdown: '最大回撤', n_days: '天数',
  sharpe_mean: '夏普均值', sharpe_std: '夏普标准差', sharpe_min: '夏普最小',
  sharpe_max: '夏普最大', positive_ratio: '正收益占比',
}
function wfLabel(k) { return _WF_LABELS[k] || k }
function wfFmt(k, v) {
  if (v == null || v === '') return '--'
  const n = Number(v)
  if (Number.isNaN(n)) return '--'
  if (k === 'n_days') return String(Math.round(n))
  if (['total_return', 'annual_return', 'annual_volatility', 'max_drawdown', 'positive_ratio'].includes(k)) {
    return (n * 100).toFixed(2) + '%'
  }
  return n.toFixed(4)
}

async function doCreate() {
  if (!form.name || !form.factor_ids.length) {
    ElMessage.warning('请填写名称并选择因子')
    return
  }
  creating.value = true
  try {
    await createStrategy(form)
    ElMessage.success('策略已创建')
    showCreate.value = false
    loadStrategies()
  } catch (e) {
    if (e !== 'cancel') ElMessage.error('创建策略失败')
  } finally {
    creating.value = false
  }
}

onMounted(() => {
  loadStrategies()
  loadFactors()
})

onBeforeUnmount(() => {
  stopPolling()
  stopStatusPolling()
  stopWfPolling()
})
</script>

<style scoped lang="scss">
// 页面头
.page-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: var(--space-md);
  margin-bottom: var(--space-lg);
  animation: fadeInUp 0.5s var(--ease-out-expo) both;

  &__title {
    font-size: var(--font-size-2xl);
    font-weight: var(--font-weight-semibold);
    color: var(--text-primary);
    margin: 0;
    line-height: var(--line-height-tight);
  }

  &__subtitle {
    font-size: var(--font-size-lg);
    color: var(--text-tertiary);
    margin: 4px 0 0;
  }

  &__actions {
    display: flex;
    align-items: center;
    gap: var(--space-sm);
  }
}

// 策略列表表格卡片
.table-card {
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  overflow: hidden;
  margin-bottom: var(--space-lg);

  :deep(.el-table) {
    --el-table-border-color: var(--border-light);
    --el-table-header-bg-color: var(--bg-tertiary);
    --el-table-tr-bg-color: transparent;
    --el-table-row-hover-bg-color: var(--bg-hover);
    font-size: var(--font-size-base);

    th.el-table__cell {
      background: var(--bg-tertiary);
      font-size: var(--font-size-sm);
      color: var(--text-tertiary);
      font-weight: var(--font-weight-medium);
    }

    .el-table__row {
      cursor: pointer;
    }

    // 选中行高亮
    .el-table__row.is-selected td.el-table__cell {
      background: rgba(var(--primary-rgb), 0.05) !important;
    }
  }
}

// 单元格样式
.cell-mono {
  font-family: var(--font-mono);
  color: var(--text-tertiary);
}
.cell-name {
  font-weight: var(--font-weight-medium);
  color: var(--text-primary);
}
.cell-factors {
  display: inline-block;
  max-width: 200px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-family: var(--font-mono);
  font-size: var(--font-size-sm);
  color: var(--text-tertiary);
  vertical-align: middle;
}
.cell-tnum {
  font-variant-numeric: tabular-nums;
}
.cell-sm {
  font-size: var(--font-size-sm);
}

// 组合方式 pill 徽标
.pill {
  display: inline-block;
  font-size: var(--font-size-sm);
  padding: 2px 10px;
  border-radius: var(--radius-full);
  line-height: 1.5;
}
.pill--muted {
  background: var(--bg-tertiary);
  color: var(--text-tertiary);
}
.pill--primary {
  background: rgba(var(--primary-rgb), 0.1);
  color: var(--primary);
}

// 回测状态徽标
.status-badge {
  display: inline-block;
  font-size: var(--font-size-sm);
  padding: 2px 10px;
  border-radius: var(--radius-full);
  line-height: 1.5;
}
.status-badge--idle {
  background: var(--bg-tertiary);
  color: var(--text-tertiary);
}
.status-badge--running {
  background: rgba(var(--primary-rgb), 0.1);
  color: var(--primary);
}
.status-badge--completed {
  background: rgba(0, 128, 0, 0.1);
  color: var(--success);
}
.status-badge--failed {
  background: rgba(255, 0, 0, 0.1);
  color: var(--danger);
}

// 操作链接
.row-actions {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: var(--space-md);
}
.link {
  cursor: pointer;
  font-size: var(--font-size-base);
  user-select: none;

  &:hover {
    opacity: 0.8;
  }
}
.link--primary { color: var(--primary); }
.link--success { color: var(--success); }
.link--danger { color: var(--danger); }

// 回测指标区
.metrics-section {
  margin-bottom: var(--space-lg);
  animation: fadeInUp 0.5s var(--ease-out-expo) both;
}
.metrics-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: var(--space-md);
  gap: var(--space-md);
}
.metrics-title {
  font-size: var(--font-size-xl);
  font-weight: var(--font-weight-semibold);
  color: var(--text-primary);
  margin: 0;
}
.metrics-range {
  font-family: var(--font-mono);
  font-size: var(--font-size-sm);
  color: var(--text-tertiary);
}
.metrics-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: var(--space-md);
  min-height: 80px;
}
.metric-card {
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  padding: var(--space-md);
}
.metric-label {
  font-size: var(--font-size-sm);
  color: var(--text-tertiary);
  margin-bottom: var(--space-xs);
}
.metric-value {
  font-size: var(--font-size-2xl);
  font-weight: var(--font-weight-semibold);
  font-variant-numeric: tabular-nums;
  color: var(--text-primary);
}
.tone-success { color: var(--success); }
.tone-danger { color: var(--danger); }

// 净值曲线卡
.chart-card {
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  padding: var(--space-lg);
  margin-bottom: var(--space-lg);
  animation: fadeInUp 0.5s var(--ease-out-expo) both;
}
.chart-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: var(--space-md);
}
.chart-title {
  font-size: var(--font-size-xl);
  font-weight: var(--font-weight-semibold);
  color: var(--text-primary);
  margin: 0;
}
.chart-legend {
  display: flex;
  align-items: center;
  gap: var(--space-lg);
  font-size: var(--font-size-base);
  color: var(--text-tertiary);
}
.legend-item {
  display: inline-flex;
  align-items: center;
  gap: var(--space-xs);
}
.legend-line {
  display: inline-block;
  width: 16px;
  height: 2px;
}
.legend-line--solid {
  background: var(--primary);
}
.legend-line--dashed {
  border-top: 1.5px dashed var(--text-tertiary);
  height: 0;
}
.chart-body {
  width: 100%;
  height: 320px;
}

/* Walk-forward 样式 */
.link--warning { color: var(--warning, #c8801c); }
.wf-result { margin-top: 8px; }
.wf-section-title {
  font-size: 14px; font-weight: 600; color: var(--text-primary, #1f2329);
  margin: 16px 0 8px;
}
.wf-metrics {
  display: grid; grid-template-columns: repeat(auto-fill, minmax(120px, 1fr));
  gap: 8px;
}
.wf-metric-card {
  background: var(--bg-tertiary, #f5f6f7); border-radius: 6px;
  padding: 8px 12px;
}
.wf-metric-label { font-size: 12px; color: var(--text-tertiary, #8a9099); }
.wf-metric-value { font-size: 16px; font-weight: 600; margin-top: 2px; font-variant-numeric: tabular-nums; }
</style>
