<template>
  <PageContainer>
    <!-- 页头：因子名 + 状态 + 操作（沿用回测详情样式） -->
    <div class="jq-head">
      <div class="jq-head__top">
        <span class="jq-head__back" @click="goList">← 返回因子库</span>
        <span class="jq-head__name">{{ factor?.name || factorName || '因子详情' }}</span>
        <span v-if="factor" class="jq-head__id">#{{ factor.id }}</span>
        <div class="jq-head__tags" v-if="factor">
          <span class="badge" :class="`badge--${categoryBadge(factor.category)}`">{{ categoryLabel(factor.category) }}</span>
          <span class="badge" :class="factor.status === 'active' ? 'badge--success' : 'badge--muted'">
            {{ factor.status === 'active' ? '启用' : '禁用' }}
          </span>
        </div>
        <div class="jq-head__actions">
          <el-button size="small" @click="scrollTo('sec-ai')">AI 解读</el-button>
          <el-button size="small" :loading="quantileLoading" @click="runQuantile">分层评价</el-button>
          <el-button size="small" :loading="evaluating" @click="onEvaluate">补算指标</el-button>
          <el-button size="small" type="danger" plain :disabled="!factor || factor.status !== 'active'" @click="onDisable">
            禁用
          </el-button>
        </div>
      </div>
      <div class="jq-head__meta" v-if="factor">
        <span class="mono jq-head__expr" :title="factor.expression">{{ factor.expression }}</span>
        <span v-if="factor.description" class="jq-head__desc">{{ factor.description }}</span>
        <span>评价区间：<b>{{ factor.eval_start || '--' }} ~ {{ factor.eval_end || '--' }}</b></span>
        <span v-if="factor.evaluated_at">评价时间：<b>{{ formatTime(factor.evaluated_at) }}</b></span>
        <span v-if="factor.source_task_id">来源任务：<b>#{{ factor.source_task_id }}</b></span>
      </div>
    </div>

    <div v-loading="loading" class="jq-body">
      <template v-if="factor">
        <!-- 左侧章节菜单 -->
        <aside class="jq-side">
          <span
            v-for="s in sections"
            :key="s.id"
            class="jq-side__item"
            :class="{ 'jq-side__item--on': activeSection === s.id }"
            @click="scrollTo(s.id)"
            >{{ s.label }}</span>
        </aside>

        <!-- 主区 -->
        <div class="jq-main">
          <!-- 指标总览 -->
          <section id="sec-overview" class="jq-card">
            <h4 class="jq-card__title">指标总览</h4>
            <p v-if="!isEvaluated" class="jq-note">该因子尚未评价，点击页头「补算指标」或用列表页补算后查看。</p>
            <div class="jq-grid">
              <div v-for="m in metricCells" :key="m.label" class="jq-grid__item">
                <span class="jq-grid__label">{{ m.label }}</span>
                <b class="jq-grid__value" :class="m.cls">{{ m.value }}</b>
              </div>
            </div>
          </section>

          <!-- 分层收益 -->
          <section id="sec-quantile" class="jq-card">
            <div class="jq-card__bar">
              <h4 class="jq-card__title">分层收益</h4>
              <div class="jq-zoom">
                <span>分组：</span>
                <span
                  v-for="n in [3, 5, 10]"
                  :key="n"
                  class="jq-zoom__btn"
                  :class="{ 'jq-zoom__btn--on': quantileGroups === n }"
                  @click="changeQuantileGroups(n)"
                  >{{ n }}</span>
              </div>
            </div>
            <p v-if="!quantileResult && !quantileLoading" class="jq-note">点击页头「分层评价」运行分层收益计算。</p>
            <div v-loading="quantileLoading" class="jq-chart-box">
              <template v-if="quantileResult">
                <div class="jq-inline-metrics">
                  <span>分组数：{{ quantileResult.n_groups }}</span>
                  <span>
                    单调性评分：
                    <b :class="monotonicityClass">{{ quantileResult.monotonicity_score?.toFixed(2) ?? '--' }}</b>
                  </span>
                  <span>
                    多空净值：
                    <b class="mono">{{ longShortNav }}</b>
                  </span>
                  <span>区间：{{ quantileResult.dates?.[0] }} ~ {{ quantileResult.dates?.[quantileResult.dates.length - 1] }}</span>
                </div>
                <VChart :option="quantileOption" autoresize class="jq-chart" />
              </template>
              <el-empty v-else-if="!quantileLoading" description="暂无分层收益数据" :image-size="72" />
            </div>
          </section>

          <!-- IC 分析 -->
          <section id="sec-ic" class="jq-card">
            <div class="jq-card__bar">
              <h4 class="jq-card__title">IC 分析</h4>
              <div class="jq-zoom">
                <span>horizon：</span>
                <span
                  v-for="h in [1, 5, 20]"
                  :key="h"
                  class="jq-zoom__btn"
                  :class="{ 'jq-zoom__btn--on': horizon === h }"
                  @click="changeHorizon(h)"
                  >{{ h }}</span>
              </div>
              <span class="jq-switch">
                <el-button size="small" :loading="deepLoading" @click="runDeepAnalysis">刷新分析</el-button>
              </span>
            </div>
            <div v-loading="deepLoading" class="jq-chart-pair">
              <div class="jq-chart-box">
                <p class="jq-note">IC 时序：日 IC（浅色）与 60 日均线（深色），虚线为 0 轴</p>
                <el-empty v-if="!hasIcTs && !deepLoading" description="暂无 IC 时序" :image-size="64" />
                <VChart v-else :option="icTimeseriesOption" autoresize class="jq-chart jq-chart--sub" />
              </div>
              <div class="jq-chart-box">
                <p class="jq-note">IC 分布：直方图，虚线为 IC 均值</p>
                <el-empty v-if="!hasIcDist && !deepLoading" description="暂无 IC 分布" :image-size="64" />
                <VChart v-else :option="icDistOption" autoresize class="jq-chart jq-chart--sub" />
              </div>
            </div>
          </section>

          <!-- 分层净值 -->
          <section id="sec-hold" class="jq-card">
            <h4 class="jq-card__title">分层净值</h4>
            <p class="jq-note">Q1（红）→ Q{{ quantileGroups }}（绿）分组净值与多空曲线（深色粗线）</p>
            <div v-loading="deepLoading" class="jq-chart-box">
              <el-empty v-if="!hasQuantileNav && !deepLoading" description="暂无分层净值数据" :image-size="72" />
              <VChart v-else :option="quantileNavOption" autoresize class="jq-chart" />
            </div>
          </section>

          <!-- 换手率与 IC 衰减 -->
          <section id="sec-turnover" class="jq-card">
            <h4 class="jq-card__title">换手率与 IC 衰减</h4>
            <div v-loading="deepLoading" class="jq-chart-pair">
              <div class="jq-chart-box">
                <p class="jq-note">换手率曲线，虚线为平均换手率</p>
                <el-empty v-if="!hasTurnover && !deepLoading" description="暂无换手率数据" :image-size="64" />
                <VChart v-else :option="turnoverOption" autoresize class="jq-chart jq-chart--sub" />
              </div>
              <div class="jq-chart-box">
                <p class="jq-note">IC 衰减曲线，阴影区为 IC &gt; 0.03 的有效区间</p>
                <el-empty v-if="!hasDecay && !deepLoading" description="暂无 IC 衰减数据" :image-size="64" />
                <VChart v-else :option="decayOption" autoresize class="jq-chart jq-chart--sub" />
              </div>
            </div>
          </section>

          <!-- 中性化 -->
          <section id="sec-neutralize" class="jq-card">
            <div class="jq-card__bar">
              <h4 class="jq-card__title">中性化</h4>
              <div class="jq-zoom">
                <span>方法：</span>
                <el-radio-group v-model="neutralizeMethod" size="small">
                  <el-radio-button value="market_cap">市值</el-radio-button>
                  <el-radio-button value="industry">行业+市值</el-radio-button>
                  <el-radio-button value="both">两者</el-radio-button>
                </el-radio-group>
              </div>
              <span class="jq-switch">
                <el-button size="small" :loading="neutralizeLoading" @click="fetchNeutralize">运行对比</el-button>
              </span>
            </div>
            <p class="jq-note">对比中性化前后的 IC / RankIC / ICIR / IR。</p>
            <div v-loading="neutralizeLoading" style="min-height: 120px">
              <el-table v-if="neutralizeResult" :data="neutralizeTableData" border size="small" style="width: 100%">
                <el-table-column prop="metric" label="指标" width="120" />
                <el-table-column prop="before" label="中性化前" align="right" />
                <el-table-column prop="after" label="中性化后" align="right" />
                <el-table-column prop="delta" label="变化" align="right" />
              </el-table>
              <el-empty v-else-if="!neutralizeLoading" description="点击「运行对比」查看中性化效果" :image-size="64" />
            </div>
          </section>

          <!-- AI 解读 -->
          <section id="sec-ai" class="jq-card">
            <div class="jq-card__bar">
              <h4 class="jq-card__title">AI 解读</h4>
              <span class="jq-switch">
                <el-button
                  v-if="aiDetail?.explanation?.generated_at"
                  link
                  type="primary"
                  size="small"
                  :loading="aiGenLoading"
                  @click="onRegenerateAiExplain"
                  >重新生成</el-button>
                <el-button
                  v-else
                  size="small"
                  type="primary"
                  :loading="aiGenLoading"
                  @click="onGenerateAiExplain"
                  >生成解读</el-button>
              </span>
            </div>
            <div v-loading="aiDetailLoading" class="ai-explain" style="min-height: 80px">
              <template v-if="!aiDetailLoading && aiDetail?.explanation">
                <div class="ai-explain__summary" v-html="renderMarkdown(aiDetail.explanation.summary)"></div>
                <el-collapse v-model="aiOpenSections" class="ai-explain__collapse">
                  <el-collapse-item name="logic">
                    <template #title><span class="ai-explain__label">它怎么构造</span></template>
                    <div class="ai-explain__text ai-explain__markdown" v-html="renderMarkdown(aiDetail.explanation.logic)"></div>
                  </el-collapse-item>
                  <el-collapse-item name="rationale">
                    <template #title><span class="ai-explain__label">为什么可能有效</span></template>
                    <div class="ai-explain__text ai-explain__markdown" v-html="renderMarkdown(aiDetail.explanation.rationale)"></div>
                  </el-collapse-item>
                  <el-collapse-item v-if="aiDetail.explanation.caveats?.length" name="caveats">
                    <template #title><span class="ai-explain__label">使用时注意</span></template>
                    <ul class="ai-explain__caveats">
                      <li
                        v-for="(c, i) in aiDetail.explanation.caveats"
                        :key="i"
                        class="ai-explain__markdown"
                        v-html="renderMarkdown(c)"
                      ></li>
                    </ul>
                  </el-collapse-item>
                </el-collapse>
                <div class="ai-explain__meta">
                  <span v-if="aiDetail.explanation.generated_at">生成于 {{ timeAgo(aiDetail.explanation.generated_at) }}</span>
                </div>
              </template>
              <el-empty
                v-else-if="!aiDetailLoading"
                description="这个因子还没有 AI 解读，点击「生成解读」创建一份"
                :image-size="80"
              />
            </div>

            <!-- 追问对话区 -->
            <div v-if="aiDetail?.explanation" class="ai-chat">
              <div class="ai-chat__scroll" ref="aiChatRef">
                <div v-if="!aiChatMessages.length" class="ai-chat__empty">
                  想深入了解这个因子？直接问它，比如「适合什么股票池？」「和动量类因子有什么区别？」
                </div>
                <div v-for="(m, i) in aiChatMessages" :key="i" class="ai-chat__msg" :class="'ai-chat__msg--' + m.role">
                  <div
                    v-if="m.role === 'assistant'"
                    class="ai-chat__bubble ai-chat__bubble--md ai-explain__markdown"
                    v-html="renderMarkdown(m.content)"
                  ></div>
                  <div v-else class="ai-chat__bubble">{{ m.content }}</div>
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
                <el-button type="primary" :loading="aiChatting" :disabled="!aiQuestion.trim()" @click="onSendChat">发送</el-button>
              </div>
            </div>
          </section>

          <!-- 因子信息 -->
          <section id="sec-params" class="jq-card">
            <h4 class="jq-card__title">因子信息</h4>
            <div class="jq-params">
              <div v-for="p in paramCells" :key="p.label" class="jq-params__item">
                <span>{{ p.label }}</span>
                <b>{{ p.value }}</b>
              </div>
            </div>
            <p class="jq-note" v-if="factor.decay">衰减曲线（lag → IC）：{{ JSON.stringify(factor.decay) }}</p>
            <p class="jq-note" v-if="factor.ic_by_horizon">IC by horizon：{{ JSON.stringify(factor.ic_by_horizon) }}</p>
          </section>
        </div>
      </template>
    </div>

    <!-- 禁用确认弹窗 -->
    <el-dialog v-model="disableOpen" title="禁用因子" width="440px">
      <p>禁用后该因子不会进入策略组合（保留评价数据）。确认禁用「{{ factor?.name }}」？</p>
      <template #footer>
        <el-button @click="disableOpen = false">取消</el-button>
        <el-button type="danger" :loading="disabling" @click="confirmDisable">确认禁用</el-button>
      </template>
    </el-dialog>
  </PageContainer>
