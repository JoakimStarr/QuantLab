<template>
  <PageContainer narrow>
    <PageHeader
      title="每日晨报"
      subtitle="政策定调 + 外盘隔夜 + 宏观快照 + 市场概况 + AI 综合研判；LLM 不可用时自动降级为纯结构化展示"
    />

    <SectionCard class="mb-6">
      <!-- 工具栏 -->
      <div class="report-toolbar">
        <el-select v-model="reportDate" size="small" style="width: 150px" placeholder="选择日期" @change="loadReport">
          <el-option v-for="d in historyDates" :key="d" :value="d" :label="d" />
        </el-select>
        <el-button size="small" type="primary" :loading="generating" @click="onGenerateClick">
          {{ report ? '重新生成' : '生成晨报' }}
        </el-button>
        <span v-if="report && report.llm_status === 'degraded'" class="degraded-hint">
          AI 综合研判暂不可用（provider 未恢复），当前为纯结构化数据
        </span>
      </div>

      <el-alert
        v-if="report && report.status === 'failed'"
        :title="`生成失败：${report.error || '数据源全部为空'}`"
        type="error"
        :closable="false"
        show-icon
        class="mb-4"
      />

      <template v-if="loading">
        <el-skeleton :rows="8" animated />
      </template>

      <template v-else-if="report">
        <!-- 政策定调 -->
        <div class="report-section">
          <div class="report-section-title">政策定调 · {{ report.report_date }}</div>
          <template v-if="policy">
            <div class="tone-row">
              <el-tag size="small" type="warning" effect="plain">当日定调</el-tag>
              <span class="tone-text">{{ policy.policy_tone || '—' }}</span>
            </div>
            <div v-if="policy.summary" class="tone-summary">{{ policy.summary }}</div>
            <div v-if="policy.sectors?.length" class="ai-block">
              <div class="ai-label">点名板块（政策原文）</div>
              <div class="ai-chips">
                <el-tooltip v-for="s in policy.sectors" :key="s.name" :content="s.reason || ''" placement="top">
                  <el-tag size="small" :type="dirType(s.direction)" effect="light">{{ s.name }} {{ s.direction }}</el-tag>
                </el-tooltip>
              </div>
            </div>
            <div v-if="policy.market_impact" class="impact-row">
              <el-tag size="small" type="danger" effect="plain">市场影响</el-tag>
              <span>{{ policy.market_impact }}</span>
            </div>
          </template>
          <div v-else class="placeholder">该日无政策解读</div>
        </div>

        <!-- AI 综合研判 -->
        <div class="report-section">
          <div class="report-section-title">AI 综合研判</div>
          <div v-if="report.synthesis" class="markdown-body" v-html="renderMarkdown(report.synthesis)"></div>
          <div v-else class="placeholder">AI 研判暂不可用，可查看下方结构化数据</div>
          <div v-if="report.focus_sectors?.length" class="ai-block">
            <div class="ai-label">今日关注</div>
            <div class="ai-chips">
              <el-tooltip v-for="s in report.focus_sectors" :key="s.name" :content="s.reason || ''" placement="top">
                <el-tag size="small" :type="dirType(s.direction)" effect="light">{{ s.name }} {{ s.direction }}</el-tag>
              </el-tooltip>
            </div>
          </div>
          <div v-if="report.outlook" class="outlook-line">
            <el-tag size="small" type="primary" effect="plain">今日展望</el-tag>
            <span>{{ report.outlook }}</span>
          </div>
          <div v-if="report.risk_notes?.length" class="risk-block">
            <div class="ai-label">风险提示</div>
            <ul class="risk-list">
              <li v-for="(n, i) in report.risk_notes" :key="i">{{ n }}</li>
            </ul>
          </div>
        </div>

        <!-- 外盘隔夜 -->
        <div class="report-section">
          <div class="report-section-title">外盘隔夜情绪<span v-if="extSyncedAt" class="section-hint">{{ extSyncedAt }}</span></div>
          <el-table v-if="externalRows.length" :data="externalRows" size="small" border>
            <el-table-column prop="label" label="市场" min-width="90" />
            <el-table-column prop="last_date" label="日期" width="100" />
            <el-table-column prop="close" label="收盘" align="right" width="90" />
            <el-table-column label="涨跌%" align="right" width="90">
              <template #default="{ row }">
                <span v-if="row.ret != null" :class="perfClass(row.ret)">{{ fmtPct(row.ret) }}</span>
                <span v-else class="placeholder">—</span>
              </template>
            </el-table-column>
          </el-table>
          <div v-else class="placeholder">外盘数据未同步</div>
        </div>

        <!-- 宏观快照 -->
        <div class="report-section">
          <div class="report-section-title">宏观指标快照</div>
          <el-table v-if="macroRows.length" :data="macroRows" size="small" border max-height="420">
            <el-table-column prop="indicator" label="指标" width="90" />
            <el-table-column prop="field_name" label="字段" min-width="120" show-overflow-tooltip />
            <el-table-column prop="latest_value" label="最新值" align="right" min-width="90" show-overflow-tooltip />
            <el-table-column prop="unit" label="单位" width="64" align="center" />
            <el-table-column prop="latest_date" label="日期" width="92" />
            <el-table-column label="环比" align="right" width="90">
              <template #default="{ row }">
                <span v-if="row.prev_value != null && row.latest_value != null" :class="perfClass(row.latest_value - row.prev_value)">
                  {{ fmtNum(row.latest_value - row.prev_value) }}
                </span>
                <span v-else>—</span>
              </template>
            </el-table-column>
          </el-table>
          <div v-else class="placeholder">宏观数据未同步</div>
        </div>

        <!-- 市场概况 -->
        <div class="report-section">
          <div class="report-section-title">市场概况</div>
          <div v-if="marketRows.length" class="market-grid">
            <div v-for="m in marketRows" :key="m.code" class="market-card">
              <span class="m-name">{{ m.name }}</span>
              <span class="m-price">{{ m.price }}</span>
              <span class="m-pct" :class="perfClass(m.pct_change)">{{ fmtPct(m.pct_change) }}</span>
            </div>
          </div>
          <div v-else class="placeholder">市场行情未同步</div>
        </div>
      </template>

      <el-empty v-else description="暂无晨报，点击「生成晨报」创建今日晨报" :image-size="72">
        <el-button type="primary" :loading="generating" @click="onGenerateClick">生成晨报</el-button>
      </el-empty>
    </SectionCard>

    <ConfirmDialog
      v-model="confirmVisible"
      title="确认重新生成"
      message="将重新采集数据并调用 AI 生成综合研判，覆盖当前晨报内容。"
      icon="warning"
      confirm-text="重新生成"
      :loading="generating"
      @confirm="doGenerate"
    />

    <div v-if="report" class="disclaimer">{{ report.disclaimer }}</div>
  </PageContainer>
