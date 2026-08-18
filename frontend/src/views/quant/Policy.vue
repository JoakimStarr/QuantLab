<template>
  <PageContainer narrow>
    <PageHeader
      title="政策风向"
      subtitle="多源政策新闻（新闻联播/财经早餐/快讯）+ AI 政策解读：当日定调、点名板块、主题热度；点击主题/板块可联动检索"
    />

    <!-- L1 当日政策定调 -->
    <SectionCard title="当日政策定调" class="mb-6">
      <template #extra>
        <div class="tone-toolbar">
          <el-select v-model="latestDate" size="small" style="width: 130px" placeholder="选择日期">
            <el-option v-for="d in latestDates" :key="d" :value="d" :label="d" />
          </el-select>
          <el-button size="small" type="primary" plain @click="jumpToNews({ d: latestDate })">
            查看当日新闻
          </el-button>
        </div>
      </template>
      <div v-if="latestLoading" class="chart-wrap">
        <el-skeleton :rows="4" animated />
      </div>
      <template v-else-if="latestItem">
        <div v-if="latestItem.policy_tone" class="tone-row">
          <el-tag size="small" type="warning" effect="plain">当日定调</el-tag>
          <span class="tone-text">{{ latestItem.policy_tone }}</span>
        </div>
        <div v-if="latestItem.summary" class="tone-summary">{{ latestItem.summary }}</div>
        <div v-if="latestItem.sectors?.length" class="tone-sectors">
          <span class="ai-label">点名板块（点击检索）</span>
          <div class="ai-chips">
            <el-tooltip v-for="s in latestItem.sectors" :key="s.name" :content="s.reason || ''" placement="top">
              <el-tag
                size="small"
                :type="dirType(s.direction)"
                effect="light"
                class="tone-chip"
                @click="jumpToNews({ kw: s.name })"
              >
                {{ s.name }} {{ s.direction }}
              </el-tag>
            </el-tooltip>
          </div>
        </div>
        <div v-if="latestItem.market_impact" class="ai-impact">
          <el-tag size="small" type="danger" effect="plain">市场影响</el-tag>
          <span>{{ latestItem.market_impact }}</span>
        </div>
      </template>
      <el-empty v-else description="暂无 AI 解读，请先在「新闻列表」运行 AI 解读" :image-size="64" />
    </SectionCard>

    <!-- L2 功能 Tab -->
    <SectionCard class="mb-6">
      <el-tabs v-model="activeTab" class="policy-tabs">
        <!-- 板块表现 -->
        <el-tab-pane label="板块表现" name="sector">
          <div class="sector-switch">
            <el-radio-group v-model="sectorView" size="small">
              <el-radio-button value="daily">单日点名 × 市场表现</el-radio-button>
              <el-radio-button value="week">近7天板块表现榜</el-radio-button>
            </el-radio-group>
          </div>

          <!-- 单日点名 × 市场表现 -->
          <template v-if="sectorView === 'daily'">
            <div class="sector-toolbar">
              <el-select v-model="sectorPerfDate" size="small" style="width: 160px" placeholder="选择日期">
                <el-option v-for="d in sectorPerfDates" :key="d" :value="d" :label="d" />
              </el-select>
              <span class="sector-hint">成分股等权收益，T+1 起为政策日之后首个交易日；「超额」为相对沪深300 同期单日涨跌</span>
            </div>
            <div v-if="sectorPerfLoading" class="chart-wrap">
              <el-skeleton :rows="6" animated />
            </div>
            <el-table v-else-if="sectorPerfRows.length" :data="sectorPerfRows" size="small" border stripe>
              <el-table-column label="板块" prop="name" min-width="150" show-overflow-tooltip>
                <template #default="{ row }">
                  <el-tag v-if="row.is_benchmark" size="small" type="warning" effect="plain">基准</el-tag>
                  <span v-if="row.is_benchmark" class="bench-name">{{ row.name }}</span>
                  <span v-else>{{ row.name }}</span>
                </template>
              </el-table-column>
              <el-table-column label="方向" width="64" align="center">
                <template #default="{ row }">
                  <el-tag size="small" :type="dirType(row.direction)" effect="light">{{ row.direction }}</el-tag>
                </template>
              </el-table-column>
              <el-table-column label="匹配行业" min-width="160" show-overflow-tooltip>
                <template #default="{ row }">
                  <span v-if="row.industry">{{ row.industry }}</span>
                  <span v-else class="sector-no-match">{{ row.is_benchmark ? '—' : '暂无对照' }}</span>
                </template>
              </el-table-column>
              <el-table-column label="代表股数" width="80" align="right">
                <template #default="{ row }">{{ row.stocks ?? '—' }}</template>
              </el-table-column>
              <el-table-column v-for="h in sectorPerfHorizons" :key="h" width="96" align="right">
                <template #header>
                  <div class="sector-hdr">T+{{ h }}<span class="sector-hdr-sub">超额</span></div>
                </template>
                <template #default="{ row }">
                  <div v-if="row[`ret_${h}d`] != null">
                    <div :class="perfClass(row[`ret_${h}d`])">{{ fmtPct(row[`ret_${h}d`]) }}</div>
                    <div
                      v-if="!row.is_benchmark && row[`ex_${h}d`] != null"
                      :class="perfClass(row[`ex_${h}d`])"
                      class="sector-excess"
                    >
                      {{ fmtPct(row[`ex_${h}d`]) }}
                    </div>
                  </div>
                  <span v-else class="sector-no-match">—</span>
                </template>
              </el-table-column>
              <el-table-column type="expand" width="36">
                <template #default="{ row }">
                  <div v-if="row.is_benchmark" class="bench-note">
                    市场基准：沪深300 同期单日涨跌幅（与板块成分股等权收益口径一致，用于判断板块相对大盘强弱）
                  </div>
                  <div v-else-if="row.top?.length" class="top-stocks">
                    <div class="ai-label">龙头股（按 T+1 涨幅排序，最多 {{ row.top.length }} 只）</div>
                    <el-table :data="row.top" size="mini" border>
                      <el-table-column label="代码" prop="code" width="110" />
                      <el-table-column label="名称" prop="name" min-width="140" show-overflow-tooltip />
                      <el-table-column v-for="h in sectorPerfHorizons" :key="h" :label="`T+${h}`" align="right" width="80">
                        <template #default="{ row: stock }">
                          <span v-if="stock[`ret_${h}d`] != null" :class="perfClass(stock[`ret_${h}d`])">
                            {{ fmtPct(stock[`ret_${h}d`]) }}
                          </span>
                          <span v-else class="sector-no-match">—</span>
                        </template>
                      </el-table-column>
                    </el-table>
                  </div>
                  <el-empty v-else description="暂无龙头股数据（板块未匹配行业）" :image-size="36" />
                </template>
              </el-table-column>
            </el-table>
            <el-empty v-else description="暂无板块表现数据" :image-size="64" />
          </template>

          <!-- 近 7 天板块表现榜 -->
          <template v-else>
            <div v-if="sectorPerfLoading" class="chart-wrap">
              <el-skeleton :rows="6" animated />
            </div>
            <el-table v-else-if="weekPerfRows.length" :data="weekPerfRows" size="small" border stripe>
              <el-table-column label="板块" prop="name" min-width="150" show-overflow-tooltip />
              <el-table-column label="点名次数" prop="count" width="80" align="center">
                <template #default="{ row }">{{ row.count }}<span class="sector-no-match"> 天</span></template>
              </el-table-column>
              <el-table-column label="最近方向" width="64" align="center">
                <template #default="{ row }">
                  <el-tag size="small" :type="dirType(row.direction)" effect="light">{{ row.direction }}</el-tag>
                </template>
              </el-table-column>
              <el-table-column label="匹配行业" min-width="160" show-overflow-tooltip>
                <template #default="{ row }">
                  <span v-if="row.industry">{{ row.industry }}</span>
                  <span v-else class="sector-no-match">暂无对照</span>
                </template>
              </el-table-column>
              <el-table-column v-for="h in sectorPerfHorizons" :key="h" :label="`T+${h}`" width="84" align="right">
                <template #default="{ row }">
                  <span v-if="row[`ret_${h}d`] != null" :class="perfClass(row[`ret_${h}d`])">
                    {{ fmtPct(row[`ret_${h}d`]) }}
                  </span>
                  <span v-else class="sector-no-match">—</span>
                </template>
              </el-table-column>
            </el-table>
            <el-empty v-else description="暂无板块表现数据" :image-size="64" />
          </template>
        </el-tab-pane>

        <!-- 主题热度 -->
        <el-tab-pane label="主题热度" name="topic">
          <div class="sector-toolbar">
            <el-radio-group v-model="topicRange" size="small">
              <el-radio-button v-for="r in topicRanges" :key="r.key" :value="r.key">{{ r.label }}</el-radio-button>
            </el-radio-group>
            <span class="sector-hint">点击主题/日期可联动到「新闻列表」检索</span>
          </div>
          <div v-if="topicsLoading" class="chart-wrap">
            <el-skeleton :rows="8" animated />
          </div>
          <v-chart
            v-else-if="topicHeatData.length"
            :option="topicChart"
            class="chart-topics"
            autoresize
            @click="onChartClick"
          />
          <el-empty v-else description="暂无 AI 主题热度，请先运行「AI 解读」" :image-size="64" />
          <div v-if="topicRank.length" class="topic-rank">
            <span class="ai-label">主题排行（点击检索）</span>
            <div class="ai-chips">
              <el-tag
                v-for="t in topicRank"
                :key="t.topic"
                size="small"
                type="primary"
                effect="plain"
                class="topic-chip"
                @click="jumpToNews({ kw: t.topic })"
              >
                {{ t.topic }} {{ Number(t.score).toFixed(1) }}
              </el-tag>
            </div>
          </div>
        </el-tab-pane>

        <!-- 新闻列表 -->
        <el-tab-pane label="新闻列表" name="news">
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
            <el-button size="small" @click="openScheduleDialog" :class="{ 'schedule-enabled': schedule.enabled }">
              定时更新
            </el-button>
          </div>

          <div v-if="schedule.enabled" class="schedule-banner">
            定时更新已开启：每日 {{ schedule.run_time }} {{ schedule.workdays_only ? '（仅工作日）' : '' }} 自动同步
            <template v-if="schedule.include_news"> · 新闻</template>
            <template v-if="schedule.include_ai"> · AI 解读</template>
            <template v-if="schedule.include_market"> · 行情</template>
          </div>

          <div v-if="syncMessage" class="sync-message">{{ syncMessage }}</div>
          <div v-if="aiProgress" class="ai-progress">
            本次任务进度：{{ aiProgress.done }} / {{ aiProgress.total }} 天（失败 {{ aiProgress.failed }}）...
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
            <div v-if="status.source_breakdown" class="policy-status-sources">
              <span v-for="(cnt, s) in status.source_breakdown" :key="s" class="policy-status-src">
                {{ sourceLabel(s) }} {{ cnt }}
              </span>
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
            <el-select v-model="source" placeholder="来源" clearable style="width: 120px" @change="search">
              <el-option v-for="(label, key) in SOURCE_LABELS" :key="key" :value="key" :label="label" />
            </el-select>
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
                <el-tag v-if="item.source !== 'cctv'" size="small" effect="plain" class="policy-item-source">
                  {{ sourceLabel(item.source) }}
                </el-tag>
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
                          <el-tag size="small" :type="dirType(s.direction)" effect="light">
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
        </el-tab-pane>
      </el-tabs>
    </SectionCard>

    <!-- 定时数据刷新设置 -->
    <el-dialog v-model="scheduleDialogVisible" title="定时数据刷新" width="480px" :close-on-click-modal="false">
      <div class="schedule-form">
        <el-form label-width="110px" label-position="left">
          <el-form-item label="启用定时刷新">
            <el-switch v-model="scheduleForm.enabled" />
            <span class="schedule-hint">开启后每天到点自动同步，无需手动点击</span>
          </el-form-item>
          <el-form-item label="每日时间">
            <el-time-select
              v-model="scheduleForm.run_time"
              start="00:00"
              step="00:05"
              end="23:55"
              placeholder="选择时间"
              style="width: 140px"
            />
          </el-form-item>
          <el-form-item label="仅工作日">
            <el-switch v-model="scheduleForm.workdays_only" />
            <span class="schedule-hint">周一至周五执行，周末跳过</span>
          </el-form-item>
          <el-form-item label="同步环节">
            <div class="schedule-scopes">
              <el-checkbox v-model="scheduleForm.include_news">新闻联播</el-checkbox>
              <el-checkbox v-model="scheduleForm.include_ai">AI 解读</el-checkbox>
              <el-checkbox v-model="scheduleForm.include_market">行情 EOD</el-checkbox>
            </div>
          </el-form-item>
          <el-form-item v-if="scheduleForm.include_ai" label="AI 回填窗口">
            <el-select v-model="scheduleForm.ai_backfill_days" style="width: 140px">
              <el-option v-for="w in aiWindows" :key="w.value" :value="w.value" :label="w.label" />
            </el-select>
          </el-form-item>
          <el-form-item v-if="scheduleForm.include_market" label="EOD 参数">
            <div class="schedule-scopes">
              <el-select v-model="scheduleForm.market_universe" style="width: 110px">
                <el-option label="沪深300" value="csi300" />
                <el-option label="中证500" value="csi500" />
                <el-option label="全部A股" value="all" />
              </el-select>
              <el-select v-model="scheduleForm.market_days" style="width: 90px">
                <el-option v-for="d in [1, 3, 5, 10, 20]" :key="d" :value="d" :label="`近${d}天`" />
              </el-select>
            </div>
          </el-form-item>
        </el-form>
      </div>
      <template #footer>
        <el-button @click="scheduleDialogVisible = false">关闭</el-button>
        <el-button type="primary" @click="doSaveSchedule" :loading="scheduleSaving">保存</el-button>
      </template>
    </el-dialog>
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
import { buildTopicHeatMatrix, buildTopicCumulative } from '@/utils/policyTopics'
import {
  syncPolicy, getPolicyNews, getPolicyStatus, getPolicyLatest,
  syncPolicyAi, getPolicyAiProgress, getPolicyAiDetail, getPolicyAiTopics, getPolicySectorPerf,
  getPolicySchedule, savePolicySchedule,
} from '@/api/policy'

