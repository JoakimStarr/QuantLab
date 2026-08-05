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
        <el-button type="primary" :icon="Plus" @click="onAdd">新增因子</el-button>
        <el-button :icon="Warning" :loading="decayChecking" @click="onDecayCheck">检测衰减</el-button>
      </div>
    </header>

    <!-- 指标概览条 -->
    <section class="factor-overview">
      <div class="factor-overview__item">
        <div class="factor-overview__num">{{ factors.length }}</div>
        <div class="factor-overview__label">因子总数</div>
      </div>
      <div class="factor-overview__item factor-overview__item--decay">
        <div class="factor-overview__num">{{ decayCount }}</div>
        <div class="factor-overview__label">衰减因子</div>
      </div>
      <div class="factor-overview__item">
        <div class="factor-overview__num">{{ avgIc.toFixed(3) }}</div>
        <div class="factor-overview__label">平均 IC</div>
      </div>
      <div class="factor-overview__cats">
        <span v-for="c in categoryCounts" :key="c.key" class="factor-overview__cat" :title="`${c.label} ${c.count}`">
          <span class="badge" :class="`badge--${c.badge}`">{{ c.label }}</span>
          <span class="factor-overview__cat-count">{{ c.count }}</span>
        </span>
      </div>
    </section>

    <!-- 过滤工具栏 -->
    <SectionCard>
      <div class="filter-toolbar">
        <el-select v-model="filterCategory" class="filter-toolbar__select" placeholder="因子类别">
          <el-option label="全部" value="" />
          <el-option label="内置" value="builtin" />
          <el-option label="LLM" value="llm" />
          <el-option label="符号" value="symbolic" />
          <el-option label="文本" value="text" />
          <el-option label="AutoML" value="automl" />
          <el-option label="Alpha158" value="alpha158" />
        </el-select>
        <el-select
          v-model="filterStatus"
          class="filter-toolbar__select filter-toolbar__select--mid"
          placeholder="因子状态"
        >
          <el-option label="全部状态" value="" />
          <el-option label="仅启用" value="active" />
          <el-option label="仅禁用" value="disabled" />
          <el-option label="仅衰减" value="decaying" />
        </el-select>
        <el-input
          v-model="searchQuery"
          class="filter-toolbar__search"
          :prefix-icon="Search"
          placeholder="搜索名称 / 表达式 / 描述"
          clearable
        />
        <div class="filter-toolbar__spacer" />
        <el-date-picker
          v-model="backfillPeriod"
          type="daterange"
          range-separator="~"
          start-placeholder="评价开始"
          end-placeholder="评价结束"
          value-format="YYYY-MM-DD"
          unlink-panels
          :clearable="true"
          style="width: 260px"
          title="补算指标的评价区间（留空则用默认回测区间）"
        />
        <el-button
          type="primary"
          :icon="MagicStick"
          :loading="backfillingMetrics"
          :disabled="selectedKeys.length === 0"
          title="勾选因子后点击，用所选区间重算 IC/RankIC/ICIR/换手"
          @click="onBackfillMetrics"
          >补算指标 ({{ selectedKeys.length }})</el-button
        >
        <el-button
          type="warning"
          :loading="aiExplaining"
          :disabled="selectedKeys.length === 0"
          title="勾选因子后点击，用 AI 生成因子金融逻辑解释"
          @click="onAiExplain"
          >✨ AI 解释 ({{ selectedKeys.length }})</el-button
        >
        <el-button type="primary" :disabled="selectedKeys.length < 2" @click="compareFactors"
          >对比选中因子 ({{ selectedKeys.length }})</el-button
        >
      </div>
    </SectionCard>

    <!-- 因子表格（虚拟滚动 el-table-v2） -->
    <SectionCard class="factor-table-card" title="因子列表">
      <template #extra>
        <span class="factor-table__count">共 {{ factors.length }} 个因子</span>
      </template>
      <div class="factor-table">
        <el-skeleton v-if="loading" :rows="10" animated class="factor-table__skeleton" />
        <el-auto-resizer v-else>
          <template #default="{ height, width }">
            <el-table-v2
              :columns="columns"
              :data="sortedData"
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
            >
              <template #empty>
                <el-empty description="暂无因子" :image-size="80" />
              </template>
            </el-table-v2>
          </template>
        </el-auto-resizer>
      </div>
    </SectionCard>

    <!-- 分层收益对话框 -->
    <el-dialog v-model="showQuantile" :title="`分层收益评价 — ${quantileFactor?.name ?? ''}`" width="780px">
      <div v-loading="quantileLoading" style="min-height: 320px">
        <div v-if="quantileResult" style="margin-bottom: 12px; display: flex; gap: 24px; flex-wrap: wrap">
          <span>分组数：{{ quantileResult.n_groups }}</span>
          <span
            >单调性评分：<b
              :style="{ color: quantileResult.monotonicity_score > 0 ? 'var(--success)' : 'var(--danger)' }"
              >{{ quantileResult.monotonicity_score.toFixed(3) }}</b
            ></span
          >
          <span
            >多空净值：<b>{{
              quantileResult.long_short_nav?.[quantileResult.long_short_nav.length - 1]?.toFixed(3)
            }}</b></span
          >
        </div>
        <v-chart
          v-if="quantileResult && !quantileLoading"
          :option="quantileChartOption"
          style="height: 360px; width: 100%"
          autoresize
        />
        <el-empty v-else-if="!quantileLoading" description="暂无分层收益数据" :image-size="64" />
      </div>
    </el-dialog>

    <!-- 因子中性化对话框 -->
    <el-dialog v-model="showNeutralize" :title="`因子中性化 — ${neutralizeFactorData?.name ?? ''}`" width="560px">
      <div v-loading="neutralizeLoading" style="min-height: 200px">
        <el-form-item label="中性化方法" v-if="!neutralizeLoading">
          <el-radio-group v-model="neutralizeMethod" @change="onNeutralizeMethodChange">
            <el-radio value="market_cap">市值中性化</el-radio>
            <el-radio value="industry">行业+市值中性化</el-radio>
            <el-radio value="both">两者</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-table v-if="neutralizeResult" :data="neutralizeTableData" border style="width: 100%">
          <el-table-column prop="metric" label="指标" width="120" />
          <el-table-column prop="before" label="中性化前" align="right" />
          <el-table-column prop="after" label="中性化后" align="right" />
          <el-table-column prop="delta" label="变化" align="right" />
        </el-table>
        <el-empty v-else-if="!neutralizeLoading" description="暂无中性化结果" :image-size="64" />
      </div>
    </el-dialog>

    <!-- 完整表达式对话框 -->
    <el-dialog v-model="showExpr" :title="`因子表达式 — ${exprFactor?.name ?? ''}`" width="640px">
      <div class="expr-viewer">
        <el-descriptions :column="1" border size="small" class="expr-viewer__meta">
          <el-descriptions-item label="名称">{{ exprFactor?.name }}</el-descriptions-item>
          <el-descriptions-item label="类别">{{ categoryLabel(exprFactor?.category) }}</el-descriptions-item>
        </el-descriptions>
        <pre class="expr-viewer__code">{{ exprFactor?.expression }}</pre>
        <p v-if="exprFactor?.description" class="expr-viewer__desc">{{ exprFactor.description }}</p>
      </div>
    </el-dialog>

    <!-- AI 因子解释弹窗：完整详细解读 + 重新解释 + 继续追问 -->
    <el-dialog v-model="showAiExplain" :title="`因子解读 · ${aiFactor?.name ?? ''}`" width="680px" destroy-on-close>
      <!-- 表达式上下文 -->
      <div class="ai-ctx">
        <span class="ai-ctx__label">表达式</span>
        <code class="ai-ctx__expr">{{ aiFactor?.expression }}</code>
      </div>

      <div v-loading="aiDetailLoading" class="ai-explain" style="min-height: 120px">
        <template v-if="!aiDetailLoading && !aiDetail">
          <el-empty description="这个因子还没有 AI 解读，点下面按钮生成一份" :image-size="80">
            <el-button type="primary" :loading="aiGenLoading" @click="onGenerateAiExplain">生成解读</el-button>
          </el-empty>
        </template>

        <template v-else-if="aiDetail">
          <div class="ai-explain__summary">{{ aiDetail.explanation?.summary }}</div>
          <div class="ai-explain__section">
            <div class="ai-explain__label">它怎么构造</div>
            <div class="ai-explain__text">{{ aiDetail.explanation?.logic }}</div>
          </div>
          <div class="ai-explain__section">
            <div class="ai-explain__label">为什么可能有效</div>
            <div class="ai-explain__text">{{ aiDetail.explanation?.rationale }}</div>
          </div>
          <div v-if="aiDetail.explanation?.caveats?.length" class="ai-explain__section">
            <div class="ai-explain__label">使用时注意</div>
            <ul class="ai-explain__caveats">
              <li v-for="(c, i) in aiDetail.explanation.caveats" :key="i">{{ c }}</li>
            </ul>
          </div>
          <div class="ai-explain__meta">
            <span v-if="aiDetail.explanation?.generated_at"
              >生成于 {{ timeAgo(aiDetail.explanation.generated_at) }}</span
            >
            <el-button
              v-if="aiDetail.explanation?.generated_at"
              link
              type="primary"
              size="small"
              :loading="aiGenLoading"
              @click="onRegenerateAiExplain"
              >重新生成</el-button
            >
          </div>
        </template>
      </div>

      <!-- 追问对话区 -->
      <template v-if="aiDetail && !aiDetailLoading">
        <div class="ai-chat" ref="aiChatRef">
          <div v-if="!aiChatMessages.length" class="ai-chat__empty">
            想深入了解这个因子？直接问它，比如「适合什么股票池？」「和动量类因子有什么区别？」
          </div>
          <div v-for="(m, i) in aiChatMessages" :key="i" class="ai-chat__msg" :class="'ai-chat__msg--' + m.role">
            <div class="ai-chat__bubble">{{ m.content }}</div>
          </div>
          <div v-if="aiChatting" class="ai-chat__msg ai-chat__msg--assistant">
            <div class="ai-chat__bubble ai-chat__bubble--typing">思考中…</div>
          </div>
        </div>
        <div class="ai-chat__input">
          <el-input
            v-model="aiQuestion"
            placeholder="继续追问，例如：适合什么股票池？"
            :disabled="aiChatting"
            @keyup.enter="onSendChat"
          />
          <el-button type="primary" :loading="aiChatting" :disabled="!aiQuestion.trim()" @click="onSendChat"
            >发送</el-button
          >
        </div>
      </template>

      <template #footer>
        <el-button @click="showAiExplain = false">关闭</el-button>
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
defineOptions({ name: 'FactorLibrary' })
import { h, ref, computed, onMounted, nextTick } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus/es/components/message/index'
import { ElCheckbox } from 'element-plus/es/components/checkbox/index'
import { ElButton } from 'element-plus/es/components/button/index'
import { ElTooltip } from 'element-plus/es/components/tooltip/index'
import { Plus, Refresh, Download, Warning, MagicStick, Search } from '@element-plus/icons-vue'
import PageContainer from '@/components/common/PageContainer.vue'
import ConfirmDialog from '@/components/common/ConfirmDialog.vue'
import VChart from 'vue-echarts'
import '@/utils/echarts'
import { useFactorStore } from '@/stores/factor'
import { syncQuantData } from '@/api/quant'
import {
  seedAlpha158,
  backfillAlpha158Metrics,
  getQuantileAnalysis,
  neutralizeFactor,
  decayCheck,
  aiExplainFactorsBatch,
  aiExplainFactor,
  getFactorAiDetail,
  chatFactorAi,
} from '@/api/factor'
import { chartTheme, echartPalette as C } from '@/utils/chartTheme'
import { useThemeRev } from '@/composables/useChartTheme'

