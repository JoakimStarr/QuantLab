<template>
  <PageContainer>
    <div class="mining-page">
      <!-- 页面头 -->
      <header class="page-header">
        <div class="page-header__lead">
          <h1 class="page-header__title">AI 因子挖掘</h1>
          <p class="page-header__subtitle">大模型与遗传编程驱动的因子发现</p>
        </div>
        <div class="page-header__actions">
          <span class="model-badge" v-if="aiProviders.length">
            <span class="model-badge__dot"></span>
            {{ aiBadgeText }}
          </span>
        </div>
      </header>

      <!-- 两栏布局 -->
      <div class="mining-layout">
        <!-- 左栏：挖掘方式 + 参数配置 -->
        <aside class="mining-sidebar">
          <!-- 挖掘方式 -->
          <SectionCard title="挖掘方式">
            <div class="mode-grid">
              <button
                v-for="m in modes"
                :key="m.value"
                type="button"
                class="mode-card"
                :class="{ 'mode-card--active': selectedMode === m.value }"
                @click="selectedMode = m.value"
              >
                <el-icon class="mode-card__icon"><component :is="m.icon" /></el-icon>
                <div class="mode-card__title">{{ m.title }}</div>
                <div class="mode-card__desc">{{ m.desc }}</div>
              </button>
            </div>
          </SectionCard>

          <!-- 参数配置 -->
          <SectionCard title="参数配置">
            <el-form label-position="top" class="config-form">
              <el-form-item label="候选数量">
                <el-input-number v-model="form.candidates" :min="1" :max="50" controls-position="right" />
              </el-form-item>

              <el-form-item label="迭代轮数" v-if="selectedMode === 'llm'">
                <el-input-number v-model="form.nRounds" :min="1" :max="5" controls-position="right" />
                <div class="form-hint">大于 1 时启用迭代挖掘：每轮反馈给 LLM 逐轮改进</div>
              </el-form-item>

              <el-form-item label="IC 阈值">
                <el-input-number
                  v-model="form.icThreshold"
                  :min="0"
                  :max="1"
                  :step="0.01"
                  :precision="2"
                  controls-position="right"
                />
              </el-form-item>

              <el-form-item label="回测区间">
                <div class="date-range">
                  <el-date-picker
                    v-model="form.startDate"
                    type="date"
                    value-format="YYYY-MM-DD"
                    placeholder="开始日期"
                    :clearable="false"
                  />
                  <span class="date-sep">~</span>
                  <el-date-picker
                    v-model="form.endDate"
                    type="date"
                    value-format="YYYY-MM-DD"
                    placeholder="结束日期"
                    :clearable="false"
                  />
                </div>
              </el-form-item>

              <el-form-item label="股票池">
                <el-select v-model="form.universe">
                  <el-option v-for="o in universeOptions" :key="o.value" :label="o.label" :value="o.value" />
                </el-select>
              </el-form-item>

              <el-form-item label="允许算子">
                <div class="operator-tags">
                  <span v-for="op in operators" :key="op" class="operator-tag">{{ op }}</span>
                </div>
              </el-form-item>

              <div class="actions">
                <el-button class="start-btn" type="primary" :loading="submitting" @click="startMining">
                  <el-icon v-if="!submitting"><VideoPlay /></el-icon>
                  <span>开始挖掘</span>
                </el-button>
                <el-button @click="resetForm">重置</el-button>
              </div>
            </el-form>
          </SectionCard>
        </aside>

        <!-- 右栏：挖掘历史 -->
        <SectionCard title="挖掘历史" class="history-card">
          <template #extra>
            <a class="refresh-link" @click="loadTasks">
              <el-icon><Refresh /></el-icon>
              <span>刷新</span>
            </a>
          </template>

          <el-table :data="tasks" v-loading="loading" size="default">
            <el-table-column label="类型" min-width="90">
              <template #default="{ row }">
                <span class="badge" :class="typeBadgeClass(row.type)">{{ typeLabel(row.type) }}</span>
              </template>
            </el-table-column>

            <el-table-column label="状态" min-width="110">
              <template #default="{ row }">
                <span
                  class="badge"
                  :class="[statusBadgeClass(row), row.status === 'running' ? 'badge--running' : '']"
                >
                  <span v-if="row.status === 'running'" class="status-dot"></span>
                  {{ statusLabel(row) }}
                </span>
              </template>
            </el-table-column>

            <el-table-column label="候选/通过" min-width="100">
              <template #default="{ row }">
                <span class="cell-mono">{{ fmtCand(row) }}</span>
              </template>
            </el-table-column>

            <el-table-column label="最佳IC" min-width="90">
              <template #default="{ row }">
                <span
                  class="cell-mono"
                  :class="{ 'cell-ic-positive': row.best_ic != null && Number(row.best_ic) > 0 }"
                  >{{ fmtIc(row.best_ic) }}</span
                >
              </template>
            </el-table-column>

            <el-table-column label="耗时" min-width="90">
              <template #default="{ row }">
                <span class="cell-meta">{{ fmtDuration(row) }}</span>
              </template>
            </el-table-column>

            <el-table-column label="时间" min-width="110" align="right">
              <template #default="{ row }">
                <span class="cell-time">{{ fmtTime(row.created_at) }}</span>
              </template>
            </el-table-column>

            <template #empty>
              <el-empty description="暂无挖掘任务" :image-size="72" />
            </template>
          </el-table>
        </SectionCard>
      </div>
      <!-- 文本因子挖掘对话框 -->
      <el-dialog v-model="textDialog.visible" title="文本因子挖掘" width="480px" :close-on-click-modal="false">
        <el-form label-position="top" class="config-form">
          <el-form-item label="股票池">
            <el-select v-model="textDialog.form.universe">
              <el-option v-for="o in universeOptions" :key="o.value" :label="o.label" :value="o.value" />
            </el-select>
          </el-form-item>
          <el-form-item label="新闻天数">
            <el-input-number v-model="textDialog.form.newsDays" :min="1" :max="365" controls-position="right" />
          </el-form-item>
        </el-form>
        <template #footer>
          <el-button @click="textDialog.visible = false">取消</el-button>
          <el-button type="primary" :loading="textDialog.submitting" @click="submitTextMining"> 开始挖掘 </el-button>
        </template>
      </el-dialog>

      <!-- AutoML 因子组合对话框 -->
      <el-dialog v-model="automlDialog.visible" title="AutoML 因子组合" width="560px" :close-on-click-modal="false">
        <el-form label-position="top" class="config-form">
          <el-form-item label="基础因子">
            <el-select
              v-model="automlDialog.form.factorIds"
              multiple
              filterable
              placeholder="选择参与组合的因子"
              style="width: 100%"
            >
              <el-option
                v-for="f in factorList"
                :key="f.id"
                :label="f.name + ' (IC: ' + (f.ic != null ? Number(f.ic).toFixed(2) : '--') + ')'"
                :value="f.id"
              />
            </el-select>
          </el-form-item>
          <el-form-item label="组合方法">
            <el-select v-model="automlDialog.form.method">
              <el-option label="LightGBM" value="lightgbm" />
              <el-option label="线性回归" value="linear" />
            </el-select>
          </el-form-item>
        </el-form>
        <template #footer>
          <el-button @click="automlDialog.visible = false">取消</el-button>
          <el-button type="primary" :loading="automlDialog.submitting" @click="submitAutoML"> 开始训练 </el-button>
        </template>
      </el-dialog>
    </div>
  </PageContainer>