const themeRev = useThemeRev()
const loading = ref(false)
const syncing = ref(false)
const aiSyncing = ref(false)
const syncMessage = ref('')
const aiProgress = ref(null)
// AI 任务轮询时连续「无进度文件」次数兜底（worker 启动即崩溃之类，避免无限轮询）
let aiProgressEmptyPolls = 0
const status = ref(null)
const items = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)
const keyword = ref('')
const source = ref('')
const dateRange = ref([])
const expandedId = ref(null)
const aiDetail = ref(null)

// ==== 数据源 ====
const SOURCE_LABELS = { cctv: '新闻联播', cjzc: '金十早餐', em: '东财快讯' }
const sourceLabel = (s) => SOURCE_LABELS[s] || s

// ==== L2 Tab ====
const activeTab = ref('sector')
const sectorView = ref('daily')

// ==== L1 当日定调 ====
const latestLoading = ref(false)
const latestItems = ref([])
const latestDate = ref('')
const latestDates = computed(() => latestItems.value.map((it) => it.news_date))
const latestItem = computed(() => latestItems.value.find((it) => it.news_date === latestDate.value) || null)

async function loadLatest() {
  latestLoading.value = true
  try {
    const r = await getPolicyLatest(7)
    latestItems.value = r?.items || []
    latestDate.value = latestItems.value[0]?.news_date || ''
  } finally {
    latestLoading.value = false
  }
}

