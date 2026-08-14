<template>
  <PageContainer narrow>
    <PageHeader title="策略库" subtitle="规则信号模板 + 学术经典策略，读懂逻辑、一键回测（规则回测自动保存历史）" />

    <!-- 分类切换 + 统计 -->
    <div class="library-hero">
      <el-segmented v-model="kindFilter" :options="segmentedOptions" class="lib-segmented" />
      <div class="lib-stats">
        <div class="stat-item" v-for="s in statItems" :key="s.label">
          <span class="stat-value">{{ s.value }}</span>
          <span class="stat-label">{{ s.label }}</span>
        </div>
      </div>
    </div>

    <!-- 卡片网格（统一渲染，按 type 分支） -->
    <transition-group name="card" tag="div" class="tpl-grid">
      <el-card
        v-for="item in filteredCards"
        :key="cardKey(item)"
        class="tpl-card"
        shadow="never"
      >
        <!-- 头部：名称 + 类型/类别标签 -->
        <div class="card-head">
          <div class="card-title">
            <span class="card-name">{{ item.name }}</span>
            <span class="card-type-badge" :class="`badge--${item.type}`">
              {{ item.type === 'factor' ? '截面经典' : '技术信号' }}
            </span>
          </div>
          <el-tag size="small" :type="catType(item.category)" effect="light" round>{{ item.category }}</el-tag>
        </div>

        <!-- 技术模板：描述 -->
        <p v-if="item.type === 'template'" class="card-desc">{{ item.description }}</p>

        <!-- 截面经典：一句话逻辑 + 因子表达式 -->
        <template v-else>
          <p class="card-desc">{{ item.tagline }}</p>
          <div class="expr-box">
            <span class="expr-label">因子表达式</span>
            <code class="expr-code">{{ item.expression }}</code>
          </div>
        </template>

        <!-- 教学解读（两类都可能有） -->
        <div v-if="teaching(item)" class="teach-wrap">
          <button class="teach-toggle" type="button" @click="toggleTeach(item.key)">
            <span class="teach-toggle__dot">
              <el-icon :size="12"><component :is="teachOpen[item.key] ? ArrowUp : ArrowDown" /></el-icon>
            </span>
            <span>为什么有效 · 何时失效</span>
            <span v-if="!teachOpen[item.key]" class="teach-hint">经典解读</span>
          </button>
          <div v-if="teachOpen[item.key]" class="teach-body">
            <div class="teach-sec">
              <span class="teach-sec__badge teach-sec__badge--why">为什么有效</span>
              <p class="teach-sec__text">{{ teaching(item).why_works }}</p>
            </div>
            <div class="teach-sec">
              <span class="teach-sec__badge teach-sec__badge--fail">何时失效</span>
              <p class="teach-sec__text teach-sec__text--danger">{{ teaching(item).when_fails }}</p>
            </div>
            <div class="teach-ref">文献 · {{ teaching(item).reference }}</div>
          </div>
        </div>

        <!-- 底部：类型 + 配置按钮 -->
        <div class="card-foot">
          <span class="card-meta">
            {{ metaText(item) }}
            <template v-if="item.type === 'template'">
              · {{ item.kind === 'pairs' ? '双标的' : '单标的' }}
            </template>
            <template v-else>
              · 截面排序 topk
            </template>
          </span>
          <el-button size="small" type="primary" class="card-run" @click="openConfig(item)">
            <el-icon :size="13" class="run-icon"><Aim /></el-icon>
            配置并回测
          </el-button>
        </div>
      </el-card>
    </transition-group>
    <el-empty
      v-if="!loading && !filteredCards.length"
      description="当前分类下暂无策略"
      :image-size="72"
    />

    <!-- 配置弹窗 -->
    <el-dialog
      v-model="dialogVisible"
      :title="`配置策略：${current?.name || ''}`"
      width="540px"
      :close-on-click-modal="false"
      class="config-dialog"
    >
      <div class="config-dialog__tip">
        <el-icon :size="14"><InfoFilled /></el-icon>
        <span>{{ dialogTip }}</span>
      </div>
      <el-form label-width="88px" label-position="left">
        <!-- 技术模板参数 -->
        <template v-if="current?.type === 'template'">
          <el-form-item v-for="p in current?.params || []" :key="p.key" :label="p.label">
            <el-input-number
              v-model="formParams[p.key]"
              :min="p.min"
              :max="p.max"
              :step="p.step || 1"
              class="full-input"
            />
          </el-form-item>
          <el-form-item label="标的">
            <div v-if="current?.kind === 'pairs'" class="sym-pair">
              <SymbolSearchSelect v-model="formSymbols[0]" />
              <span class="sym-sep">vs</span>
              <SymbolSearchSelect v-model="formSymbols[1]" />
            </div>
            <SymbolSearchSelect v-else v-model="formSymbols[0]" />
          </el-form-item>
        </template>

        <!-- 截面经典参数 -->
        <template v-else>
          <div class="param-row">
            <el-form-item label="topk">
              <el-input-number v-model="formTopk" :min="5" :max="300" class="full-input" />
            </el-form-item>
            <el-form-item label="n_drop">
              <el-input-number v-model="formNDrop" :min="1" :max="50" class="full-input" />
            </el-form-item>
          </div>
          <el-form-item label="调仓频率">
            <el-segmented v-model="formRebalance" :options="rebalanceOptions" block />
          </el-form-item>
          <el-form-item label="标的池">
            <el-select v-model="formUniverse" class="full-input">
              <el-option v-for="o in universeOptions" :key="o.value" :label="o.label" :value="o.value" />
            </el-select>
          </el-form-item>
        </template>

        <el-form-item label="日期区间">
          <el-date-picker
            v-model="formDates"
            type="daterange"
            range-separator="至"
            start-placeholder="开始"
            end-placeholder="结束"
            value-format="YYYY-MM-DD"
            class="full-input"
          />
        </el-form-item>
        <el-form-item label="基准">
          <el-select v-model="formBenchmark" class="full-input">
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
            class="full-input"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <div class="dialog-footer">
          <el-button @click="dialogVisible = false">取消</el-button>
          <el-button type="primary" :loading="running" class="run-cta" @click="runBacktest">
            <el-icon v-if="!running" :size="14" class="run-icon"><VideoPlay /></el-icon>
            {{ running ? '回测中...' : '运行回测' }}
          </el-button>
        </div>
      </template>
    </el-dialog>

    <!-- 回测历史（规则模板自动保存） -->
    <div class="history-section mt-6">
      <div class="history-head">
        <div class="history-title">
          <span class="history-title__txt">回测历史</span>
          <span class="history-count">共 {{ historyTotal }} 条</span>
        </div>
        <el-button size="small" :loading="historyLoading" text @click="loadHistory">
          <el-icon :size="14" class="refresh-icon"><Refresh /></el-icon>
          刷新
        </el-button>
      </div>
      <el-table
        v-loading="historyLoading"
        :data="historyList"
        size="small"
        class="history-table"
        :row-key="(row) => row.history_id"
        :expand-row-keys="expandedKeys"
        @expand-change="onExpandChange"
        @row-click="onRowClick"
      >
        <el-table-column type="expand" width="48">
          <template #default="{ row }">
            <BacktestResultDetail
              v-if="expandedId === row.history_id && historyDetail"
              :result="historyDetail"
              :strategy="displayedStrategy"
              :loading="historyDetailLoading"
              :deletable="false"
            />
          </template>
        </el-table-column>
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
                <el-button size="small" text type="primary" :icon="View" @click.stop="expandHistory(row)" />
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

    <!-- 回测结果（本次运行，行内展开的为历史详情） -->
    <div v-if="result" class="mt-6">
      <BacktestResultDetail
        :result="result"
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
import {
  View, RefreshRight, Delete, Aim, Refresh, ArrowUp, ArrowDown, VideoPlay, InfoFilled,
} from '@element-plus/icons-vue'
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
import { getClassicStrategies, runClassicStrategy } from '@/api/classicStrategy'
import { getQuantDataStatus, listUniverses } from '@/api/quant'

