<template>
  <PageContainer narrow>
    <PageHeader
      title="政策风向"
      subtitle="央视《新闻联播》文字稿（akshare news_cctv，非东财）+ AI 政策解读：当日政策定调、点名行业、主题热度，点击条目展开全文与解读"
    />

    <!-- AI 政策主题热度 -->
    <SectionCard title="政策主题热度（AI 生成）" class="mb-6">
      <template #extra>
        <el-radio-group v-model="topicRange" size="small">
          <el-radio-button v-for="r in topicRanges" :key="r.key" :value="r.key">{{ r.label }}</el-radio-button>
        </el-radio-group>
      </template>
      <div v-if="topicsLoading" class="chart-wrap">
        <el-skeleton :rows="8" animated />
      </div>
      <v-chart v-else-if="topicSeries.length" :option="topicChart" class="chart-topics" autoresize />
      <el-empty v-else description="暂无 AI 主题热度，请先运行「AI 解读」" :image-size="64" />
    </SectionCard>

    <SectionCard title="政策风向" class="mb-6">
      <template #extra>
        <div class="policy-toolbar">
          <el-button size="small" @click="loadAll" :loading="loading">刷新</el-button>
          <el-select v-model="aiWindow" size="small" style="width: 110px">
            <el-option v-for="w in aiWindows" :key="w.value" :value="w.value" :label="w.label" />
          </el-select>
          <el-button size="small" type="warning" @click="doAiSync" :loading="aiSyncing">
            {{ aiSyncing ? 'AI解读中...' : 'AI 解读' }}
          </el-button>
          <el-button size="small" type="primary" @click="doSync" :loading="syncing">
            {{ syncing ? '同步中...' : '同步新闻联播' }}
          </el-button>
        </div>
      </template>

      <div v-if="syncMessage" class="sync-message">{{ syncMessage }}</div>
      <div v-if="aiProgress" class="ai-progress">
        AI 解读进度：{{ aiProgress.done }} / {{ aiProgress.pending }} 天完成（失败 {{ aiProgress.failed }}）...
      </div>

      <!-- 数据状态 -->
      <div v-if="status" class="policy-status">
        <div class="policy-status-item">
          <div class="policy-status-label">最新一期</div>
          <div class="policy-status-value">{{ status.latest_date || '--' }}</div>
        </div>
        <div class="policy-status-item">
          <div class="policy-status-label">覆盖天数</div>
          <div class="policy-status-value">{{ status.days || 0 }}</div>
        </div>
        <div class="policy-status-item">
          <div class="policy-status-label">累计条数</div>
          <div class="policy-status-value">{{ status.total || 0 }}</div>
        </div>
        <div class="policy-status-item">
          <div class="policy-status-label">AI 已解读</div>
          <div class="policy-status-value">{{ status.ai_done || 0 }}<span class="policy-status-sub"> / {{ status.ai_total || 0 }} 天</span></div>
        </div>
        <div class="policy-status-item">
          <div class="policy-status-label">AI 失败</div>
          <div class="policy-status-value" :class="{ 'is-error': (status.ai_failed || 0) > 0 }">
            {{ status.ai_failed || 0 }}
          </div>
        </div>
        <div class="policy-status-item">
          <div class="policy-status-label">待解读</div>
          <div class="policy-status-value">{{ status.ai_pending || 0 }}<span class="policy-status-sub"> 天</span></div>
        </div>
        <div class="policy-status-item">
          <div class="policy-status-label">最早一期</div>
          <div class="policy-status-value">{{ status.earliest_date || '--' }}</div>
        </div>
      </div>

      <!-- 筛选 -->
      <div class="policy-filter">
        <el-input
          v-model="keyword"
          placeholder="关键词：标题 / 正文 / AI关键词，如：人工智能、降准、脑机接口"
          clearable
          class="policy-filter-keyword"
          @keyup.enter="search"
        />
        <el-date-picker
          v-model="dateRange"
          type="daterange"
          range-separator="至"
          start-placeholder="开始日期"
          end-placeholder="结束日期"
          value-format="YYYY-MM-DD"
          class="policy-filter-range"
        />
        <el-button type="primary" @click="search">查询</el-button>
        <el-button @click="reset">重置</el-button>
      </div>

      <!-- 列表 -->
      <template v-if="items.length">
        <div
          v-for="item in items"
          :key="item.id"
          class="policy-item"
          :class="{ 'policy-item--open': expandedId === item.id }"
        >
          <div class="policy-item-head" @click="toggle(item.id, item)">
            <el-tag size="small" effect="plain" class="policy-item-date">{{ item.news_date }}</el-tag>
            <el-tag v-if="item.ai_analyzed" size="small" type="success" effect="light" class="policy-item-ai">
              AI解读
            </el-tag>
            <span class="policy-item-title">{{ item.title }}</span>
            <el-icon class="policy-item-caret">
              <CaretBottom v-if="expandedId !== item.id" />
              <CaretTop v-else />
            </el-icon>
          </div>
          <transition name="expand">
            <div v-if="expandedId === item.id" class="policy-item-body">
              <!-- 新闻全文 -->
              <div class="policy-item-section-title">新闻全文</div>
              <div v-if="item.content" class="policy-item-content">{{ item.content }}</div>
              <el-empty v-else description="无全文内容" :image-size="40" />

              <!-- AI 解读 -->
              <template v-if="aiDetail">
                <div class="policy-item-section-title">AI 政策解读</div>
                <div v-if="aiDetail.policy_tone" class="ai-tone">
                  <el-tag size="small" type="warning" effect="plain">当日定调</el-tag>
                  <span>{{ aiDetail.policy_tone }}</span>
                </div>
                <div v-if="aiDetail.summary" class="ai-summary">{{ aiDetail.summary }}</div>

                <!-- 点名行业/板块 -->
                <div v-if="aiDetail.sectors?.length" class="ai-sectors">
                  <div class="ai-label">点名行业/板块</div>
                  <div class="ai-chips">
                    <el-tooltip
                      v-for="s in aiDetail.sectors"
                      :key="s.name"
                      :content="s.reason || ''"
                      placement="top"
                    >
                      <el-tag
                        size="small"
                        :type="s.direction === '利好' ? 'success' : s.direction === '利空' ? 'danger' : 'info'"
                        effect="light"
                      >
                        {{ s.name }} {{ s.direction }}
                      </el-tag>
                    </el-tooltip>
                  </div>
                </div>

                <!-- 政策主题 -->
                <div v-if="aiDetail.topics?.length" class="ai-topics">
                  <div class="ai-label">政策主题</div>
                  <div class="ai-chips">
                    <el-tag v-for="t in aiDetail.topics" :key="t.topic" size="small" type="primary" effect="plain">
                      {{ t.topic }} {{ (t.score * 100).toFixed(0) }}
                    </el-tag>
                  </div>
                </div>

                <!-- 对市场影响 -->
                <div v-if="aiDetail.market_impact" class="ai-impact">
                  <el-tag size="small" type="danger" effect="plain">市场影响</el-tag>
                  <span>{{ aiDetail.market_impact }}</span>
                </div>
              </template>
              <div v-else-if="item.ai_analyzed" class="ai-loading">解读加载中...</div>
              <div v-else class="ai-missing">该日暂无 AI 解读，可在顶部点击「AI 解读」批量生成</div>
            </div>
          </transition>
        </div>

        <div class="policy-pager">
          <el-pagination
            v-model:current-page="page"
            :page-size="pageSize"
            :total="total"
            layout="total, prev, pager, next"
            @current-change="loadList"
          />
        </div>
      </template>
      <el-empty v-else-if="!loading" description="暂无数据，请先同步新闻联播" :image-size="64" />
    </SectionCard>
  </PageContainer>