function dirType(dir) {
  if (dir === '利好') return 'success'
  if (dir === '利空') return 'danger'
  return 'info'
}

// 跨 tab 联动：跳转新闻列表并按主题/日期检索
function jumpToNews({ kw = '', d = null } = {}) {
  activeTab.value = 'news'
  if (kw) keyword.value = kw
  if (d) dateRange.value = [d, d]
  page.value = 1
  loadList()
}

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

// ==== 定时数据刷新 ====
const schedule = ref({ enabled: false, run_time: '18:00', workdays_only: true })
const scheduleDialogVisible = ref(false)
const scheduleSaving = ref(false)
const scheduleForm = ref({ ...schedule.value })

async function loadSchedule() {
  const s = await getPolicySchedule()
  schedule.value = { ...schedule.value, ...(s || {}) }
}

function openScheduleDialog() {
  scheduleForm.value = {
    enabled: schedule.value.enabled,
    run_time: schedule.value.run_time,
    workdays_only: schedule.value.workdays_only,
    include_news: schedule.value.include_news,
    include_ai: schedule.value.include_ai,
    include_market: schedule.value.include_market,
    ai_backfill_days: schedule.value.ai_backfill_days ?? 30,
    market_days: schedule.value.market_days ?? 5,
    market_universe: schedule.value.market_universe ?? 'csi300',
  }
  scheduleDialogVisible.value = true
}

