<template>
  <SectionCard title="最近挖掘任务" collapsible>
    <template #extra>
      <router-link to="/quant/mining" class="link">查看全部</router-link>
    </template>
    <el-skeleton v-if="loading" :rows="5" animated />
    <el-table v-else :data="tasks" class="dashboard-table" empty-text="暂无任务" size="default">
      <el-table-column label="类型" width="90" align="center">
        <template #default="{ row }">
          <span class="badge" :class="`badge--${typeBadgeClass(row.type)}`">{{ typeLabel[row.type] || row.type }}</span>
        </template>
      </el-table-column>
      <el-table-column label="状态" width="80" align="center">
        <template #default="{ row }">
          <span class="badge" :class="`badge--${statusBadgeClass(row.status)}`">{{
            statusLabel[row.status] || row.status
          }}</span>
        </template>
      </el-table-column>
      <el-table-column label="候选" width="70" align="right">
        <template #default="{ row }"
          ><span class="num">{{ row.candidates_generated ?? 0 }}</span></template
        >
      </el-table-column>
      <el-table-column label="通过" width="70" align="right">
        <template #default="{ row }"
          ><span class="num">{{ row.candidates_passed ?? 0 }}</span></template
        >
      </el-table-column>
      <el-table-column label="最佳IC" width="90" align="right">
        <template #default="{ row }"
          ><span class="num">{{ formatIc(row.best_ic) }}</span></template
        >
      </el-table-column>
      <el-table-column label="时间" min-width="140">
        <template #default="{ row }">{{ (row.finished_at || row.created_at || '').slice(0, 16) }}</template>
      </el-table-column>
    </el-table>
  </SectionCard>
</template>

<script setup>
import SectionCard from '@/components/common/SectionCard.vue'
import { typeLabel, typeBadgeClass, statusLabel, statusBadgeClass, formatIc } from './utils'

defineProps({
  tasks: { type: Array, default: () => [] },
  loading: { type: Boolean, default: false },
})
</script>

<style scoped lang="scss">
.link {
  color: var(--primary);
  font-size: 13px;
  text-decoration: none;
  &:hover {
    text-decoration: underline;
  }
}
.badge {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 4px;
  font-size: 12px;
  font-weight: 500;
  line-height: 1.5;
}
.badge--primary {
  color: var(--primary);
  background: var(--primary-soft);
}
.badge--success {
  color: var(--success);
  background: var(--success-soft);
}
.badge--warning {
  color: var(--warning);
  background: var(--warning-soft);
}
.badge--danger {
  color: var(--danger);
  background: var(--danger-soft);
}
.badge--info {
  color: var(--info);
  background: var(--info-soft);
}
.num {
  font-family: var(--font-mono);
  font-variant-numeric: tabular-nums;
}
.dashboard-table :deep(.el-table) {
  --el-table-bg-color: transparent;
  --el-table-tr-bg-color: transparent;
  --el-table-header-bg-color: var(--bg-tertiary);
  --el-table-border-color: var(--border);
  --el-table-border: 1px solid var(--border);
  --el-table-text-color: var(--text-primary);
  --el-table-header-text-color: var(--text-tertiary);
  --el-table-row-hover-bg-color: var(--bg-hover);
  background: transparent;
}
.dashboard-table :deep(.el-table__header-wrapper) th.el-table__cell {
  font-size: 12px;
  color: var(--text-tertiary);
  background: var(--bg-tertiary);
  font-weight: 500;
}
.dashboard-table :deep(.el-table__row) td.el-table__cell {
  font-size: 13px;
  color: var(--text-primary);
}
.dashboard-table :deep(.el-table__row:hover > td.el-table__cell) {
  background: var(--bg-hover) !important;
}
.dashboard-table :deep(.el-table__inner-wrapper::before),
.dashboard-table :deep(.el-table__border-left-patch) {
  display: none;
}
</style>
