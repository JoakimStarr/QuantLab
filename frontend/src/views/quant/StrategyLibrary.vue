<template>
  <PageContainer narrow>
    <PageHeader title="策略库" subtitle="内置规则/信号型策略模板，选模板配参数即可回测（每次回测自动保存历史）" />

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
    <el-dialog
      v-model="dialogVisible"
      :title="`配置策略：${currentTpl?.name || ''}`"
      width="520px"
      :close-on-click-modal="false"
    >
      <el-form label-width="90px" label-position="left">
        <el-form-item v-for="p in currentTpl?.params || []" :key="p.key" :label="p.label">
          <el-input-number
            v-model="formParams[p.key]"
            :min="p.min"
            :max="p.max"
            :step="p.step || 1"
            style="width: 100%"
          />
        </el-form-item>
        <el-form-item label="标的">
          <div v-if="currentTpl?.kind === 'pairs'" class="sym-pair">
            <SymbolSearchSelect v-model="formSymbols[0]" />
            <span class="sym-sep">vs</span>
            <SymbolSearchSelect v-model="formSymbols[1]" />
          </div>
          <SymbolSearchSelect v-else v-model="formSymbols[0]" />
        </el-form-item>
        <el-form-item label="日期区间">
          <el-date-picker
            v-model="formDates"
            type="daterange"
            range-separator="至"
            start-placeholder="开始"
            end-placeholder="结束"
            value-format="YYYY-MM-DD"
            style="width: 100%"
          />
        </el-form-item>
        <el-form-item label="基准">
          <el-select v-model="formBenchmark" style="width: 100%">
            <el-option label="沪深300 (SH000300)" value="SH000300" />
            <el-option label="中证500 (SH000905)" value="SH000905" />
            <el-option label="上证指数 (SH000001)" value="SH000001" />
          </el-select>
        </el-form-item>
        <el-form-item label="初始资金">
          <el-input-number
            v-model="formCapital"
            :min="10000"
            :max="1000000000"
            :step="100000"
            :precision="0"
            style="width: 100%"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="running" @click="runBacktest">{{
          running ? '回测中...' : '运行回测'
        }}</el-button>
      </template>
    </el-dialog>

    <!-- 回测历史（每次运行自动保存） -->
    <div class="history-section mt-6">
      <div class="history-head">
        <div class="history-head__left">
          <span class="history-title">回测历史</span>
          <span class="history-count">共 {{ historyTotal }} 条</span>
        </div>
        <el-button size="small" :loading="historyLoading" @click="loadHistory">刷新</el-button>
      </div>
      <el-table
        v-loading="historyLoading"
        :data="historyList"
        size="small"
        class="history-table"
        :row-key="(row) => row.history_id"
        highlight-current-row
        :current-row-key="historyDetail?.history_id ?? null"
        @row-click="viewHistory"
      >
        <el-table-column label="模板" min-width="120">
          <template #default="{ row }">
            <div class="cell-tpl" :title="`查看「${row.template_name}」回测结果`">
              <span class="cell-name cell-name--link">{{ row.template_name }}</span>
              <span v-if="row.category" class="cell-cat-text">{{ row.category }}</span>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="标的" min-width="110">
          <template #default="{ row }">
            <span class="cell-mono">{{ (row.symbols || []).join(' / ') || '--' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="区间" min-width="150">
          <template #default="{ row }">
            <span class="cell-mono cell-range">{{ row.start_date }} ~ {{ row.end_date }}</span>
          </template>
        </el-table-column>
        <el-table-column label="年化收益" width="86" align="right">
          <template #default="{ row }">
            <span :class="['cell-tnum', numClass(row.annual_return)]">{{ fmtPct(row.annual_return) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="夏普" width="70" align="right">
          <template #default="{ row }"><span class="cell-tnum">{{ fmtNum(row.sharpe) }}</span></template>
        </el-table-column>
        <el-table-column label="最大回撤" width="84" align="right">
          <template #default="{ row }"><span class="cell-tnum">{{ fmtPct(row.max_drawdown) }}</span></template>
        </el-table-column>
        <el-table-column label="交易笔数" width="74" align="right">
          <template #default="{ row }"><span class="cell-tnum">{{ row.n_trades ?? '--' }}</span></template>
        </el-table-column>
        <el-table-column label="回测时间" min-width="110">
          <template #default="{ row }"><span class="cell-time">{{ shortTime(row.created_at) }}</span></template>
        </el-table-column>
        <el-table-column label="操作" width="120" align="center">
          <template #default="{ row }">
            <div class="row-actions">
              <el-tooltip content="查看详情" :show-after="200">
                <el-button size="small" text type="primary" :icon="View" @click="viewHistory(row)" />
              </el-tooltip>
              <el-tooltip content="重跑" :show-after="200">
                <el-button size="small" text type="primary" :icon="RefreshRight" @click="rerunHistory(row)" />
              </el-tooltip>
              <el-tooltip content="删除" :show-after="200">
                <el-button size="small" text type="danger" :icon="Delete" @click="removeHistory(row)" />
              </el-tooltip>
            </div>
          </template>
        </el-table-column>
      </el-table>
      <el-empty
        v-if="!historyLoading && !historyList.length"
        description="暂无回测历史，配置上方模板运行一次即可生成"
        :image-size="56"
      />
    </div>

    <!-- 回测结果 / 历史详情（复用与策略回测同一展示组件，保证两处一致） -->
    <div v-if="displayedResult" class="mt-6">
      <div v-if="historyDetail" class="history-detail-head">
        <span class="history-detail-title">历史回测详情 #{{ historyDetail.history_id }}</span>
        <el-button size="small" @click="historyDetail = null">关闭</el-button>
      </div>
      <BacktestResultDetail
        :result="displayedResult"
        :strategy="displayedStrategy"
        :deletable="false"
      />
    </div>
  </PageContainer>
</template>

<script setup>
defineOptions({ name: 'QuantStrategyLibrary' })
import { ref, reactive, computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus/es/components/message/index'
import { ElMessageBox } from 'element-plus/es/components/message-box/index'
import PageContainer from '@/components/common/PageContainer.vue'
import SymbolSearchSelect from '@/components/common/SymbolSearchSelect.vue'
import BacktestResultDetail from '@/components/quant/BacktestResultDetail.vue'
import { fmtPct, fmtNum, numClass } from '@/utils/format'
import {
  getStrategyTemplates,
  runStrategyLibraryBacktest,
  getStrategyHistory,
  getStrategyHistoryDetail,
  deleteStrategyHistory,
} from '@/api/strategyLibrary'
import { getQuantDataStatus } from '@/api/quant'

const templates = ref([])
const dialogVisible = ref(false)
const currentTpl = ref(null)
const formParams = reactive({})
const formSymbols = reactive(['', ''])
const formDates = ref([])
const formBenchmark = ref('SH000300')
// 默认初始资金 1000 万，支持改动
const formCapital = ref(10_000_000)
const running = ref(false)
const result = ref(null)

// === 回测历史 ===
const historyList = ref([])
const historyTotal = ref(0)
const historyLoading = ref(false)
const historyDetail = ref(null)

// 展示区：优先历史详情，其次当前回测结果
const displayedResult = computed(() => historyDetail.value || result.value)
const displayedStrategy = computed(() => ({
  name: historyDetail.value?.template_name || result.value?.name || '',
}))

const catType = (c) => ({ 均值回归: 'success', 趋势: 'primary', 统计套利: 'warning' })[c] || 'info'

// 时间简写（到分钟）：2026-08-07 17:40
const shortTime = (ts) => (ts ? String(ts).replace('T', ' ').slice(0, 16) : '--')

// 默认回测区间 = 最近 2 年 → 最新数据日期（而非硬编码 2020 / 1 年）
const defaultFormDates = ref([])

async function refreshDefaultFormDates() {
  try {
    const status = await getQuantDataStatus()
    const dates = (status?.items || [])
      .map((it) => it.latest_date)
      .filter(Boolean)
      .sort()
    const end = dates.length ? dates[dates.length - 1] : new Date().toISOString().slice(0, 10)
    const start = new Date(end)
    start.setFullYear(start.getFullYear() - 2)
    defaultFormDates.value = [start.toISOString().slice(0, 10), end]
  } catch {
    const now = new Date()
    const start = new Date(now)
    start.setFullYear(now.getFullYear() - 2)
    defaultFormDates.value = [start.toISOString().slice(0, 10), now.toISOString().slice(0, 10)]
  }
}

function openConfig(t) {
  currentTpl.value = t
  for (const k of Object.keys(formParams)) delete formParams[k]
  for (const p of t.params || []) formParams[p.key] = p.default
  formSymbols[0] = ''
  formSymbols[1] = ''
  formCapital.value = 10_000_000
  // 默认最近 2 年（异步取最新数据日期，取不到则回退今天）；用户已手动选过则不覆盖
  refreshDefaultFormDates().then(() => {
    if (!formDates.value?.length) formDates.value = [...defaultFormDates.value]
  })
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
    // 默认最近 2 年（对话框打开时异步预填，这里兜底避免空区间）
    const now = new Date()
    end = now.toISOString().slice(0, 10)
    const s = new Date(now)
    s.setFullYear(now.getFullYear() - 2)
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
      initial_capital: formCapital.value,
    })
    dialogVisible.value = false
    historyDetail.value = null
    await loadHistory() // 自动保存后刷新历史列表
  } catch (e) {
    if (e !== 'cancel') ElMessage.error('回测失败: ' + (e?.message || e))
  } finally {
    running.value = false
  }
}

// === 回测历史 ===
async function loadHistory() {
  historyLoading.value = true
  try {
    const data = await getStrategyHistory({ limit: 50 })
    historyList.value = data?.items || []
    historyTotal.value = data?.total || 0
  } catch (e) {
    if (e !== 'cancel') ElMessage.error('加载回测历史失败')
  } finally {
    historyLoading.value = false
  }
}

async function viewHistory(row) {
  try {
    historyDetail.value = await getStrategyHistoryDetail(row.history_id)
  } catch (e) {
    if (e !== 'cancel') ElMessage.error('加载回测历史详情失败')
  }
}

// 用历史配置预填回测弹窗（用户可微调后再跑）
async function rerunHistory(row) {
  try {
    const detail = await getStrategyHistoryDetail(row.history_id)
    const tpl = templates.value.find((t) => t.key === detail.template)
    if (!tpl) {
      ElMessage.error('该模板已下架，无法重跑')
      return
    }
    currentTpl.value = tpl
    for (const k of Object.keys(formParams)) delete formParams[k]
    for (const p of tpl.params || []) formParams[p.key] = detail.params?.[p.key] ?? p.default
    formSymbols[0] = detail.symbols?.[0] || ''
    formSymbols[1] = detail.symbols?.[1] || ''
    formDates.value = [detail.start_date, detail.end_date]
    formBenchmark.value = detail.benchmark || 'SH000300'
    formCapital.value = detail.initial_capital || 10_000_000
    dialogVisible.value = true
  } catch (e) {
    if (e !== 'cancel') ElMessage.error('加载历史配置失败')
  }
}

async function removeHistory(row) {
  try {
    await ElMessageBox.confirm(`确认删除「${row.template_name}」回测历史？`, '删除历史', {
      type: 'warning',
      confirmButtonText: '删除',
      cancelButtonText: '取消',
    })
  } catch {
    return
  }
  try {
    await deleteStrategyHistory(row.history_id)
    ElMessage.success('已删除')
    if (historyDetail.value?.history_id === row.history_id) historyDetail.value = null
    await loadHistory()
  } catch (e) {
    if (e !== 'cancel') ElMessage.error('删除回测历史失败')
  }
}

onMounted(async () => {
  try {
    const data = await getStrategyTemplates()
    templates.value = data?.items || []
  } catch (e) {
    if (e !== 'cancel') ElMessage.error('加载策略模板失败')
  }
  await loadHistory()
})
</script>

<style scoped lang="scss">
.tpl-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 16px;
}
.tpl-card {
  :deep(.el-card__body) {
    display: flex;
    flex-direction: column;
    height: 100%;
  }
}
.tpl-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.tpl-name {
  font-size: 16px;
  font-weight: 600;
  color: var(--text-primary);
}
.tpl-desc {
  font-size: 13px;
  color: var(--text-secondary);
  line-height: 1.6;
  margin: 10px 0 14px;
  min-height: 42px;
}
.tpl-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: auto;
}
.tpl-kind {
  font-size: 12px;
  color: var(--text-tertiary);
}