async function doSaveSchedule() {
  scheduleSaving.value = true
  try {
    const s = await savePolicySchedule({ ...scheduleForm.value })
    schedule.value = { ...schedule.value, ...(s || {}) }
    ElMessage.success('定时刷新设置已保存')
    scheduleDialogVisible.value = false
  } catch (e) {
    ElMessage.error('保存失败')
  } finally {
    scheduleSaving.value = false
  }
}


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

// 点名板块 × 市场表现（成分股等权 T+N 收益）
const sectorPerfLoading = ref(false)
const sectorPerfItems = ref([]) // [{date, sectors: [...]}]
const sectorPerfDate = ref('')
const sectorPerfHorizons = [1, 3, 5]
const sectorPerfDates = computed(() => sectorPerfItems.value.map((it) => it.date))

const round2 = (v) => (v == null ? null : Number(v.toFixed(2)))
// 超额收益 = 板块成分股等权收益 − 沪深300 同期单日涨跌（基准已由后端返回）
const sectorPerfRows = computed(() => {
  const it = sectorPerfItems.value.find((x) => x.date === sectorPerfDate.value)
  if (!it) return []
  const rows = (it.sectors || []).map((s) => ({
    ...s,
    ex_1d: s.ret_1d != null && it.bench_ret_1d != null ? round2(s.ret_1d - it.bench_ret_1d) : null,
    ex_3d: s.ret_3d != null && it.bench_ret_3d != null ? round2(s.ret_3d - it.bench_ret_3d) : null,
    ex_5d: s.ret_5d != null && it.bench_ret_5d != null ? round2(s.ret_5d - it.bench_ret_5d) : null,
  }))
  // 首行插入市场基准（沪深300 同口径单日涨跌），供用户对比板块相对大盘的强弱
  if (it.bench_ret_1d != null || it.bench_ret_3d != null || it.bench_ret_5d != null) {
    rows.unshift({
      is_benchmark: true,
      name: '沪深300',
      direction: '基准',
      industry: null,
      stocks: null,
      ret_1d: it.bench_ret_1d ?? null,
      ret_3d: it.bench_ret_3d ?? null,
      ret_5d: it.bench_ret_5d ?? null,
      ex_1d: null,
      ex_3d: null,
      ex_5d: null,
    })
  }
  return rows
})

