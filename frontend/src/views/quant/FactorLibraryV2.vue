<template>
  <PageContainer>
    <!-- 页头：主操作 + 导入类操作收进下拉，保持头部清爽 -->
    <PageHeader title="因子库" subtitle="因子的评价、筛选与批量管理（点名称进入因子详情）">
      <template #actions>
        <el-button :icon="Refresh" @click="syncData">同步数据</el-button>
        <el-dropdown @command="onImportCommand" :disabled="!!seeding">
          <el-button :icon="Download" :loading="!!seeding">
            {{ seeding === 'alpha158' ? '导入 Alpha158…' : seeding === 'etf' ? '导入 ETF 因子…' : '导入因子集' }}<el-icon
              class="el-icon--right"
              ><ArrowDown /></el-icon>
          </el-button>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item command="alpha158">Alpha158（158 个经典因子）</el-dropdown-item>
              <el-dropdown-item command="etf">ETF 因子集（OHLCV-only）</el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
        <el-button :icon="Warning" :loading="decayChecking" @click="onDecayCheck">检测衰减</el-button>
        <el-button type="primary" :icon="Plus" @click="openAdd">新增因子</el-button>
      </template>
    </PageHeader>

    <!-- 教学提示：核心评价指标含义 -->
    <LearnTip
      storage-key="learn_tip_factor_ic"
      title="如何读懂因子评价指标？"
      desc="IC 衡量因子值与下期收益的相关性（|IC|≥0.03 即有经济意义）；RankIC 用秩相关抗极端值；ICIR = IC 均值 / IC 标准差，衡量预测稳定性。评价基于样本外验证区间，配合 BH 多重检验校正防止过拟合。"
      doc-slug="factor-engine"
    />

    <!-- 指标概览条（数据来自 /factors/summary 轻量统计，列表只按需加载当前页） -->
    <section class="factor-overview">
      <div class="factor-overview__item">
        <div class="factor-overview__num">{{ summary.total }}</div>
        <div class="factor-overview__label">因子总数</div>
      </div>
      <div class="factor-overview__item">
        <div class="factor-overview__num">{{ evaluatedCount }}</div>
        <div class="factor-overview__label">已评价</div>
      </div>
      <div class="factor-overview__item factor-overview__item--decay">
        <div class="factor-overview__num">{{ decayCount }}</div>
        <div class="factor-overview__label">衰减因子</div>
      </div>
      <div class="factor-overview__item">
        <el-tooltip placement="bottom" :show-after="200">
          <template #content>
            <div>仅统计 active 且已评价的因子</div>
            <div v-if="avgIcDetail.labels.length">按类别均值：</div>
            <div v-for="(label, i) in avgIcDetail.labels" :key="label">
              {{ label }}：{{ avgIcDetail.means[i].toFixed(3) }}
            </div>
            <div>共 {{ avgIcDetail.count }} 个因子</div>
          </template>
          <div class="factor-overview__num">{{ avgIcDisplay }}</div>
        </el-tooltip>
        <div class="factor-overview__label">平均 IC（active）</div>
      </div>
      <div class="factor-overview__cats">
        <span v-for="c in categoryCounts" :key="c.key" class="factor-overview__cat" :title="`${c.label} ${c.count}`">
          <span class="badge" :class="`badge--${c.badge}`">{{ c.label }}</span>
          <span class="factor-overview__cat-count">{{ c.count }}</span>
        </span>
      </div>
    </section>

    <!-- 筛选与批量操作 -->
    <SectionCard>
      <div class="filter-bar">
        <el-select v-model="filterCategory" class="filter-bar__select" placeholder="因子类别" clearable>
          <el-option label="全部" value="" />
          <el-option label="内置" value="builtin" />
          <el-option label="LLM" value="llm" />
          <el-option label="符号" value="symbolic" />
          <el-option label="文本" value="text" />
          <el-option label="AutoML" value="automl" />
          <el-option label="Alpha158" value="alpha158" />
        </el-select>
        <el-select v-model="filterStatus" class="filter-bar__select filter-bar__select--mid" placeholder="因子状态">
          <el-option label="全部状态" value="" />
          <el-option label="仅启用" value="active" />
          <el-option label="仅禁用" value="disabled" />
          <el-option label="仅衰减" value="decaying" />
        </el-select>
        <el-input
          v-model="searchQuery"
          class="filter-bar__search"
          :prefix-icon="Search"
          placeholder="搜索名称 / 表达式 / 描述"
          clearable
        />
        <el-select
          v-model="evalUniverse"
          clearable
          placeholder="标的池（默认）"
          style="width: 170px"
          title="评价/补算指标所用的标的池（ETF 池需先同步 ETF 数据）"
        >
          <el-option v-for="o in universeOptions" :key="o.value" :label="o.label" :value="o.value" />
        </el-select>
        <el-date-picker
          v-model="backfillPeriod"
          type="daterange"
          range-separator="~"
          start-placeholder="评价开始"
          end-placeholder="评价结束"
          value-format="YYYY-MM-DD"
          unlink-panels
          :clearable="true"
          style="width: 250px"
          title="补算指标的评价区间（留空则用默认回测区间）"
        />
        <div class="filter-bar__spacer" />
        <span v-if="selectedKeys.length" class="filter-bar__selected">已选 {{ selectedKeys.length }} 项</span>
        <el-button
          type="primary"
          :icon="MagicStick"
          :loading="backfillingMetrics"
          :disabled="selectedKeys.length === 0"
          title="用所选区间重算 IC/RankIC/ICIR/换手"
          @click="onBackfillMetrics"
          >补算指标</el-button>
        <el-button
          type="warning"
          :loading="aiExplaining"
          :disabled="selectedKeys.length === 0"
          title="为所选因子生成 AI 金融逻辑解释"
          @click="onAiExplain"
          >✨ AI 解释</el-button>
        <el-button type="primary" :disabled="selectedKeys.length < 2" @click="compareFactors">对比选中</el-button>
      </div>
    </SectionCard>

    <!-- 因子表格（服务端分页，滚动到底自动加载下一页） -->
    <SectionCard class="factor-table-card" title="因子列表">
      <template #extra>
        <span class="factor-table__count">
          共 {{ total }} 个 · 已加载 {{ rows.length }}<span v-if="selectedKeys.length"> · 已选 {{ selectedKeys.length }}</span>
          <span v-if="loadingMore" class="factor-table__loading">加载中…</span>
        </span>
      </template>
      <div class="factor-table">
        <el-skeleton v-if="listLoading" :rows="10" animated class="factor-table__skeleton" />
        <el-auto-resizer v-else>
          <template #default="{ height, width }">
            <el-table-v2
              :columns="columns"
              :data="rows"
              :width="width"
              :height="height"
              row-key="id"
              :row-class="rowClass"
              :sort-by="tableSortBy"
              :header-height="44"
              :row-height="44"
              :scrollbar-always-on="true"
              fixed
              @column-sort="onColumnSort"
              @rows-rendered="onRowsRendered"
            >
              <template #empty>
                <el-empty description="暂无因子" :image-size="80" />
              </template>
            </el-table-v2>
          </template>
        </el-auto-resizer>
      </div>
    </SectionCard>

    <!-- 新增因子弹窗 -->
    <el-dialog v-model="showAdd" title="新增因子" width="640px" destroy-on-close>
      <el-form ref="addFormRef" :model="addForm" :rules="addRules" label-width="88px">
        <el-form-item label="因子名称" prop="name">
          <el-input v-model="addForm.name" placeholder="如 momentum_20d" maxlength="100" show-word-limit />
        </el-form-item>
        <el-form-item label="表达式" prop="expression">
          <QlibExprEditor v-model="addForm.expression" />
          <div class="add-expr-hint">
            输入 $ 或算子名可自动补全（如 $close / Ref($close, 20) - 1）；负数 Ref = 未来数据会被拒绝
            <a class="add-expr-link" @click.prevent="openQlibDocs">QLib 表达式学习文档 →</a>
          </div>
        </el-form-item>
        <el-form-item label="类别" prop="category">
          <el-select v-model="addForm.category" style="width: 100%">
            <el-option label="内置（自定义）" value="builtin" />
            <el-option label="LLM" value="llm" />
            <el-option label="符号回归" value="symbolic" />
            <el-option label="文本" value="text" />
          </el-select>
        </el-form-item>
        <el-form-item label="描述" prop="description">
          <el-input v-model="addForm.description" type="textarea" :rows="2" maxlength="500" show-word-limit />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showAdd = false">取消</el-button>
        <el-button type="primary" :loading="adding" @click="submitAdd">保存</el-button>
      </template>
    </el-dialog>

    <!-- 禁用因子确认弹窗 -->
    <ConfirmDialog
      v-model="disableDialog.visible"
      title="禁用因子"
      message="禁用后该因子不会进入策略组合（保留评价数据），并将排列在列表最底端。"
      icon="warning"
      type="danger"
      confirm-text="确认禁用"
      :loading="disabling"
      @confirm="confirmDisable"
    >
      <span v-if="disableDialog.target" class="disable-target">
        目标因子：<span class="mono">{{ disableDialog.target.name }}</span>
      </span>
    </ConfirmDialog>
  </PageContainer>