</template>

<script setup>
defineOptions({ name: 'QuantPolicy' })
import { ref, computed, watch, onMounted, onBeforeUnmount } from 'vue'
import { ElMessage } from 'element-plus/es/components/message/index'
import { CaretTop, CaretBottom } from '@element-plus/icons-vue'
import PageContainer from '@/components/common/PageContainer.vue'
import PageHeader from '@/components/common/PageHeader.vue'
import SectionCard from '@/components/common/SectionCard.vue'
import VChart from 'vue-echarts'
import '@/utils/echarts'
import { useThemeRev } from '@/composables/useChartTheme'
import { chartTheme } from '@/utils/chartTheme'
import {
  syncPolicy, getPolicyNews, getPolicyStatus,
  syncPolicyAi, getPolicyAiDetail, getPolicyAiTopics,
} from '@/api/policy'

const themeRev = useThemeRev()
const loading = ref(false)
const syncing = ref(false)
const aiSyncing = ref(false)
const syncMessage = ref('')
const aiProgress = ref(null)
const status = ref(null)
const items = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)
const keyword = ref('')
const dateRange = ref([])
const expandedId = ref(null)
const aiDetail = ref(null)

// AI 解读回填窗口（对应后端 backfill_days）
const aiWindow = ref(30)
const aiWindows = [
  { value: 7, label: '近7天' },
  { value: 30, label: '近30天' },
  { value: 90, label: '近90天' },
  { value: 180, label: '近180天' },
  { value: 365, label: '近一年' },
]
let aiPollTimer = null