</template>

<script setup>
import { ref, reactive, computed, onMounted, onBeforeUnmount, watch } from 'vue'
import { ElMessage } from 'element-plus/es/components/message/index'
import { MagicStick, Operation, ChatLineSquare, Connection, VideoPlay, Refresh } from '@element-plus/icons-vue'
import PageContainer from '@/components/common/PageContainer.vue'
import { mineLlm, mineSymbolic, mineText, mineAutoml, listMiningTasks, getMiningTask } from '@/api/mining'
import { getAiStatus } from '@/api/auth'
import { listFactors } from '@/api/factor'

// 挖掘方式定义
const modes = [
  { value: 'llm', title: 'LLM 生成', desc: '大模型生成因子表达式', icon: MagicStick },
  { value: 'symbolic', title: '符号回归', desc: 'gplearn 遗传编程搜索', icon: Operation },
  { value: 'text', title: '文本因子', desc: '新闻情感与文本特征', icon: ChatLineSquare },
  { value: 'automl', title: 'AutoML 组合', desc: 'LightGBM 因子组合优化', icon: Connection },
]

// 允许算子（静态展示）
const operators = ['Ref', 'Mean', 'Std', 'Max', 'Min', 'Sum', 'Rank', 'Corr', 'Cov', 'Delta', 'Slope']