</template>

<script setup>
defineOptions({ name: 'FactorDetail' })
import { ref, computed, onMounted, onBeforeUnmount, nextTick } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus/es/components/message/index'
import VChart from 'vue-echarts'
import '@/utils/echarts'
import PageContainer from '@/components/common/PageContainer.vue'
import { getFactor, getQuantileAnalysis, neutralizeFactor, aiExplainFactor, getFactorAiDetail, chatFactorAi, backfillAlpha158Metrics } from '@/api/factor'
import { deepAnalysis } from '@/api/quant'
import { fmt, numClass, formatTime } from '@/utils/format'
import { renderMarkdown } from '@/utils/markdown'
import { chartTheme, quantileGradient } from '@/utils/chartTheme'
import { useThemeRev } from '@/composables/useChartTheme'
import { useFactorStore } from '@/stores/factor'

const themeRev = useThemeRev()
const route = useRoute()
const router = useRouter()
const factorStore = useFactorStore()

const factorId = computed(() => Number(route.params.id))
const factorName = computed(() => (route.query.name ? String(route.query.name) : ''))

const loading = ref(true)
const factor = ref(null)
const evaluating = ref(false)
const disabling = ref(false)
const disableOpen = ref(false)

// 懒加载标记：重章节进入视口才拉取数据，避免挂载时 3 个重请求并发拖慢首屏
const deepLoaded = ref(false)
const quantileLoaded = ref(false)
const aiLoaded = ref(false)