const themeRev = useThemeRev()

const router = useRouter()
const factorStore = useFactorStore()

// 因子列表与加载状态（从全局 store 读取，5 分钟缓存）
const factors = computed(() => factorStore.factors)
const loading = computed(() => factorStore.loading)
const syncing = ref(false)
const seedingAlpha158 = ref(false)
const backfillingMetrics = ref(false)
const backfillPeriod = ref([])
const aiExplaining = ref(false)
const decayChecking = ref(false)
const decayMap = ref({}) // factor_id -> is_decaying

// === AI 因子解释弹窗 ===
const showAiExplain = ref(false)
const aiFactor = ref(null)
const aiDetail = ref(null)
const aiDetailLoading = ref(false)
const aiGenLoading = ref(false)
const aiChatting = ref(false)
const aiQuestion = ref('')
const aiChatRef = ref(null)
const aiChatMessages = computed(() => aiDetail.value?.chat_history || [])

// === 分层收益评价 ===
const showQuantile = ref(false)
const quantileLoading = ref(false)
const quantileFactor = ref(null)
const quantileResult = ref(null)

// 指标 tooltip 说明（hover 在表头即可查看）
const METRIC_TIPS = {
  ic: 'IC（Information Coefficient）：因子值与下期收益的相关系数。绝对值越大，因子预测力越强；一般认为 |IC| ≥ 0.03 才有显著预测能力。',
  rank_ic:
    'RankIC：因子排名与收益排名的相关系数（Spearman 系数）。比 IC 更稳健，对极端值不敏感；|RankIC| ≥ 0.05 通常是有效因子的参考线。',
  icir: 'ICIR（IC Information Ratio）：IC 均值 / IC 标准差，反映因子预测的稳定性。ICIR ≥ 0.5 表示因子稳健，≥ 1 表示非常稳定。',
  turnover:
    '换手率：因子分层组合在调仓时的股票变动比例。越低说明因子选股越稳定，但过低可能意味因子区分度不足；通常 20%-50% 为合理区间。',
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
  return metrics.map((m) => {
    const b = before[m.key]
    const a = after[m.key]
    const delta = b != null && a != null ? Number(a) - Number(b) : null
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
    factorStore.invalidate()
    await loadFactors()
  } catch {
    /* 拦截器已提示 */
  } finally {
    seedingAlpha158.value = false
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
    const data = await backfillAlpha158Metrics(ids, start, end)
    ElMessage.success(data?.message || `重算完成 ${data?.evaluated || 0}/${data?.total || 0}`)
    factorStore.invalidate()
    await loadFactors()
  } catch {
    /* 拦截器已提示 */
  } finally {
    backfillingMetrics.value = false
  }
}

// AI 因子解释：勾选因子后用 LLM 生成金融逻辑描述（幂等，已有解释的跳过）
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
    await loadFactors()
  } catch {
    /* 拦截器已提示 */
  } finally {
    aiExplaining.value = false
  }
}

