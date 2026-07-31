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
        <div class="kpi-label">数据源</div>
        <div class="kpi-value">
          <el-select v-model="currentSource" size="small" style="width: 160px" @change="switchSource">
            <el-option v-for="s in dataSources" :key="s.value" :label="s.label" :value="s.value" />
          </el-select>
        </div>
        <div class="kpi-sub">{{ currentSource === 'chenditc' ? 'GitHub 每日构建' : '实时拉取' }}</div>
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
              <span class="badge badge-info">{{ currentSource }}</span>
            </span>
            <span class="meta-item">
              <span class="meta-label">最后更新:</span>
              <span>{{ formatTime(currentStatus.last_updated) }}</span>
            </span>
          </div>
          <div v-if="currentStatus.last_error" class="source-error">
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
          <el-button type="primary" @click="syncData" :loading="syncing" :disabled="!qlib.available || syncing">
            {{ syncing ? '同步中...' : '同步数据' }}
          </el-button>
          <el-button type="success" @click="showEodDialog = true" :loading="eodSyncing" :disabled="!qlib.available || eodSyncing">
            {{ eodSyncing ? '增量同步中...' : '增量同步' }}
          </el-button>
          <el-button type="warning" @click="doSyncIndices" :loading="indexSyncing" :disabled="!qlib.available || indexSyncing">
            {{ indexSyncing ? '指数同步中...' : '同步指数' }}
          </el-button>
          <el-button type="primary" @click="doSyncIndustry" :loading="industrySyncing" :disabled="industrySyncing">
            {{ industrySyncing ? '行业同步中...' : '同步行业' }}
          </el-button>
          <el-button type="info" @click="doIntegrityCheck" :loading="integrityChecking" :disabled="!qlib.available">
            {{ integrityChecking ? '校验中...' : '数据校验' }}
          </el-button>
        </div>
      </div>
    </SectionCard>

    <!-- 同步进度提示 -->
    <div v-if="syncing" class="sync-progress mb-6">
      <div class="progress-bar">
        <div class="progress-indicator"></div>
      </div>
      <div class="progress-text">{{ syncProgressText }}</div>
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
          基于 <strong>akshare</strong>（国内源）拉取最近 N 天的日K数据（OHLCV），<br>
          增量追加到 qlib bin 目录。适合日常快速更新，无需下载 500MB+ 全量包。
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
            <span class="eod-warn-hint">开启后将用 akshare 数据覆盖已有日期（可能因复权差异导致价格断裂）</span>
          </el-form-item>
        </el-form>
        <div v-if="eodResult" class="eod-result">
          <el-alert
            :title="`同步完成: 成功 ${eodResult.success}/${eodResult.total_stocks}，新增 ${eodResult.new_dates?.length || 0} 个交易日`"
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
    <el-dialog v-model="showIntegrityDialog" title="数据完整性校验" width="720px">
      <div v-if="integrityChecking" v-loading="true" style="min-height: 200px"></div>
      <div v-else-if="integrityResult && integrityResult.ok" class="integrity-result">
        <el-alert :title="integrityResult.summary" :type="(integrityResult.length_mismatches > 0 || integrityResult.stocks_missing_fields > 0) ? 'warning' : 'success'" :closable="false" show-icon style="margin-bottom: 16px" />
        <div class="integrity-stats">
          <div class="stat-item"><span class="stat-label">日历天数</span><span class="stat-value">{{ integrityResult.calendar_days }}</span></div>
          <div class="stat-item"><span class="stat-label">股票总数</span><span class="stat-value">{{ integrityResult.total_stocks }}</span></div>
          <div class="stat-item"><span class="stat-label">有效股票</span><span class="stat-value">{{ integrityResult.valid_stocks }}</span></div>
          <div class="stat-item"><span class="stat-label">缺字段</span><span class="stat-value" :class="{warn: integrityResult.stocks_missing_fields > 0}">{{ integrityResult.stocks_missing_fields }}</span></div>
          <div class="stat-item"><span class="stat-label">全NaN</span><span class="stat-value" :class="{warn: integrityResult.stocks_all_nan > 0}">{{ integrityResult.stocks_all_nan }}</span></div>
          <div class="stat-item"><span class="stat-label">长度不匹配</span><span class="stat-value" :class="{warn: integrityResult.length_mismatches > 0}">{{ integrityResult.length_mismatches }}</span></div>
        </div>
        <div v-if="integrityResult.issues && integrityResult.issues.length" style="margin-top: 16px">
          <div style="font-weight: 600; margin-bottom: 8px">问题明细（{{ integrityResult.issues.length }} 条）</div>
          <el-table :data="integrityResult.issues" size="small" stripe max-height="300">
            <el-table-column prop="code" label="股票代码" width="120" />
            <el-table-column prop="field" label="字段" width="100" />
            <el-table-column prop="issue" label="问题" width="120">
              <template #default="{row}">
                <el-tag :type="row.issue === 'file_missing' ? 'danger' : 'warning'" size="small">{{ row.issue === 'file_missing' ? '文件缺失' : '长度不匹配' }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="预期/实际" width="140">
              <template #default="{row}">{{ row.expected }} / {{ row.actual }}</template>
            </el-table-column>
          </el-table>
        </div>
        <div v-if="integrityResult.all_nan_stocks && integrityResult.all_nan_stocks.length" style="margin-top: 16px">
          <div style="font-weight: 600; margin-bottom: 8px">全 NaN 股票（{{ integrityResult.all_nan_stocks.length }} 只）</div>
          <div style="display: flex; flex-wrap: wrap; gap: 4px">
            <el-tag v-for="s in integrityResult.all_nan_stocks" :key="s" size="small" type="info">{{ s }}</el-tag>
          </div>
        </div>
      </div>
      <div v-else-if="integrityResult && !integrityResult.ok">
        <el-alert :title="integrityResult.error || '校验失败'" type="error" :closable="false" show-icon />
      </div>
      <template #footer>
        <el-button @click="showIntegrityDialog = false">关闭</el-button>
        <el-button type="primary" @click="doIntegrityCheck" :loading="integrityChecking" :disabled="integrityChecking">重新校验</el-button>
      </template>
    </el-dialog>
  </PageContainer>
</template>

<script setup>
defineOptions({ name: 'QuantData' })
import { ref, reactive, computed, onMounted, onBeforeUnmount } from 'vue'
import { ElMessage } from 'element-plus/es/components/message/index'
import PageContainer from '@/components/common/PageContainer.vue'
import SectionCard from '@/components/common/SectionCard.vue'
import { getQuantDataStatus, syncQuantData, getQlibStatus, switchDataSource, getDataPreview, getSyncHistory, eodSync, getDataSource, syncIndices, integrityCheck, syncIndustry } from '@/api/quant'

const statusList = ref([])
const loading = ref(false)
const syncing = ref(false)
const qlib = reactive({ available: false, provider_uri: '', earliest_date: null, calendar_count: 0 })
const dataSources = [
  { value: 'chenditc', label: 'chenditc (GitHub 每日构建)' },
  { value: 'akshare', label: 'akshare (实时拉取)' },
]
const currentSource = ref('chenditc')
const previewVisible = ref(false)
const previewData = ref([])
const previewLoading = ref(false)
const previewCode = ref('')
const previewCodeInput = ref('')
const syncHistory = ref([])
const showEodDialog = ref(false)
const eodSyncing = ref(false)
const eodResult = ref(null)
const eodForm = reactive({ universe: 'csi300', days: 5, overwrite: false })
const indexSyncing = ref(false)
const industrySyncing = ref(false)
  const integrityChecking = ref(false)
  const showIntegrityDialog = ref(false)
  const integrityResult = ref(null)
let pollTimer = null

const currentStatus = computed(() => statusList.value[0] || {})

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
  if (currentSource.value === 'akshare') {
    return `正在通过akshare逐只拉取行情数据（国内源），请耐心等待...`
  }
  return `正在从 chenditc/investment_data 下载 qlib_bin.tar.gz（约 533MB），请耐心等待...`
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

async function switchSource(source) {
  try {
    await switchDataSource(source)
    currentSource.value = source
    ElMessage.success(`数据源已切换到 ${source}`)
    loadAll()
  } catch (e) {
    if (e !== 'cancel') ElMessage.error('数据源切换失败')
  }
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
    const cur = statusList.value[0]
    if (cur && cur.status === 'syncing') {
      if (!pollTimer) {
        pollTimer = setInterval(loadStatus, 5000)
      }
    } else {
      if (pollTimer) {
        clearInterval(pollTimer)
        pollTimer = null
        syncing.value = false
      }
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

async function loadDataSource() {
  try {
    const data = await getDataSource()
    if (data?.source) {
      currentSource.value = data.source
    }
  } catch (e) {
    // 静默失败，使用默认值
  }
}

async function loadAll() {
  loading.value = true
  await Promise.all([loadStatus(), loadQlib(), loadSyncHistory(), loadDataSource()])
  loading.value = false
}

async function syncData() {
  syncing.value = true
  try {
    const params = currentSource.value === 'akshare' ? { days: 30 } : {}
    await syncQuantData(params)
    const sourceName = currentSource.value === 'akshare' ? 'akshare增量同步' : 'chenditc全量同步'
    ElMessage.success(`${sourceName}已提交（后台执行）`)
    setTimeout(loadStatus, 3000)
  } catch (e) {
    if (e !== 'cancel') ElMessage.error('数据同步提交失败')
    syncing.value = false
  }
}


async function doEodSync() {
  eodSyncing.value = true
  eodResult.value = null
  try {
    const data = await eodSync(eodForm.universe, eodForm.days, eodForm.overwrite)
    eodResult.value = data
    const msg = `增量同步完成: 成功 ${data.success}/${data.total_stocks}，新增 ${data.new_dates?.length || 0} 个交易日`
    ElMessage.success(msg)
    loadAll()
  } catch (e) {
    if (e !== 'cancel') ElMessage.error('增量EOD同步失败: ' + (e?.message || e))
  } finally {
    eodSyncing.value = false
  }
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
  try {
    const data = await integrityCheck()
    integrityResult.value = data
    ElMessage.success(data?.summary || '校验完成')
  } catch (e) {
    integrityResult.value = { ok: false, error: String(e?.message || e) }
  } finally {
    integrityChecking.value = false
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
onBeforeUnmount(() => { if (pollTimer) clearInterval(pollTimer) })
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

.integrity-stats { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; }
.integrity-stats .stat-item { display: flex; flex-direction: column; align-items: center; padding: 12px; background: var(--bg-tertiary, #f5f7fa); border-radius: 6px; }
.integrity-stats .stat-label { font-size: 12px; color: var(--text-tertiary); margin-bottom: 4px; }
.integrity-stats .stat-value { font-size: 20px; font-weight: 700; color: var(--text-primary); font-variant-numeric: tabular-nums; }
.integrity-stats .stat-value.warn { color: var(--warning); }
</style>