</template>

<script setup>
defineOptions({ name: 'FactorLibraryV2' })
import { h, ref, computed, onMounted, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus/es/components/message/index'
import { ElCheckbox } from 'element-plus/es/components/checkbox/index'
import { ElButton } from 'element-plus/es/components/button/index'
import { ElTooltip } from 'element-plus/es/components/tooltip/index'
import { Plus, Refresh, Download, Warning, MagicStick, Search, ArrowDown } from '@element-plus/icons-vue'
import PageContainer from '@/components/common/PageContainer.vue'
import PageHeader from '@/components/common/PageHeader.vue'
import SectionCard from '@/components/common/SectionCard.vue'
import LearnTip from '@/components/common/LearnTip.vue'
import ConfirmDialog from '@/components/common/ConfirmDialog.vue'
import QlibExprEditor from '@/components/quant/QlibExprEditor.vue'
import { fmt, numClass } from '@/utils/format'
import { useFactorStore } from '@/stores/factor'
import { listUniverses } from '@/api/quant'
import { useSyncStore } from '@/stores/sync'
import { listFactors, listFactorSummary, addFactor, disableFactor, seedAlpha158, seedEtfFactors, backfillAlpha158Metrics, decayCheck, aiExplainFactorsBatch } from '@/api/factor'

const router = useRouter()
const factorStore = useFactorStore()
const syncStore = useSyncStore()

// === 列表数据：服务端分页 + 滚动加载（不再整表拉取 500 条） ===
const PAGE_SIZE = 50
const rows = ref([]) // 已加载行（滚动追加）
const total = ref(0) // 服务端匹配总数
const page = ref(0) // 已加载的最后一页索引
const listLoading = ref(false)
const loadingMore = ref(false)
// 概览条统计来自轻量的 /factors/summary，避免为了几个数字整表拉取
const summary = ref({ total: 0, evaluated: 0, avg_ic: 0, categories: [] })
const seeding = ref('') // 'alpha158' | 'etf'
const decayChecking = ref(false)
const decayMap = ref({}) // factor_id -> is_decaying

// el-table-v2 列 key → 服务端排序字段
const SORT_FIELDS = { ic: 'ic', rank_ic: 'rank_ic', icir: 'icir', turnover: 'turnover', name: 'name', category: 'category' }

async function fetchSummary() {
  try {
    const data = await listFactorSummary()
    summary.value = data || { total: 0, evaluated: 0, avg_ic: 0, categories: [] }
  } catch {
    /* 拦截器已提示 */
  }
}

// 按当前筛选条件加载一页（reset=true 从第一页重新加载）
async function fetchPage(reset = false) {
  const targetPage = reset ? 0 : page.value + 1
  const params = {
    category: filterCategory.value || undefined,
    status: filterStatus.value === 'active' ? 'active' : filterStatus.value === 'disabled' ? 'disabled' : undefined,
    sort_by: SORT_FIELDS[tableSortBy.value.key] || 'ic',
    sort_order: tableSortBy.value.order || 'desc',
    keyword: searchQuery.value.trim() || undefined,
    limit: PAGE_SIZE,
    offset: targetPage * PAGE_SIZE,
  }
  // 衰减视图：只取上次衰减检测标记的因子集合（服务端按 ids 过滤，保证分页一致）
  if (filterStatus.value === 'decaying') {
    params.status = undefined
    const ids = Object.keys(decayMap.value).map(Number)
    if (!ids.length) {
      rows.value = []
      total.value = 0
      page.value = 0
      return
    }
    params.ids = ids.join(',')
  }
  if (reset) {
    listLoading.value = true
    try {
      const data = await listFactors(params)
      rows.value = data?.items ?? []
      total.value = data?.total ?? 0
      page.value = 0
    } finally {
      listLoading.value = false
    }
  } else {
    loadingMore.value = true
    try {
      const data = await listFactors(params)
      const items = data?.items ?? []
      if (items.length) {
        rows.value = rows.value.concat(items)
        page.value = targetPage
      } else {
        // 无更多数据，封顶避免滚动触发死循环
        total.value = rows.value.length
      }
    } finally {
      loadingMore.value = false
    }
  }
}

// 虚拟列表渲染到接近底部时加载下一页
function onRowsRendered({ rowVisibleEnd }) {
  if (rowVisibleEnd >= rows.value.length - 5 && rows.value.length < total.value
      && !loadingMore.value && !listLoading.value) {
    fetchPage(false)
  }
}

let reloadTimer = null

async function refreshList() {
  await Promise.all([fetchPage(true), fetchSummary()])
}

// 评价/补算指标所用的标的池（空 = 后端 config 默认）；选项从 GET /quant/universes 拉取
const evalUniverse = ref('')
const universeOptions = ref([])
async function loadUniverses() {
  try {
    const items = await listUniverses()
    if (Array.isArray(items) && items.length) {
      universeOptions.value = items.map((u) => ({ value: u.name, label: `${u.name}（${u.count}）` }))
    }
  } catch (e) {
    // 拉取失败保留空选项
  }
}

const backfillingMetrics = ref(false)
const evaluatingId = ref(null)
const backfillPeriod = ref([])
const aiExplaining = ref(false)

// 指标 tooltip 说明（hover 在表头即可查看）
const METRIC_TIPS = {
  ic: 'IC（Information Coefficient）：因子值与下期收益的相关系数。绝对值越大，因子预测力越强；一般认为 |IC| ≥ 0.03 才有显著预测能力。',
  rank_ic: 'RankIC：因子排名与收益排名的相关系数（Spearman 系数）。比 IC 更稳健，对极端值不敏感；|RankIC| ≥ 0.05 通常是有效因子的参考线。',
  icir: 'ICIR（IC Information Ratio）：IC 均值 / IC 标准差，反映因子预测的稳定性。ICIR ≥ 0.5 表示因子稳健，≥ 1 表示非常稳定。',
  turnover: '换手率：因子分层组合在调仓时的股票变动比例。越低说明因子选股越稳定，但过低可能意味因子区分度不足；通常 20%-50% 为合理区间。',
  status: '因子状态：启用（active）= 因子可被策略使用；禁用 = 因子被暂时屏蔽（不会进入策略组合，但保留评价数据）。',
}

// === 前端筛选与排序 ===
const filterCategory = ref('')
const filterStatus = ref('')
const searchQuery = ref('')
const tableSortBy = ref({ key: 'ic', order: 'desc' })

// 筛选 / 搜索 / 排序变化 → 防抖后从第一页重新加载（服务端过滤）
watch([filterCategory, filterStatus, searchQuery, tableSortBy], () => {
  clearTimeout(reloadTimer)
  reloadTimer = setTimeout(() => fetchPage(true), 250)
})

function onColumnSort({ key, order }) {
  tableSortBy.value = { key: key || '', order: order || 'asc' }
}

// 概览条：衰减数量 / 平均 IC / 各类别计数 / 已评价数（数据来自 summary 接口）
const decayCount = computed(() => Object.values(decayMap.value).filter(Boolean).length)
const evaluatedCount = computed(() => summary.value.evaluated || 0)
const avgIc = computed(() => Number(summary.value.avg_ic || 0))
const avgIcDisplay = computed(() => avgIc.value.toFixed(3))
const avgIcDetail = computed(() => {
  const cats = (summary.value.categories || []).filter((c) => c.avg_ic != null)
  return {
    count: cats.reduce((a, c) => a + (c.active_evaluated || 0), 0),
    labels: cats.map((c) => categoryMap[c.key]?.label || c.key),
    means: cats.map((c) => Number(c.avg_ic)),
  }
})
const categoryCounts = computed(() => {
  const order = ['builtin', 'llm', 'symbolic', 'text', 'automl', 'alpha158']
  return order
    .filter((k) => categoryMap[k])
    .map((k) => {
      const c = (summary.value.categories || []).find((x) => x.key === k)
      return { key: k, label: categoryMap[k].label, badge: categoryMap[k].badge, count: c?.count || 0 }
    })
    .filter((c) => c.count > 0)
})

// === 行选择 ===
const selectedKeys = ref([])
function toggleRowSelection(rowData) {
  const idx = selectedKeys.value.indexOf(rowData.id)
  if (idx >= 0) {
    selectedKeys.value = selectedKeys.value.filter((id) => id !== rowData.id)
  } else {
    selectedKeys.value = [...selectedKeys.value, rowData.id]
  }
}
function toggleSelectAll(val) {
  if (val) {
    selectedKeys.value = rows.value.map((f) => f.id)
  } else {
    selectedKeys.value = []
  }
}

// 跳转因子详情页
function goDetail(row) {
  router.push({ path: `/quant/factor/${row.id}`, query: { name: row.name } })
}

// 对比选中因子：跳转因子对比页
function compareFactors() {
  const ids = selectedKeys.value.join(',')
  router.push(`/quant/factor-compare?ids=${ids}`)
}

// 类别映射
const categoryMap = {
  builtin: { label: '内置', badge: 'primary' },
  llm: { label: 'LLM', badge: 'success' },
  symbolic: { label: '符号', badge: 'warning' },
  text: { label: '文本', badge: 'info' },
  automl: { label: 'AutoML', badge: 'danger' },
  alpha158: { label: 'Alpha158', badge: 'primary' },
}
const categoryLabel = (c) => categoryMap[c]?.label || c || '—'
const categoryBadge = (c) => categoryMap[c]?.badge || 'muted'

// 换手率：小数 → 百分比
function turnoverPct(val) {
  const n = Number(val)
  if (val == null || Number.isNaN(n)) return '—'
  return (n * 100).toFixed(1) + '%'
}
function turnoverClass(val) {
  const n = Number(val)
  if (val == null || Number.isNaN(n)) return ''
  if (n > 0.5) return 'is-warning'
  if (n < 0.2) return 'is-success'
  return ''
}

// === el-table-v2 列定义 ===
const columns = computed(() => [
  {
    key: 'selection',
    title: '',
    width: 48,
    align: 'center',
    cellRenderer: ({ rowData }) => {
      const checked = selectedKeys.value.includes(rowData.id)
      return h(ElCheckbox, {
        modelValue: checked,
        'onUpdate:modelValue': () => toggleRowSelection(rowData),
      })
    },
    headerCellRenderer: () => {
      const all = rows.value.length > 0
      const allSelected = all && selectedKeys.value.length === rows.value.length
      const indeterminate = selectedKeys.value.length > 0 && selectedKeys.value.length < rows.value.length
      return h(ElCheckbox, {
        modelValue: all && allSelected,
        indeterminate,
        'onUpdate:modelValue': toggleSelectAll,
      })
    },
  },
  {
    key: 'name',
    title: '因子名称',
    dataKey: 'name',
    width: 150,
    sortable: true,
    cellRenderer: ({ cellData, rowData }) =>
      h(
        'span',
        {
          class: 'cell-name',
          title: '查看因子详情',
          onClick: () => goDetail(rowData),
        },
        cellData
      ),
  },
  {
    key: 'category',
    title: '类别',
    dataKey: 'category',
    width: 90,
    align: 'center',
    sortable: true,
    cellRenderer: ({ cellData }) => {
      return h('span', { class: `badge badge--${categoryBadge(cellData)}` }, categoryLabel(cellData))
    },
  },
  {
    key: 'description',
    title: '描述',
    dataKey: 'description',
    width: 180,
    cellRenderer: ({ cellData }) => {
      const text = cellData || '—'
      return h('span', { class: 'cell-desc', title: text }, text)
    },
  },
  {
    key: 'expression',
    title: '表达式',
    dataKey: 'expression',
    width: 180,
    cellRenderer: ({ cellData }) => {
      const text = cellData || '—'
      return h(
        ElTooltip,
        {
          placement: 'top-start',
          effect: 'dark',
          showArrow: false,
          content: text,
          disabled: text.length < 40,
        },
        {
          default: () => h('span', { class: 'cell-expr' }, text),
        }
      )
    },
  },
  {
    key: 'ic',
    title: 'IC',
    dataKey: 'ic',
    width: 90,
    align: 'right',
    sortable: true,
    cellRenderer: ({ cellData }) => {
      const cls = numClass(cellData)
      return h('span', { class: ['num', cls].filter(Boolean).join(' ') }, fmt(cellData, 3))
    },
    headerCellRenderer: () => {
      return h(
        ElTooltip,
        { content: METRIC_TIPS.ic, placement: 'top', effect: 'dark' },
        {
          default: () => h('span', { class: 'th-tip' }, 'IC'),
        }
      )
    },
  },
  {
    key: 'rank_ic',
    title: 'RankIC',
    dataKey: 'rank_ic',
    width: 90,
    align: 'right',
    sortable: true,
    cellRenderer: ({ cellData }) => {
      const cls = numClass(cellData)
      return h('span', { class: ['num', cls].filter(Boolean).join(' ') }, fmt(cellData, 3))
    },
    headerCellRenderer: () => {
      return h(
        ElTooltip,
        { content: METRIC_TIPS.rank_ic, placement: 'top', effect: 'dark' },
        {
          default: () => h('span', { class: 'th-tip' }, 'RankIC'),
        }
      )
    },
  },
  {
    key: 'icir',
    title: 'ICIR',
    dataKey: 'icir',
    width: 90,
    align: 'right',
    sortable: true,
    cellRenderer: ({ cellData }) => {
      const cls = numClass(cellData)
      return h('span', { class: ['num', cls].filter(Boolean).join(' ') }, fmt(cellData, 2))
    },
    headerCellRenderer: () => {
      return h(
        ElTooltip,
        { content: METRIC_TIPS.icir, placement: 'top', effect: 'dark' },
        {
          default: () => h('span', { class: 'th-tip' }, 'ICIR'),
        }
      )
    },
  },
  {
    key: 'turnover',
    title: '换手',
    dataKey: 'turnover',
    width: 90,
    align: 'right',
    sortable: true,
    cellRenderer: ({ cellData }) => {
      const cls = turnoverClass(cellData)
      return h('span', { class: ['num', cls].filter(Boolean).join(' ') }, turnoverPct(cellData))
    },
    headerCellRenderer: () => {
      return h(
        ElTooltip,
        { content: METRIC_TIPS.turnover, placement: 'top', effect: 'dark' },
        {
          default: () => h('span', { class: 'th-tip' }, '换手'),
        }
      )
    },
  },
  {
    key: 'status',
    title: '状态',
    dataKey: 'status',
    width: 84,
    align: 'center',
    cellRenderer: ({ rowData }) => {
      const active = rowData.status === 'active'
      return h('span', { class: `badge ${active ? 'badge--success' : 'badge--muted'}` }, active ? '启用' : '禁用')
    },
  },
  {
    key: 'actions',
    title: '操作',
    width: 170,
    align: 'center',
    cellRenderer: ({ rowData }) => {
      return h('div', { style: 'display:flex;gap:4px;justify-content:center' }, [
        h(ElButton, { link: true, type: 'primary', size: 'small', onClick: () => goDetail(rowData) }, () => '详情'),
        h(ElButton, {
          link: true,
          type: 'success',
          size: 'small',
          loading: evaluatingId.value === rowData.id,
          disabled: evaluatingId.value !== null && evaluatingId.value !== rowData.id,
          onClick: () => onEvaluate(rowData),
        }, () => '评价'),
        h(
          ElButton,
          {
            link: true,
            type: 'danger',
            size: 'small',
            disabled: rowData.status !== 'active',
            onClick: () => onDisable(rowData),
          },
          () => (rowData.status === 'active' ? '禁用' : '已禁用')
        ),
      ])
    },
  },
])

// === 导入因子集（下拉菜单） ===
async function onImportCommand(cmd) {
  if (cmd === 'alpha158') return onSeedAlpha158()
  if (cmd === 'etf') return onSeedEtfFactors()
}

async function onSeedAlpha158() {
  seeding.value = 'alpha158'
  try {
    const data = await seedAlpha158()
    if (data?.already_imported) {
      ElMessage.info(data?.message || 'Alpha158 已导入，无需重复操作')
    } else if (data?.evaluated != null) {
      ElMessage.success(
        `${data.message || ''}（导入 ${data.count} 个，评价 ${data.evaluated} 个，失败 ${data.eval_failed || 0} 个）`
      )
    } else {
      ElMessage.success(`Alpha158 导入成功：${data?.count ?? 0} 个因子`)
    }
    factorStore.invalidate()
    await refreshList()
  } catch {
    /* 拦截器已提示 */
  } finally {
    seeding.value = ''
  }
}

async function onSeedEtfFactors() {
  seeding.value = 'etf'
  try {
    const data = await seedEtfFactors()
    if (data?.already_imported) {
      ElMessage.info(data?.message || 'ETF 因子集已导入，无需重复操作')
    } else {
      ElMessage.success(data?.message || `已导入 ${data?.count ?? 0} 个 ETF 因子`)
    }
    factorStore.invalidate()
    await refreshList()
  } catch {
    /* 拦截器已提示 */
  } finally {
    seeding.value = ''
  }
}

async function onBackfillMetrics() {
  const ids = selectedKeys.value
  if (ids.length === 0) {
    ElMessage.warning('请先勾选要重算指标的因子')
    return
  }
  backfillingMetrics.value = true
  try {
    const [start, end] = backfillPeriod.value || []
    const params = { start_date: start || undefined, end_date: end || undefined }
    if (evalUniverse.value) params.universe = evalUniverse.value
    const data = await backfillAlpha158Metrics(ids, params)
    const total = data?.total ?? ids.length
    const failed = Number(data?.eval_failed ?? data?.failed ?? 0)
    const okCount = Number(data?.evaluated ?? 0)
    if (failed > 0) {
      const failures = data?.failures || []
      const brief = failures
        .slice(0, 5)
        .map((f) => `${f.name || f.factor_id}: ${f.error || '未知原因'}`)
        .join('；')
      ElMessage.warning(
        `补算完成 ${okCount}/${total}，失败 ${failed} 个` + (brief ? `（${brief}${failures.length > 5 ? '…' : ''}）` : '')
      )
    } else {
      ElMessage.success(data?.message || `补算完成 ${okCount}/${total}`)
    }
    factorStore.invalidate()
    await refreshList()
  } catch {
    /* 拦截器已提示 */
  } finally {
    backfillingMetrics.value = false
  }
}

// 批量 AI 因子解释：勾选后由 LLM 生成描述（幂等，已有解释跳过）
async function onAiExplain() {
  const ids = selectedKeys.value
  if (ids.length === 0) {
    ElMessage.warning('请先勾选要解释的因子')
    return
  }
  aiExplaining.value = true
  try {
    const data = await aiExplainFactorsBatch(ids)
    const total = data?.total || ids.length
    const generated = (data?.items || []).filter((i) => !i.cached).length
    const skipped = total - generated
    ElMessage.success(
      skipped > 0 ? `已生成 ${generated} 个，${skipped} 个已有解释已跳过` : `已为 ${generated} 个因子生成 AI 解释`
    )
    factorStore.invalidate()
    await refreshList()
  } catch {
    /* 拦截器已提示 */
  } finally {
    aiExplaining.value = false
  }
}

// 检测因子衰减
async function onDecayCheck() {
  decayChecking.value = true
  try {
    const data = await decayCheck()
    const map = {}
    ;(data?.decaying_factors || []).forEach((f) => {
      if (f.factor_id != null) map[f.factor_id] = true
    })
    decayMap.value = map
    // 衰减视图按 decayMap 分页过滤，检测完成后需按新集合刷新
    if (filterStatus.value === 'decaying') await fetchPage(true)
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

// 行样式：衰减因子标红 + 条纹
function rowClass({ rowData, rowIndex }) {
  const classes = []
  if (decayMap.value[rowData.id]) classes.push('row--decaying')
  if (rowIndex % 2 === 1) classes.push('row--striped')
  return classes.join(' ')
}

// 单因子评价：复用补算链路同步计算
async function onEvaluate(row) {
  evaluatingId.value = row.id
  try {
    const params = {}
    if (evalUniverse.value) params.universe = evalUniverse.value
    const [start, end] = backfillPeriod.value || []
    if (start) params.start_date = start
    if (end) params.end_date = end
    const data = await backfillAlpha158Metrics([row.id], params)
    const total = data?.total ?? 1
    const failed = Number(data?.eval_failed ?? data?.failed ?? 0)
    const okCount = Number(data?.evaluated ?? 0)
    if (failed > 0) {
      const brief = (data?.failures || [])
        .map((f) => `${f.name || f.factor_id}: ${f.error || '未知原因'}`)
        .join('；')
      ElMessage.warning(`${row.name} 补算失败 ${failed}/${total}${brief ? `（${brief}）` : ''}`)
    } else {
      ElMessage.success(`${row.name} 补算完成 ${okCount}/${total}`)
    }
    factorStore.invalidate()
    await refreshList()
  } catch (e) {
    ElMessage.error(`${row.name} 补算失败：${e?.response?.data?.detail || e?.message || '未知原因'}`)
  } finally {
    evaluatingId.value = null
  }
}

// === 新增因子 ===
const showAdd = ref(false)
const adding = ref(false)
const addFormRef = ref(null)
const addForm = ref({ name: '', expression: '', category: 'builtin', description: '' })
const addRules = {
  name: [{ required: true, message: '请输入因子名称', trigger: 'blur' }],
  expression: [{ required: true, message: '请输入因子表达式', trigger: 'blur' }],
}

function openAdd() {
  addForm.value = { name: '', expression: '', category: 'builtin', description: '' }
  showAdd.value = true
}

async function submitAdd() {
  if (!addFormRef.value) return
  try {
    await addFormRef.value.validate()
  } catch {
    return
  }
  adding.value = true
  try {
    const item = await addFactor({ ...addForm.value })
    ElMessage.success(`因子「${item?.name || addForm.value.name}」已添加`)
    showAdd.value = false
    factorStore.invalidate()
    await refreshList()
  } catch {
    /* 拦截器已提示 */
  } finally {
    adding.value = false
  }
}

function openQlibDocs() {
  router.push({ path: '/docs', query: { slug: 'qlib-expression' } })
}

// === 禁用因子 ===
const disableDialog = ref({ visible: false, target: null })
const disabling = ref(false)
function onDisable(row) {
  disableDialog.value = { visible: true, target: row }
}
async function confirmDisable() {
  const row = disableDialog.value.target
  if (!row) return
  disabling.value = true
  try {
    await disableFactor(row.id)
    ElMessage.success(`因子「${row.name}」已禁用`)
    factorStore.invalidate()
    await refreshList()
  } catch {
    ElMessage.error(`禁用因子「${row.name}」失败`)
  } finally {
    disabling.value = false
    disableDialog.value = { visible: false, target: null }
  }
}

// 同步数据：打开全局同步中心
function syncData() {
  syncStore.open()
}

onMounted(() => {
  refreshList()
  loadUniverses()
})
</script>

<style scoped lang="scss">
// 指标概览条
.factor-overview {
  display: flex;
  align-items: stretch;
  gap: 12px;
  margin-bottom: var(--space-md);
  flex-wrap: wrap;
}
.factor-overview__item {
  min-width: 120px;
  padding: 12px 16px;
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  display: flex;
  flex-direction: column;
  gap: 4px;
  &.factor-overview__item--decay .factor-overview__num {
    color: var(--danger);
  }
}
.factor-overview__num {
  font-size: 24px;
  font-weight: var(--font-weight-semibold);
  color: var(--text-primary);
  font-variant-numeric: tabular-nums;
  line-height: 1.1;
}
.factor-overview__label {
  font-size: var(--font-size-sm);
  color: var(--text-tertiary);
}
.factor-overview__cats {
  flex: 1;
  min-width: 260px;
  padding: 12px 16px;
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}
.factor-overview__cat {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
.factor-overview__cat-count {
  font-size: var(--font-size-sm);
  font-weight: var(--font-weight-semibold);
  color: var(--text-primary);
  font-variant-numeric: tabular-nums;
}

// 筛选工具栏
.filter-bar {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}
.filter-bar__select {
  width: 140px;
}
.filter-bar__select--mid {
  width: 120px;
}
.filter-bar__search {
  width: 240px;
}
.filter-bar__spacer {
  flex: 1;
}
.filter-bar__selected {
  font-size: var(--font-size-sm);
  color: var(--primary);
  font-weight: var(--font-weight-medium);
  white-space: nowrap;
}

// 因子表格卡片
.factor-table-card {
  overflow: hidden;

  :deep(.section-card__body) {
    padding: 0;
  }
}
.factor-table {
  height: calc(100vh - 470px);
  min-height: 380px;
}
.factor-table__count {
  font-size: var(--font-size-sm);
  color: var(--text-tertiary);
  white-space: nowrap;
}
.factor-table__skeleton {
  padding: 16px;
}

// 单元格内容样式
:deep(.cell-name) {
  font-weight: var(--font-weight-medium);
  color: var(--primary);
  cursor: pointer;
  transition: color 0.15s;
  &:hover {
    text-decoration: underline;
  }
}
:deep(.cell-expr) {
  font-family: var(--font-mono);
  font-size: var(--font-size-sm);
  color: var(--text-secondary);
  background-color: var(--bg-tertiary);
  padding: 2px 6px;
  border-radius: 4px;
  display: inline-block;
  max-width: 100%;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  word-break: keep-all;
}
:deep(.cell-desc) {
  font-size: var(--font-size-sm);
  color: var(--text-secondary);
  display: inline-block;
  max-width: 100%;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
:deep(.num) {
  font-family: var(--font-mono);
  font-size: var(--font-size-sm);
  color: var(--text-primary);
}

// 新增因子弹窗：表达式提示
.add-expr-hint {
  font-size: var(--font-size-xs);
  color: var(--text-tertiary);
  line-height: 1.5;
  margin-top: 4px;
}
.add-expr-link {
  margin-left: 8px;
  color: var(--primary);
  cursor: pointer;
  text-decoration: none;
  &:hover {
    text-decoration: underline;
  }
}

// 禁用确认弹窗
.disable-target {
  display: flex;
  align-items: center;
  gap: var(--space-xs);
}
.mono {
  font-family: var(--font-mono);
  color: var(--text-primary);
}

// Badge
:deep(.badge) {
  display: inline-block;
  padding: 2px 8px;
  border-radius: var(--radius-sm);
  font-size: var(--font-size-sm);
  font-weight: var(--font-weight-medium);
  line-height: 1.4;
}
:deep(.badge--primary) {
  background: var(--primary-soft);
  color: var(--primary);
}
:deep(.badge--success) {
  background: var(--success-soft);
  color: var(--success);
}
:deep(.badge--warning) {
  background: var(--warning-soft);
  color: var(--warning);
}
:deep(.badge--info) {
  background: var(--info-soft);
  color: var(--info);
}
:deep(.badge--danger) {
  background: var(--danger-soft);
  color: var(--danger);
}
:deep(.badge--muted) {
  background: var(--bg-hover);
  color: var(--text-tertiary);
}

// el-table-v2 样式覆盖
.factor-table :deep(.el-table-v2) {
  --el-table-border-color: var(--border);
  --el-table-header-bg-color: var(--bg-tertiary);
  --el-table-header-text-color: var(--text-tertiary);
  --el-table-text-color: var(--text-primary);
  --el-table-row-hover-bg-color: var(--bg-hover);
  font-size: var(--font-size-base);
}
.factor-table :deep(.el-table-v2__header) {
  background: var(--bg-tertiary);
}
.factor-table :deep(.el-table-v2__header-row) {
  background: var(--bg-tertiary);
}
.factor-table :deep(.el-table-v2__header-cell) {
  background: var(--bg-tertiary);
  font-size: var(--font-size-sm);
  color: var(--text-tertiary);
  font-weight: var(--font-weight-medium);
  padding: 0 12px;
}
.factor-table :deep(.el-table-v2__cell) {
  padding: 0 12px;
}
.factor-table :deep(.el-table-v2__row:hover) {
  background: var(--bg-hover);
}
.factor-table :deep(.el-table-v2__row.row--striped) {
  background: var(--bg-secondary);
}
.factor-table :deep(.el-table-v2__row.row--decaying) {
  background: rgba(210, 69, 69, 0.08) !important;
}
.factor-table :deep(.el-table-v2__row.row--decaying:hover) {
  background: rgba(210, 69, 69, 0.14) !important;
}
:deep(.th-tip) {
  display: inline-block;
  cursor: help;
  border-bottom: 1px dashed var(--text-tertiary);
  padding-bottom: 1px;
}
</style>