// 分析参数
const horizon = ref(5)
const quantileGroups = ref(5)
const icWindow = 60

// === 数据状态 ===
const deepLoading = ref(false)
const deepResult = ref(null)
const quantileLoading = ref(false)
const quantileResult = ref(null)
const neutralizeLoading = ref(false)
const neutralizeResult = ref(null)
const neutralizeMethod = ref('market_cap')

// === AI 解读 ===
const aiDetailLoading = ref(false)
const aiDetail = ref(null)
const aiGenLoading = ref(false)
const aiChatting = ref(false)
const aiQuestion = ref('')
const aiOpenSections = ref(['logic', 'rationale', 'caveats'])
const aiChatRef = ref(null)
const aiChatMessages = computed(() => aiDetail.value?.chat_history || [])

// === 章节菜单 ===
const activeSection = ref('sec-overview')
const allSections = [
  { id: 'sec-overview', label: '指标总览' },
  { id: 'sec-quantile', label: '分层收益' },
  { id: 'sec-ic', label: 'IC 分析' },
  { id: 'sec-hold', label: '分层净值' },
  { id: 'sec-turnover', label: '换手与衰减' },
  { id: 'sec-neutralize', label: '中性化' },
  { id: 'sec-ai', label: 'AI 解读' },
  { id: 'sec-params', label: '因子信息' },
]
const sections = allSections
let sectionObserver = null

async function loadFactor() {
  loading.value = true
  try {
    factor.value = await getFactor(factorId.value)
  } catch (e) {
    ElMessage.error('加载因子失败：' + (e?.message || e))
  } finally {
    loading.value = false
    await nextTick()
    setupSectionObserver()
  }
}

async function loadAll() {
  await loadFactor()
}

// 滚动联动：section 进入视口时同步左侧菜单高亮，并懒加载对应重章节数据
function setupSectionObserver() {
  sectionObserver?.disconnect()
  if (typeof IntersectionObserver === 'undefined') return
  sectionObserver = new IntersectionObserver(
    (entries) => {
      for (const e of entries) {
        if (!e.isIntersecting) continue
        activeSection.value = e.target.id
        const id = e.target.id
        // 分层收益 → 分层评价；IC/分层净值/换手衰减 → 深度分析；AI 解读
        if (id === 'sec-quantile' && !quantileLoaded.value) runQuantile()
        if ((id === 'sec-ic' || id === 'sec-hold' || id === 'sec-turnover') && !deepLoaded.value) runDeepAnalysis()
        if (id === 'sec-ai' && !aiLoaded.value) loadAiDetail()
      }
    },
    { rootMargin: '-15% 0px -75% 0px' }
  )
  for (const s of sections) {
    const el = document.getElementById(s.id)
    if (el) sectionObserver.observe(el)
  }
}
onBeforeUnmount(() => {
  sectionObserver?.disconnect()
  sectionObserver = null
})

// === 评价区间（默认用因子自身评价区间，缺失用平台默认） ===
const period = computed(() => {
  const f = factor.value || {}
  return { start: f.eval_start || '2020-01-01', end: f.eval_end || '2024-12-31' }
})

async function runDeepAnalysis() {
  if (!factor.value || deepLoading.value) return
  deepLoaded.value = true
  deepLoading.value = true
  try {
    const data = await deepAnalysis(factorId.value, {
      start_date: period.value.start,
      end_date: period.value.end,
      horizon: horizon.value,
      n_groups: quantileGroups.value,
      ic_window: icWindow,
    })
    deepResult.value = data || {}
  } catch (e) {
    ElMessage.error('深度分析失败：' + (e?.message || e))
    deepResult.value = null
  } finally {
    deepLoading.value = false
  }
}

async function runQuantile() {
  if (!factor.value || quantileLoading.value) return
  quantileLoaded.value = true
  quantileLoading.value = true
  try {
    const data = await getQuantileAnalysis(factorId.value, { n_groups: quantileGroups.value })
    quantileResult.value = data || {}
  } catch (e) {
    ElMessage.error('分层收益计算失败：' + (e?.message || e))
    quantileResult.value = null
  } finally {
    quantileLoading.value = false
  }
}