// 主题热度
const topicRange = ref('30D')
const topicRanges = [
  { key: '30D', label: '近30天' },
  { key: '90D', label: '近90天' },
  { key: '180D', label: '近180天' },
]
const topicsLoading = ref(false)
const topicItems = ref([])
const topicRank = ref([])

async function loadStatus() {
  const r = await getPolicyStatus()
  status.value = r
  // 有待解读任务时展示实时进度
  if (r?.ai_pending && r.ai_pending > 0 && !aiSyncing.value) {
    aiProgress.value = { pending: r.ai_pending, done: r.ai_done || 0, failed: r.ai_failed || 0 }
  } else {
    aiProgress.value = null
  }
}

async function loadList() {
  loading.value = true
  try {
    const r = await getPolicyNews({
      page: page.value,
      page_size: pageSize.value,
      keyword: keyword.value || undefined,
      start: dateRange.value?.[0] || undefined,
      end: dateRange.value?.[1] || undefined,
    })
    items.value = r?.items || []
    total.value = r?.total || 0
  } finally {
    loading.value = false
  }
}

async function loadAll() {
  await Promise.all([loadStatus(), loadList(), loadTopics()])
}

function search() {
  page.value = 1
  loadList()
}

function reset() {
  keyword.value = ''
  dateRange.value = []
  page.value = 1
  loadList()
}

// 展开条目：先展示全文，再按需拉取该日 AI 解读
async function toggle(id, item) {
  if (expandedId.value === id) {
    expandedId.value = null
    aiDetail.value = null
    return
  }
  expandedId.value = id
  aiDetail.value = null
  if (item.ai_analyzed) {
    const r = await getPolicyAiDetail(item.news_date)
    aiDetail.value = r || null
  }
}

async function doSync() {
  syncing.value = true
  syncMessage.value = ''
  try {
    const r = await syncPolicy()
    syncMessage.value = r?.message || '同步已提交'
    ElMessage.success('同步已提交，稍后刷新查看')
    setTimeout(() => loadStatus(), 3000)
  } catch (e) {
    ElMessage.error('同步提交失败')
  } finally {
    syncing.value = false
  }
}

function stopAiPoll() {
  if (aiPollTimer) {
    clearInterval(aiPollTimer)
    aiPollTimer = null
  }
}

async function doAiSync() {
  aiSyncing.value = true
  try {
    const r = await syncPolicyAi(aiWindow.value)
    ElMessage.success(r?.message || 'AI 解读已提交')
    // 提交后立即轮询进度直到 done+failed == pending
    const poll = async () => {
      try {
        const s = await getPolicyStatus()
        status.value = s
        const pending = s?.ai_pending || 0
        const done = s?.ai_done || 0
        const failed = s?.ai_failed || 0
        aiProgress.value = { pending, done, failed }
        if (pending === 0) {
          stopAiPoll()
          aiSyncing.value = false
          ElMessage.success('AI 解读完成')
          loadTopics()
        }
      } catch {
        stopAiPoll()
        aiSyncing.value = false
      }
    }
    stopAiPoll()
    await poll()
    aiPollTimer = setInterval(poll, 10000)
  } catch (e) {
    ElMessage.error('AI 解读提交失败')
    aiSyncing.value = false
  }
}

// ==== 主题热度图 ====
async function loadTopics() {
  topicsLoading.value = true
  try {
    const days = { '30D': 30, '90D': 90, '180D': 180 }[topicRange.value]
    const end = new Date()
    const start = new Date()
    start.setDate(start.getDate() - days)
    const r = await getPolicyAiTopics({
      start: start.toISOString().slice(0, 10),
      end: end.toISOString().slice(0, 10),
    })
    topicItems.value = r?.items || []
    topicRank.value = r?.topic_rank || []
  } finally {
    topicsLoading.value = false
  }
}