</template>

<script setup>
defineOptions({ name: 'QuantDailyReport' })
import { ref, computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import PageContainer from '@/components/common/PageContainer.vue'
import PageHeader from '@/components/common/PageHeader.vue'
import SectionCard from '@/components/common/SectionCard.vue'
import ConfirmDialog from '@/components/common/ConfirmDialog.vue'
import { renderMarkdown } from '@/utils/markdown'
import {
  getDailyReport,
  generateDailyReport,
  getDailyReportHistory,
} from '@/api/dailyReport'

const loading = ref(false)
const generating = ref(false)
const confirmVisible = ref(false)
const report = ref(null)
const reportDate = ref('')
const historyDates = ref([])

// 各板块数据
const policy = computed(() => report.value?.sections?.policy || null)
const externalRows = computed(() => {
  const items = report.value?.sections?.external?.items || {}
  return Object.values(items)
})
const extSyncedAt = computed(() => {
  const t = report.value?.sections?.external?.synced_at
  if (!t) return ''
  return `（同步于 ${String(t).replace('T', ' ').slice(0, 16)}）`
})
const macroRows = computed(() => report.value?.sections?.macro || [])
const marketRows = computed(() => report.value?.sections?.market || [])

async function loadHistory() {
  try {
    const r = await getDailyReportHistory({ limit: 30 })
    historyDates.value = (r?.items || []).map((it) => it.report_date)
  } catch {
    /* 列表失败不影响主内容 */
  }
}

async function loadReport() {
  loading.value = true
  try {
    const r = await getDailyReport(reportDate.value || undefined)
    report.value = r || null
    // 未指定日期时跟随最新一条
    if (r && !reportDate.value) reportDate.value = r.report_date
  } catch {
    /* 拦截器已提示 */
  } finally {
    loading.value = false
  }
}

async function onGenerateClick() {
  // 已有晨报 → 弹统一确认框；无晨报 → 直接生成
  if (report.value) {
    confirmVisible.value = true
    return
  }
  await doGenerate()
}

async function doGenerate() {
  generating.value = true
  try {
    const r = await generateDailyReport({ date: reportDate.value || undefined, force: !!report.value })
    report.value = r || null
    if (r && !historyDates.value.includes(r.report_date)) {
      historyDates.value.unshift(r.report_date)
    }
    if (r && !reportDate.value) reportDate.value = r.report_date
    ElMessage.success(`晨报${r?.llm_status === 'degraded' ? '已生成（AI 研判降级）' : '已生成'}`)
  } catch (e) {
    if (e?.detail) ElMessage.warning(e.detail)
  } finally {
    generating.value = false
    confirmVisible.value = false
  }
}

function dirType(dir) {
  if (dir === '利好') return 'success'
  if (dir === '利空') return 'danger'
  return 'info'
}

function perfClass(v) {
  if (v == null) return ''
  if (v > 0) return 'perf-up'
  if (v < 0) return 'perf-down'
  return ''
}

function fmtPct(v) {
  if (v == null) return '—'
  return `${v > 0 ? '+' : ''}${Number(v).toFixed(2)}%`
}

function fmtNum(v) {
  if (v == null) return '—'
  return Number(v).toFixed(4)
}

onMounted(async () => {
  await loadHistory()
  await loadReport()
})
</script>

<style scoped>
.report-toolbar {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 14px;
  flex-wrap: wrap;
}

.degraded-hint {
  font-size: 12px;
  color: var(--el-color-warning);
}

.mb-4 {
  margin-bottom: 12px;
}

.report-section {
  margin-bottom: 18px;
  padding: 14px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
}

.report-section-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--el-text-color-secondary);
  margin-bottom: 10px;
}

