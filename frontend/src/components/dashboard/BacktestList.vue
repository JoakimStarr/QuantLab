<template>
  <SectionCard title="最近回测结果" collapsible>
    <template #extra>
      <router-link to="/quant/strategy" class="link">查看全部</router-link>
    </template>
    <el-skeleton v-if="loading" :rows="5" animated />
    <el-table v-else :data="backtests" class="dashboard-table" empty-text="暂无回测" size="default">
      <el-table-column label="策略" min-width="100">
        <template #default="{ row }">{{ row.strategy_name || row.strategy_id }}</template>
      </el-table-column>
      <el-table-column label="夏普" width="80" align="right">
        <template #default="{ row }">
          <span class="num" :class="numClass(row.sharpe)">{{ formatNum(row.sharpe) }}</span>
        </template>
      </el-table-column>
      <el-table-column label="年化" width="80" align="right">
        <template #default="{ row }">
          <span class="num" :class="numClass(row.annual_return)">{{ formatPercent(row.annual_return) }}</span>
        </template>
      </el-table-column>
      <el-table-column label="回撤" width="80" align="right">
        <template #default="{ row }">
          <span class="num num--danger">{{ formatPercent(row.max_drawdown) }}</span>
        </template>
      </el-table-column>
      <el-table-column label="卡玛" width="70" align="right">
        <template #default="{ row }"
          ><span class="num">{{ formatNum(row.calmar) }}</span></template
        >
      </el-table-column>
      <el-table-column label="区间" min-width="160">
        <template #default="{ row }">{{ row.start_date }}~{{ row.end_date }}</template>
      </el-table-column>
    </el-table>
  </SectionCard>
</template>

<script setup>
import SectionCard from '@/components/common/SectionCard.vue'
import { formatNum, formatPercent, numClass } from './utils'

defineProps({
  backtests: { type: Array, default: () => [] },
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
.num {
  font-family: var(--font-mono);
  font-variant-numeric: tabular-nums;
  &.num--success {
    color: var(--success);
  }
  &.num--danger {
    color: var(--danger);
  }
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