async function loadSectorPerf() {
  sectorPerfLoading.value = true
  try {
    const r = await getPolicySectorPerf(14)
    sectorPerfItems.value = r?.items || []
    // 默认最新 AI 解读日期；仅当其全部板块 T+1 收益都为空（T+N 尚未发生/落库）时，
    // 回退到最近一个有收益的日期
    const items = sectorPerfItems.value
    const latest = items[0]
    if (latest && latest.sectors.some((s) => s.ret_1d != null)) {
      sectorPerfDate.value = latest.date
    } else {
      const withRet = [...items].reverse().find((it) => it.sectors.some((s) => s.ret_1d != null))
      sectorPerfDate.value = withRet?.date || latest?.date || ''
    }
  } finally {
    sectorPerfLoading.value = false
  }
}

function fmtPct(v) {
  if (v == null) return '—'
  const s = Number(v).toFixed(2)
  return `${v > 0 ? '+' : ''}${s}%`
}

function perfClass(v) {
  if (v == null) return ''
  if (v > 0) return 'perf-up'
  if (v < 0) return 'perf-down'
  return ''
}

// 近 7 天板块表现榜：按板块名聚合，收益取最近一次点名的 T+N（items 已按日期倒序）
const weekPerfRows = computed(() => {
  const map = new Map()
  for (const it of sectorPerfItems.value.slice(0, 7)) {
    for (const s of it.sectors || []) {
      let e = map.get(s.name)
      if (!e) {
        e = { name: s.name, count: 0 }
        map.set(s.name, e)
      }
      e.count += 1
      if (e.ret_1d === undefined) {
        e.direction = s.direction
        e.reason = s.reason
        e.industry = s.industry
        e.ret_1d = s.ret_1d
        e.ret_3d = s.ret_3d
        e.ret_5d = s.ret_5d
      }
    }
  }
  return [...map.values()].sort((a, b) => b.count - a.count)
})

async function loadStatus() {
  status.value = await getPolicyStatus()
}