// 打开 AI 解释弹窗：加载完整解释与追问历史
async function openAiExplain(row) {
  aiFactor.value = row
  showAiExplain.value = true
  aiDetail.value = null
  aiQuestion.value = ''
  aiDetailLoading.value = true
  try {
    aiDetail.value = await getFactorAiDetail(row.id)
  } catch {
    /* 拦截器已提示 */
  } finally {
    aiDetailLoading.value = false
  }
}

async function onGenerateAiExplain() {
  aiGenLoading.value = true
  try {
    await aiExplainFactor(aiFactor.value.id, false)
    factorStore.invalidate()
    await loadFactors()
    aiDetail.value = await getFactorAiDetail(aiFactor.value.id)
  } catch {
    /* 拦截器已提示 */
  } finally {
    aiGenLoading.value = false
  }
}

async function onRegenerateAiExplain() {
  aiGenLoading.value = true
  try {
    await aiExplainFactor(aiFactor.value.id, true)
    factorStore.invalidate()
    await loadFactors()
    aiDetail.value = await getFactorAiDetail(aiFactor.value.id)
    aiQuestion.value = ''
  } catch {
    /* 拦截器已提示 */
  } finally {
    aiGenLoading.value = false
  }
}

async function onSendChat() {
  const q = aiQuestion.value.trim()
  if (!q || aiChatting.value) return
  aiChatting.value = true
  aiQuestion.value = ''
  if (aiDetail.value) {
    aiDetail.value.chat_history = [...(aiDetail.value.chat_history || []), { role: 'user', content: q }]
  }
  scrollAiChat()
  try {
    const data = await chatFactorAi(aiFactor.value.id, q)
    if (aiDetail.value && data) {
      aiDetail.value.chat_history = data.chat_history || aiDetail.value.chat_history
    }
  } catch {
    /* 拦截器已提示 */
  } finally {
    aiChatting.value = false
    scrollAiChat()
  }
}