// 股票池选项（优先从后端拉取实际存在的 instruments/*.txt，含 ETF 池）
const universeOptions = ref([
  { value: 'csi300', label: 'CSI300' },
  { value: 'csi500', label: 'CSI500' },
  { value: 'all', label: '全A股' },
])
async function loadUniverses() {
  try {
    const { listUniverses } = await import('@/api/quant')
    const items = await listUniverses()
    if (Array.isArray(items) && items.length) {
      universeOptions.value = items.map((u) => ({ value: u.name, label: `${u.name}（${u.count}）` }))
    }
  } catch (e) {
    // 拉取失败保留默认股票池选项
  }
}

// 默认表单值（重置用）
const todayStr = () => new Date().toISOString().slice(0, 10)
const defaultForm = () => ({
  candidates: 10,
  nRounds: 1,
  icThreshold: 0.03,
  startDate: '2020-01-01',
  endDate: todayStr(),
  universe: 'csi300',
})

// AI Provider 动态状态（badge 显示真实可用模型）
const aiProviders = ref([])
const aiBadgeText = computed(() => {
  if (!aiProviders.value.length) return ''
  const models = aiProviders.value.map((p) => p.model).join(' / ')
  return `${models} 就绪`
})
async function loadAiStatus() {
  try {
    const data = await getAiStatus()
    aiProviders.value = data?.providers ?? []
  } catch {
    aiProviders.value = []
  }
}

const selectedMode = ref('llm')
const form = reactive(defaultForm())
const tasks = ref([])
const loading = ref(false)
const submitting = ref(false)

// 文本因子挖掘对话框
const textDialog = reactive({
  visible: false,
  submitting: false,
  form: { universe: 'csi300', newsDays: 30 },
})

// AutoML 因子组合对话框
const automlDialog = reactive({
  visible: false,
  submitting: false,
  form: { factorIds: [], method: 'lightgbm' },
})

// 因子库列表（AutoML 多选用）
const factorList = ref([])

// 轮询与实时耗时
let pollTimer = null
let tickTimer = null
const now = ref(Date.now())

const hasRunning = computed(() => tasks.value.some((t) => t.status === 'running' || t.status === 'pending'))

// 类型 / 状态标签映射
const typeLabel = (t) => ({ llm: 'LLM', symbolic: '符号', text: '文本', automl: 'AutoML' })[t] || t
const typeBadgeClass = (t) =>
  ({
    llm: 'badge--primary',
    symbolic: 'badge--warning',
    text: 'badge--info',
    automl: 'badge--danger',
  })[t] || 'badge--muted'
// 状态标签映射：done 但 0 个候选通过 ≠ 成功，单独展示，避免误判为运行失败
const statusLabel = (row) => {
  const s = typeof row === 'string' ? row : row.status
  if (s === 'done') {
    const p = row.candidates_passed
    return p != null && Number(p) > 0 ? '成功' : '完成(0达标)'
  }
  return { done: '成功', running: '运行中', failed: '失败', pending: '等待' }[s] || s
}
const statusBadgeClass = (row) => {
  const s = typeof row === 'string' ? row : row.status
  if (s === 'done') {
    const p = row.candidates_passed
    return p != null && Number(p) > 0 ? 'badge--success' : 'badge--muted'
  }
  return (
    {
      done: 'badge--success',
      running: 'badge--warning',
      failed: 'badge--danger',
      pending: 'badge--muted',
    }[s] || 'badge--muted'
  )
}