.section-hint {
  margin-left: 8px;
  font-weight: 400;
  color: var(--el-text-color-placeholder);
}

.tone-row {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 14px;
  margin-bottom: 8px;
}

.tone-text {
  font-weight: 600;
  color: var(--el-text-color-primary);
}

.tone-summary {
  font-size: 13px;
  line-height: 1.8;
  color: var(--el-text-color-regular);
  margin-bottom: 8px;
}

.ai-block {
  margin: 8px 0;
}

.ai-label {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  margin-bottom: 6px;
}

.ai-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.impact-row {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  color: var(--el-text-color-regular);
  padding: 8px 10px;
  border-radius: 6px;
  background: var(--el-fill-color-light);
}

.outlook-line {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  color: var(--el-text-color-regular);
  margin-top: 8px;
}

.risk-block {
  margin-top: 10px;
}

.risk-list {
  margin: 0;
  padding-left: 18px;
  font-size: 13px;
  color: var(--el-text-color-regular);
  line-height: 1.8;
}

.markdown-body {
  font-size: 13px;
  line-height: 1.9;
  color: var(--el-text-color-regular);
}

.placeholder {
  font-size: 13px;
  color: var(--el-text-color-placeholder);
  padding: 8px 0;
}

.market-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(140px, 1fr));
  gap: 10px;
}

.market-card {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 10px 12px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
}

.m-name {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.m-price {
  font-size: 16px;
  font-weight: 600;
  color: var(--el-text-color-primary);
}

.m-pct {
  font-size: 13px;
  font-weight: 600;
}

.perf-up {
  color: var(--el-color-danger);
  font-weight: 600;
}

.perf-down {
  color: var(--el-color-success);
  font-weight: 600;
}

.disclaimer {
  text-align: center;
  font-size: 12px;
  color: var(--el-text-color-placeholder);
  margin: 12px 0 24px;
}
</style>