function changeHorizon(h) {
  if (horizon.value === h) return
  horizon.value = h
  runDeepAnalysis()
}

function changeQuantileGroups(n) {
  if (quantileGroups.value === n) return
  quantileGroups.value = n
  runQuantile()
  runDeepAnalysis()
}

async function fetchNeutralize() {
  if (!factor.value) return
  neutralizeLoading.value = true
  try {
    const data = await neutralizeFactor(factorId.value, { method: neutralizeMethod.value })
    neutralizeResult.value = data || {}
  } catch (e) {
    ElMessage.error('中性化分析失败：' + (e?.message || e))
  } finally {
    neutralizeLoading.value = false
  }
}

// === AI 解读 ===
async function loadAiDetail() {
  aiLoaded.value = true
  aiDetailLoading.value = true
  try {
    aiDetail.value = await getFactorAiDetail(factorId.value)
  } catch (e) {
    ElMessage.error('加载 AI 解读失败：' + (e?.message || e))
  } finally {
    aiDetailLoading.value = false
  }
}

async function onGenerateAiExplain() {
  aiGenLoading.value = true
  try {
    await aiExplainFactor(factorId.value, false)
    factorStore.invalidate()
    await loadAiDetail()
    ElMessage.success('AI 解读已生成')
  } catch {
    /* 拦截器已提示 */
  } finally {
    aiGenLoading.value = false
  }
}

async function onRegenerateAiExplain() {
  aiGenLoading.value = true
  try {
    await aiExplainFactor(factorId.value, true)
    factorStore.invalidate()
    aiQuestion.value = ''
    await loadAiDetail()
    ElMessage.success('AI 解读已重新生成')
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
    const data = await chatFactorAi(factorId.value, q)
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

// === 单因子评价（补算） ===
async function onEvaluate() {
  if (!factor.value) return
  evaluating.value = true
  try {
    const data = await backfillAlpha158Metrics([factorId.value], {})
    const failed = Number(data?.eval_failed ?? data?.failed ?? 0)
    const okCount = Number(data?.evaluated ?? 0)
    if (failed > 0) {
      ElMessage.warning(`${factor.value.name} 补算失败 ${failed}/${okCount + failed}`)
    } else {
      ElMessage.success(`${factor.value.name} 补算完成 ${okCount}`)
    }
    factorStore.invalidate()
    await loadFactor()
  } catch (e) {
    ElMessage.error(`${factor.value.name} 补算失败：${e?.message || e}`)
  } finally {
    evaluating.value = false
  }
}

// === 禁用 ===
function onDisable() {
  disableOpen.value = true
}
async function confirmDisable() {
  disabling.value = true
  try {
    await factorStore.remove(factorId.value)
    ElMessage.success(`因子「${factor.value.name}」已禁用`)
    disableOpen.value = false
    await loadFactor()
  } catch {
    ElMessage.error('禁用失败')
  } finally {
    disabling.value = false
  }
}

// === 格式化 ===
const isEvaluated = computed(() => factor.value?.ic != null && !Number.isNaN(Number(factor.value.ic)))
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

const metricCells = computed(() => {
  const f = factor.value || {}
  const cls = (v) => numClass(v)
  return [
    { label: 'IC', value: fmt(f.ic, 3), cls: cls(f.ic) },
    { label: 'RankIC', value: fmt(f.rank_ic, 3), cls: cls(f.rank_ic) },
    { label: 'ICIR', value: fmt(f.icir, 2), cls: cls(f.icir) },
    { label: 'IR', value: fmt(f.ir, 2), cls: cls(f.ir) },
    { label: '正交IC', value: fmt(f.orthogonal_ic, 3), cls: cls(f.orthogonal_ic) },
    { label: '换手率', value: f.turnover != null ? (f.turnover * 100).toFixed(1) + '%' : '--', cls: '' },
    { label: '评价区间', value: `${f.eval_start || '--'} ~ ${f.eval_end || '--'}`, cls: '' },
    { label: '评价时间', value: f.evaluated_at ? formatTime(f.evaluated_at) : '--', cls: '' },
    { label: '状态', value: f.status === 'active' ? '启用' : '禁用', cls: '' },
    { label: 'ID', value: f.id != null ? String(f.id) : '--', cls: '' },
  ]
})

const longShortNav = computed(() => {
  const arr = quantileResult.value?.long_short_nav
  if (!arr || !arr.length) return '--'
  return arr[arr.length - 1].toFixed(2)
})
const monotonicityClass = computed(() => {
  const s = quantileResult.value?.monotonicity_score
  if (s == null) return ''
  return s > 0 ? 'num-up' : 'num-down'
})

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
      before: b != null ? Number(b).toFixed(2) : '—',
      after: a != null ? Number(a).toFixed(2) : '—',
      delta: delta != null ? (delta >= 0 ? '+' : '') + delta.toFixed(2) : '—',
    }
  })
})

const paramCells = computed(() => {
  const f = factor.value || {}
  return [
    { label: '名称', value: f.name || '--' },
    { label: 'ID', value: f.id != null ? String(f.id) : '--' },
    { label: '类别', value: categoryLabel(f.category) },
    { label: '状态', value: f.status === 'active' ? '启用' : '禁用' },
    { label: '创建时间', value: f.created_at ? formatTime(f.created_at) : '--' },
    { label: '评价时间', value: f.evaluated_at ? formatTime(f.evaluated_at) : '--' },
    { label: '评价区间', value: `${f.eval_start || '--'} ~ ${f.eval_end || '--'}` },
    { label: '来源任务', value: f.source_task_id != null ? '#' + f.source_task_id : '--' },
    { label: '表达式', value: f.expression || '--' },
    { label: '描述', value: f.description || '--' },
  ]
})

// === 图表（复用深度分析数据解析） ===
const colors = [
  chartTheme.primary(),
  chartTheme.success(),
  chartTheme.danger(),
  chartTheme.warning(),
  chartTheme.info(),
]

// 大数据降采样：超过 max 点时按索引等距抽取（保留首尾），只用于展示，减轻 ECharts 渲染/动画负担
function downsample(arr, max = 600) {
  if (!arr || arr.length <= max) return arr
  const step = (arr.length - 1) / (max - 1)
  const out = []
  for (let i = 0; i < max; i++) out.push(arr[Math.round(i * step)])
  out[max - 1] = arr[arr.length - 1]
  return out
}