const templates = ref([])      // 技术模板
const classics = ref([])       // 经典卡
const loading = ref(false)
const kindFilter = ref('all')
const teachOpen = reactive({})

// ================= 分类 =================
const templateCards = computed(() =>
  templates.value.map((t) => ({
    ...t,
    type: 'template',
    classic: classics.value.find((c) => c.kind === 'rule' && c.key === RULE_CLASSIC_MAP[t.key]),
  }))
)
const factorCards = computed(() =>
  classics.value
    .filter((c) => c.kind === 'factor')
    .map((c) => ({ ...c, type: 'factor' }))
)

// 按当前分类过滤的卡片（核心修复：之前列表未按 kindFilter 过滤）
const filteredCards = computed(() => {
  if (kindFilter.value === 'template') return templateCards.value
  if (kindFilter.value === 'factor') return factorCards.value
  return [...templateCards.value, ...factorCards.value]
})

const segmentedOptions = computed(() => [
  { label: `全部 ${templateCards.value.length + factorCards.value.length}`, value: 'all' },
  { label: `技术信号 ${templateCards.value.length}`, value: 'template' },
  { label: `截面经典 ${factorCards.value.length}`, value: 'factor' },
])

const statItems = computed(() => [
  { label: '技术模板', value: templateCards.value.length },
  { label: '截面经典', value: factorCards.value.length },
  { label: '含教学解读', value: countTeaching.value },
  { label: '回测历史', value: historyTotal.value },
])
const countTeaching = computed(
  () => templateCards.value.filter((t) => t.classic).length + factorCards.value.length
)

