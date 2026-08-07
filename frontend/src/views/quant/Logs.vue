<template>
  <PageContainer>
    <PageHeader title="日志管理">
      <template #actions>
        <el-select
          v-model="currentLevel"
          style="width: 110px"
          :disabled="levelSaving"
          @change="onLevelChange">
          <el-option v-for="lvl in levelOptions" :key="lvl" :label="lvl" :value="lvl" />
        </el-select>
        <el-switch v-model="autoRefresh" active-text="自动刷新" @change="toggleAutoRefresh" />
        <el-button type="danger" plain :icon="Delete" :disabled="!currentFileSize" @click="clearCurrentLog"
          >清空日志</el-button
        >
        <el-button @click="loadLogs" :icon="Refresh" :loading="loading">刷新</el-button>
      </template>
    </PageHeader>

    <SectionCard class="mb-16">
      <div class="filter-bar">
        <el-select v-model="filter.file" @change="onFilterChange" style="width: 160px">
          <el-option
            v-for="f in logFiles"
            :key="f.name"
            :label="`${f.name} (${f.size_human})`"
            :value="f.name"
            :disabled="f.size === 0"
          />
        </el-select>
        <el-select v-model="filter.level" @change="onFilterChange" clearable placeholder="级别" style="width: 120px">
          <el-option label="ERROR" value="ERROR" />
          <el-option label="WARNING" value="WARNING" />
          <el-option label="INFO" value="INFO" />
          <el-option label="DEBUG" value="DEBUG" />
        </el-select>
        <el-input
          v-model="filter.request_id"
          clearable
          placeholder="Request ID"
          style="width: 160px"
          @keyup.enter="onFilterChange"
        />
        <el-input
          v-model="filter.search"
          clearable
          placeholder="关键词搜索"
          style="width: 200px"
          @keyup.enter="onFilterChange"
        />
        <el-button type="primary" @click="loadLogs" :icon="Search">查询</el-button>
      </div>
    </SectionCard>

    <SectionCard title="日志条目">
      <el-table :data="logs" size="small" stripe max-height="600" :row-class-name="rowClassName">
        <template #empty>
          <el-empty description="暂无日志记录" :image-size="72" />
        </template>
        <el-table-column type="expand">
          <template #default="{ row }">
            <div class="log-detail">
              <div v-if="row.traceback" class="traceback-block">
                <div class="traceback-label">Traceback:</div>
                <pre>{{ row.traceback }}</pre>
              </div>
              <div class="detail-meta">
                <span>时间: {{ formatTime(row.timestamp) }}</span>
                <span>级别: {{ row.level }}</span>
                <span>Logger: {{ row.logger }}</span>
                <span v-if="row.request_id">Request ID: {{ row.request_id }}</span>
              </div>
              <div class="full-message">{{ row.message }}</div>
            </div>
          </template>
        </el-table-column>
        <el-table-column prop="timestamp" label="时间" width="170" align="center">
          <template #default="{ row }">{{ formatTime(row.timestamp) }}</template>
        </el-table-column>
        <el-table-column label="级别" width="80" align="center">
          <template #default="{ row }">
            <el-tag size="small" :type="levelTag(row.level)">{{ row.level }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="logger" label="Logger" width="180" show-overflow-tooltip />
        <el-table-column label="来源" width="100" align="center">
          <template #default="{ row }">
            <el-tag v-if="row.worker_kind" size="small" type="success">{{ row.worker_kind }}</el-tag>
            <span v-else>--</span>
          </template>
        </el-table-column>
        <el-table-column label="Request ID" width="120" align="center">
          <template #default="{ row }">
            <span v-if="row.request_id" class="req-id" @click.stop="searchByReqId(row.request_id)">
              {{ row.request_id }}
            </span>
            <span v-else>--</span>
          </template>
        </el-table-column>
        <el-table-column prop="message" label="消息" min-width="300" show-overflow-tooltip />
      </el-table>

      <div class="pagination-bar">
        <el-pagination
          v-model:current-page="currentPage"
          :page-size="filter.limit"
          :total="total"
          layout="total, prev, pager, next"
          @current-change="onPageChange"
          size="small"
        />
      </div>
    </SectionCard>

    <el-dialog v-model="clearDialogVisible" title="清空日志" width="460px" :close-on-click-modal="false" append-to-body>
      <div class="clear-confirm">
        <div class="clear-header">
          <div class="clear-icon">
            <el-icon><WarningFilled /></el-icon>
          </div>
          <div class="clear-title">
            确认清空「<span class="mono">{{ filter.file }}</span
            >」？
          </div>
        </div>
        <p class="clear-desc">将截断当前日志文件并删除其全部轮转备份，<b>操作无法恢复</b>。</p>
        <div class="clear-summary">
          <div class="summary-row">
            <span class="label">当前文件</span>
            <span class="value mono">{{ currentFile?.size_human || '0 B' }}</span>
          </div>
          <div class="summary-row">
            <span class="label">轮转备份</span>
            <span class="value mono"
              >{{ currentFile?.backup_count ?? 0 }} 个（{{ currentFile?.backup_size_human || '0 B' }}）</span
            >
          </div>
          <div class="summary-row total">
            <span class="label">预计释放</span>
            <span class="value mono highlight">{{ clearTotalHuman }}</span>
          </div>
        </div>
      </div>
      <template #footer>
        <el-button @click="clearDialogVisible = false">取消</el-button>
        <el-button type="danger" :loading="clearing" @click="confirmClear">确认清空</el-button>
      </template>
    </el-dialog>
  </PageContainer>
</template>

<script setup>
import { ref, reactive, computed, onMounted, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Delete, Refresh, Search, WarningFilled } from '@element-plus/icons-vue'
import PageContainer from '@/components/common/PageContainer.vue'
import PageHeader from '@/components/common/PageHeader.vue'
import SectionCard from '@/components/common/SectionCard.vue'
import { usePolling } from '@/composables/usePolling'
import { getLogFiles, getLogs, clearLogs, getLogLevel, setLogLevel } from '@/api/logs'
import { humanSize } from '@/utils/format'

const STORAGE_KEY = 'quantlab:logview:state'

const logFiles = ref([])
const logs = ref([])
const total = ref(0)
const loading = ref(false)
const autoRefresh = ref(false)
// 自动刷新轮询：增量拉取（since=当前最新时间，只扫尾部新日志）
const autoRefreshPolling = usePolling(
  () => {
    const since = logs.value.length ? logs.value[0].timestamp : undefined
    loadLogs({ since })
  },
  10000,
  { immediate: false }
)

const filter = reactive({
  file: 'error.log',
  level: '',
  request_id: '',
  search: '',
  limit: 100,
  offset: 0,
})

const currentPage = ref(1)

// 运行时日志级别（排查用：切 DEBUG 复现问题 → 查看 quantlab.log → 切回 INFO）
const levelOptions = ['DEBUG', 'INFO', 'WARNING', 'ERROR']
const currentLevel = ref('INFO')
// 已确认生效的级别（取消切换时回滚用）
const appliedLevel = ref('INFO')
const levelSaving = ref(false)

// 清空日志确认弹窗状态
const clearDialogVisible = ref(false)
const clearing = ref(false)

const currentFile = computed(() => logFiles.value.find((x) => x.name === filter.file))
// 当前选中文件是否有内容（无内容时禁用清空按钮）
const currentFileSize = computed(() => currentFile.value?.size > 0)
// 预计释放 = 当前文件 + 全部轮转备份
const clearTotalHuman = computed(() => {
  const f = currentFile.value
  if (!f) return '0 B'
  return humanSize((f.size || 0) + (f.backup_size || 0))
})

const levelTag = (level) =>
  ({
    ERROR: 'danger',
    WARNING: 'warning',
    INFO: 'primary',
    DEBUG: 'info',
  })[String(level || '').toUpperCase()] || 'info'

// structlog JSON 日志时间戳为 UTC（带 Z），统一转成本地时区显示
function formatTime(ts) {
  if (!ts) return ''
  const d = new Date(ts)
  if (Number.isNaN(d.getTime())) return ts
  const pad = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}

function rowClassName({ row }) {
  return `log-row--${(row.level || 'info').toLowerCase()}`
}

// 状态持久化：记住用户最后选中的文件、过滤条件与分页
function saveState() {
  localStorage.setItem(
    STORAGE_KEY,
    JSON.stringify({
      file: filter.file,
      level: filter.level,
      request_id: filter.request_id,
      search: filter.search,
      limit: filter.limit,
      offset: filter.offset,
      currentPage: currentPage.value,
    })
  )
}

function loadState() {
  try {
    return JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}')
  } catch {
    return {}
  }
}

