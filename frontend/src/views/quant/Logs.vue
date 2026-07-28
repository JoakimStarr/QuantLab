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
        <el-select v-model="filter.file" @change="loadLogs" style="width:160px">
          <el-option v-for="f in logFiles" :key="f.name"
            :label="`${f.name} (${f.size_human})`" :value="f.name"
            :disabled="f.size === 0" />
        </el-select>
        <el-select v-model="filter.level" @change="loadLogs" clearable placeholder="级别" style="width:120px">
          <el-option label="ERROR" value="ERROR" />
          <el-option label="WARNING" value="WARNING" />
          <el-option label="INFO" value="INFO" />
          <el-option label="DEBUG" value="DEBUG" />
        </el-select>
        <el-input v-model="filter.request_id" clearable placeholder="Request ID" style="width:160px"
          @keyup.enter="loadLogs" />
        <el-input v-model="filter.search" clearable placeholder="关键词搜索" style="width:200px"
          @keyup.enter="loadLogs" />
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
          small
        />
      </div>
    </SectionCard>
  </PageContainer>
</template>

<script setup>
import { ref, reactive, onMounted, onBeforeUnmount } from 'vue'
import { ElMessage } from 'element-plus'
import { Refresh, Search } from '@element-plus/icons-vue'
import PageContainer from '@/components/common/PageContainer.vue'
import SectionCard from '@/components/common/SectionCard.vue'
import { getLogFiles, getLogs } from '@/api/logs'

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

onMounted(() => { loadLogFiles(); loadLogs() })
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