const icTimeseries = computed(() => {
  const d = deepResult.value?.ic_timeseries
  if (!d) return { dates: [], ic: [] }
  if (Array.isArray(d)) {
    return { dates: d.map((x) => x.date || x.ts || x.time || ''), ic: d.map((x) => Number(x.ic ?? x.value)) }
  }
  const dates = d.dates || d.x || []
  const ic = d.ic_series || d.ic || d.values || d.y || []
  return { dates, ic: ic.map(Number) }
})
const icDistribution = computed(() => {
  const d = deepResult.value?.ic_distribution
  if (!d) return { bins: [], counts: [] }
  if (Array.isArray(d)) {
    return { bins: d.map((x) => x.bin ?? x.label ?? x.x ?? ''), counts: d.map((x) => Number(x.count ?? x.freq ?? x.y ?? 0)) }
  }
  const bins = d.bins || d.edges || d.labels || d.x || []
  const counts = d.counts || d.freq || d.frequencies || d.y || []
  return { bins, counts: counts.map(Number) }
})
const quantileReturns = computed(() => {
  const d = deepResult.value?.quantile_returns
  if (!d) return { dates: [], groups: {}, longShort: [] }
  const dates = d.dates || d.x || []
  const groups = d.quantile_nav || d.group_nav || d.groups || d.quantiles || d.nav || {}
  const longShort = d.long_short_nav || d.long_short || d.ls_nav || []
  return { dates, groups, longShort: longShort.map(Number) }
})
const turnoverCurve = computed(() => {
  const d = deepResult.value?.turnover_curve
  if (!d) return { dates: [], turnover: [] }
  if (Array.isArray(d)) {
    return { dates: d.map((x) => x.date || x.ts || ''), turnover: d.map((x) => Number(x.turnover ?? x.value ?? 0)) }
  }
  const dates = d.dates || d.x || []
  const turnover = d.turnover_series || d.turnover || d.values || d.y || []
  return { dates, turnover: turnover.map(Number) }
})
const decay = computed(() => {
  const d = deepResult.value?.decay
  if (!d) return { lags: [], ic: [] }
  if (Array.isArray(d)) {
    return { lags: d.map((x) => Number(x.lag ?? x.lag_days ?? x.x ?? 0)), ic: d.map((x) => Number(x.ic ?? x.value ?? x.y ?? 0)) }
  }
  const lags = d.lags || d.lag || d.x || []
  const ic = d.ic_by_lag || d.ic || d.values || d.y || []
  return { lags: lags.map(Number), ic: ic.map(Number) }
})

const hasIcTs = computed(() => icTimeseries.value.ic.length > 0)
const hasIcDist = computed(() => icDistribution.value.counts.length > 0)
const hasQuantileNav = computed(() => quantileReturns.value.dates.length > 0)
const hasTurnover = computed(() => turnoverCurve.value.turnover.length > 0)
const hasDecay = computed(() => decay.value.ic.length > 0)

function binCenter(b) {
  if (typeof b === 'number') return b
  const nums = String(b).match(/-?\d+\.?\d*/g)
  if (nums && nums.length >= 2) return (Number(nums[0]) + Number(nums[1])) / 2
  if (nums && nums.length === 1) return Number(nums[0])
  return NaN
}

// IC 时序：日 IC 浅色 + 60 日均线深色 + 0 轴（>600 点降采样）
const icTimeseriesOption = computed(() => {
  void themeRev.value
  const raw = icTimeseries.value
  const win = icWindow
  const ma = []
  for (let i = 0; i < raw.ic.length; i++) {
    const start = Math.max(0, i - win + 1)
    const slice = raw.ic.slice(start, i + 1).filter((v) => !Number.isNaN(v))
    ma.push(slice.length ? Number((slice.reduce((a, b) => a + b, 0) / slice.length).toFixed(6)) : null)
  }
  const dates = downsample(raw.dates, 600)
  const ic = downsample(raw.ic, 600)
  const maD = downsample(ma, 600)
  return {
    backgroundColor: 'transparent',
    animation: false,
    tooltip: { trigger: 'axis' },
    textStyle: { color: chartTheme.axisText() },
    legend: { top: 0, data: ['日 IC', win + '日均线'], textStyle: { color: chartTheme.axisText() } },
    grid: { left: 50, right: 20, top: 30, bottom: 24 },
    xAxis: { type: 'category', data: dates, boundaryGap: false, axisLabel: { hideOverlap: true, color: chartTheme.axisText() } },
    yAxis: { type: 'value', name: 'IC', scale: true, axisLabel: { color: chartTheme.axisText() } },
    dataZoom: [{ type: 'inside' }],
    series: [
      {
        name: '日 IC',
        type: 'line',
        data: ic,
        showSymbol: false,
        lineStyle: { width: 1, color: chartTheme.line() },
        itemStyle: { color: chartTheme.line() },
        markLine: {
          silent: true,
          symbol: 'none',
          lineStyle: { color: chartTheme.neutral(), type: 'dashed' },
          data: [{ yAxis: 0 }],
        },
      },
      {
        name: win + '日均线',
        type: 'line',
        data: maD,
        showSymbol: false,
        smooth: true,
        lineStyle: { width: 2, color: colors[0] },
        itemStyle: { color: colors[0] },
      },
    ],
  }
})

// IC 分布直方图
const icDistOption = computed(() => {
  void themeRev.value
  const { bins, counts } = icDistribution.value
  const centers = bins.map(binCenter)
  const allNumeric = centers.length > 0 && centers.every((c) => !Number.isNaN(c))
  const icMean = Number(deepResult.value?.summary?.ic_mean)
  const base = { type: 'bar', barCategoryGap: '0%', itemStyle: { color: colors[4] } }
  if (allNumeric) {
    const data = centers.map((c, i) => [c, counts[i]])
    return {
      backgroundColor: 'transparent',
      animation: false,
      tooltip: { trigger: 'axis' },
      textStyle: { color: chartTheme.axisText() },
      grid: { left: 50, right: 20, top: 20, bottom: 24 },
      xAxis: { type: 'value', name: 'IC', axisLabel: { color: chartTheme.axisText() } },
      yAxis: { type: 'value', name: '频次', axisLabel: { color: chartTheme.axisText() } },
      series: [
        {
          ...base,
          data,
          markLine: !Number.isNaN(icMean)
            ? {
                silent: true,
                symbol: 'none',
                lineStyle: { color: colors[2], type: 'dashed', width: 2 },
                data: [{ xAxis: icMean, label: { formatter: '均值 ' + icMean.toFixed(2) } }],
              }
            : undefined,
        },
      ],
    }
  }
  return {
    backgroundColor: 'transparent',
    animation: false,
    tooltip: { trigger: 'axis' },
    textStyle: { color: chartTheme.axisText() },
    grid: { left: 50, right: 20, top: 20, bottom: 24 },
    xAxis: { type: 'category', data: bins.map(String), axisLabel: { color: chartTheme.axisText() } },
    yAxis: { type: 'value', name: '频次', axisLabel: { color: chartTheme.axisText() } },
    series: [{ ...base, data: counts }],
  }
})