// 候选 / 通过格式化
function fmtCand(row) {
  if (row.status === 'pending') return '--'
  const c = row.candidates_generated ?? 0
  const p = row.candidates_passed
  return `${c}/${p == null ? '—' : p}`
}

// 最佳 IC 格式化
function fmtIc(v) {
  if (v == null || v === '') return '--'
  const n = Number(v)
  return Number.isNaN(n) ? '--' : n.toFixed(2)
}

// 耗时格式化：started_at -> finished_at，运行中以当前时间计
function fmtDuration(row) {
  // 引用 now 以便运行中耗时实时刷新
  void now.value
  if (!row.started_at) return '--'
  const start = new Date(row.started_at).getTime()
  if (Number.isNaN(start)) return '--'
  const isRunning = row.status === 'running' || row.status === 'pending'
  let endTs
  if (isRunning) {
    endTs = Date.now()
  } else if (row.finished_at) {
    endTs = new Date(row.finished_at).getTime()
    if (Number.isNaN(endTs)) return '--'
  } else {
    return '--'
  }
  let s = Math.max(0, Math.floor((endTs - start) / 1000))
  const m = Math.floor(s / 60)
  s = s % 60
  return `${m}m ${String(s).padStart(2, '0')}s`
}

// 时间格式化 MM-DD HH:mm
function fmtTime(t) {
  if (!t) return '--'
  const d = new Date(t)
  if (Number.isNaN(d.getTime())) return '--'
  const mm = String(d.getMonth() + 1).padStart(2, '0')
  const dd = String(d.getDate()).padStart(2, '0')
  const hh = String(d.getHours()).padStart(2, '0')
  const mi = String(d.getMinutes()).padStart(2, '0')
  return `${mm}-${dd} ${hh}:${mi}`
}

// 加载历史任务
async function loadTasks() {
  loading.value = true
  try {
    const data = await listMiningTasks({ limit: 20 })
    tasks.value = data?.items || []
    if (hasRunning.value) startPolling()
  } catch (e) {
    // 错误由全局 axios 拦截器统一提示
  } finally {
    loading.value = false
  }
}

// 轮询运行中任务，每 5s 拉取单个任务状态
function startPolling() {
  if (pollTimer) return
  pollTimer = setInterval(async () => {
    const running = tasks.value.filter((t) => t.status === 'running' || t.status === 'pending')
    if (!running.length) {
      stopPolling()
      return
    }
    await Promise.all(
      running.map(async (t) => {
        try {
          const data = await getMiningTask(t.id)
          const idx = tasks.value.findIndex((x) => x.id === t.id)
          if (idx > -1) tasks.value[idx] = { ...tasks.value[idx], ...data }
        } catch (e) {
          // 单个任务查询失败忽略，下轮继续
        }
      })
    )
  }, 5000)
}

function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

// 秒级 ticker：仅在有运行中任务时刷新耗时显示
watch(
  hasRunning,
  (v) => {
    if (v && !tickTimer) {
      tickTimer = setInterval(() => {
        now.value = Date.now()
      }, 1000)
    } else if (!v && tickTimer) {
      clearInterval(tickTimer)
      tickTimer = null
    }
  },
  { immediate: true }
)

// 开始挖掘
async function startMining() {
  if (submitting.value) return
  submitting.value = true
  try {
    let data
    if (selectedMode.value === 'llm') {
      data = await mineLlm({ n_candidates: form.candidates, n_rounds: form.nRounds, universe: form.universe })
    } else if (selectedMode.value === 'symbolic') {
      data = await mineSymbolic({ population: 1000, generations: 20, universe: form.universe })
    } else if (selectedMode.value === 'text') {
      textDialog.visible = true
      return
    } else if (selectedMode.value === 'automl') {
      await loadFactors()
      automlDialog.visible = true
      return
    } else {
      ElMessage.info('该模式开发中')
      return
    }
    if (data && data.task_id != null) {
      ElMessage.success('挖掘任务已创建')
      await loadTasks()
      startPolling()
    }
  } catch (e) {
    ElMessage.error('挖掘启动失败')
  } finally {
    submitting.value = false
  }
}

