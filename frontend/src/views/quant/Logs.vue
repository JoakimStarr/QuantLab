<template>
  <PageContainer>
    <div class="page-header mb-16">
      <h2 class="page-title">日志管理</h2>
      <div class="page-actions">
        <el-switch v-model="autoRefresh" active-text="自动刷新" @change="toggleAutoRefresh" />
        <el-button @click="loadLogs" :icon="Refresh" :loading="loading">刷新</el-button>
      </div>
    </div>

    <SectionCard class="mb-16">
      <div class="filter-bar">
        <el-select v-model="filter.file" @change="onFilterChange" style="width:160px">
          <el-option v-for="f in logFiles" :key="f.name"
            :label="`${f.name} (${f.size_human})`" :value="f.name"
            :disabled="f.size === 0" />
        </el-select>
        <el-select v-model="filter.level" @change="onFilterChange" clearable placeholder="级别" style="width:120px">
          <el-option label="ERROR" value="ERROR" />
          <el-option label="WARNING" value="WARNING" />
          <el-option label="INFO" value="INFO" />
          <el-option label="DEBUG" value="DEBUG" />
        </el-select>
        <el-input v-model="filter.request_id" clearable placeholder="Request ID" style="width:160px"
          @keyup.enter="onFilterChange" />
        <el-input v-model="filter.search" clearable placeholder="关键词搜索" style="width:200px"
          @keyup.enter="onFilterChange" />
        <el-button type="primary" @click="loadLogs" :icon="Search">查询</el-button>
      </div>
    </SectionCard>

    <SectionCard title="日志条目">
      <el-table :data="logs" size="small" stripe max-height="600"
        :row-class-name="rowClassName">
        <el-table-column type="expand">
          <template #default="{row}">
            <div class="log-detail">
              <div v-if="row.traceback" class="traceback-block">
                <div class="traceback-label">Traceback:</div>
                <pre>{{ row.traceback }}</pre>
              </div>
              <div class="detail-meta">
                <span>时间: {{ row.timestamp }}</span>
                <span>级别: {{ row.level }}</span>
                <span>Logger: {{ row.logger }}</span>
                <span v-if="row.request_id">Request ID: {{ row.request_id }}</span>
              </div>
              <div class="full-message">{{ row.message }}</div>
            </div>
          </template>
        </el-table-column>
        <el-table-column prop="timestamp" label="时间" width="170" align="center">
          <template #default="{row}">{{ row.timestamp?.slice(0, 19) }}</template>
        </el-table-column>
        <el-table-column label="级别" width="80" align="center">
          <template #default="{row}">
            <el-tag size="small" :type="levelTag(row.level)">{{ row.level }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="logger" label="Logger" width="180" show-overflow-tooltip />
        <el-table-column label="Request ID" width="120" align="center">
          <template #default="{row}">
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
  </PageContainer>
</template>

<script setup>
import { ref, reactive, onMounted, onBeforeUnmount, watch } from 'vue'
import { ElMessage } from 'element-plus/es/components/message/index'
import { Refresh, Search } from '@element-plus/icons-vue'
import PageContainer from '@/components/common/PageContainer.vue'
import SectionCard from '@/components/common/SectionCard.vue'
import { getLogFiles, getLogs } from '@/api/logs'

const STORAGE_KEY = 'quantlab:logview:state'

const logFiles = ref([])
const logs = ref([])
const total = ref(0)
const loading = ref(false)
const autoRefresh = ref(false)
let refreshTimer = null

const filter = reactive({
  file: 'error.log',
  level: '',
  request_id: '',
  search: '',
  limit: 100,
  offset: 0,
})

const currentPage = ref(1)

const levelTag = (level) => ({
  ERROR: 'danger',
  WARNING: 'warning',
  INFO: 'primary',
  DEBUG: 'info',
}[level] || 'info')

function rowClassName({ row }) {
  return `log-row--${(row.level || 'info').toLowerCase()}`
}

// 状态持久化：记住用户最后选中的文件、过滤条件与分页
function saveState() {
  localStorage.setItem(STORAGE_KEY, JSON.stringify({
    file: filter.file,
    level: filter.level,
    request_id: filter.request_id,
    search: filter.search,
    limit: filter.limit,
    offset: filter.offset,
    currentPage: currentPage.value,
  }))
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
  } catch (e) { if (e !== 'cancel') ElMessage.error('加载日志文件列表失败') }
}

async function loadLogs() {
  loading.value = true
  try {
    const data = await getLogs({
      file: filter.file,
      level: filter.level || undefined,
      request_id: filter.request_id || undefined,
      search: filter.search || undefined,
      limit: filter.limit,
      offset: (currentPage.value - 1) * filter.limit,
    })
    logs.value = data?.items || []
    total.value = data?.total ?? 0
  } catch (e) { if (e !== 'cancel') ElMessage.error('加载日志失败') } finally {
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
    refreshTimer = setInterval(loadLogs, 10000)
  } else {
    if (refreshTimer) clearInterval(refreshTimer)
    refreshTimer = null
  }
}

onMounted(async () => {
  await loadLogFiles()
  const saved = loadState()
  const fileNames = logFiles.value.map(f => f.name)
  // 保存的 file 在当前列表中则复用，否则回退到第一个（列表空时保留默认）
  filter.file = fileNames.includes(saved.file) ? saved.file : (fileNames[0] || filter.file)
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

onBeforeUnmount(() => { if (refreshTimer) clearInterval(refreshTimer) })
</script>

<style scoped lang="scss">
.page-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: var(--space-md);
  animation: fadeInUp 0.5s var(--ease-out-expo);
}
.page-title { font-size: var(--font-size-2xl); font-weight: 700; }
.page-actions { display: flex; align-items: center; gap: var(--space-sm); }
.filter-bar { display: flex; align-items: center; gap: var(--space-sm); flex-wrap: wrap; }

.req-id {
  font-family: var(--font-mono);
  font-size: var(--font-size-xs);
  color: var(--primary);
  cursor: pointer;
  &:hover { text-decoration: underline; }
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

:deep(.log-row--error) {
  td { background: rgba(225, 112, 85, 0.04) !important; }
}
:deep(.log-row--warning) {
  td { background: rgba(243, 156, 18, 0.04) !important; }
}
</style>