// 分层收益：Q1-Q5 渐变 + 多空深色粗线（>600 点降采样）
const quantileOption = computed(() => {
  void themeRev.value
  const r = quantileResult.value
  if (!r) return {}
  const dates = downsample(r.dates || [], 600)
  const groupNav = r.group_nav || {}
  const n = r.n_groups || quantileGroups.value
  const series = []
  for (let g = 1; g <= n; g++) {
    const color = quantileGradient[(g - 1) % quantileGradient.length]
    series.push({
      name: 'Q' + g,
      type: 'line',
      data: downsample(groupNav[String(g)] || [], 600),
      smooth: true,
      showSymbol: false,
      lineStyle: { width: 1.5, color },
      itemStyle: { color },
    })
  }
  series.push({
    name: '多空',
    type: 'line',
    data: downsample(r.long_short_nav || [], 600),
    smooth: true,
    showSymbol: false,
    lineStyle: { width: 2.5, color: chartTheme.textPrimary(), type: 'dashed' },
    itemStyle: { color: chartTheme.textPrimary() },
  })
  return {
    backgroundColor: 'transparent',
    animation: false,
    tooltip: { trigger: 'axis', axisPointer: { type: 'cross' } },
    textStyle: { color: chartTheme.axisText() },
    legend: { top: 0, textStyle: { color: chartTheme.axisText() } },
    grid: { left: 50, right: 20, top: 30, bottom: 28 },
    xAxis: { type: 'category', data: dates, boundaryGap: false, axisLabel: { hideOverlap: true, color: chartTheme.axisText() } },
    yAxis: { type: 'value', name: '净值', scale: true, axisLabel: { color: chartTheme.axisText() } },
    dataZoom: [{ type: 'inside' }],
    series,
  }
})

// 分层净值（深度分析版）：Q1-Qn + 多空（>600 点降采样）
const quantileNavOption = computed(() => {
  void themeRev.value
  const { dates, groups, longShort } = quantileReturns.value
  const n = quantileGroups.value
  const series = []
  for (let g = 1; g <= n; g++) {
    const data = downsample((groups[String(g)] || []).map(Number), 600)
    const color = quantileGradient[(g - 1) % quantileGradient.length]
    series.push({
      name: 'Q' + g,
      type: 'line',
      data,
      showSymbol: false,
      lineStyle: { width: 1.5, color },
      itemStyle: { color },
    })
  }
  series.push({
    name: '多空',
    type: 'line',
    data: downsample(longShort, 600),
    showSymbol: false,
    lineStyle: { width: 2.5, color: chartTheme.textPrimary() },
    itemStyle: { color: chartTheme.textPrimary() },
  })
  return {
    backgroundColor: 'transparent',
    animation: false,
    tooltip: { trigger: 'axis', axisPointer: { type: 'cross' } },
    textStyle: { color: chartTheme.axisText() },
    legend: { top: 0, textStyle: { color: chartTheme.axisText() } },
    grid: { left: 50, right: 20, top: 30, bottom: 28 },
    xAxis: { type: 'category', data: dates, boundaryGap: false, axisLabel: { hideOverlap: true, color: chartTheme.axisText() } },
    yAxis: { type: 'value', name: '净值', scale: true, axisLabel: { color: chartTheme.axisText() } },
    dataZoom: [{ type: 'inside' }],
    series,
  }
})

// 换手率曲线：bar + markLine 平均换手率（>600 点降采样）
const turnoverOption = computed(() => {
  void themeRev.value
  const { dates, turnover } = turnoverCurve.value
  const valid = turnover.filter((v) => !Number.isNaN(v))
  const avg = valid.length ? valid.reduce((a, b) => a + b, 0) / valid.length : 0
  const dDates = downsample(dates, 600)
  const dData = downsample(
    turnover.map((v) => (Number.isNaN(v) ? null : Number((v * 100).toFixed(2)))),
    600
  )
  return {
    backgroundColor: 'transparent',
    animation: false,
    tooltip: {
      trigger: 'axis',
      formatter: (params) => {
        if (!params.length) return ''
        let s = params[0].axisValue + '<br/>'
        params.forEach((p) => {
          s += p.marker + p.seriesName + ': ' + (p.value != null ? p.value + '%' : '—') + '<br/>'
        })
        return s
      },
    },
    grid: { left: 60, right: 20, top: 16, bottom: 24 },
    xAxis: { type: 'category', data: dDates, axisLabel: { hideOverlap: true, color: chartTheme.axisText() } },
    yAxis: { type: 'value', name: '换手率%', axisLabel: { formatter: '{value}%', color: chartTheme.axisText() } },
    dataZoom: [{ type: 'inside' }],
    series: [
      {
        name: '换手率',
        type: 'bar',
        data: dData,
        itemStyle: { color: colors[3] },
        markLine: {
          silent: true,
          symbol: 'none',
          lineStyle: { color: colors[2], type: 'dashed' },
          data: [{ yAxis: Number((avg * 100).toFixed(2)), label: { formatter: '均值 ' + (avg * 100).toFixed(2) + '%' } }],
        },
      },
    ],
  }
})