// 重置表单
function resetForm() {
  Object.assign(form, defaultForm())
  selectedMode.value = 'llm'
}

// 加载因子库列表
async function loadFactors() {
  try {
    const data = await listFactors({ status: 'active', sort_by: 'ic', limit: 200 })
    factorList.value = data?.items || []
  } catch (e) {
    // 忽略，列表为空时用户可见
  }
}

// 提交文本因子挖掘
async function submitTextMining() {
  if (textDialog.submitting) return
  textDialog.submitting = true
  try {
    const data = await mineText({
      universe: textDialog.form.universe,
      news_days: textDialog.form.newsDays,
    })
    if (data && data.task_id != null) {
      ElMessage.success('文本因子挖掘任务已创建')
      textDialog.visible = false
      await loadTasks()
      startPolling()
    }
  } catch (e) {
    ElMessage.error('文本因子挖掘启动失败')
  } finally {
    textDialog.submitting = false
  }
}

// 提交 AutoML 因子组合
async function submitAutoML() {
  if (automlDialog.submitting) return
  if (!automlDialog.form.factorIds.length) {
    ElMessage.warning('请至少选择一个基础因子')
    return
  }
  automlDialog.submitting = true
  try {
    const data = await mineAutoml({
      factor_ids: automlDialog.form.factorIds,
      method: automlDialog.form.method,
    })
    if (data && data.task_id != null) {
      ElMessage.success('AutoML 因子组合任务已创建')
      automlDialog.visible = false
      await loadTasks()
      startPolling()
    }
  } catch (e) {
    ElMessage.error('AutoML 因子组合启动失败')
  } finally {
    automlDialog.submitting = false
  }
}

onMounted(() => {
  loadTasks()
  loadUniverses()
})

onBeforeUnmount(() => {
  stopPolling()
  if (tickTimer) clearInterval(tickTimer)
})
</script>

<style scoped lang="scss">
.mining-page {
  animation: fadeInUp 0.5s var(--ease-out-expo) both;
}

/* 页面头 */
.page-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--space-md);
  margin-bottom: var(--space-lg);
  flex-wrap: wrap;

  &__lead {
    flex: 1;
    min-width: 0;
  }
  &__title {
    font-size: var(--font-size-2xl);
    font-weight: 700;
    color: var(--text-primary);
    margin: 0 0 var(--space-xs);
    line-height: var(--line-height-tight);
  }
  &__subtitle {
    font-size: var(--font-size-sm);
    color: var(--text-secondary);
  }
  &__actions {
    display: flex;
    align-items: center;
    gap: var(--space-sm);
  }
}
.model-badge {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 5px 12px;
  border-radius: var(--radius-full);
  background: var(--bg-tertiary);
  color: var(--text-secondary);
  font-size: var(--font-size-sm);
  font-weight: var(--font-weight-medium);
  white-space: nowrap;
}
.model-badge__dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--success);
}

/* 两栏布局 */
.mining-layout {
  display: grid;
  grid-template-columns: 420px 1fr;
  gap: var(--space-lg);
  align-items: start;
}
.mining-sidebar {
  display: flex;
  flex-direction: column;
  gap: var(--space-md);
}

/* 历史卡片 */
.history-card {
  overflow: hidden;
}

/* 挖掘方式 */
.mode-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
}
.mode-card {
  display: block;
  width: 100%;
  padding: 12px;
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  background: var(--bg-card);
  cursor: pointer;
  text-align: left;
  transition: all var(--duration-fast) var(--ease-in-out);
  font-family: inherit;

  &:hover {
    background: var(--bg-hover);
  }
  &--active {
    border-color: var(--primary);
    background: rgba(var(--primary-rgb), 0.05);

    .mode-card__icon {
      color: var(--primary);
    }
  }
}
.mode-card__icon {
  font-size: 20px;
  color: var(--text-tertiary);
  margin-bottom: 8px;
}
.mode-card__title {
  font-size: var(--font-size-base);
  font-weight: var(--font-weight-medium);
  color: var(--text-primary);
}
.mode-card__desc {
  font-size: var(--font-size-sm);
  color: var(--text-tertiary);
  margin-top: 2px;
}