// 技术模板 → 经典策略 key 映射
const RULE_CLASSIC_MAP = { ma_cross: 'dual_ma', bollinger: 'bollinger', rsi: 'rsi', momentum: 'turtle' }

const catType = (c) => {
  const map = { 均值回归: 'success', 趋势: 'primary', 统计套利: 'warning', 动量: 'danger', 反转: 'danger', '质量低波': 'success', 截面因子: 'info' }
  return map[c] || 'info'
}

const cardKey = (item) => `${item.type}-${item.key}`
const teaching = (item) => item.classic || (item.type === 'factor' ? item : null)
function toggleTeach(key) {
  teachOpen[key] = !teachOpen[key]
}
const metaText = (item) =>
  item.type === 'factor'
    ? `topk=${item.defaults?.topk ?? 50} · n_drop=${item.defaults?.n_drop ?? 5}`
    : `默认 ${(item.params || []).map((p) => p.default).join('/') || '—'}`

// ================= 配置弹窗 =================
const dialogVisible = ref(false)
const current = ref(null)
const formParams = reactive({})
const formSymbols = reactive(['', ''])
const formTopk = ref(50)
const formNDrop = ref(5)
const formRebalance = ref('week')
const formUniverse = ref('')
const universeOptions = ref([])
const formDates = ref([])
const formBenchmark = ref('SH000300')
const formCapital = ref(10_000_000)
const running = ref(false)
const result = ref(null)
const rebalanceOptions = [
  { label: '每日', value: 'day' },
  { label: '每周', value: 'week' },
  { label: '每月', value: 'month' },
]

const dialogTip = computed(() =>
  current.value?.type === 'template'
    ? `技术信号模板：在"${current.value?.name}"标的上按 ${current.value?.description?.split('。')[0]}`
    : `截面经典策略：按因子对标的池排序，买入 topk 一篮子、淘汰 n_drop 只（${current.value?.category || ''}逻辑）`
)

// 默认回测区间 = 最近 2 年 → 最新数据日期
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

function openConfig(item) {
  current.value = item
  // 技术模板：重置模板参数 + 标的
  if (item.type === 'template') {
    for (const k of Object.keys(formParams)) delete formParams[k]
    for (const p of item.params || []) formParams[p.key] = p.default
    formSymbols[0] = ''
    formSymbols[1] = ''
  }
  // 截面经典：默认 topk/n_drop/调仓/标的池
  else {
    formTopk.value = item.defaults?.topk ?? 50
    formNDrop.value = item.defaults?.n_drop ?? 5
    formRebalance.value = item.defaults?.rebalance_freq ?? 'week'
    formUniverse.value = item.defaults?.universe ?? ''
  }
  formCapital.value = 10_000_000
  refreshDefaultFormDates().then(() => {
    if (!formDates.value?.length) formDates.value = [...defaultFormDates.value]
  })
  dialogVisible.value = true
}