// IC 衰减曲线：折线 + markArea 标 IC>0.03 区间
const decayOption = computed(() => {
  void themeRev.value
  const { lags, ic } = decay.value
  const data = lags.map((l, i) => [l, ic[i]])
  const areas = []
  let start = null
  for (let i = 0; i < lags.length; i++) {
    if (ic[i] > 0.03) {
      if (start === null) start = lags[i]
    } else if (start !== null) {
      areas.push([{ xAxis: start }, { xAxis: lags[i - 1] }])
      start = null
    }
  }
  if (start !== null && lags.length) {
    areas.push([{ xAxis: start }, { xAxis: lags[lags.length - 1] }])
  }
  return {
    backgroundColor: 'transparent',
    animation: false,
    tooltip: { trigger: 'axis' },
    grid: { left: 50, right: 20, top: 16, bottom: 24 },
    xAxis: { type: 'value', name: 'lag', minInterval: 1 },
    yAxis: { type: 'value', name: 'IC' },
    series: [
      {
        name: 'IC',
        type: 'line',
        data,
        smooth: true,
        showSymbol: true,
        lineStyle: { width: 2, color: colors[0] },
        itemStyle: { color: colors[0] },
        markLine: {
          silent: true,
          symbol: 'none',
          data: [
            { yAxis: 0, lineStyle: { color: chartTheme.neutral(), type: 'dashed' } },
            { yAxis: 0.03, lineStyle: { color: colors[1], type: 'dashed' }, label: { formatter: 'IC=0.03' } },
          ],
        },
        markArea: {
          silent: true,
          itemStyle: { color: chartTheme.successSoft() },
          data: areas,
        },
      },
    ],
  }
})