/* 参数表单 */
.config-form {
  :deep(.el-form-item) {
    margin-bottom: var(--space-md);
  }
  :deep(.el-form-item__label) {
    font-size: var(--font-size-base);
    color: var(--text-tertiary);
    padding-bottom: 4px;
    line-height: 1.5;
  }
  :deep(.el-input-number) {
    width: 100%;
  }
  :deep(.el-select) {
    width: 100%;
  }
}
.date-range {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;

  :deep(.el-date-editor) {
    flex: 1;
  }
}
.date-sep {
  color: var(--text-tertiary);
  flex-shrink: 0;
}
.operator-tags {
  display: flex;
  flex-wrap: wrap;
  margin: 4px 0;
}
.operator-tag {
  background: var(--bg-tertiary);
  color: var(--text-primary);
  padding: 4px 8px;
  border-radius: var(--radius-sm);
  font-family: var(--font-mono);
  font-size: var(--font-size-sm);
  display: inline-block;
  margin: 4px;
}
.form-hint {
  font-size: var(--font-size-sm);
  color: var(--text-tertiary);
  margin-top: 4px;
  line-height: 1.4;
}
.actions {
  display: flex;
  gap: 8px;
  margin-top: var(--space-md);

  :deep(.el-button) {
    margin: 0;
  }
  .start-btn {
    flex: 1;
  }
}

/* 历史卡片 */
.history-card {
  overflow: hidden;
}
.refresh-link {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  color: var(--primary);
  font-size: var(--font-size-base);
  cursor: pointer;
  user-select: none;

  &:hover {
    opacity: 0.85;
  }
}

/* 表格 */
:deep(.el-table) {
  --el-table-border-color: var(--border-light);
  --el-table-header-bg-color: var(--bg-tertiary);
  --el-table-row-hover-bg-color: var(--bg-hover);
  --el-table-bg-color: transparent;
  --el-table-tr-bg-color: transparent;

  th.el-table__cell {
    background: var(--bg-tertiary) !important;
    font-size: var(--font-size-sm);
    color: var(--text-tertiary);
    font-weight: var(--font-weight-medium);
  }
  .el-table__cell {
    padding: 8px 0;
  }
}

/* Badge */
.badge {
  display: inline-block;
  padding: 2px 8px;
  border-radius: var(--radius-sm);
  font-size: var(--font-size-sm);
  font-weight: var(--font-weight-medium);
}
.badge--primary {
  background: var(--primary-soft);
  color: var(--primary);
}
.badge--success {
  background: var(--success-soft);
  color: var(--success);
}
.badge--warning {
  background: var(--warning-soft);
  color: var(--warning);
}
.badge--info {
  background: var(--info-soft);
  color: var(--info);
}
.badge--danger {
  background: var(--danger-soft);
  color: var(--danger);
}
.badge--muted {
  background: var(--bg-hover);
  color: var(--text-tertiary);
}

/* 运行中状态徽章 */
.badge--running {
  display: inline-flex;
  align-items: center;
  gap: 5px;

  .status-dot {
    width: 6px;
    height: 6px;
    border-radius: 50%;
    background: currentColor;
    animation: pulse-dot 1.5s ease-in-out infinite;
  }
}
@keyframes pulse-dot {
  0%,
  100% {
    opacity: 1;
  }
  50% {
    opacity: 0.3;
  }
}

/* 表格内文本样式 */
.cell-mono {
  font-family: var(--font-mono);
  font-variant-numeric: tabular-nums;
}
.cell-ic-positive {
  color: var(--success);
}
.cell-meta {
  font-family: var(--font-mono);
  font-size: var(--font-size-sm);
  color: var(--text-secondary);
}
.cell-time {
  font-size: var(--font-size-sm);
  color: var(--text-tertiary);
}

/* 响应式：移动端单栏 */
@media (max-width: 1024px) {
  .mining-layout {
    grid-template-columns: 1fr;
  }
}
</style>
