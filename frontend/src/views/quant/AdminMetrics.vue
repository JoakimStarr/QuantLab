<template>
  <div class="admin-metrics">
    <el-card shadow="never" class="health-card">
      <template #header>
        <div class="card-header">
          <span>系统健康状态</span>
          <el-tag :type="healthStatus === 'ok' ? 'success' : 'warning'" size="small">
            {{ healthStatus }}
          </el-tag>
        </div>
      </template>
      <el-descriptions :column="3" border size="small">
        <el-descriptions-item label="数据库">
          <el-tag :type="checks.database === 'ok' ? 'success' : 'danger'" size="small">
            {{ checks.database || '...' }}
          </el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="QLib">
          <el-tag :type="checks.qlib === 'ok' ? 'success' : 'info'" size="small">
            {{ checks.qlib || '...' }}
          </el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="调度器">
          <el-tag :type="checks.scheduler === 'running' ? 'success' : 'warning'" size="small">
            {{ checks.scheduler || '...' }}
          </el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="磁盘空间">
          {{ checks.disk || '...' }}
        </el-descriptions-item>
        <el-descriptions-item label="WS 连接数">
          {{ checks.ws_connections ?? '...' }}
        </el-descriptions-item>
        <el-descriptions-item label="版本">
          {{ version }}
        </el-descriptions-item>
      </el-descriptions>
    </el-card>

    <el-row :gutter="16" class="metrics-row">
      <el-col :span="12">
        <el-card shadow="never">
          <template #header>Prometheus 指标（原始文本）</template>
          <pre class="metrics-text">{{ metricsText }}</pre>
        </el-card>
      </el-col>
      <el-col :span="12">
        <el-card shadow="never">
          <template #header>关键指标摘要</template>
          <el-descriptions :column="1" border size="small">
            <el-descriptions-item v-for="(v, k) in parsedMetrics" :key="k" :label="k">
              {{ v }}
            </el-descriptions-item>
          </el-descriptions>
        </el-card>
      </el-col>
    </el-row>

    <el-card shadow="never" class="audit-card">
      <template #header>
        <div class="card-header">
          <span>审计日志（最近 20 条）</span>
          <el-button size="small" @click="refreshAll" :loading="loading">刷新</el-button>
        </div>
      </template>
      <el-table :data="auditLogs" size="small" border stripe max-height="400">
        <el-table-column prop="timestamp" label="时间" width="180" />
        <el-table-column prop="action" label="操作" width="140" />
        <el-table-column prop="user" label="用户" width="100" />
        <el-table-column prop="resource" label="对象" width="120" />
        <el-table-column prop="detail" label="详情" />
      </el-table>
    </el-card>
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import api from '@/api'

const healthStatus = ref('loading')
const checks = ref({})
const version = ref('')
const metricsText = ref('')
const parsedMetrics = ref({})
const auditLogs = ref([])
const loading = ref(false)
let timer = null

async function fetchHealth() {
  try {
    const res = await api.get('/health')
    healthStatus.value = res.data.status
    checks.value = res.data.checks || {}
    version.value = res.data.version || ''
  } catch (e) {
    healthStatus.value = 'error'
  }
}

async function fetchMetrics() {
  try {
    const res = await api.get('/metrics', { baseURL: '' })
    metricsText.value = res.data
    // 解析关键指标
    const lines = res.data.split('\n')
    const result = {}
    for (const line of lines) {
      if (line.startsWith('#') || !line.trim()) continue
      const [name, value] = line.split(' ')
      if (name && value !== undefined) {
        result[name] = value
      }
    }
    parsedMetrics.value = result
  } catch (e) {
    metricsText.value = '获取失败: ' + (e.message || '')
  }
}

async function fetchAuditLogs() {
  try {
    const res = await api.get('/v1/logs', { params: { file: 'audit.jsonl', limit: 20 } })
    auditLogs.value = (res.data?.data?.items || []).reverse()
  } catch (e) {
    auditLogs.value = []
  }
}

async function refreshAll() {
  loading.value = true
  await Promise.all([fetchHealth(), fetchMetrics(), fetchAuditLogs()])
  loading.value = false
}

onMounted(() => {
  refreshAll()
  timer = setInterval(refreshAll, 15000)
})

onUnmounted(() => {
  if (timer) clearInterval(timer)
})
</script>

<style scoped>
.admin-metrics {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.metrics-text {
  max-height: 400px;
  overflow: auto;
  font-size: 12px;
  line-height: 1.4;
  white-space: pre-wrap;
  word-break: break-all;
}
</style>