.sym-pair {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
}
.sym-sep {
  color: var(--text-tertiary);
  font-size: 12px;
  flex-shrink: 0;
}

.mt-6 {
  margin-top: 24px;
}

// 回测历史区
.history-section {
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  padding: 14px 16px;
}
.history-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;

  &__left {
    display: flex;
    align-items: baseline;
    gap: 8px;
  }
}
.history-title {
  font-size: 15px;
  font-weight: 600;
  color: var(--text-primary);
}
.history-count {
  font-size: 12px;
  color: var(--text-tertiary);
}
.history-table {
  :deep(.el-table__header-wrapper th.el-table__cell) {
    font-weight: 600;
  }
}
.cell-tpl {
  display: flex;
  align-items: center;
  gap: 6px;
}
.cell-name {
  font-weight: 600;
  color: var(--text-primary);

  &--link {
    cursor: pointer;
    transition: color 0.15s;

    &:hover {
      color: var(--primary);
    }
  }
}
.cell-cat {
  flex-shrink: 0;
}
.cell-mono {
  font-family: var(--font-mono);
  color: var(--text-secondary);
  font-variant-numeric: tabular-nums;
}
.cell-tnum {
  font-variant-numeric: tabular-nums;
}
.cell-time {
  font-size: 12px;
  color: var(--text-tertiary);
  font-variant-numeric: tabular-nums;
}
.history-detail-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 12px;
  padding: 8px 12px;
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  background: var(--bg-secondary);

  &-title {
    font-size: 13px;
    font-weight: 600;
    color: var(--text-secondary);
  }
}

@keyframes fadeInUp {
  from {
    opacity: 0;
    transform: translateY(8px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}
</style>
