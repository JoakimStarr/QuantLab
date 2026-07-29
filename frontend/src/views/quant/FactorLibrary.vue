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
        <el-button type="primary" :icon="Plus" @click="onAdd">新增因子</el-button>
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
        @sort-change="handleSortChange"
        @selection-change="handleSelectionChange"
      >
        <el-table-column type="selection" width="48" />
        <el-table-column prop="name" label="因子名称" min-width="160" sortable>
          <template #default="{ row }">
            <span class="cell-name">{{ row.name }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="category" label="类别" width="100" align="center" sortable>
          <template #default="{ row }">
            <span class="badge" :class="`badge--${categoryBadge(row.category)}`">{{ categoryLabel(row.category) }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="expression" label="表达式" width="280" show-overflow-tooltip>
          <template #default="{ row }">
            <span class="cell-expr">{{ row.expression }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="ic" label="IC" width="100" align="right" sortable>
          <template #default="{ row }">
            <span class="num" :class="numClass(row.ic)">{{ fmt(row.ic, 3) }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="rank_ic" label="RankIC" width="100" align="right" sortable>
          <template #default="{ row }">
            <span class="num" :class="numClass(row.rank_ic)">{{ fmt(row.rank_ic, 3) }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="icir" label="ICIR" width="90" align="right" sortable>
          <template #default="{ row }">
            <span class="num">{{ fmt(row.icir, 2) }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="turnover" label="换手" width="90" align="right" sortable>
          <template #default="{ row }">
            <span class="num">{{ fmt(row.turnover, 2) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="90" align="center">
          <template #default="{ row }">
            <span class="badge" :class="row.status === 'active' ? 'badge--success' : 'badge--muted'">
              {{ row.status === 'active' ? '启用' : '禁用' }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="120" align="center">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="onEvaluate(row)">评价</el-button>
            <el-button link type="danger" size="small" @click="onDisable(row)">禁用</el-button>
          </template>
        </el-table-column>
        <template #empty>
          <el-empty description="暂无因子" :image-size="80" />
        </template>
      </el-table>
    </section>
  </PageContainer>
</template>

<script setup>
import { ref, computed, onMounted, watch, nextTick } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Plus, Refresh } from '@element-plus/icons-vue'
import PageContainer from '@/components/common/PageContainer.vue'
import { listFactors } from '@/api/factor'
import { syncQuantData } from '@/api/quant'

const router = useRouter()

// 因子列表与加载状态
const factors = ref([])
const loading = ref(false)
const syncing = ref(false)

// 前端筛选与排序
const filterCategory = ref('')
const sortBy = ref('ic')
const tableRef = ref()

// 选中的因子（用于对比）
const selectedFactors = ref([])
function handleSelectionChange(val) {
  selectedFactors.value = val
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
  automl: { label: 'AutoML', badge: 'danger' }
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

// 加载因子列表：GET /factors?limit=100
async function loadFactors() {
  loading.value = true
  try {
    const data = await listFactors({ limit: 100 })
    factors.value = data?.items || []
  } catch (e) {
    if (e !== 'cancel') ElMessage.error('加载因子列表失败')
  } finally {
    loading.value = false
  }
}

// 同步数据：POST /quant/data/sync
async function syncData() {
  syncing.value = true
  try {
    await syncQuantData({})
    ElMessage.success('数据同步已提交，后台执行中')
  } catch (e) {
    if (e !== 'cancel') ElMessage.error('数据同步提交失败')
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
  color: var(--text-tertiary);
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
</style>