function scrollAiChat() {
  nextTick(() => {
    const el = aiChatRef.value
    if (el) el.scrollTop = el.scrollHeight
  })
}

// 相对时间：ISO → "刚刚 / N 分钟前 / N 小时前 / N 天前"
function timeAgo(v) {
  if (!v) return '—'
  const d = new Date(v)
  if (Number.isNaN(d.getTime())) return '—'
  const min = Math.floor((Date.now() - d.getTime()) / 60000)
  if (min < 1) return '刚刚'
  if (min < 60) return `${min} 分钟前`
  const h = Math.floor(min / 60)
  if (h < 24) return `${h} 小时前`
  return `${Math.floor(h / 24)} 天前`
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
  void themeRev.value
  const r = quantileResult.value
  if (!r) return {}
  const dates = r.dates || []
  const groupNav = r.group_nav || {}
  const series = []
  const n = r.n_groups || 5
  for (let g = 1; g <= n; g++) {
    series.push({
      name: `G${g}`,
      type: 'line',
      data: groupNav[String(g)] || [],
      smooth: true,
      showSymbol: false,
      lineStyle: { width: 1.5, color: chartTheme.palette(g) },
      itemStyle: { color: chartTheme.palette(g) },
    })
  }
  series.push({
    name: '多空',
    type: 'line',
    data: r.long_short_nav || [],
    smooth: true,
    showSymbol: false,
    lineStyle: { width: 2.5, color: C.grape, type: 'dashed' },
    itemStyle: { color: C.grape },
  })
  return {
    grid: { top: 40, right: 24, bottom: 30, left: 50 },
    tooltip: { trigger: 'axis' },
    textStyle: { color: chartTheme.axisText() },
    legend: { top: 4, textStyle: { fontSize: 11, color: chartTheme.axisText() } },
    xAxis: {
      type: 'category',
      data: dates,
      boundaryGap: false,
      axisLabel: { fontSize: 10, hideOverlap: true, color: chartTheme.axisText() },
    },
    yAxis: {
      type: 'value',
      scale: true,
      axisLabel: { fontSize: 10, formatter: (v) => Number(v).toFixed(2), color: chartTheme.axisText() },
    },
    series,
  }
})

