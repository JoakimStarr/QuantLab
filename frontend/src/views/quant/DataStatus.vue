<template>
  <PageContainer narrow>
    <div class="page-header mb-6">
      <h2 class="page-title">数据管理</h2>
      <p class="page-desc">管理 qlib 数据源同步与新鲜度</p>
    </div>

    <!-- KPI 概览 -->
    <div class="kpi-grid mb-6">
      <div class="kpi-card">
        <div class="kpi-label">股票总数</div>
        <div class="kpi-value">{{ currentStatus.stock_count || '--' }}</div>
        <div class="kpi-sub">universe: {{ currentStatus.universe || '--' }}</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-label">最新交易日</div>
        <div class="kpi-value">{{ currentStatus.latest_date || '--' }}</div>
        <div class="kpi-sub">{{ daysSinceUpdate }} 天前更新</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-label">同步状态</div>
        <div class="kpi-value">
          <span class="status-badge" :class="statusClass">{{ statusLabel }}</span>
        </div>
        <div class="kpi-sub">{{ qlib.available ? 'qlib 已就绪' : 'qlib 未就绪' }}</div>
      </div>
        <div class="kpi-card">
          <div class="kpi-label">数据时间范围</div>
          <div class="kpi-value" style="font-size: 15px; line-height: 1.6;">
            {{ qlib.earliest_date || '--' }}<br/>~ {{ currentStatus.latest_date || '--' }}
          </div>
          <div class="kpi-sub">{{ qlib.calendar_count ? qlib.calendar_count + ' 个交易日' : '--' }}</div>
        </div>
        <div class="kpi-card">
          <div class="kpi-label">数据覆盖率</div>
          <div class="kpi-value">{{ coveragePercent }}%</div>
          <div class="kpi-sub">{{ currentStatus.stock_count?.toLocaleString() || 0 }} 只股票有数据</div>
        </div>
    </div>

      <!-- 数据损坏告警横幅：latest_date 被置空 + last_error 标记数据损坏时显示 -->
      <el-alert
        v-if="isDataCorrupt"
        class="mb-6 corrupt-alert"
        title="数据损坏 — 已标记下次同步全量重建"
        type="warning"
        :closable="false"
        show-icon
      >
        <template #default>
          检测到数据完整性问题，latest_date 已置空。下次定时同步（工作日 18:00）将自动走 chenditc 全量重建路径。
          如需立即重建，点击右侧「智能同步」按钮。
        </template>
      </el-alert>

      <!-- 数据覆盖率进度条 -->
      <SectionCard v-if="currentStatus.stock_count || qlib.earliest_date" class="mb-6">
        <div class="coverage-section">
          <div class="coverage-header">
            <span class="coverage-title">数据新鲜度</span>
            <span class="coverage-value">{{ coveragePercent }}%</span>
          </div>
          <el-progress :percentage="coveragePercent" :color="coverageColor" :stroke-width="10" :show-text="false" />
          <div class="coverage-detail">
            <span>股票数: {{ currentStatus.stock_count?.toLocaleString() || 0 }}</span>
            <span v-if="qlib.calendar_count">交易日: {{ qlib.calendar_count }}</span>
            <span v-if="qlib.earliest_date">范围: {{ qlib.earliest_date }} ~ {{ currentStatus.latest_date }}</span>
            <span v-if="currentStatus.latest_date">更新: {{ daysSinceUpdate }} 天前</span>
          </div>
        </div>
      </SectionCard>

    <!-- 数据源操作 -->
    <SectionCard class="mb-6">
      <div class="source-header">
        <div class="source-info">
          <div class="source-title">qlib 数据源</div>
          <div class="source-meta">
            <span class="meta-item">
              <span class="meta-label">数据目录:</span>
              <code>{{ qlib.provider_uri || '--' }}</code>
            </span>
            <span class="meta-item">
              <span class="meta-label">数据源:</span>
              <span class="badge badge-info">baostock</span>
            </span>
            <span class="meta-item">
              <span class="meta-label">最后更新:</span>
              <span>{{ formatTime(currentStatus.last_updated) }}</span>
            </span>
          </div>
          <div v-if="currentStatus.last_error && !isDataCorrupt" class="source-error">
            <span class="error-icon">!</span>
            <div class="error-content">
              <div class="error-head">
                <span class="error-category" :class="errorCategoryClass">{{ errorCategoryLabel }}</span>
                <span class="error-msg">{{ errorMessageBody }}</span>
              </div>
              <div v-if="errorSuggestion" class="error-suggestion">{{ errorSuggestion }}</div>
              <div class="error-actions">
                <el-button size="small" type="primary" @click="retrySync" :loading="syncing" :disabled="!qlib.available">重试同步</el-button>
              </div>
            </div>
          </div>
        </div>
        <div class="source-actions">
          <el-button @click="loadPreview()" size="small">预览数据</el-button>
          <el-button @click="loadAll" :loading="loading" size="small">刷新</el-button>
          <div class="sync-years-group">
            <span class="sync-years-label">同步</span>
            <el-input-number v-model="syncYears" :min="1" :max="30" size="small" style="width: 88px" />
            <span class="sync-years-label">年</span>
            <el-button type="primary" @click="smartSync" :loading="syncing" :disabled="!qlib.available || syncing">
              {{ syncing ? '同步中...' : '开始同步' }}
            </el-button>
          </div>
          <el-button type="success" @click="showEodDialog = true" :loading="eodSyncing" :disabled="!qlib.available || eodSyncing">
            {{ eodSyncing ? '增量同步中...' : '增量同步' }}
          </el-button>
          <el-button type="warning" @click="doSyncIndices" :loading="indexSyncing" :disabled="!qlib.available || indexSyncing">
            {{ indexSyncing ? '指数同步中...' : '同步指数' }}
          </el-button>
          <el-button type="info" @click="doIntegrityCheck" :loading="integrityChecking" :disabled="!qlib.available">
            {{ integrityChecking ? '校验中...' : '数据校验' }}
          </el-button>
        </div>
      </div>
    </SectionCard>

    <!-- 同步进度提示（轮询 /sync-progress 实时百分比） -->
    <div v-if="syncing" class="sync-progress mb-6">
      <div class="progress-header">
        <span class="progress-status">{{ syncProgress?.message || syncProgressText }}</span>
        <span class="progress-pct">{{ (syncProgress?.progress_pct || 0).toFixed(1) }}%</span>
      </div>
      <el-progress
        :percentage="syncProgress?.progress_pct || 0"
        :status="syncProgress?.status === 'failed' ? 'exception' : syncProgress?.status === 'done' ? 'success' : ''"
        :stroke-width="14"
        :show-text="false"
      />
      <div v-if="syncProgress?.data_source" class="progress-detail">
        <span>路径: {{ syncProgress.data_source }}</span>
        <span v-if="syncProgress.started_at">开始: {{ syncProgress.started_at.slice(11, 19) }}</span>
      </div>
    </div>

    <!-- 数据状态详情 -->
    <SectionCard title="数据状态详情">
        <div class="quick-preview-bar">
          <span class="quick-preview-label">快速预览:</span>
          <el-button size="small" @click="loadPreview('csi300')">沪深300</el-button>
          <el-button size="small" @click="loadPreview('csi500')">中证500</el-button>
          <el-button size="small" @click="loadPreview('all')">全部A股</el-button>
          <el-button size="small" @click="loadPreview('sh600000')">浦发银行</el-button>
        </div>

      <el-table :data="statusList" size="small" stripe empty-text="暂无数据" max-height="400">
        <el-table-column prop="universe" label="股票池" width="120" align="center">
          <template #default="{row}">
            <span class="font-mono">{{ row.universe }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="latest_date" label="最新日期" width="130" align="center" />
        <el-table-column prop="stock_count" label="股票数" width="100" align="center">
          <template #default="{row}">
            <span class="num">{{ row.stock_count?.toLocaleString() || '--' }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="row_count" label="记录数" width="100" align="center">
          <template #default="{row}">
            <span class="num">{{ row.row_count?.toLocaleString() || '--' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="100" align="center">
          <template #default="{row}">
            <span class="status-badge sm" :class="getStatusClass(row.status)">{{ row.status }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="last_error" label="错误信息" min-width="200" show-overflow-tooltip>
          <template #default="{row}">
            <span v-if="row.last_error" class="error-text">{{ row.last_error }}</span>
            <span v-else class="text-muted">--</span>
          </template>
        </el-table-column>
        <el-table-column label="更新时间" width="180" align="center">
          <template #default="{row}">
            <span class="time">{{ formatTime(row.last_updated) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="100" align="center" fixed="right">
          <template #default="{row}">
            <el-button size="small" link type="primary" @click="loadPreview(row.universe)">预览</el-button>
          </template>
        </el-table-column>
      </el-table>
    </SectionCard>

    <!-- 同步统计（成功率/耗时/路径分布/失败原因） -->
    <SectionCard v-if="syncStats" title="同步统计" class="mt-6">
      <div class="sync-stats-grid">
        <div class="stat-cell">
          <div class="stat-label">最近 30 天成功率</div>
          <div class="stat-value">
            <span :class="syncStats.success_rate?.rate >= 0.8 ? 'text-success' : syncStats.success_rate?.rate >= 0.5 ? 'text-warning' : 'text-danger'">
              {{ ((syncStats.success_rate?.rate || 0) * 100).toFixed(1) }}%
            </span>
            <span class="stat-sub">成功 {{ syncStats.success_rate?.ok || 0 }} / 失败 {{ syncStats.success_rate?.failed || 0 }} / 共 {{ syncStats.success_rate?.total || 0 }}</span>
          </div>
        </div>
        <div class="stat-cell">
          <div class="stat-label">平均耗时</div>
          <div class="stat-value">
            {{ formatDuration(syncStats.duration_stats?.avg) }}
            <span class="stat-sub">p50 {{ formatDuration(syncStats.duration_stats?.p50) }} / p95 {{ formatDuration(syncStats.duration_stats?.p95) }}</span>
          </div>
        </div>
        <div class="stat-cell">
          <div class="stat-label">路径分布</div>
          <div class="stat-value">
            <span v-if="Object.keys(syncStats.path_distribution || {}).length" class="path-chips">
              <el-tag v-for="(cnt, path) in syncStats.path_distribution" :key="path" size="small" class="mr-1">
                {{ path }} ×{{ cnt }}
              </el-tag>
            </span>
            <span v-else class="text-muted">--</span>
          </div>
        </div>
      </div>
      <div v-if="syncStats.failure_reasons?.length" class="failure-reasons mt-3">
        <div class="stat-label mb-1">失败原因</div>
        <div class="reason-chips">
          <el-tag
            v-for="r in syncStats.failure_reasons"
            :key="r.reason"
            size="small"
            type="danger"
            class="mr-1"
          >{{ r.reason }} ×{{ r.count }}</el-tag>
        </div>
      </div>
    </SectionCard>

    <!-- 同步历史 -->
    <SectionCard v-if="syncHistory.length" title="同步历史" class="mt-6">
      <el-table :data="syncHistory" size="small" stripe max-height="300">
        <el-table-column prop="version" label="版本" width="120" />
        <el-table-column prop="release_date" label="发布日期" width="120" />
        <el-table-column prop="file_size_mb" label="文件大小" width="100" align="right">
          <template #default="{row}">{{ row.file_size_mb ? row.file_size_mb + ' MB' : '--' }}</template>
        </el-table-column>
        <el-table-column label="状态" width="100" align="center">
          <template #default="{row}">
            <span class="status-badge sm" :class="getStatusClass(row.status)">{{ row.status }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="started_at" label="开始时间" width="180">
          <template #default="{row}"><span class="time">{{ formatTime(row.started_at) }}</span></template>
        </el-table-column>
        <el-table-column prop="finished_at" label="完成时间" width="180">
          <template #default="{row}"><span class="time">{{ formatTime(row.finished_at) }}</span></template>
        </el-table-column>
          <el-table-column label="耗时" width="90" align="center">
            <template #default="{row}">
              <span class="time">{{ formatDuration(row.duration_seconds) }}</span>
            </template>
          </el-table-column>
        <el-table-column prop="error" label="错误" min-width="200" show-overflow-tooltip>
          <template #default="{row}">
            <span v-if="row.error" class="error-text">{{ row.error }}</span>
            <span v-else class="text-muted">--</span>
          </template>
        </el-table-column>
      </el-table>
    </SectionCard>

    <!-- 数据预览对话框 -->
    <el-dialog v-model="previewVisible" title="数据预览" width="80%" :close-on-click-modal="true">
        <div class="preview-toolbar">
          <el-input
            v-model="previewCodeInput"
            placeholder="输入股票代码如 sh600000 或股票池 csi300"
            style="width: 260px"
            @keyup.enter="loadPreview(previewCodeInput)"
          />
          <el-button @click="loadPreview(previewCodeInput)" size="small">查询</el-button>
          <el-button @click="loadPreview()" size="small">最近数据</el-button>
          <span class="preview-hint-inline">
            {{ previewCode ? '当前: ' + previewCode : '最近数据' }}
            ({{ previewData.length }} 条)
          </span>
        </div>
        <el-table :data="previewData" size="small" stripe max-height="500" v-loading="previewLoading">
          <el-table-column
            v-for="col in previewColumns"
            :key="col"
            :prop="col"
            :label="col"
            min-width="120"
            show-overflow-tooltip
          />
          <template #empty>
            <div class="preview-empty">
              <p v-if="!previewLoading">暂无数据，请检查股票代码是否正确，或尝试输入其他代码</p>
              <p v-else>加载中...</p>
            </div>
          </template>
        </el-table>
      </el-dialog>
  
    <!-- 增量EOD同步对话框 -->
    <el-dialog v-model="showEodDialog" title="增量EOD同步" width="460px" :close-on-click-modal="false">
      <div class="eod-sync-form">
        <p class="eod-hint">
          基于 <strong>baostock</strong>（主源, 一次拉全市场）或 <strong>akshare</strong>（兜底, 逐只爬）拉取最近 N 天的日K数据，<br>
          增量追加到 qlib bin 目录。baostock 含 ST 标记和估值字段，推荐使用。
        </p>
        <el-form label-width="90px" label-position="left">
          <el-form-item label="股票池">
            <el-select v-model="eodForm.universe" style="width: 100%">
              <el-option label="沪深300" value="csi300" />
              <el-option label="中证500" value="csi500" />
              <el-option label="全部A股" value="all" />
            </el-select>
          </el-form-item>
          <el-form-item label="同步天数">
            <el-slider v-model="eodForm.days" :min="1" :max="30" show-input style="width: 100%" />
          </el-form-item>
          <el-form-item label="覆盖已有">
            <el-switch v-model="eodForm.overwrite" />
            <span class="eod-warn-hint">开启后将用 baostock/akshare 数据覆盖已有日期（可能因复权差异导致价格断裂）</span>
          </el-form-item>
        </el-form>
        <div v-if="eodResult" class="eod-result">
          <el-alert
            :title="eodResultTitle"
            :type="eodResult.failed > 0 ? 'warning' : 'success'"
            :closable="false"
            show-icon
          />
          <div v-if="eodResult.new_dates?.length" class="eod-dates">
            <span class="eod-dates-label">新增日期:</span>
            <el-tag v-for="d in eodResult.new_dates" :key="d" size="small" class="eod-date-tag">{{ d }}</el-tag>
          </div>
        </div>
      </div>
      <template #footer>
        <el-button @click="showEodDialog = false">关闭</el-button>
        <el-button type="primary" @click="doEodSync" :loading="eodSyncing" :disabled="eodSyncing">
          开始同步
        </el-button>
      </template>
    </el-dialog>

    <!-- 数据完整性校验弹窗 -->
    <el-dialog v-model="showIntegrityDialog" title="数据完整性校验" width="760px">
      <div v-if="integrityChecking" v-loading="true" style="min-height: 200px"></div>
      <div v-else-if="validationReport" class="integrity-result">
        <el-alert
          :title="validationReport.summary"
          :type="validationReport.ok ? 'success' : 'warning'"
          :closable="false" show-icon style="margin-bottom: 12px"
        />
        <div v-if="validationReport.sync_state?.syncing" class="calendar-sync-note">
          <el-tag size="small" type="warning">回填中</el-tag>
          <span class="calendar-sync-text">数据同步进行中，校验结果可能不完整，请等同步完成后再校验</span>
        </div>
        <div class="check-list">
          <div v-for="(chk, name) in validationReport.checks" :key="name" class="check-item">
            <el-tag :type="checkStatusTagType(chk.status)" size="small" class="check-status">{{ checkStatusLabel(chk.status) }}</el-tag>
            <span class="check-name">{{ checkName(name) }}</span>
            <span class="check-msg">{{ chk.message }}</span>
          </div>
        </div>
        <div v-if="driftNeedsRepair" class="drift-box">
          <div class="drift-title">待修复差异</div>
          <el-tag v-if="validationReport.checks.fields.bad_size_stocks" size="small">bin 长度异常 {{ validationReport.checks.fields.bad_size_stocks }} 只</el-tag>
          <el-tag v-if="validationReport.drift.stocks_with_gaps" size="small">疑似损坏 {{ validationReport.drift.stocks_with_gaps }} 只</el-tag>
          <el-tag v-if="validationReport.drift.missing_calendar_days" size="small">day.txt 缺 {{ validationReport.drift.missing_calendar_days }} 天</el-tag>
          <el-tag v-if="validationReport.drift.missing_field_files" size="small">字段文件缺 {{ validationReport.drift.missing_field_files }} 个</el-tag>
          <el-tag v-if="validationReport.drift.db_without_bin" size="small">DB 无 bin {{ validationReport.drift.db_without_bin }} 只</el-tag>
          <el-tag v-if="validationReport.drift.range_mismatch" size="small">区间错位 {{ validationReport.drift.range_mismatch }} 只</el-tag>
          <el-tag v-if="validationReport.drift.bin_without_db" size="small" type="info">bin 无 DB 记录 {{ validationReport.drift.bin_without_db }} 只</el-tag>
          <el-tag v-if="validationReport.drift.pg_missing_dates" size="small" type="warning">缺 {{ validationReport.drift.pg_missing_dates }} 个交易日（需 baostock）</el-tag>
        </div>
        <div v-if="integrityResult && integrityResult.calendar_sync" class="calendar-sync-note">
          <el-tag size="small" type="info">只读说明</el-tag>
          <span class="calendar-sync-text">{{ integrityResult.calendar_sync }}</span>
        </div>
        <div class="integrity-stats" v-if="validationReport.rows !== undefined">
          <div class="stat-item"><span class="stat-label">qlib 抽样行数</span><span class="stat-value">{{ validationReport.rows }}</span></div>
          <div class="stat-item"><span class="stat-label">抽样股票</span><span class="stat-value">{{ validationReport.total_stocks }}</span></div>
          <div class="stat-item"><span class="stat-label">bin 股票数</span><span class="stat-value">{{ validationReport.checks.coverage.stocks_in_bin }}</span></div>
        </div>
      </div>
      <div v-else-if="integrityResult && !integrityResult.ok">
        <el-alert :title="integrityResult.error || '校验失败'" type="error" :closable="false" show-icon />
      </div>
      <template #footer>
        <el-button @click="showIntegrityDialog = false">关闭</el-button>
        <el-button
          v-if="driftNeedsRepair && !validationReport?.sync_state?.syncing"
          type="warning" @click="doRepair" :loading="repairing" :disabled="repairing"
        >
          {{ repairLabel }}
        </el-button>
        <el-button type="primary" @click="doIntegrityCheck" :loading="integrityChecking" :disabled="integrityChecking">重新校验</el-button>
      </template>
    </el-dialog>
  </PageContainer>
</template>

<script setup>
defineOptions({ name: 'QuantData' })
import { ref, reactive, computed, onMounted, onBeforeUnmount, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus/es/components/message/index'
import { ElMessageBox } from 'element-plus'
import PageContainer from '@/components/common/PageContainer.vue'
import SectionCard from '@/components/common/SectionCard.vue'
import { getQuantDataStatus, syncQuantData, getQlibStatus, getDataPreview, getSyncHistory, getSyncStats, eodSync, getEodResult, syncIndices, syncIndustry, getSyncProgress, validateData, repairData } from '@/api/quant'

const statusList = ref([])
const route = useRoute()
const loading = ref(false)
const syncing = ref(false)
const qlib = reactive({ available: false, provider_uri: '', earliest_date: null, calendar_count: 0 })
const syncProgress = ref(null)
let progressTimer = null
// 轮询连续拿不到进度（data=null）的次数，超过阈值停止轮询，避免空转泄漏
let nullPollCount = 0
const previewVisible = ref(false)
const previewData = ref([])
const previewLoading = ref(false)
const previewCode = ref('')
const previewCodeInput = ref('')
const syncHistory = ref([])
const syncStats = ref(null)
const showEodDialog = ref(false)
const eodSyncing = ref(false)
const eodResult = ref(null)
const eodForm = reactive({ universe: 'csi300', days: 5, overwrite: false })
const syncYears = ref(5)
const indexSyncing = ref(false)
const industrySyncing = ref(false)
  const integrityChecking = ref(false)
  const showIntegrityDialog = ref(false)
  const integrityResult = ref(null)
  const validationReport = ref(null)
  const repairing = ref(false)
const currentStatus = computed(() => statusList.value[0] || {})

// 数据损坏检测：latest_date 被置空且 last_error 标记数据损坏时为 true
// （兼容旧版 smart_sync 路径写入的状态；baostock 回填失败时 status=failed + last_error 由 sync_runner 标记）
const isDataCorrupt = computed(() => {
  return currentStatus.value.latest_date === null
    && !!currentStatus.value.last_error
    && currentStatus.value.last_error.includes('数据损坏')
})

const statusLabel = computed(() => {
  const s = currentStatus.value.status
  if (s === 'ok') return '正常'
  if (s === 'syncing') return '同步中'
  if (s === 'failed') return '失败'
  return '--'
})

const statusClass = computed(() => {
  const s = currentStatus.value.status
  if (s === 'ok') return 'success'
  if (s === 'syncing') return 'warning'
  if (s === 'failed') return 'danger'
  return ''
})

const daysSinceUpdate = computed(() => {
  if (!currentStatus.value.latest_date) return '--'
  const diff = Math.floor((Date.now() - new Date(currentStatus.value.latest_date).getTime()) / 86400000)
  return diff
})

const syncProgressText = computed(() => {
  if (syncProgress.value?.data_source === 'repair') return '正在执行数据补齐（独立进程后台运行），请耐心等待...'
  return '正在通过 baostock 逐日回填全市场数据（从最新向旧），请耐心等待...'
})

const eodResultTitle = computed(() => {
  const r = eodResult.value
  if (!r) return ''
  if (r.ok === false || r.failed === undefined) return r.error || '同步失败'
  return `同步完成: 成功 ${r.success ?? 0}/${r.total_stocks ?? 0}，新增 ${r.new_dates?.length || 0} 个交易日`
})

const previewColumns = computed(() => {
  if (!previewData.value.length) return []
  return Object.keys(previewData.value[0])
})

const coveragePercent = computed(() => {
  const days = daysSinceUpdate.value
  if (days === '--') return 0
  if (days <= 1) return 100
  if (days <= 3) return 90
  if (days <= 7) return 70
  if (days <= 14) return 50
  if (days <= 30) return 30
  return 10
})

const coverageColor = computed(() => {
  const p = coveragePercent.value
  if (p >= 90) return '#67c23a'
  if (p >= 70) return '#e6a23c'
  if (p >= 50) return '#f56c6c'
  return '#909399'
})

function getStatusClass(status) {
  if (status === 'ok') return 'success'
  if (status === 'syncing') return 'warning'
  if (status === 'failed') return 'danger'
  return ''
}

function formatTime(ts) {
  if (!ts) return '--'
  return ts.replace('T', ' ').slice(0, 19)
}

function formatDuration(seconds) {
  if (!seconds || seconds <= 0) return '--'
  if (seconds < 60) return seconds + 's'
  if (seconds < 3600) return Math.floor(seconds / 60) + 'm' + (seconds % 60 > 0 ? Math.round(seconds % 60) + 's' : '')
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  return h + 'h' + (m > 0 ? m + 'm' : '')
}

async function loadPreview(code) {
  previewCode.value = code || ''
    previewCodeInput.value = code || ''
  previewVisible.value = true
  previewLoading.value = true
  try {
    const data = await getDataPreview(code, 20)
    previewData.value = data?.items || data || []
  } catch (e) {
    if (e !== 'cancel') ElMessage.error('数据预览加载失败')
    previewData.value = []
  } finally {
    previewLoading.value = false
  }
}

async function loadSyncHistory() {
  try {
    const data = await getSyncHistory(10)
    syncHistory.value = data?.items || []
  } catch (e) {
    // 静默失败
  }
}

async function loadStatus() {
  try {
    const data = await getQuantDataStatus()
    statusList.value = data?.items || []
    // syncing 状态由 progressTimer 统一轮询；检测到外部（如定时任务）触发的 syncing 时启动进度轮询
    const cur = statusList.value[0]
    if (cur && cur.status === 'syncing' && !progressTimer && !syncing.value) {
      syncing.value = true
      startProgressPolling()
    }
  } catch (e) {
    if (e !== 'cancel') ElMessage.error('加载数据状态失败')
  }
}

async function loadQlib() {
  try {
    const data = await getQlibStatus()
    qlib.available = data?.available || false
    qlib.provider_uri = data?.provider_uri || ''
      qlib.earliest_date = data?.earliest_date || null
      qlib.calendar_count = data?.calendar_count || 0
  } catch (e) {
    if (e !== 'cancel') ElMessage.error('qlib 状态检查失败')
  }
}

async function loadSyncStats() {
  try {
    const data = await getSyncStats(30)
    syncStats.value = data
  } catch (e) {
    if (e !== 'cancel') syncStats.value = null
  }
}

async function loadAll() {
  loading.value = true
  await Promise.all([loadStatus(), loadQlib(), loadSyncHistory(), loadSyncStats()])
  loading.value = false
}

async function smartSync() {
  syncing.value = true
  syncProgress.value = null
  try {
    await syncQuantData({ years: syncYears.value })
    ElMessage.success(`数据同步已提交（baostock 回填 ${syncYears.value} 年，后台执行）`)
    startProgressPolling()
  } catch (e) {
    if (e !== 'cancel') ElMessage.error('数据同步提交失败')
    syncing.value = false
  }
}

function startProgressPolling() {
  if (progressTimer) clearInterval(progressTimer)
  nullPollCount = 0
  pollSyncProgress()
  progressTimer = setInterval(pollSyncProgress, 1000)
}

const taskLabel = (src) => ({
  repair: '数据补齐',
  backfill: '数据回填',
  baostock: '数据同步',
  eod: '增量同步',
  eastmoney: '宏观同步',
  indices: '指数同步',
}[src] || '后台任务')

async function pollSyncProgress() {
  try {
    const data = await getSyncProgress()
    syncProgress.value = data
    if (data?.status === 'done' || data?.status === 'failed') {
      if (progressTimer) { clearInterval(progressTimer); progressTimer = null }
      nullPollCount = 0
      syncing.value = false
      const label = taskLabel(data?.data_source)
      if (data?.status === 'done') {
        ElMessage.success(label + '完成')
        // 补齐完成后自动重新校验，刷新报告
        if (data?.data_source === 'repair' && showIntegrityDialog.value) {
          doIntegrityCheck()
        }
      } else {
        ElMessage.error(label + '失败: ' + (data?.error || '未知错误'))
      }
      loadAll()
      return
    }
    if (data === null) {
      // 连续一段时间无进度（worker 未写入/已退出且无残留文件），停止轮询
      nullPollCount += 1
      if (nullPollCount > 30) {
        if (progressTimer) { clearInterval(progressTimer); progressTimer = null }
        nullPollCount = 0
        syncing.value = false
      }
    } else {
      nullPollCount = 0
    }
  } catch (e) {
    // 静默失败，继续轮询
  }
}


async function doEodSync() {
  eodSyncing.value = true
  eodResult.value = null
  try {
    // EOD 同步是后台任务，提交后立即返回（无实际结果），需轮询 /eod-result 获取真实结果
    await eodSync(eodForm.universe, eodForm.days, eodForm.overwrite)
    ElMessage.success('增量同步已提交，后台执行中')
    await loadEodResult()
    eodSyncing.value = false
    loadAll()
  } catch (e) {
    if (e !== 'cancel') ElMessage.error('增量EOD同步失败: ' + (e?.message || e))
    eodSyncing.value = false
  }
}

async function loadEodResult() {
  // 后台任务完成后轮询真实结果（最多等 60s，避免进度未写入时拿到 null）
  for (let i = 0; i < 60; i++) {
    await new Promise(r => setTimeout(r, 1000))
    try {
      const data = await getEodResult()
      if (data && data.ok !== false && data.success !== undefined) {
        eodResult.value = data
        return data
      }
    } catch (e) {
      // 结果尚未写入，继续轮询
    }
  }
  return null
}

async function doSyncIndices() {
  indexSyncing.value = true
  try {
    await syncIndices()
    ElMessage.success('指数同步已提交，后台执行中')
    setTimeout(loadAll, 3000)
  } catch (e) {
    if (e !== 'cancel') ElMessage.error('指数同步失败: ' + (e?.message || e))
  } finally {
    indexSyncing.value = false
  }
}

async function doSyncIndustry() {
  industrySyncing.value = true
  try {
    const data = await syncIndustry()
    ElMessage.success(`行业分类同步成功：${data?.industries ?? 0} 个行业, ${data?.stocks ?? 0} 只股票`)
  } catch {
    /* 拦截器已提示 */
  } finally {
    industrySyncing.value = false
  }
}

async function doIntegrityCheck() {
  integrityChecking.value = true
  showIntegrityDialog.value = true
  integrityResult.value = null
  validationReport.value = null
  try {
    // 校验为只读操作：不重建 day.txt、不触发任何同步，避免与回填互相影响。
    // day.txt 与数据库不一致时由「日历同步」检查项报告，可用「一键补齐」修复。
    const data = await validateData()
    validationReport.value = data
    integrityResult.value = { ...data, calendar_sync: '校验只读，未改动任何数据' }
    ElMessage.success(data?.summary || '校验完成')
  } catch (e) {
    integrityResult.value = { ok: false, error: String(e?.message || e) }
  } finally {
    integrityChecking.value = false
  }
}

const driftNeedsRepair = computed(() => !!validationReport.value?.drift?.needs_repair)
const repairLabel = computed(() => {
  const d = validationReport.value?.drift
  if (d?.needs_baostock) return `一键补齐（含 baostock ${d.pg_missing_dates} 天）`
  return '一键补齐'
})
const checkStatusLabel = (s) => ({ ok: '正常', warn: '警告', error: '异常' }[s] || s)
const checkStatusTagType = (s) => ({ ok: 'success', warn: 'warning', error: 'danger' }[s] || 'info')
const checkName = (n) => ({
  fields: 'bin 字段完整性',
  fieldset: '字段集合',
  calendar: '日历同步',
  coverage: '覆盖一致性',
  qlib: 'qlib 可读性',
}[n] || n)

async function doRepair() {
  const d = validationReport.value?.drift
  if (!d) return
  const f = validationReport.value?.checks?.fields || {}
  const parts = [
    d.missing_calendar_days ? `day.txt 缺 ${d.missing_calendar_days} 天` : '',
    f.bad_size_stocks ? `bin 长度异常 ${f.bad_size_stocks} 只` : '',
    d.stocks_with_gaps ? `疑似损坏 ${d.stocks_with_gaps} 只` : '',
    d.missing_field_files ? `字段文件缺 ${d.missing_field_files} 个` : '',
    d.db_without_bin ? `DB 无 bin ${d.db_without_bin} 只` : '',
    d.range_mismatch ? `区间错位 ${d.range_mismatch} 只` : '',
  ].filter(Boolean)
  let msg = '将修复：' + (parts.join('、') || 'bin 数据不一致')
  if (d.needs_baostock) msg += `<br>另需从 baostock 补拉 ${d.pg_missing_dates} 个缺失交易日（消耗网络与请求配额）。`
  try {
    await ElMessageBox.confirm(msg + '<br>确认执行？', '一键补齐', {
      confirmButtonText: '执行',
      cancelButtonText: '取消',
      type: 'warning',
      dangerouslyUseHTMLString: true,
    })
  } catch {
    return
  }
  repairing.value = true
  syncing.value = true
  syncProgress.value = null
  try {
    await repairData({ include_baostock: !!d.needs_baostock, universe: 'all' })
    ElMessage.success('补齐任务已提交（独立进程后台执行）')
    startProgressPolling()
  } catch (e) {
    if (e?.code !== 'SYNC_IN_PROGRESS') {
      // 非 409 冲突才重复提示（拦截器已弹过"正在同步/修复中"）
      if (e !== 'cancel') ElMessage.error('补齐提交失败: ' + (e?.message || e))
    }
    syncing.value = false
  } finally {
    repairing.value = false
  }
}

// 解析后端错误信息（格式: [分类] 详情\n建议: 建议内容）
const errorCategoryLabel = computed(() => {
  const err = currentStatus.value.last_error || ''
  const m = err.match(/^\[([^\]]+)\]/)
  return m ? m[1] : '错误'
})
const errorCategoryClass = computed(() => {
  const label = errorCategoryLabel.value
  if (label.includes('网络')) return 'network'
  if (label.includes('磁盘')) return 'disk_full'
  if (label.includes('损坏')) return 'data_corrupt'
  if (label.includes('中断') || label.includes('超时')) return 'interrupted'
  return ''
})
const errorMessageBody = computed(() => {
  const err = currentStatus.value.last_error || ''
  const body = err.replace(/^\[[^\]]+\]\s*/, '').split('\n')[0]
  return body || err
})
const errorSuggestion = computed(() => {
  const err = currentStatus.value.last_error || ''
  const m = err.match(/建议[:：]\s*([\s\S]+)$/)
  return m ? m[1].trim() : ''
})

async function retrySync() {
  syncing.value = true
  try {
    await syncQuantData({})
    ElMessage.success('已重新提交数据同步（后台执行）')
    setTimeout(loadStatus, 3000)
  } catch (e) {
    if (e !== 'cancel') ElMessage.error('重试同步提交失败')
    syncing.value = false
  }
}

onMounted(() => { loadAll() })

watch(
  () => route.query.preview,
  (code) => {
    if (code) loadPreview(String(code))
  },
  { immediate: true },
)
onBeforeUnmount(() => { if (progressTimer) clearInterval(progressTimer) })
</script>

<style scoped lang="scss">
.page-header { animation: fadeInUp 0.5s var(--ease-out-expo); }
.page-title { font-size: var(--font-size-2xl); font-weight: 700; color: var(--text-primary); }
.page-desc { font-size: var(--font-size-sm); color: var(--text-secondary); margin-top: 4px; }

.kpi-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 16px;
  @media (max-width: 767px) { grid-template-columns: repeat(2, 1fr); }
}

.kpi-card {
  background: var(--bg-card);
  border: 1px solid var(--border-light);
  border-radius: 8px;
  padding: 20px;
  .kpi-label { font-size: 13px; color: var(--text-tertiary); margin-bottom: 8px; }
  .kpi-value { font-size: 28px; font-weight: 700; color: var(--text-primary); font-variant-numeric: tabular-nums; }
  .kpi-sub { font-size: 12px; color: var(--text-tertiary); margin-top: 6px; }
}

.source-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
}
.source-title { font-size: var(--font-size-lg); font-weight: 600; color: var(--text-primary); }
.source-meta { display: flex; flex-wrap: wrap; gap: 16px; margin-top: 8px; font-size: 13px; color: var(--text-secondary); }
.meta-label { color: var(--text-tertiary); margin-right: 4px; }
.source-meta code { background: var(--bg-tertiary); padding: 2px 6px; border-radius: 4px; font-size: 12px; color: var(--primary); }
.path-prediction {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
  padding: 6px 12px;
  background: var(--bg-secondary, #f5f7fa);
  border-radius: 6px;
  font-size: var(--font-size-sm, 13px);
}
.path-prediction .path-reason {
  color: var(--text-secondary, #909399);
}
.source-actions { display: flex; gap: 8px; flex-shrink: 0; }

.source-error {
  margin-top: 10px;
  padding: 8px 12px;
  background: rgba(210, 69, 69, 0.06);
  border: 1px solid rgba(210, 69, 69, 0.2);
  border-radius: 6px;
  font-size: 13px;
  color: var(--danger);
  display: flex;
  align-items: center;
  gap: 8px;
  .error-icon {
    display: inline-flex;
    width: 18px; height: 18px;
    border-radius: 50%;
    background: var(--danger);
    color: #fff;
    align-items: center;
    justify-content: center;
    font-size: 12px;
    font-weight: 700;
    flex-shrink: 0;
  }
}

.source-error { align-items: flex-start; }
.error-content { flex: 1; min-width: 0; }
.error-head { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.error-category {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 4px;
  font-size: 11px;
  font-weight: 600;
  flex-shrink: 0;
  background: rgba(210, 69, 69, 0.15);
  color: var(--danger);
}
.error-category.network { background: rgba(200, 128, 28, 0.15); color: var(--warning); }
.error-category.disk_full { background: rgba(210, 69, 69, 0.15); color: var(--danger); }
.error-category.data_corrupt { background: rgba(31, 75, 160, 0.12); color: var(--primary); }
.error-category.interrupted { background: rgba(200, 128, 28, 0.15); color: var(--warning); }
.error-msg { font-size: 13px; color: var(--danger); word-break: break-word; }
.error-suggestion { margin-top: 6px; font-size: 12px; color: var(--text-secondary); }
.error-actions { margin-top: 8px; }

.sync-progress {
  background: var(--bg-card);
  border: 1px solid var(--border-light);
  border-radius: 8px;
  padding: 20px;
  .progress-bar {
    height: 4px;
    background: var(--bg-tertiary);
    border-radius: 2px;
    overflow: hidden;
    margin-bottom: 12px;
  }
  .progress-indicator {
    height: 100%;
    background: var(--primary);
    border-radius: 2px;
    animation: progress-pulse 2s ease-in-out infinite;
  }
  .progress-text { font-size: 13px; color: var(--text-secondary); }
  .progress-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }
  .progress-status { font-size: 13px; color: var(--text-primary); }
  .progress-pct { font-size: 13px; font-weight: 600; color: var(--primary); }
  .progress-detail { display: flex; justify-content: space-between; margin-top: 6px; font-size: 12px; color: var(--text-secondary); }
}

@keyframes progress-pulse {
  0%, 100% { width: 30%; opacity: 0.7; }
  50% { width: 70%; opacity: 1; }
}

.status-badge {
  display: inline-block;
  padding: 4px 10px;
  border-radius: 9999px;
  font-size: 12px;
  font-weight: 500;
  &.sm { padding: 2px 8px; font-size: 11px; }
  &.success { background: rgba(31, 157, 107, 0.1); color: var(--success); }
  &.warning { background: rgba(200, 128, 28, 0.1); color: var(--warning); }
  &.danger { background: rgba(210, 69, 69, 0.1); color: var(--danger); }
}

.badge {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 4px;
  font-size: 12px;
  font-weight: 500;
  &.badge-info { background: rgba(31, 75, 160, 0.08); color: var(--primary); }
}

.font-mono { font-family: var(--font-mono, monospace); font-size: 13px; }
.num { font-variant-numeric: tabular-nums; font-weight: 500; }
.time { font-size: 12px; color: var(--text-tertiary); font-variant-numeric: tabular-nums; }
.error-text { color: var(--danger); font-size: 12px; }
.text-muted { color: var(--text-tertiary); }

.mt-6 { margin-top: 24px; }
.preview-hint { font-size: 13px; color: var(--text-secondary); margin-bottom: 12px; }

.preview-toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 16px;
  flex-wrap: wrap;
}
.preview-hint-inline { font-size: 13px; color: var(--text-secondary); margin-left: auto; }
.preview-empty { padding: 32px 0; text-align: center; color: var(--text-tertiary); font-size: 13px; }

.quick-preview-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 16px;
  flex-wrap: wrap;
}
.quick-preview-label { font-size: 13px; color: var(--text-tertiary); margin-right: 4px; }

.coverage-section { padding: 4px 0; }
.coverage-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
}
.coverage-title { font-size: 14px; font-weight: 600; color: var(--text-primary); }
.coverage-value { font-size: 20px; font-weight: 700; color: var(--primary); font-variant-numeric: tabular-nums; }
.coverage-detail {
  display: flex;
  gap: 24px;
  margin-top: 12px;
  font-size: 13px;
  color: var(--text-secondary);
  flex-wrap: wrap;
}

@keyframes fadeInUp {
  from { opacity: 0; transform: translateY(8px); }
  to { opacity: 1; transform: translateY(0); }
}

.eod-sync-form { padding: 0 4px; }
.eod-hint { font-size: 13px; color: var(--text-secondary); line-height: 1.6; margin-bottom: 16px; }
.eod-result { margin-top: 16px; }
.eod-dates { margin-top: 12px; display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
.eod-dates-label { font-size: 13px; color: var(--text-secondary); }
.eod-date-tag { font-family: var(--font-mono, monospace); }
.eod-warn-hint { margin-left: 12px; font-size: 12px; color: var(--text-tertiary); }
.sync-years-group { display: inline-flex; align-items: center; gap: 6px; }
.sync-years-label { font-size: 13px; color: var(--text-secondary); }

.sync-stats-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px; }
.sync-stats-grid .stat-cell { display: flex; flex-direction: column; gap: 4px; }
.sync-stats-grid .stat-label { font-size: 12px; color: var(--text-tertiary); }
.sync-stats-grid .stat-value { font-size: 18px; font-weight: 700; color: var(--text-primary); display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap; }
.sync-stats-grid .stat-sub { font-size: 12px; font-weight: 400; color: var(--text-secondary); }
.path-chips, .reason-chips { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
.text-success { color: var(--success, #67c23a); }
.text-warning { color: var(--warning, #e6a23c); }
.text-danger { color: var(--danger, #f56c6c); }
.mr-1 { margin-right: 4px; }
.mt-3 { margin-top: 12px; }
.mb-1 { margin-bottom: 4px; }

.integrity-stats { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; }
.integrity-stats .stat-item { display: flex; flex-direction: column; align-items: center; padding: 12px; background: var(--bg-tertiary, #f5f7fa); border-radius: 6px; }
.integrity-stats .stat-label { font-size: 12px; color: var(--text-tertiary); margin-bottom: 4px; }
.integrity-stats .stat-value { font-size: 20px; font-weight: 700; color: var(--text-primary); font-variant-numeric: tabular-nums; }
.integrity-stats .stat-value.warn { color: var(--warning); }
.calendar-sync-note {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
  padding: 8px 12px;
  background: var(--bg-tertiary, #f5f7fa);
  border-radius: 6px;
}
.calendar-sync-text { font-size: 13px; color: var(--text-secondary); }
.check-list { display: flex; flex-direction: column; gap: 6px; margin-bottom: 12px; }
.check-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 10px;
  background: var(--bg-tertiary, #f5f7fa);
  border-radius: 6px;
  font-size: 13px;
}
.check-item .check-status { flex-shrink: 0; }
.check-item .check-name { flex-shrink: 0; color: var(--text-primary); font-weight: 600; min-width: 88px; }
.check-item .check-msg { color: var(--text-secondary); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.drift-box {
  margin-bottom: 12px;
  padding: 10px 12px;
  border: 1px dashed var(--warning);
  border-radius: 6px;
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  align-items: center;
}
.drift-box .drift-title { font-size: 13px; font-weight: 600; color: var(--warning); margin-right: 4px; }
</style>