async function loadList() {
  loading.value = true
  try {
    const r = await getPolicyNews({
      page: page.value,
      page_size: pageSize.value,
      keyword: keyword.value || undefined,
      source: source.value || undefined,
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
  await Promise.all([loadStatus(), loadList(), loadLatest(), loadTopics(), loadSectorPerf(), loadSchedule()])
}

function search() {
  page.value = 1
  loadList()
}

function reset() {
  keyword.value = ''
  source.value = ''
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
  aiProgress.value = null
  try {
    const r = await syncPolicyAi(aiWindow.value)
    ElMessage.success(r?.message || 'AI 解读已提交')
    // 本次窗口无待解读日期 → 任务直接完成，无需轮询
    if ((r?.pending_count ?? 0) === 0) {
      aiSyncing.value = false
      loadTopics()
      loadStatus()
      loadLatest()
      return
    }
    // 以任务级进度（policy/ai/progress）为准轮询，直到终态 done/failed。
    // 不能拿全历史口径的 status.ai_pending===0 当完成信号——旧历史未解读日期
    // 会让它永远不为 0，导致轮询永不终止。
    const poll = async () => {
      try {
        const p = await getPolicyAiProgress()
        if (!p) {
          // 安全兜底：worker 启动后短时间内必然写进度文件；文件迟迟不出现
          // （进程启动即崩溃等）说明任务异常，不应无限轮询
          if (++aiProgressEmptyPolls > 6) {
            stopAiPoll()
            aiSyncing.value = false
            aiProgress.value = null
            ElMessage.error('AI 解读任务启动失败（无进度信号），请查看后端日志')
          }
          return
        }
        aiProgress.value = { total: p.total || 0, done: p.done || 0, failed: p.failed || 0 }
        if (p.status === 'done' || p.status === 'failed') {
          stopAiPoll()
          aiSyncing.value = false
          if (p.status === 'failed') {
            ElMessage.warning('AI 解读任务异常结束')
          } else {
            ElMessage.success('AI 解读完成')
          }
          loadTopics()
          loadStatus()
          loadLatest()
        }
      } catch (e) {
        stopAiPoll()
        aiSyncing.value = false
        aiProgress.value = null
      }
    }
    stopAiPoll()
    aiProgressEmptyPolls = 0
    await poll()
    aiPollTimer = setInterval(poll, 10000)
  } catch (e) {
    ElMessage.error('AI 解读提交失败')
    aiSyncing.value = false
    aiProgress.value = null
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

// 主题热度矩阵：rows=日期，cols=主题，值为 score（0~1），供热力图
const topicTopics = computed(() => topicRank.value.slice(0, 8).map((t) => t.topic))
// x 轴标签间隔：按天数均摊，约显示 15 个完整日期标签，避免密集重叠
const topicXInterval = computed(() => {
  const n = topicItems.value.length
  if (!n) return 1
  return Math.max(1, Math.ceil(n / 15))
})
const topicHeatData = computed(() => {
  void themeRev.value
  return buildTopicHeatMatrix(topicItems.value, topicTopics.value)
})

// 最热主题的累计热度折线（叠加在热力图上，右轴），体现热度积累节奏
const topicCumSeries = computed(() => {
  const topic = topicTopics.value[0]
  if (!topic) return null
  const data = buildTopicCumulative(topicItems.value, topic)
  if (data.every((v) => v === 0)) return null
  return {
    name: `${topic} 累计`,
    type: 'line',
    yAxisIndex: 1,
    smooth: true,
    symbol: 'none',
    lineStyle: { width: 2 },
    data,
  }
})

const topicChart = computed(() => ({
  tooltip: {
    position: 'top',
    formatter: (p) => {
      if (Array.isArray(p)) return ''
      if (p?.seriesType === 'line') {
        const date = topicItems.value[p.dataIndex]?.date ?? ''
        return `${p.seriesName}<br/>${date}<br/>${Number(p.value).toFixed(2)}`
      }
      if (p?.data?.length === 3) {
        const [di, ti, score] = p.data
        return `${topicTopics.value[ti]}<br/>${topicItems.value[di]?.date ?? ''}<br/>热度：${Number(score).toFixed(2)}`
      }
      return ''
    },
  },
  grid: { left: 8, right: 60, top: 8, bottom: 42, containLabel: true },
  xAxis: {
    type: 'category',
    data: topicItems.value.map((it) => it.date),
    splitArea: { show: true },
    axisLabel: { fontSize: 10, interval: topicXInterval.value, hideOverlap: true },
  },
  yAxis: [
    {
      type: 'category',
      data: topicTopics.value,
      inverse: true,
      splitArea: { show: true },
      axisLabel: { fontSize: 11 },
    },
    {
      type: 'value',
      name: '累计',
      nameTextStyle: { fontSize: 10 },
      splitLine: { show: false },
      axisLabel: { fontSize: 10 },
    },
  ],
  visualMap: {
    min: 0,
    max: 1,
    calculable: true,
    orient: 'horizontal',
    left: 'center',
    bottom: -8,
    text: ['高', '低'],
    textStyle: { fontSize: 10 },
    inRange: { color: [chartTheme.bgTertiary(), chartTheme.palette(1)] },
  },
  dataZoom: [{ type: 'inside', xAxisIndex: 0, zoomOnMouseWheel: true, moveOnMouseMove: true }],
  series: [
    { type: 'heatmap', data: topicHeatData.value, animation: false },
    ...(topicCumSeries.value ? [topicCumSeries.value] : []),
  ],
}))

// 热力图点击联动：主题/日期 → 新闻列表检索
function onChartClick(params) {
  if (!params || params.seriesType !== 'heatmap' || !Array.isArray(params.data) || params.data.length !== 3) return
  const [di, ti] = params.data
  const topic = topicTopics.value[ti]
  const d = topicItems.value[di]?.date
  if (!topic || !d) return
  jumpToNews({ kw: topic, d })
}

watch(topicRange, loadTopics)
onMounted(loadAll)
onBeforeUnmount(stopAiPoll)
</script>

<style scoped>
.policy-toolbar {
  display: flex;
  gap: 8px;
  margin-bottom: 12px;
  flex-wrap: wrap;
}

.tone-toolbar {
  display: flex;
  gap: 8px;
  align-items: center;
}

.tone-row {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 14px;
  margin-bottom: 10px;
}

.tone-text {
  font-weight: 600;
  color: var(--el-text-color-primary);
}

.tone-summary {
  font-size: 13px;
  line-height: 1.9;
  color: var(--el-text-color-regular);
  margin-bottom: 10px;
}

.tone-sectors {
  margin-bottom: 10px;
}

.tone-chip {
  cursor: pointer;
}

.schedule-enabled {
  border-color: var(--el-color-primary) !important;
  color: var(--el-color-primary) !important;
}

.schedule-banner {
  margin-bottom: 12px;
  padding: 8px 12px;
  border-radius: 6px;
  font-size: 13px;
  color: var(--el-color-primary);
  background: var(--el-color-primary-light-9);
}

.schedule-hint {
  margin-left: 8px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.schedule-scopes {
  display: flex;
  gap: 16px;
  align-items: center;
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
  align-items: center;
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

.policy-status-sources {
  display: flex;
  gap: 12px;
  margin-left: 8px;
  padding-left: 24px;
  border-left: 1px solid var(--el-border-color-lighter);
}

.policy-status-src {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  white-space: nowrap;
}

.policy-filter {
  display: flex;
  gap: 10px;
  align-items: center;
  margin-bottom: 14px;
  flex-wrap: wrap;
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
.policy-item-ai,
.policy-item-source {
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

/* 板块 × 市场表现 */
.sector-switch {
  margin-bottom: 14px;
}

.sector-toolbar {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 12px;
  flex-wrap: wrap;
}

.sector-hint {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.sector-hdr {
  display: flex;
  flex-direction: column;
  line-height: 1.2;
}

.sector-hdr-sub {
  font-size: 11px;
  color: var(--el-text-color-secondary);
  font-weight: 400;
}

.sector-excess {
  font-size: 11px;
  opacity: 0.85;
}

.perf-up {
  color: var(--el-color-danger);
  font-weight: 600;
}

.perf-down {
  color: var(--el-color-success);
  font-weight: 600;
}

.sector-no-match {
  color: var(--el-text-color-placeholder);
}

.bench-name {
  font-weight: 600;
  color: var(--el-color-warning);
  vertical-align: middle;
  margin-left: 4px;
}

.bench-note {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  padding: 4px 0;
}

.top-stocks {
  padding: 6px 8px;
}

/* 主题排行 */
.topic-rank {
  margin-top: 16px;
}

.topic-chip {
  cursor: pointer;
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