// === 前端筛选与排序 ===
const filterCategory = ref('')
const filterStatus = ref('')
const searchQuery = ref('')
const tableSortBy = ref({ key: 'ic', order: 'desc' }) // el-table-v2 排序状态

// 列头点击排序
function onColumnSort({ key, order }) {
  tableSortBy.value = { key: key || '', order: order || 'asc' }
}

// 概览条：衰减数量 / 平均 IC / 各类别计数
const decayCount = computed(() => Object.values(decayMap.value).filter(Boolean).length)
const avgIc = computed(() => {
  const vals = factors.value.map((f) => Number(f.ic)).filter((v) => !Number.isNaN(v))
  if (!vals.length) return 0
  return vals.reduce((a, b) => a + b, 0) / vals.length
})
const categoryCounts = computed(() => {
  const order = ['builtin', 'llm', 'symbolic', 'text', 'automl', 'alpha158']
  return order
    .filter((k) => categoryMap[k])
    .map((k) => ({
      key: k,
      label: categoryMap[k].label,
      badge: categoryMap[k].badge,
      count: factors.value.filter((f) => f.category === k).length,
    }))
    .filter((c) => c.count > 0)
})

// 筛选 + 搜索 + 排序后的数据
const sortedData = computed(() => {
  let list = factors.value
  if (filterCategory.value) {
    list = list.filter((f) => f.category === filterCategory.value)
  }
  if (filterStatus.value) {
    list = list.filter((f) => {
      if (filterStatus.value === 'active') return f.status === 'active'
      if (filterStatus.value === 'disabled') return f.status !== 'active'
      if (filterStatus.value === 'decaying') return !!decayMap.value[f.id]
      return true
    })
  }
  const q = searchQuery.value.trim().toLowerCase()
  if (q) {
    list = list.filter((f) =>
      [f.name, f.expression, f.description].some((v) => (v != null ? String(v).toLowerCase().includes(q) : false))
    )
  }
  const { key, order } = tableSortBy.value
  if (key) {
    list = [...list].sort((a, b) => {
      const aVal = a[key]
      const bVal = b[key]
      // 空值排末尾
      if (aVal == null) return 1
      if (bVal == null) return -1
      return order === 'asc' ? aVal - bVal : bVal - aVal
    })
  }
  // 禁用的因子排在最底端（active 优先；稳定排序保持两组内原有顺序）
  return [...list].sort((a, b) => (a.status === 'active' ? 0 : 1) - (b.status === 'active' ? 0 : 1))
})

