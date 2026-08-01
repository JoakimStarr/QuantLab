<template>
  <PageContainer>
    <!-- 页面头 -->
    <header class="page-header">
      <div class="page-header__lead">
        <h1 class="page-header__title">因子库</h1>
        <p class="page-header__subtitle">因子的评价、管理与组合</p>
      </div>
      <div class="page-header__actions">
        <el-button :icon="Refresh" :loading="syncing" @click="syncData">同步数据</el-button>
        <el-button :icon="Download" :loading="seedingAlpha158" @click="onSeedAlpha158">导入 Alpha158</el-button>
        <el-button :icon="MagicStick" :loading="backfillingMetrics" @click="onBackfillAlpha158" title="为已存在但缺指标的 Alpha158 因子补算 IC/RankIC/ICIR/换手">补算指标</el-button>
        <el-button type="primary" :icon="Plus" @click="onAdd">新增因子</el-button>
        <el-button :icon="Warning" :loading="decayChecking" @click="onDecayCheck">检测衰减</el-button>
      </div>
    </header>

    <!-- 过滤工具栏 -->
    <section class="filter-toolbar">
      <el-select v-model="filterCategory" class="filter-toolbar__select" placeholder="因子类别">
        <el-option label="全部" value="" />
        <el-option label="内置" value="builtin" />
        <el-option label="LLM" value="llm" />
        <el-option label="符号" value="symbolic" />
        <el-option label="文本" value="text" />
        <el-option label="AutoML" value="automl" />
        <el-option label="Alpha158" value="alpha158" />
      </el-select>
      <el-select v-model="sortBy" class="filter-toolbar__select">
        <el-option label="按IC" value="ic" />
        <el-option label="按RankIC" value="rank_ic" />
        <el-option label="按ICIR" value="icir" />
      </el-select>
      <div class="filter-toolbar__spacer" />
      <el-button type="primary" :disabled="selectedFactors.length < 2" @click="compareFactors">对比选中因子 ({{ selectedFactors.length }})</el-button>
      <span class="filter-toolbar__count">共 {{ factors.length }} 个因子</span>
    </section>

    <!-- 因子表格 -->
    <section class="factor-table">
      <el-skeleton v-if="loading" :rows="10" animated class="factor-table__skeleton" />
      <el-table
        v-else
        ref="tableRef"
        :data="filteredFactors"
        stripe
        :default-sort="defaultSort"
        max-height="680"
        @sort-change="handleSortChange"
        @selection-change="handleSelectionChange"
        :row-class-name="decayRowClass"
      >
        <el-table-column type="selection" width="48" />
        <el-table-column prop="name" label="因子名称" min-width="140" sortable>
          <template #default="{ row }">
            <span class="cell-name">{{ row.name }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="category" label="类别" width="100" align="center" sortable>
          <template #default="{ row }">
            <span class="badge" :class="`badge--${categoryBadge(row.category)}`">{{ categoryLabel(row.category) }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="description" label="描述" min-width="140" show-overflow-tooltip>
          <template #default="{ row }">
            <span class="cell-desc">{{ row.description || '—' }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="expression" label="表达式" width="220" show-overflow-tooltip>
          <template #default="{ row }">
            <span class="cell-expr">{{ row.expression }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="ic" label="IC" width="100" align="right" sortable>
          <template #header>
            <el-tooltip :content="METRIC_TIPS.ic" placement="top" effect="dark">
              <span class="th-tip">IC</span>
            </el-tooltip>
          </template>
          <template #default="{ row }">
            <span class="num" :class="numClass(row.ic)">{{ fmt(row.ic, 3) }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="rank_ic" label="RankIC" width="100" align="right" sortable>
          <template #header>
            <el-tooltip :content="METRIC_TIPS.rank_ic" placement="top" effect="dark">
              <span class="th-tip">RankIC</span>
            </el-tooltip>
          </template>
          <template #default="{ row }">
            <span class="num" :class="numClass(row.rank_ic)">{{ fmt(row.rank_ic, 3) }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="icir" label="ICIR" width="100" align="right" sortable>
          <template #header>
            <el-tooltip :content="METRIC_TIPS.icir" placement="top" effect="dark">
              <span class="th-tip">ICIR</span>
            </el-tooltip>
          </template>
          <template #default="{ row }">
            <span class="num">{{ fmt(row.icir, 2) }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="turnover" label="换手" width="100" align="right" sortable>
          <template #header>
            <el-tooltip :content="METRIC_TIPS.turnover" placement="top" effect="dark">
              <span class="th-tip">换手</span>
            </el-tooltip>
          </template>
          <template #default="{ row }">
            <span class="num">{{ fmt(row.turnover, 2) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="100" align="center">
          <template #header>
            <el-tooltip :content="METRIC_TIPS.status" placement="top" effect="dark">
              <span class="th-tip">状态</span>
            </el-tooltip>
          </template>
          <template #default="{ row }">
            <span class="badge" :class="row.status === 'active' ? 'badge--success' : 'badge--muted'">
              {{ row.status === 'active' ? '启用' : '禁用' }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="240" align="center">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="onEvaluate(row)">评价</el-button>
            <el-button link type="success" size="small" @click="onQuantile(row)">分层</el-button>
            <el-button link type="primary" size="small" @click="onDeepAnalysis(row)">深度分析</el-button>
            <el-button link type="warning" size="small" @click="onNeutralize(row)">中性化</el-button>
            <el-button link type="danger" size="small" @click="onDisable(row)">禁用</el-button>
          </template>
        </el-table-column>
        <template #empty>
          <el-empty description="暂无因子" :image-size="80" />
        </template>
      </el-table>
    </section>
    <!-- 分层收益对话框 -->
    <el-dialog v-model="showQuantile" :title="`分层收益评价 — ${quantileFactor?.name ?? ''}`" width="780px">
      <div v-loading="quantileLoading" style="min-height:320px">
        <div v-if="quantileResult" style="margin-bottom:12px;display:flex;gap:24px;flex-wrap:wrap">
          <span>分组数：{{ quantileResult.n_groups }}</span>
          <span>单调性评分：<b :style="{color: quantileResult.monotonicity_score > 0 ? 'var(--success)' : 'var(--danger)'}">{{ quantileResult.monotonicity_score.toFixed(3) }}</b></span>
          <span>多空净值：<b>{{ quantileResult.long_short_nav?.[quantileResult.long_short_nav.length-1]?.toFixed(3) }}</b></span>
        </div>
        <v-chart v-if="quantileResult && !quantileLoading" :option="quantileChartOption" style="height:360px;width:100%" autoresize />
        <el-empty v-else-if="!quantileLoading" description="暂无分层收益数据" :image-size="64" />
      </div>
    </el-dialog>

    <!-- 因子中性化对话框 -->
    <el-dialog v-model="showNeutralize" :title="`因子中性化 — ${neutralizeFactorData?.name ?? ''}`" width="560px">
      <div v-loading="neutralizeLoading" style="min-height:200px">
        <el-form-item label="中性化方法" v-if="!neutralizeLoading">
          <el-radio-group v-model="neutralizeMethod" @change="onNeutralizeMethodChange">
            <el-radio label="market_cap">市值中性化</el-radio>
            <el-radio label="industry">行业+市值中性化</el-radio>
            <el-radio label="both">两者</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-table v-if="neutralizeResult" :data="neutralizeTableData" border style="width:100%">
          <el-table-column prop="metric" label="指标" width="120" />
          <el-table-column prop="before" label="中性化前" align="right" />
          <el-table-column prop="after" label="中性化后" align="right" />
          <el-table-column prop="delta" label="变化" align="right" />
        </el-table>
        <el-empty v-else-if="!neutralizeLoading" description="暂无中性化结果" :image-size="64" />
      </div>
    </el-dialog>
  </PageContainer>
</template>

<script setup>
defineOptions({ name: 'FactorLibrary' })
import { ref, computed, onMounted, watch, nextTick } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus/es/components/message/index'
import { Plus, Refresh, Download, Warning, MagicStick } from '@element-plus/icons-vue'
import PageContainer from '@/components/common/PageContainer.vue'
import VChart from 'vue-echarts'
import { useFactorStore } from '@/stores/factor'
import { syncQuantData } from '@/api/quant'
import { seedAlpha158, backfillAlpha158Metrics, getQuantileAnalysis, neutralizeFactor, decayCheck } from '@/api/factor'

const router = useRouter()
const factorStore = useFactorStore()

// 因子列表与加载状态（从全局 store 读取，5 分钟缓存）
const factors = computed(() => factorStore.factors)
const loading = computed(() => factorStore.loading)
const syncing = ref(false)
const seedingAlpha158 = ref(false)
const backfillingMetrics = ref(false)
const decayChecking = ref(false)
const decayMap = ref({})  // factor_id -> is_decaying

// === 分层收益评价 ===
const showQuantile = ref(false)
const quantileLoading = ref(false)
const quantileFactor = ref(null)
const quantileResult = ref(null)

// 指标 tooltip 说明（hover 在表头即可查看）
const METRIC_TIPS = {
  ic: 'IC（Information Coefficient）：因子值与下期收益的相关系数。绝对值越大，因子预测力越强；一般认为 |IC| ≥ 0.03 才有显著预测能力。',
  rank_ic: 'RankIC：因子排名与收益排名的相关系数（Spearman 系数）。比 IC 更稳健，对极端值不敏感；|RankIC| ≥ 0.05 通常是有效因子的参考线。',
  icir: 'ICIR（IC Information Ratio）：IC 均值 / IC 标准差，反映因子预测的稳定性。ICIR ≥ 0.5 表示因子稳健，≥ 1 表示非常稳定。',
  turnover: '换手率：因子分层组合在调仓时的股票变动比例。越低说明因子选股越稳定，但过低可能意味因子区分度不足；通常 20%-50% 为合理区间。',
  status: '因子状态：启用（active）= 因子可被策略使用；禁用 = 因子被暂时屏蔽（不会进入策略组合，但保留评价数据）。',
}

// === 因子中性化 ===
const showNeutralize = ref(false)
const neutralizeLoading = ref(false)
const neutralizeFactorData = ref(null)
const neutralizeResult = ref(null)
const neutralizeMethod = ref('market_cap')

const neutralizeTableData = computed(() => {
  const r = neutralizeResult.value
  if (!r) return []
  const before = r.ic_before || {}
  const after = r.ic_after || {}
  const metrics = [
    { key: 'ic', label: 'IC' },
    { key: 'rank_ic', label: 'RankIC' },
    { key: 'icir', label: 'ICIR' },
    { key: 'ir', label: 'IR' },
  ]
  return metrics.map(m => {
    const b = before[m.key]
    const a = after[m.key]
    const delta = (b != null && a != null) ? Number(a) - Number(b) : null
    return {
      metric: m.label,
      before: b != null ? Number(b).toFixed(4) : '—',
      after: a != null ? Number(a).toFixed(4) : '—',
      delta: delta != null ? (delta >= 0 ? '+' : '') + delta.toFixed(4) : '—',
    }
  })
})

async function onNeutralize(row) {
  neutralizeFactorData.value = row
  neutralizeResult.value = null
  neutralizeMethod.value = 'market_cap'
  showNeutralize.value = true
  await fetchNeutralize()
}

async function onNeutralizeMethodChange() {
  await fetchNeutralize()
}

async function fetchNeutralize() {
  if (!neutralizeFactorData.value) return
  neutralizeLoading.value = true
  try {
    const data = await neutralizeFactor(neutralizeFactorData.value.id, {
      method: neutralizeMethod.value,
    })
    neutralizeResult.value = data
  } catch (e) {
    ElMessage.error('中性化分析失败: ' + (e?.message || e))
  } finally {
    neutralizeLoading.value = false
  }
}

async function onSeedAlpha158() {
  seedingAlpha158.value = true
  try {
    const data = await seedAlpha158()
    if (data?.already_imported) {
      // 重复点击：后端已识别为已导入，提示而非误导为"成功 0 个"
      ElMessage.info(data?.message || 'Alpha158 已导入，无需重复操作')
    } else if (data?.evaluated != null) {
      // 新导入：分两段显示导入数 + 评价数
      ElMessage.success(
        `${data.message || ''}（导入 ${data.count} 个，评价 ${data.evaluated} 个，失败 ${data.eval_failed || 0} 个）`
      )
    } else {
      ElMessage.success(`Alpha158 导入成功：${data?.count ?? 0} 个因子`)
    }
    await loadFactors()
  } catch {
    /* 拦截器已提示 */
  } finally {
    seedingAlpha158.value = false
  }
}

async function onBackfillAlpha158() {
  backfillingMetrics.value = true
  try {
    const data = await backfillAlpha158Metrics()
    ElMessage.success(
      data?.message || `补算完成 ${data?.evaluated || 0}/${data?.total || 0}`
    )
    await loadFactors()
  } catch {
    /* 拦截器已提示 */
  } finally {
    backfillingMetrics.value = false
  }
}

async function onQuantile(row) {
  quantileFactor.value = row
  quantileResult.value = null
  showQuantile.value = true
  quantileLoading.value = true
  try {
    const data = await getQuantileAnalysis(row.id, { n_groups: 5 })
    quantileResult.value = data
  } catch {
    /* 拦截器已提示 */
  } finally {
    quantileLoading.value = false
  }
}

const quantileChartOption = computed(() => {
  const r = quantileResult.value
  if (!r) return {}
  const dates = r.dates || []
  const groupNav = r.group_nav || {}
  const colors = ['#c0392b', '#e67e22', '#bdc3c7', '#27ae60', '#2980b9']
  const series = []
  const n = r.n_groups || 5
  for (let g = 1; g <= n; g++) {
    series.push({
      name: `G${g}`,
      type: 'line',
      data: groupNav[String(g)] || [],
      smooth: true,
      showSymbol: false,
      lineStyle: { width: 1.5, color: colors[(g - 1) % colors.length] },
      itemStyle: { color: colors[(g - 1) % colors.length] }
    })
  }
  series.push({
    name: '多空',
    type: 'line',
    data: r.long_short_nav || [],
    smooth: true,
    showSymbol: false,
    lineStyle: { width: 2.5, color: '#8e44ad', type: 'dashed' },
    itemStyle: { color: '#8e44ad' }
  })
  return {
    grid: { top: 40, right: 24, bottom: 30, left: 50 },
    tooltip: { trigger: 'axis' },
    legend: { top: 4, textStyle: { fontSize: 11 } },
    xAxis: { type: 'category', data: dates, boundaryGap: false, axisLabel: { fontSize: 10, hideOverlap: true } },
    yAxis: { type: 'value', scale: true, axisLabel: { fontSize: 10, formatter: v => Number(v).toFixed(2) } },
    series
  }
})

// 前端筛选与排序
const filterCategory = ref('')
const sortBy = ref('ic')
const tableRef = ref()

// 选中的因子（用于对比）
const selectedFactors = ref([])
function handleSelectionChange(val) {
  selectedFactors.value = val
}

// 深度分析：跳转因子深度分析页
function onDeepAnalysis(row) {
  router.push({ path: '/quant/factor-deep-analysis', query: { factor_id: row.id, factor_name: row.name } })
}

function compareFactors() {
  const ids = selectedFactors.value.map(f => f.id).join(',')
  router.push(`/quant/factor-compare?ids=${ids}`)
}

// sortBy 下拉框值 <-> el-table 排序字段映射（默认降序，指标越大越靠前）
const sortByMap = {
  ic: { prop: 'ic', order: 'descending' },
  rank_ic: { prop: 'rank_ic', order: 'descending' },
  icir: { prop: 'icir', order: 'descending' }
}
const defaultSort = { ...sortByMap[sortBy.value] }

// 下拉框变化时同步驱动 el-table 排序，避免与 el-table 内部排序冲突
let sortSyncing = false
watch(sortBy, (val) => {
  if (sortSyncing) return
  const el = tableRef.value
  if (!el) return
  const cfg = sortByMap[val]
  if (cfg) {
    el.sort(cfg.prop, cfg.order)
  } else {
    el.clearSort()
  }
})

// 类别映射：值 → 文案 + Badge 样式
const categoryMap = {
  builtin: { label: '内置', badge: 'primary' },
  llm: { label: 'LLM', badge: 'success' },
  symbolic: { label: '符号', badge: 'warning' },
  text: { label: '文本', badge: 'info' },
  automl: { label: 'AutoML', badge: 'danger' },
  alpha158: { label: 'Alpha158', badge: 'primary' }
}
const categoryLabel = (c) => categoryMap[c]?.label || c || '—'
const categoryBadge = (c) => categoryMap[c]?.badge || 'muted'

// 筛选（前端 computed，不重新请求接口）
// 排序交由 el-table 的 sortable 自行管理，避免与下拉框排序冲突
const filteredFactors = computed(() => {
  let list = factors.value
  if (filterCategory.value) {
    list = list.filter((f) => f.category === filterCategory.value)
  }
  return [...list]
})

// 点击列头排序时，同步下拉框状态（保持双向一致）
function handleSortChange({ prop, order }) {
  sortSyncing = true
  if (!order) {
    sortBy.value = ''
  } else {
    const entry = Object.entries(sortByMap).find(
      ([, v]) => v.prop === prop && v.order === order
    )
    sortBy.value = entry ? entry[0] : ''
  }
  nextTick(() => {
    sortSyncing = false
  })
}

// 数值格式化：空值显示 —
function fmt(val, digits = 3) {
  if (val === null || val === undefined || val === '') return '—'
  const n = Number(val)
  return Number.isNaN(n) ? '—' : n.toFixed(digits)
}

// 正负数着色：正数 success，负数 danger
function numClass(val) {
  const n = Number(val)
  if (Number.isNaN(n) || n === 0) return ''
  return n > 0 ? 'is-positive' : 'is-negative'
}

// 检测因子衰减：调用 /factors/decay-check，标记衰减行
async function onDecayCheck() {
  decayChecking.value = true
  try {
    const data = await decayCheck()
    const map = {}
    ;(data?.decaying_factors || []).forEach(f => {
      if (f.factor_id != null) map[f.factor_id] = true
    })
    decayMap.value = map
    if ((data?.decaying ?? 0) > 0) {
      ElMessage.warning(`检测到 ${data.decaying} 个衰减因子，已标红显示`)
    } else {
      ElMessage.success('因子衰减检测完成，全部健康')
    }
  } catch (e) {
    ElMessage.error('衰减检测失败')
  } finally {
    decayChecking.value = false
  }
}

// 行样式：衰减因子标红
function decayRowClass({ row }) {
  return decayMap.value[row.id] ? 'row--decaying' : ''
}

// 加载因子列表：通过全局 store（带缓存），失败时提示
async function loadFactors() {
  try {
    await factorStore.fetchList()
  } catch {
    ElMessage.error('加载因子列表失败')
  }
}

// 同步数据：POST /quant/data/sync
async function syncData() {
  syncing.value = true
  try {
    await syncQuantData({})
    ElMessage.success('数据同步已提交，后台执行中')
  } catch {
    ElMessage.error('数据同步提交失败')
  } finally {
    syncing.value = false
  }
}

// 操作占位提示
function onAdd() {
  ElMessage.info('新增因子功能开发中')
}
function onEvaluate() {
  ElMessage.info('评价功能开发中')
}
function onDisable() {
  ElMessage.info('禁用功能开发中')
}

onMounted(loadFactors)
</script>

<style scoped lang="scss">
// 页面头
.page-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--space-md);
  margin-bottom: var(--space-lg);
  flex-wrap: wrap;
}
.page-header__lead {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.page-header__title {
  margin: 0;
  font-size: var(--font-size-2xl);
  font-weight: var(--font-weight-semibold);
  color: var(--text-primary);
  line-height: var(--line-height-tight);
}
.page-header__subtitle {
  margin: 0;
  font-size: 14px;
  color: var(--text-tertiary);
}
.page-header__actions {
  display: flex;
  align-items: center;
  gap: var(--space-sm);
}

// 过滤工具栏
.filter-toolbar {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 16px;
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  margin-bottom: var(--space-md);
}
.filter-toolbar__select {
  width: 140px;
}
.filter-toolbar__spacer {
  flex: 1;
}
.filter-toolbar__count {
  font-size: var(--font-size-sm);
  color: var(--text-tertiary);
}

// 因子表格卡片
.factor-table {
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  overflow: hidden;
}
.factor-table__skeleton {
  padding: 16px;
}

// 单元格内容样式
.cell-name {
  font-weight: var(--font-weight-medium);
  color: var(--text-primary);
}
.cell-expr {
  font-family: var(--font-mono);
  font-size: var(--font-size-sm);
  // 解决白底白字：用 --text-secondary（更深）并加柔和背景，避免对比度不足
  color: var(--text-secondary);
  background-color: var(--bg-tertiary);
  padding: 2px 6px;
  border-radius: 4px;
  display: inline-block;
  max-width: 100%;
  word-break: break-all;
}
.cell-desc {
  font-size: var(--font-size-sm);
  color: var(--text-secondary);
  display: inline-block;
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.num {
  font-family: var(--font-mono);
  font-size: var(--font-size-sm);
  color: var(--text-primary);

  &.is-positive { color: var(--success); }
  &.is-negative { color: var(--danger); }
}

// Badge
.badge {
  display: inline-block;
  padding: 2px 8px;
  border-radius: var(--radius-sm);
  font-size: var(--font-size-sm);
  font-weight: var(--font-weight-medium);
  line-height: 1.4;
}
.badge--primary { background: rgba(var(--primary-rgb), 0.1); color: var(--primary); }
.badge--success { background: rgba(31, 157, 107, 0.1); color: var(--success); }
.badge--warning { background: rgba(200, 128, 28, 0.1); color: var(--warning); }
.badge--info { background: rgba(47, 125, 194, 0.1); color: var(--info); }
.badge--danger { background: rgba(210, 69, 69, 0.1); color: var(--danger); }
.badge--muted { background: var(--bg-hover); color: var(--text-tertiary); }

// el-table 样式覆盖
.factor-table :deep(.el-table) {
  --el-table-border-color: var(--border);
  --el-table-header-bg-color: var(--bg-tertiary);
  --el-table-header-text-color: var(--text-tertiary);
  --el-table-text-color: var(--text-primary);
  --el-table-row-hover-bg-color: var(--bg-hover);
  --el-table-tr-bg-color: transparent;
  background: transparent;
  font-size: var(--font-size-base);
}

// 表头
.factor-table :deep(.el-table th.el-table__cell) {
  background: var(--bg-tertiary);
  font-size: var(--font-size-sm);
  color: var(--text-tertiary);
  font-weight: var(--font-weight-medium);
}

// 单元格内边距 12px
.factor-table :deep(.el-table th.el-table__cell),
.factor-table :deep(.el-table td.el-table__cell) {
  padding: 12px 0;
}
.factor-table :deep(.el-table th .cell),
.factor-table :deep(.el-table td .cell) {
  padding-left: 12px;
  padding-right: 12px;
}

// 隔行变色：条纹行使用次级背景
.factor-table :deep(.el-table__body tr.el-table__row--striped td.el-table__cell) {
  background: var(--bg-secondary);
}

// 行 hover（置于隔行变色之后，hover 优先级更高）
.factor-table :deep(.el-table__body tr:hover > td.el-table__cell) {
  background: var(--bg-hover);
}

// 末行底边由外层卡片边框承担，避免双线
.factor-table :deep(.el-table__body tr:last-child td.el-table__cell) {
  border-bottom: 0;
}

// 衰减因子行标红
.factor-table :deep(.el-table__body tr.row--decaying td.el-table__cell) {
  background: rgba(210, 69, 69, 0.08) !important;
}
.factor-table :deep(.el-table__body tr.row--decaying:hover > td.el-table__cell) {
  background: rgba(210, 69, 69, 0.14) !important;
}

// 表头 tooltip 容器：保持表头可点击排序，hover 时显示提示
.th-tip {
  display: inline-block;
  cursor: help;
  border-bottom: 1px dashed var(--text-tertiary);
  padding-bottom: 1px;
}
</style>