async function runBacktest() {
  if (!current.value) return
  const item = current.value
  // 核心修复：用 type 判断（模板项 kind 是 single/pairs，之前误判为截面经典）
  const isTemplate = item.type === 'template'
  let [start, end] = formDates.value || []
  if (!start || !end) {
    const now = new Date()
    end = now.toISOString().slice(0, 10)
    const s = new Date(now)
    s.setFullYear(now.getFullYear() - 2)
    start = s.toISOString().slice(0, 10)
  }
  running.value = true
  try {
    if (isTemplate) {
      const syms = item.kind === 'pairs' ? [formSymbols[0], formSymbols[1]] : [formSymbols[0]]
      if (!syms[0] || (item.kind === 'pairs' && !syms[1])) {
        ElMessage.warning('请填写标的代码')
        return
      }
      result.value = await runStrategyLibraryBacktest({
        template: item.key,
        params: { ...formParams },
        symbols: syms,
        start,
        end,
        benchmark: formBenchmark.value,
        initial_capital: formCapital.value,
      })
    } else {
      result.value = await runClassicStrategy({
        key: item.key,
        params: {
          topk: formTopk.value,
          n_drop: formNDrop.value,
          rebalance_freq: formRebalance.value,
          universe: formUniverse.value,
        },
        start,
        end,
        benchmark: formBenchmark.value,
        initial_capital: formCapital.value,
      })
      // 经典回测不落历史，补全展示字段
      result.value.start_date = start
      result.value.end_date = end
      result.value.initial_capital = formCapital.value
      result.value.benchmark = formBenchmark.value
      result.value.topk = formTopk.value
      result.value.n_drop = formNDrop.value
      result.value.rebalance_freq = formRebalance.value
    }
    dialogVisible.value = false
    historyDetail.value = null
    expandedKeys.value = []
    expandedId.value = null
    if (isTemplate) await loadHistory()
  } catch (e) {
    if (e !== 'cancel') ElMessage.error('回测失败: ' + (e?.message || e))
  } finally {
    running.value = false
  }
}

// ================= 回测历史 =================
const historyList = ref([])
const historyTotal = ref(0)
const historyLoading = ref(false)
const historyDetail = ref(null)
const historyDetailLoading = ref(false)
const expandedKeys = ref([])
const expandedId = ref(null)

const displayedResult = computed(() => historyDetail.value || result.value)
const displayedStrategy = computed(() => ({
  name: historyDetail.value?.template_name || result.value?.name || '',
}))

const shortTime = (ts) => (ts ? String(ts).replace('T', ' ').slice(0, 16) : '--')

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

// === 行内展开（单开折叠）：与策略回测一致，展开时懒加载详情 ===
function onExpandChange(row, expandedRows) {
  const isExpanded = expandedRows.some((r) => r.history_id === row.history_id)
  if (isExpanded) {
    expandedKeys.value = [row.history_id]
    expandedId.value = row.history_id
    loadHistoryDetail(row)
  } else if (expandedId.value === row.history_id) {
    expandedKeys.value = []
    expandedId.value = null
  }
}

function onRowClick(row) {
  if (!row) return
  if (expandedId.value === row.history_id) {
    expandedKeys.value = []
    expandedId.value = null
  } else {
    expandedKeys.value = [row.history_id]
    expandedId.value = row.history_id
    loadHistoryDetail(row)
  }
}

function expandHistory(row) {
  onRowClick(row)
}

async function loadHistoryDetail(row) {
  if (historyDetailLoading.value && historyDetail.value?.history_id === row.history_id) return
  historyDetail.value = null
  historyDetailLoading.value = true
  try {
    historyDetail.value = await getStrategyHistoryDetail(row.history_id)
  } catch (e) {
    if (e !== 'cancel') ElMessage.error('加载回测历史详情失败')
  } finally {
    historyDetailLoading.value = false
  }
}