async function loadLogFiles() {
  try {
    const data = await getLogFiles()
    logFiles.value = data?.items || []
  } catch (e) {
    if (e !== 'cancel') ElMessage.error('加载日志文件列表失败')
  }
}

async function loadLogs(opts = {}) {
  // 顺带刷新文件列表（大小/是否存在会变化），不阻塞日志加载
  loadLogFiles()
  loading.value = true
  try {
    const data = await getLogs({
      file: filter.file,
      level: filter.level || undefined,
      request_id: filter.request_id || undefined,
      search: filter.search || undefined,
      since: opts.since || undefined,
      limit: filter.limit,
      offset: (currentPage.value - 1) * filter.limit,
    })
    logs.value = data?.items || []
    total.value = data?.total ?? 0
  } catch (e) {
    if (e !== 'cancel') ElMessage.error('加载日志失败')
  } finally {
    loading.value = false
  }
}

function onPageChange(page) {
  filter.offset = (page - 1) * filter.limit
  loadLogs()
}

// 过滤条件变化时回到第一页，避免新条件下停留在空页
function onFilterChange() {
  currentPage.value = 1
  filter.offset = 0
  loadLogs()
}

function searchByReqId(reqId) {
  filter.request_id = reqId
  currentPage.value = 1
  filter.offset = 0
  loadLogs()
  ElMessage.success(`已按 Request ID ${reqId} 过滤`)
}