// === 导航 ===
function goList() {
  router.push('/quant/factor-library')
}
function scrollTo(id) {
  activeSection.value = id
  document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

onMounted(() => {
  if (factorId.value) loadAll()
})
</script>

<style scoped>
.jq-head {
  background: var(--el-bg-color, #fff);
  border: 1px solid var(--el-border-color-lighter, #e4e7ed);
  border-radius: 8px;
  padding: 14px 18px;
  margin-bottom: 14px;
}
.jq-head__top {
  display: flex;
  align-items: center;
  gap: 14px;
  flex-wrap: wrap;
}
.jq-head__name {
  font-size: 17px;
  font-weight: 600;
}
.jq-head__id {
  font-size: 12px;
  color: var(--el-text-secondary, #909399);
  font-family: var(--font-mono, monospace);
}
.jq-head__back {
  font-size: 13px;
  color: var(--el-text-secondary, #909399);
  cursor: pointer;
}
.jq-head__back:hover {
  color: var(--el-color-primary);
}
.jq-head__tags {
  display: flex;
  gap: 6px;
  align-items: center;
}
.jq-head__actions {
  margin-left: auto;
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}
.jq-head__meta {
  display: flex;
  align-items: center;
  gap: 18px;
  flex-wrap: wrap;
  margin-top: 10px;
  font-size: 13px;
  color: var(--el-text-secondary, #909399);
}
.jq-head__meta b {
  color: var(--el-text-primary, #303133);
  font-weight: 500;
}
.jq-head__expr {
  max-width: 46%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.jq-head__desc {
  max-width: 30%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.jq-body {
  display: grid;
  grid-template-columns: 150px 1fr;
  gap: 14px;
  min-height: 400px;
}
.jq-side {
  position: sticky;
  top: 12px;
  align-self: start;
  display: flex;
  flex-direction: column;
  gap: 2px;
  background: var(--el-bg-color, #fff);
  border: 1px solid var(--el-border-color-lighter, #e4e7ed);
  border-radius: 8px;
  padding: 8px;
}
.jq-side__item {
  font-size: 13px;
  color: var(--el-text-secondary, #909399);
  padding: 7px 12px;
  border-radius: 6px;
  cursor: pointer;
}
.jq-side__item:hover {
  background: var(--el-fill-color-light, #f5f7fa);
}
.jq-side__item--on {
  background: var(--el-color-primary);
  color: var(--text-inverse);
  font-weight: 500;
}
.jq-main {
  display: flex;
  flex-direction: column;
  gap: 14px;
  min-width: 0;
}
.jq-card {
  background: var(--el-bg-color, #fff);
  border: 1px solid var(--el-border-color-lighter, #e4e7ed);
  border-radius: 8px;
  padding: 14px 18px;
  scroll-margin-top: 12px;
}
.jq-card__title {
  font-size: 15px;
  font-weight: 600;
  margin: 0 0 12px;
}
.jq-card__bar {
  display: flex;
  align-items: center;
  gap: 18px;
  flex-wrap: wrap;
  margin-bottom: 6px;
}
.jq-card__bar .jq-card__title {
  margin: 0;
}
.jq-zoom {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 12px;
  color: var(--el-text-secondary, #909399);
}
.jq-zoom__btn {
  padding: 2px 10px;
  border-radius: 999px;
  cursor: pointer;
  font-size: 12px;
}
.jq-zoom__btn:hover {
  color: var(--el-color-primary);
}
.jq-zoom__btn--on {
  background: var(--el-color-primary);
  color: var(--text-inverse);
}
.jq-switch {
  margin-left: auto;
}
.jq-grid {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 10px 18px;
}
.jq-grid__item {
  display: flex;
  flex-direction: column;
  gap: 3px;
}
.jq-grid__label {
  font-size: 12px;
  color: var(--el-text-secondary, #909399);
}
.jq-grid__value {
  font-size: 15px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}
.jq-chart {
  width: 100%;
  height: 360px;
}
.jq-chart--sub {
  height: 220px;
}
.jq-chart-pair {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
}
.jq-chart-box {
  min-width: 0;
}
.jq-inline-metrics {
  display: flex;
  gap: 24px;
  flex-wrap: wrap;
  font-size: 13px;
  color: var(--el-text-secondary, #909399);
  margin-bottom: 10px;
}
.jq-inline-metrics b {
  color: var(--el-text-primary, #303133);
  font-weight: 500;
}
.jq-note {
  font-size: 12px;
  color: var(--el-text-secondary, #909399);
  margin: 0 0 10px;
}
.jq-params {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px 18px;
}
.jq-params__item {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  font-size: 13px;
  color: var(--el-text-secondary, #909399);
  border-bottom: 1px dashed var(--el-border-color-lighter, #e4e7ed);
  padding: 6px 0;
}
.jq-params__item b {
  color: var(--el-text-primary, #303133);
  font-weight: 500;
  text-align: right;
  word-break: break-all;
}
.mono {
  font-family: var(--font-mono, monospace);
}
.badge {
  display: inline-block;
  padding: 2px 8px;
  border-radius: var(--radius-sm, 4px);
  font-size: 12px;
  font-weight: 500;
  line-height: 1.4;
}
.badge--primary {
  background: var(--primary-soft, #e8f0fe);
  color: var(--primary, #1f4ba0);
}
.badge--success {
  background: var(--success-soft, #e6f6ee);
  color: var(--success, #1f9d6b);
}
.badge--warning {
  background: var(--warning-soft, #fdf3e0);
  color: var(--warning, #c8801c);
}
.badge--info {
  background: var(--info-soft, #e8f1fa);
  color: var(--info, #2f7dc2);
}
.badge--danger {
  background: var(--danger-soft, #fdeaea);
  color: var(--danger, #d24545);
}
.badge--muted {
  background: var(--bg-hover, #f2f4f7);
  color: var(--text-tertiary, #8493ab);
}

/* AI 解读 */
.ai-explain {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.ai-explain__summary {
  font-size: 15px;
  font-weight: 600;
  color: var(--el-text-primary, #303133);
  padding: 10px 12px;
  background: rgba(var(--primary-rgb, 31, 75, 160), 0.08);
  border-radius: 8px;
}
.ai-explain__collapse {
  border-top: none;
  border-bottom: none;
}
.ai-explain__collapse :deep(.el-collapse-item__header) {
  height: 32px;
  background: transparent;
  border-bottom: 1px dashed var(--border, #e3e9f1);
  font-size: 13px;
  font-weight: 600;
  color: var(--el-color-primary);
}
.ai-explain__collapse :deep(.el-collapse-item__wrap) {
  background: transparent;
  border-bottom: none;
}
.ai-explain__collapse :deep(.el-collapse-item__content) {
  padding-bottom: 12px;
}
.ai-explain__label {
  font-size: 13px;
  font-weight: 600;
  color: var(--el-color-primary);
}
.ai-explain__text {
  font-size: 13px;
  color: var(--el-text-primary, #303133);
  line-height: 1.7;
  white-space: pre-wrap;
  word-break: break-word;
}
.ai-explain__caveats {
  margin: 0;
  padding-left: 18px;
  font-size: 13px;
  color: var(--el-text-primary, #303133);
  line-height: 1.7;
}
.ai-explain__meta {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 12px;
  color: var(--el-text-secondary, #909399);
  border-top: 1px dashed var(--border, #e3e9f1);
  padding-top: 8px;
}
.ai-explain__markdown {
  line-height: 1.7;
}
.ai-explain__markdown p {
  margin: 0 0 8px;
}
.ai-explain__markdown p:last-child {
  margin-bottom: 0;
}
.ai-explain__markdown ul,
.ai-explain__markdown ol {
  padding-left: 20px;
  margin: 0 0 8px;
}
.ai-explain__markdown li {
  margin-bottom: 4px;
}
.ai-explain__markdown h1,
.ai-explain__markdown h2,
.ai-explain__markdown h3,
.ai-explain__markdown h4,
.ai-explain__markdown h5,
.ai-explain__markdown h6 {
  margin: 12px 0 8px;
  font-weight: 600;
  line-height: 1.4;
}
.ai-explain__markdown h1 {
  font-size: 18px;
}
.ai-explain__markdown h2 {
  font-size: 16px;
}
.ai-explain__markdown h3 {
  font-size: 15px;
}
.ai-explain__markdown h4,
.ai-explain__markdown h5,
.ai-explain__markdown h6 {
  font-size: 14px;
}
.ai-explain__markdown code {
  font-family: var(--font-mono, monospace);
  font-size: 0.92em;
  background: rgba(var(--primary-rgb, 31, 75, 160), 0.12);
  padding: 1px 5px;
  border-radius: 4px;
}
.ai-explain__markdown blockquote {
  margin: 0 0 8px;
  padding: 4px 12px;
  border-left: 3px solid rgba(var(--primary-rgb, 31, 75, 160), 0.4);
  background: var(--bg-tertiary, #eef2f6);
  border-radius: 0 8px 8px 0;
  color: var(--el-text-secondary, #909399);
}
.ai-explain__markdown table {
  border-collapse: collapse;
  margin: 0 0 8px;
  width: 100%;
  font-size: 13px;
}
.ai-explain__markdown th,
.ai-explain__markdown td {
  border: 1px solid var(--border, #e3e9f1);
  padding: 5px 10px;
  text-align: left;
}
.ai-explain__markdown th {
  background: var(--bg-tertiary, #eef2f6);
  font-weight: 600;
}
.ai-explain__markdown pre.hljs {
  margin: 0 0 8px;
  padding: 10px 12px;
  border-radius: 8px;
  overflow-x: auto;
  font-size: 13px;
  line-height: 1.5;
}
.ai-explain__markdown pre.hljs code {
  background: transparent;
  padding: 0;
}
.ai-chat {
  margin-top: 12px;
  max-height: 320px;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  border-top: 1px dashed var(--border, #e3e9f1);
  padding-top: 12px;
}
.ai-chat__scroll {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 4px 0;
}
.ai-chat__empty {
  padding: 18px 12px;
  text-align: center;
  font-size: 13px;
  color: var(--el-text-secondary, #909399);
  border: 1px dashed var(--border, #e3e9f1);
  border-radius: 8px;
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
  border-radius: 8px;
  font-size: 13px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
}
.ai-chat__msg--user .ai-chat__bubble {
  background: var(--el-color-primary);
  color: #fff;
  border-top-right-radius: 2px;
}
.ai-chat__msg--assistant .ai-chat__bubble {
  background: var(--bg-tertiary, #eef2f6);
  color: var(--el-text-primary, #303133);
  border-top-left-radius: 2px;
}
.ai-chat__bubble--md {
  width: fit-content;
  max-width: 100%;
  white-space: normal;
}
.ai-chat__bubble--typing {
  color: var(--el-text-secondary, #909399);
  font-style: italic;
}
.ai-chat__input {
  display: flex;
  gap: 8px;
  margin-top: 8px;
  position: sticky;
  bottom: 0;
  background: var(--el-bg-color, #fff);
  padding-top: 8px;
}

@media (max-width: 1100px) {
  .jq-grid {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
  .jq-chart-pair {
    grid-template-columns: 1fr;
  }
  .jq-body {
    grid-template-columns: 1fr;
  }
  .jq-side {
    position: static;
    flex-direction: row;
    flex-wrap: wrap;
  }
}
</style>