async function rerunHistory(row) {
  try {
    const detail = await getStrategyHistoryDetail(row.history_id)
    const tpl = templates.value.find((t) => t.key === detail.template)
    if (!tpl) {
      ElMessage.error('该模板已下架，无法重跑')
      return
    }
    current.value = { ...tpl, type: 'template' }
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
    if (historyDetail.value?.history_id === row.history_id) {
      historyDetail.value = null
      expandedKeys.value = []
      expandedId.value = null
    }
    await loadHistory()
  } catch (e) {
    if (e !== 'cancel') ElMessage.error('删除回测历史失败')
  }
}

onMounted(async () => {
  loading.value = true
  try {
    const [tplData, clsData, uniData] = await Promise.all([
      getStrategyTemplates().catch(() => ({ items: [] })),
      getClassicStrategies().catch(() => ({ items: [] })),
      listUniverses().catch(() => ({ items: [] })),
    ])
    templates.value = tplData?.items || []
    classics.value = clsData?.items || []
    universeOptions.value = (uniData?.items || []).map((u) => ({
      value: u.name,
      label: `${u.name}（${u.count}）`,
    }))
  } catch {
    // 全部走 catch 兜底
  } finally {
    loading.value = false
  }
  await loadHistory()
})
</script>

<style scoped lang="scss">
/* ============ 分类栏 ============ */
.library-hero {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
  margin-bottom: 20px;

  .lib-segmented {
    :deep(.el-segmented) {
      --el-segmented-item-selected-bg-color: var(--bg-primary);
      border-radius: 10px;
    }
  }
}

.lib-stats {
  display: flex;
  gap: 8px;
}

.stat-item {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 2px;
  padding: 6px 14px;
  border: 1px solid var(--border);
  border-radius: 12px;
  background: var(--bg-card);
  min-width: 76px;
  transition: all 0.2s ease;

  &:hover {
    border-color: rgba(var(--primary-rgb), 0.4);
    transform: translateY(-1px);
  }

  .stat-value {
    font-size: 17px;
    font-weight: 700;
    color: var(--text-primary);
    font-variant-numeric: tabular-nums;
    line-height: 1.2;
  }
  .stat-label {
    font-size: 11px;
    color: var(--text-tertiary);
  }
}

/* ============ 卡片网格 ============ */
.tpl-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
  gap: 16px;
}

.tpl-card {
  border: 1px solid var(--border);
  border-radius: 14px;
  transition: transform 0.2s var(--ease-spring, cubic-bezier(0.34, 1.56, 0.64, 1)),
    box-shadow 0.25s ease, border-color 0.2s ease;

  &:hover {
    transform: translateY(-3px);
    border-color: rgba(var(--primary-rgb), 0.35);
    box-shadow: 0 2px 4px rgba(0, 0, 0, 0.04), 0 10px 28px rgba(0, 0, 0, 0.08);
  }

  :deep(.el-card__body) {
    display: flex;
    flex-direction: column;
    height: 100%;
    padding: 16px 16px 12px;
  }
}

