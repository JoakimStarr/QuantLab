<template>
  <SectionCard title="因子衰减告警" collapsible>
    <template #extra>
      <el-button link type="primary" size="small" :loading="checking" @click="runCheck">检测衰减</el-button>
    </template>
    <div v-if="checking" class="decay-loading">
      <el-skeleton :rows="3" animated />
    </div>
    <div v-else-if="!hasChecked" class="decay-hint">
      <el-icon :size="24" color="var(--text-tertiary)"><WarningFilled /></el-icon>
      <span>点击「检测衰减」或等待每日 18:05 自动检测</span>
    </div>
    <div v-else-if="decayingCount === 0" class="decay-empty">
      <el-icon :size="28" color="var(--success)"><CircleCheckFilled /></el-icon>
      <span>所有因子健康，无衰减告警</span>
    </div>
    <div v-else class="decay-body">
      <div class="decay-summary">
        <span class="decay-badge">{{ decayingCount }}</span>
        <span class="decay-text">个因子衰减 / 共 {{ total }} 个，建议重新挖掘</span>
      </div>
      <el-table :data="factors" size="small" max-height="240" class="decay-table">
        <el-table-column label="因子" min-width="140" prop="factor_name" show-overflow-tooltip />
        <el-table-column label="历史IC" width="90" align="right">
          <template #default="{ row }"
            ><span class="num">{{ fmt(row.historical_ic) }}</span></template
          >
        </el-table-column>
        <el-table-column label="近期IC" width="90" align="right">
          <template #default="{ row }"
            ><span class="num">{{ fmt(row.recent_ic) }}</span></template
          >
        </el-table-column>
        <el-table-column label="衰减比" width="90" align="right">
          <template #default="{ row }">
            <span class="num" :class="{ 'is-low': row.decay_ratio < 0.5 }">{{ fmt(row.decay_ratio, 2) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="70" align="center">
          <template #default><span class="badge badge--danger">衰减</span></template>
        </el-table-column>
      </el-table>
    </div>
  </SectionCard>
</template>

<script setup>
import { ref, inject, onMounted, onUnmounted } from 'vue'
import { ElMessage } from 'element-plus/es/components/message/index'
import { CircleCheckFilled, WarningFilled } from '@element-plus/icons-vue'
import SectionCard from '@/components/common/SectionCard.vue'
import { decayCheck } from '@/api/factor'

const checking = ref(false)
const hasChecked = ref(false)
const decayingCount = ref(0)
const total = ref(0)
const factors = ref([])
let unsub = null
const wsClient = inject('wsClient')

function fmt(val, digits = 3) {
  if (val === null || val === undefined || val === '') return '—'
  const n = Number(val)
  return Number.isNaN(n) ? '—' : n.toFixed(digits)
}

function applyResult(data) {
  if (!data) return
  hasChecked.value = true
  decayingCount.value = data.decaying ?? 0
  total.value = data.total ?? 0
  factors.value = (data.decaying_factors || []).slice(0, 20)
}

async function runCheck() {
  checking.value = true
  try {
    const data = await decayCheck()
    applyResult(data)
    if ((data?.decaying ?? 0) > 0) {
      ElMessage.warning(`${data.decaying} 个因子衰减，建议重新挖掘`)
    } else {
      ElMessage.success('因子衰减检测完成，全部健康')
    }
  } catch (e) {
    ElMessage.error('衰减检测失败')
  } finally {
    checking.value = false
  }
}

onMounted(() => {
  // 不自动触发全量检测（避免首页卡顿），由手动按钮或 ws 告警驱动
  unsub = wsClient.on('factor_decay_alert', (payload) => {
    applyResult({
      decaying: payload?.decaying_count ?? 0,
      total: payload?.total ?? 0,
      decaying_factors: payload?.decaying_factors || [],
    })
    if ((payload?.decaying_count ?? 0) > 0) {
      ElMessage.warning(`因子衰减告警：${payload.decaying_count} 个因子衰减`)
    }
  })
})

onUnmounted(() => {
  if (unsub) unsub()
})
</script>

<style scoped lang="scss">
.decay-loading {
  padding: 8px 0;
}
.decay-hint,
.decay-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  padding: 20px 0;
  color: var(--text-tertiary);
  font-size: 13px;
}
.decay-body {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.decay-summary {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  color: var(--text-secondary);
}
.decay-badge {
  display: inline-block;
  min-width: 24px;
  padding: 2px 8px;
  border-radius: 10px;
  background: var(--danger-soft);
  color: var(--danger);
  font-weight: 600;
  text-align: center;
  font-size: 13px;
}
.num {
  font-family: var(--font-mono);
  font-variant-numeric: tabular-nums;
}
.is-low {
  color: var(--danger);
}
.badge {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 4px;
  font-size: 12px;
  font-weight: 500;
}
.badge--danger {
  color: var(--danger);
  background: var(--danger-soft);
}
.decay-table :deep(.el-table) {
  --el-table-bg-color: transparent;
  --el-table-tr-bg-color: transparent;
  --el-table-header-bg-color: var(--bg-tertiary);
  --el-table-border-color: var(--border);
  --el-table-text-color: var(--text-primary);
  --el-table-header-text-color: var(--text-tertiary);
  background: transparent;
}
.decay-table :deep(.el-table__header-wrapper) th.el-table__cell {
  font-size: 12px;
  background: var(--bg-tertiary);
  font-weight: 500;
}
.decay-table :deep(.el-table__row) td.el-table__cell {
  font-size: 13px;
}
.decay-table :deep(.el-table__row:hover > td.el-table__cell) {
  background: var(--bg-hover) !important;
}
</style>