// === 行选择（el-table-v2 无内置 selection，用自定义 checkbox 列）===
const selectedKeys = ref([]) // 选中行的 id 列表

// === 完整表达式查看（点击省略的表达式 → 弹窗展示全文，列宽保持稳定）===
const showExpr = ref(false)
const exprFactor = ref(null)
function openExpr(row) {
  exprFactor.value = row
  showExpr.value = true
}

// === 禁用因子确认弹窗 ===
const disableDialog = ref({ visible: false, target: null })
const disabling = ref(false)

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
    selectedKeys.value = sortedData.value.map((f) => f.id)
  } else {
    selectedKeys.value = []
  }
}

// 对比选中因子：跳转因子对比页
function compareFactors() {
  const ids = selectedKeys.value.join(',')
  router.push(`/quant/factor-compare?ids=${ids}`)
}

// 深度分析：跳转因子深度分析页
function onDeepAnalysis(row) {
  router.push({ path: '/quant/factor-deep-analysis', query: { factor_id: row.id, factor_name: row.name } })
}

// 类别映射：值 → 文案 + Badge 样式
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

// 换手率：小数 → 百分比；阈值着色（过低稳定性存疑 success，过高区分度存疑 warning）
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
      const all = sortedData.value.length > 0
      const allSelected = all && selectedKeys.value.length === sortedData.value.length
      const indeterminate = selectedKeys.value.length > 0 && selectedKeys.value.length < sortedData.value.length
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
    width: 140,
    sortable: true,
    cellRenderer: ({ cellData }) => h('span', { class: 'cell-name' }, cellData),
  },
  {
    key: 'category',
    title: '类别',
    dataKey: 'category',
    width: 100,
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
    width: 170,
    cellRenderer: ({ cellData, rowData }) => {
      const text = cellData || '—'
      return h(
        'span',
        {
          class: 'cell-desc cell-desc--clickable',
          title: '双击查看 AI 详细解释',
          onDblclick: () => openAiExplain(rowData),
        },
        text
      )
    },
  },
  {
    key: 'expression',
    title: '表达式',
    dataKey: 'expression',
    width: 160,
    cellRenderer: ({ cellData, rowData }) => {
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
          default: () =>
            h(
              'span',
              {
                class: 'cell-expr',
                title: '双击查看完整表达式',
                onDblclick: () => openExpr(rowData),
              },
              text
            ),
        }
      )
    },
  },
  {
    key: 'ic',
    title: 'IC',
    dataKey: 'ic',
    width: 100,
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
    width: 100,
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
    width: 100,
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
    width: 100,
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
    width: 100,
    align: 'center',
    cellRenderer: ({ rowData }) => {
      const active = rowData.status === 'active'
      return h('span', { class: `badge ${active ? 'badge--success' : 'badge--muted'}` }, active ? '启用' : '禁用')
    },
    headerCellRenderer: () => {
      return h(
        ElTooltip,
        { content: METRIC_TIPS.status, placement: 'top', effect: 'dark' },
        {
          default: () => h('span', { class: 'th-tip' }, '状态'),
        }
      )
    },
  },
  {
    key: 'actions',
    title: '操作',
    width: 300,
    align: 'center',
    cellRenderer: ({ rowData }) => {
      return h('div', { style: 'display:flex;gap:4px;justify-content:center' }, [
        h(ElButton, { link: true, type: 'primary', size: 'small', onClick: () => onEvaluate(rowData) }, () => '评价'),
        h(ElButton, { link: true, type: 'success', size: 'small', onClick: () => onQuantile(rowData) }, () => '分层'),
        h(
          ElButton,
          { link: true, type: 'primary', size: 'small', onClick: () => onDeepAnalysis(rowData) },
          () => '深度分析'
        ),
        h(
          ElButton,
          { link: true, type: 'warning', size: 'small', onClick: () => onNeutralize(rowData) },
          () => '中性化'
        ),
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

// 检测因子衰减：调用 /factors/decay-check，标记衰减行
async function onDecayCheck() {
  decayChecking.value = true
  try {
    const data = await decayCheck()
    const map = {}
    ;(data?.decaying_factors || []).forEach((f) => {
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

// 行样式：衰减因子标红 + 条纹
function rowClass({ rowData, rowIndex }) {
  const classes = []
  if (decayMap.value[rowData.id]) classes.push('row--decaying')
  if (rowIndex % 2 === 1) classes.push('row--striped')
  return classes.join(' ')
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
function onDisable(row) {
  disableDialog.value = { visible: true, target: row }
}

async function confirmDisable() {
  const row = disableDialog.value.target
  if (!row) return
  disabling.value = true
  try {
    await factorStore.remove(row.id)
    ElMessage.success(`因子「${row.name}」已禁用`)
  } catch {
    ElMessage.error(`禁用因子「${row.name}」失败`)
  } finally {
    disabling.value = false
    disableDialog.value = { visible: false, target: null }
  }
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
  flex: 1;
  min-width: 0;
}
.page-header__title {
  margin: 0 0 var(--space-xs);
  font-size: var(--font-size-2xl);
  font-weight: 700;
  color: var(--text-primary);
  line-height: var(--line-height-tight);
}
.page-header__subtitle {
  margin: 0;
  font-size: var(--font-size-sm);
  color: var(--text-secondary);
}
.page-header__actions {
  display: flex;
  align-items: center;
  gap: var(--space-sm);
}

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

// 过滤工具栏
.filter-toolbar {
  display: flex;
  align-items: center;
  gap: 12px;
}
.filter-toolbar__select {
  width: 140px;
}
.filter-toolbar__select--mid {
  width: 120px;
}
.filter-toolbar__search {
  width: 260px;
}
.filter-toolbar__spacer {
  flex: 1;
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

// 单元格内容样式（cellRenderer 在 el-table-v2 子组件内渲染，scoped 需用 :deep 才能命中）
:deep(.cell-name) {
  font-weight: var(--font-weight-medium);
  color: var(--text-primary);
}
:deep(.cell-expr) {
  font-family: var(--font-mono);
  font-size: var(--font-size-sm);
  // 解决白底白字：用 --text-secondary（更深）并加柔和背景，避免对比度不足
  color: var(--text-secondary);
  background-color: var(--bg-tertiary);
  padding: 2px 6px;
  border-radius: 4px;
  display: inline-block;
  max-width: 100%;
  min-width: 0;
  cursor: pointer;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  word-break: keep-all;
  transition:
    color 0.15s,
    background-color 0.15s;
  &:hover {
    color: var(--text-primary);
    background-color: var(--bg-hover);
  }
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
:deep(.cell-desc--clickable) {
  cursor: pointer;
  transition: color 0.15s;

  &:hover {
    color: var(--primary);
  }
}
:deep(.num) {
  font-family: var(--font-mono);
  font-size: var(--font-size-sm);
  color: var(--text-primary);

  &.is-positive {
    color: var(--success);
  }
  &.is-negative {
    color: var(--danger);
  }
  &.is-warning {
    color: var(--warning);
  }
}

// 完整表达式查看弹窗
.expr-viewer {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.expr-viewer__code {
  margin: 0;
  padding: 12px;
  background: var(--bg-tertiary);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  font-family: var(--font-mono);
  font-size: 13px;
  color: var(--text-primary);
  white-space: pre-wrap;
  word-break: break-all;
  max-height: 320px;
  overflow: auto;
}
.expr-viewer__desc {
  margin: 0;
  font-size: var(--font-size-sm);
  color: var(--text-secondary);
}

// AI 因子解释弹窗
.ai-ctx {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  background: var(--bg-tertiary);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  margin-bottom: 12px;
}
.ai-ctx__label {
  flex-shrink: 0;
  font-size: var(--font-size-xs);
  color: var(--text-tertiary);
  font-weight: var(--font-weight-semibold);
}
.ai-ctx__expr {
  font-family: var(--font-mono);
  font-size: var(--font-size-sm);
  color: var(--text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ai-explain {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.ai-explain__summary {
  font-size: 15px;
  font-weight: var(--font-weight-semibold);
  color: var(--text-primary);
  padding: 10px 12px;
  background: rgba(var(--primary-rgb), 0.08);
  border-radius: var(--radius-md);
}
.ai-explain__section {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.ai-explain__label {
  font-size: var(--font-size-sm);
  font-weight: var(--font-weight-semibold);
  color: var(--primary);
}
.ai-explain__text {
  font-size: var(--font-size-sm);
  color: var(--text-primary);
  line-height: 1.7;
  white-space: pre-wrap;
  word-break: break-word;
}
.ai-explain__caveats {
  margin: 0;
  padding-left: 18px;
  font-size: var(--font-size-sm);
  color: var(--text-primary);
  line-height: 1.7;
}
.ai-explain__meta {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: var(--font-size-xs);
  color: var(--text-tertiary);
  border-top: 1px dashed var(--border);
  padding-top: 8px;
}

// 追问对话区
.ai-chat {
  margin-top: 8px;
  max-height: 260px;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 4px 0;
}
.ai-chat__empty {
  padding: 18px 12px;
  text-align: center;
  font-size: var(--font-size-sm);
  color: var(--text-tertiary);
  border: 1px dashed var(--border);
  border-radius: var(--radius-md);
  line-height: 1.7;
}
.ai-chat__msg {
  display: flex;
}
.ai-chat__msg--user {
  justify-content: flex-end;
}
.ai-chat__msg--assistant {
  justify-content: flex-start;
}
.ai-chat__bubble {
  max-width: 82%;
  padding: 8px 12px;
  border-radius: var(--radius-md);
  font-size: var(--font-size-sm);
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
}
.ai-chat__msg--user .ai-chat__bubble {
  background: var(--primary);
  color: #fff;
  border-top-right-radius: 2px;
}
.ai-chat__msg--assistant .ai-chat__bubble {
  background: var(--bg-tertiary);
  color: var(--text-primary);
  border-top-left-radius: 2px;
}
.ai-chat__bubble--typing {
  color: var(--text-tertiary);
  font-style: italic;
}
.ai-chat__input {
  display: flex;
  gap: 8px;
  margin-top: 8px;
}

// Badge（模板与 cellRenderer 均使用，用 :deep 让表格单元格内的 badge 也生效）
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

// 表头
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

// 单元格内边距 12px
.factor-table :deep(.el-table-v2__cell) {
  padding: 0 12px;
}

// 行 hover
.factor-table :deep(.el-table-v2__row:hover) {
  background: var(--bg-hover);
}

// 条纹行（通过 rowClass 添加 .row--striped）
.factor-table :deep(.el-table-v2__row.row--striped) {
  background: var(--bg-secondary);
}

// 衰减因子行标红
.factor-table :deep(.el-table-v2__row.row--decaying) {
  background: rgba(210, 69, 69, 0.08) !important;
}
.factor-table :deep(.el-table-v2__row.row--decaying:hover) {
  background: rgba(210, 69, 69, 0.14) !important;
}

// 表头 tooltip 容器：保持表头可点击排序，hover 时显示提示
:deep(.th-tip) {
  display: inline-block;
  cursor: help;
  border-bottom: 1px dashed var(--text-tertiary);
  padding-bottom: 1px;
}

// 禁用确认弹窗：目标因子展示
.disable-target {
  display: flex;
  align-items: center;
  gap: var(--space-xs);
}
.mono {
  font-family: var(--font-mono);
  color: var(--text-primary);
}
</style>