.card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.card-title {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.card-name {
  font-size: 15px;
  font-weight: 600;
  color: var(--text-primary);
  white-space: nowrap;
}

.card-type-badge {
  flex-shrink: 0;
  font-size: 11px;
  line-height: 1;
  padding: 3px 7px;
  border-radius: 6px;
  letter-spacing: 0.2px;

  &--template {
    color: var(--primary);
    background: rgba(var(--primary-rgb), 0.1);
  }
  &--factor {
    color: var(--warning, #e6a23c);
    background: rgba(230, 162, 60, 0.12);
  }
}

.card-desc {
  margin: 10px 0 0;
  font-size: 12.5px;
  line-height: 1.65;
  color: var(--text-secondary);
  min-height: 40px;
}

/* 因子表达式 */
.expr-box {
  margin-top: 10px;
  display: flex;
  flex-direction: column;
  gap: 4px;

  .expr-label {
    font-size: 11px;
    color: var(--text-tertiary);
  }
  .expr-code {
    font-family: var(--font-mono);
    font-size: 12px;
    color: var(--primary);
    background: rgba(var(--primary-rgb), 0.07);
    border-radius: 8px;
    padding: 5px 9px;
    word-break: break-all;
    display: block;
  }
}

/* 教学解读 */
.teach-wrap {
  margin-top: 12px;
}

.teach-toggle {
  width: 100%;
  display: flex;
  align-items: center;
  gap: 7px;
  padding: 0;
  border: none;
  background: none;
  cursor: pointer;
  font-size: 12.5px;
  font-weight: 500;
  color: var(--text-secondary);
  transition: color 0.15s ease;

  &:hover {
    color: var(--primary);
  }

  &__dot {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 20px;
    height: 20px;
    border-radius: 6px;
    background: rgba(var(--primary-rgb), 0.08);
    color: var(--primary);
  }
}

.teach-hint {
  margin-left: auto;
  font-size: 11px;
  font-weight: 400;
  color: var(--text-tertiary);
}

.teach-body {
  margin-top: 8px;
  padding: 10px 12px;
  border: 1px solid var(--border);
  border-radius: 10px;
  background: var(--bg-secondary);
  animation: teachIn 0.25s var(--ease-standard, cubic-bezier(0.2, 0, 0, 1));
}

.teach-sec {
  & + & {
    margin-top: 8px;
  }

  &__badge {
    display: inline-block;
    font-size: 11px;
    font-weight: 500;
    padding: 2px 8px;
    border-radius: 100px;
    margin-bottom: 4px;

    &--why {
      color: var(--success, #67c23a);
      background: rgba(103, 194, 58, 0.1);
    }
    &--fail {
      color: var(--danger, #f56c6c);
      background: rgba(245, 108, 108, 0.1);
    }
  }

  &__text {
    margin: 0;
    font-size: 12px;
    line-height: 1.7;
    color: var(--text-secondary);

    &--danger {
      color: rgba(var(--text-secondary), 0.85);
    }
  }
}

.teach-ref {
  margin-top: 8px;
  padding-top: 8px;
  border-top: 1px dashed var(--border);
  font-size: 11.5px;
  color: var(--text-tertiary);
  line-height: 1.5;
}

/* 卡片底部 */
.card-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-top: 12px;
  padding-top: 12px;
  border-top: 1px solid var(--border);
}

.card-meta {
  font-size: 11.5px;
  color: var(--text-tertiary);
}

.card-run {
  border-radius: 8px;
  font-weight: 500;

  .run-icon {
    margin-right: 3px;
  }
}

/* 卡片切换动画 */
.card-enter-active,
.card-leave-active {
  transition: all 0.3s var(--ease-standard, cubic-bezier(0.2, 0, 0, 1));
}
.card-enter-from {
  opacity: 0;
  transform: translateY(10px) scale(0.98);
}
.card-leave-to {
  opacity: 0;
  transform: scale(0.96);
}
.card-move {
  transition: transform 0.3s ease;
}

/* ============ 配置弹窗 ============ */
.config-dialog__tip {
  display: flex;
  align-items: center;
  gap: 7px;
  margin-bottom: 14px;
  padding: 8px 12px;
  border-radius: 10px;
  font-size: 12px;
  color: var(--text-secondary);
  background: rgba(var(--primary-rgb), 0.06);
  border: 1px solid rgba(var(--primary-rgb), 0.15);
}

.full-input {
  width: 100%;
}

.param-row {
  display: grid;
  grid-template-columns: 1fr 1fr;
  column-gap: 14px;
}

.dialog-footer {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 10px;
  width: 100%;

  .run-cta {
    border-radius: 8px;
    font-weight: 600;
    min-width: 120px;

    .run-icon {
      margin-right: 4px;
    }
  }
}

@keyframes teachIn {
  from {
    opacity: 0;
    transform: translateY(-4px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

/* ============ 历史区 ============ */
.history-section {
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: 14px;
  padding: 14px 16px;
}

.history-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
}

.history-title {
  display: flex;
  align-items: baseline;
  gap: 10px;

  &__txt {
    font-size: 15px;
    font-weight: 600;
    color: var(--text-primary);
  }
}

.history-count {
  font-size: 12px;
  color: var(--text-tertiary);
}

.refresh-icon {
  margin-right: 2px;
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

.cell-cat-text {
  font-size: 12px;
  color: var(--text-tertiary);
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
</style>