function toggleAutoRefresh(val) {
  if (val) {
    autoRefreshPolling.start()
  } else {
    autoRefreshPolling.stop()
  }
}

async function loadLevel() {
  try {
    const data = await getLogLevel()
    const level = data?.level || 'INFO'
    currentLevel.value = level
    appliedLevel.value = level
  } catch (e) {
    if (e !== 'cancel') ElMessage.error('获取日志级别失败')
  }
}

async function onLevelChange(level) {
  try {
    await ElMessageBox.confirm(
      `将日志级别切换为 ${level}？运行时立即生效，重启后端后恢复为配置默认。\n\n排查问题建议：切到 DEBUG → 复现问题 → 查看 quantlab.log → 切回 INFO。`,
      '动态调整日志级别',
      { confirmButtonText: '切换', cancelButtonText: '取消', type: 'warning' }
    )
  } catch {
    currentLevel.value = appliedLevel.value
    return
  }
  levelSaving.value = true
  try {
    await setLogLevel(level)
    appliedLevel.value = level
    ElMessage.success(`日志级别已切换为 ${level}`)
  } catch (e) {
    if (e !== 'cancel') ElMessage.error('切换日志级别失败')
    currentLevel.value = appliedLevel.value
  } finally {
    levelSaving.value = false
  }
}

function clearCurrentLog() {
  clearDialogVisible.value = true
}

async function confirmClear() {
  clearing.value = true
  try {
    const res = await clearLogs(filter.file)
    ElMessage.success(`已清空 ${filter.file}（释放 ${humanSize(res?.freed_bytes ?? 0)}）`)
    clearDialogVisible.value = false
    currentPage.value = 1
    filter.offset = 0
    await loadLogs()
  } catch (e) {
    if (e !== 'cancel') ElMessage.error('清除日志失败')
  } finally {
    clearing.value = false
  }
}