const topicSeries = computed(() => {
  void themeRev.value
  const top = topicRank.value.slice(0, 6)
  if (!top.length) return []
  return top.map((t, _i) => ({
    name: t.topic,
    type: 'line',
    smooth: true,
    symbol: 'none',
    lineStyle: { width: 2 },
    data: topicItems.value.map((it) => it.topics?.[t.topic] ?? null),
  }))
})

const topicChart = computed(() => ({
  tooltip: { trigger: 'axis' },
  legend: { top: 0 },
  grid: { left: 8, right: 12, top: 34, bottom: 8, containLabel: true },
  xAxis: {
    type: 'category',
    data: topicItems.value.map((it) => it.date),
    axisLabel: { fontSize: 10 },
  },
  yAxis: { type: 'value', min: 0, max: 1, splitLine: { lineStyle: { type: 'dashed' } } },
  series: topicSeries.value.map((s, i) => ({ ...s, itemStyle: { color: chartTheme.palette(i + 1) } })),
  animation: false,
}))

watch(topicRange, loadTopics)
onMounted(loadAll)
onBeforeUnmount(stopAiPoll)
</script>

<style scoped>
.policy-toolbar {
  display: flex;
  gap: 8px;
}

.sync-message {
  margin-bottom: 12px;
  color: var(--el-color-success);
  font-size: 13px;
}

.ai-progress {
  margin-bottom: 12px;
  color: var(--el-color-warning);
  font-size: 13px;
}

.policy-status {
  display: flex;
  flex-wrap: wrap;
  gap: 32px;
  padding: 12px 16px;
  margin-bottom: 12px;
  border-radius: 8px;
  background: var(--el-fill-color-light);
}

.policy-status-label {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.policy-status-value {
  font-size: 18px;
  font-weight: 600;
  margin-top: 2px;
}

.policy-status-value.is-error {
  color: var(--el-color-danger);
}

.policy-status-sub {
  font-size: 12px;
  font-weight: 400;
  color: var(--el-text-color-secondary);
}

.policy-filter {
  display: flex;
  gap: 10px;
  align-items: center;
  margin-bottom: 14px;
}

.policy-filter-keyword {
  width: 320px;
}

.policy-item {
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
  margin-bottom: 8px;
  overflow: hidden;
}

.policy-item-head {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 14px;
  cursor: pointer;
  user-select: none;
}

.policy-item-head:hover {
  background: var(--el-fill-color-light);
}

.policy-item-date,
.policy-item-ai {
  flex-shrink: 0;
}

.policy-item-title {
  flex: 1;
  font-size: 14px;
  line-height: 1.5;
}

.policy-item--open .policy-item-head {
  background: var(--el-fill-color-light);
}

.policy-item-caret {
  color: var(--el-text-color-secondary);
  flex-shrink: 0;
}

.policy-item-body {
  border-top: 1px dashed var(--el-border-color-lighter);
  padding: 14px;
  background: var(--el-fill-color-blank);
}

.policy-item-content {
  font-size: 13px;
  line-height: 1.9;
  color: var(--el-text-color-regular);
  white-space: pre-wrap;
}

.policy-item-section-title {
  font-size: 12px;
  font-weight: 600;
  color: var(--el-text-color-secondary);
  margin: 12px 0 8px;
}

.policy-item-section-title:first-child {
  margin-top: 0;
}

.ai-tone {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  color: var(--el-text-color-regular);
  margin-bottom: 8px;
}

.ai-summary {
  font-size: 13px;
  line-height: 1.9;
  color: var(--el-text-color-regular);
  margin-bottom: 10px;
}

.ai-label {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  margin-bottom: 6px;
}

.ai-sectors,
.ai-topics {
  margin-bottom: 10px;
}

.ai-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.ai-impact {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  color: var(--el-text-color-regular);
  padding: 8px 10px;
  border-radius: 6px;
  background: var(--el-fill-color-light);
}

.ai-loading,
.ai-missing {
  font-size: 13px;
  color: var(--el-text-color-secondary);
  margin-top: 8px;
}

.policy-pager {
  display: flex;
  justify-content: flex-end;
  margin-top: 12px;
}

.chart-topics {
  height: 260px;
}

.expand-enter-active,
.expand-leave-active {
  transition: all 0.25s ease;
}

.expand-enter-from,
.expand-leave-to {
  opacity: 0;
}
</style>