onMounted(async () => {
  await loadLogFiles()
  const saved = loadState()
  const fileNames = logFiles.value.map((f) => f.name)
  // 保存的 file 在当前列表中则复用，否则回退到第一个（列表空时保留默认）
  filter.file = fileNames.includes(saved.file) ? saved.file : fileNames[0] || filter.file
  if (saved.level !== undefined) filter.level = saved.level
  if (saved.request_id !== undefined) filter.request_id = saved.request_id
  if (saved.search !== undefined) filter.search = saved.search
  if (typeof saved.limit === 'number' && saved.limit > 0) filter.limit = saved.limit
  if (typeof saved.currentPage === 'number' && saved.currentPage > 0) {
    currentPage.value = saved.currentPage
  } else if (typeof saved.offset === 'number' && saved.offset > 0) {
    currentPage.value = Math.floor(saved.offset / filter.limit) + 1
  }
  filter.offset = (currentPage.value - 1) * filter.limit
  await loadLevel()
  await loadLogs()
})

// 任一关键字段变化即写入 localStorage
watch(
  [
    () => filter.file,
    () => filter.level,
    () => filter.request_id,
    () => filter.search,
    () => filter.limit,
    () => filter.offset,
    currentPage,
  ],
  saveState
)
</script>

<style scoped lang="scss">
.filter-bar {
  display: flex;
  align-items: center;
  gap: var(--space-sm);
  flex-wrap: wrap;
}

.req-id {
  font-family: var(--font-mono);
  font-size: var(--font-size-xs);
  color: var(--primary);
  cursor: pointer;
  &:hover {
    text-decoration: underline;
  }
}

.log-detail {
  padding: var(--space-md);
  background: var(--bg-secondary);
  border-radius: var(--radius-sm);
}

.traceback-block {
  margin-bottom: var(--space-md);
}

.traceback-label {
  font-size: var(--font-size-xs);
  color: var(--danger);
  font-weight: 600;
  margin-bottom: var(--space-xs);
}

.traceback-block pre {
  background: var(--bg-tertiary);
  padding: var(--space-sm);
  border-radius: var(--radius-sm);
  font-family: var(--font-mono);
  font-size: var(--font-size-xs);
  white-space: pre-wrap;
  word-break: break-all;
  color: var(--text-primary);
  max-height: 400px;
  overflow-y: auto;
}

.detail-meta {
  display: flex;
  gap: var(--space-lg);
  flex-wrap: wrap;
  font-size: var(--font-size-xs);
  color: var(--text-secondary);
  margin-bottom: var(--space-sm);
}

.full-message {
  font-family: var(--font-mono);
  font-size: var(--font-size-sm);
  color: var(--text-primary);
  white-space: pre-wrap;
  word-break: break-all;
}

.pagination-bar {
  display: flex;
  justify-content: flex-end;
  padding-top: var(--space-md);
}

.mono {
  font-family: var(--font-mono);
}

.clear-confirm {
  padding: var(--space-xs) 0;
}

.clear-header {
  display: flex;
  align-items: flex-start;
  gap: var(--space-sm);
  margin-bottom: var(--space-sm);
}

.clear-icon {
  flex-shrink: 0;
  width: 36px;
  height: 36px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: var(--radius-sm);
  background: var(--danger-soft);
  color: var(--danger);
  font-size: 20px;
}

.clear-title {
  font-size: var(--font-size-base);
  font-weight: 600;
  line-height: 36px;
}

.clear-desc {
  color: var(--text-secondary);
  font-size: var(--font-size-sm);
  line-height: 1.6;
  margin-bottom: var(--space-md);
  b {
    color: var(--danger);
    font-weight: 600;
  }
}

.clear-summary {
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  padding: var(--space-sm) var(--space-md);
  background: var(--bg-secondary);
}

.summary-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: var(--space-xs) 0;
  font-size: var(--font-size-sm);
  &:not(:last-child) {
    border-bottom: 1px dashed var(--border);
  }
  .label {
    color: var(--text-secondary);
  }
  .value {
    color: var(--text-primary);
  }
  &.total .value {
    font-size: var(--font-size-base);
    font-weight: 700;
    color: var(--danger);
  }
}

:deep(.log-row--error) {
  td {
    background: var(--danger-soft-faint) !important;
  }
}
:deep(.log-row--warning) {
  td {
    background: var(--warning-soft) !important;
  }
}
</style>
