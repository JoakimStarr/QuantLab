<template>
  <div class="metrics-grid">
    <el-tooltip v-for="m in metrics" :key="m.label" :content="m.tip || ''" placement="top" :show-after="120" :disabled="!m.tip">
      <div class="metric-card">
        <div class="metric-label">
          {{ m.label }}
          <span v-if="m.tip" class="metric-hint">?</span>
        </div>
        <div class="metric-value" :class="m.tone">{{ m.value }}</div>
      </div>
    </el-tooltip>
  </div>
</template>

<script setup>
defineProps({
  metrics: { type: Array, required: true },
})
</script>

<style lang="scss" scoped>
.metrics-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
  gap: 12px;
  margin-bottom: 16px;
}
.metric-card {
  padding: 12px 14px;
  background: var(--bg-tertiary, #f5f7fa);
  border-radius: 8px;
}
.metric-label {
  font-size: 12px;
  color: var(--text-tertiary);
  margin-bottom: 3px;
  display: flex;
  align-items: center;
  white-space: nowrap;
}
.metric-value {
  font-size: 18px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
  color: var(--text-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.metric-hint {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 14px;
  height: 14px;
  margin-left: 4px;
  border-radius: 50%;
  font-size: 11px;
  font-weight: 500;
  background: var(--text-tertiary, #8a9099);
  color: var(--text-inverse, #fff);
  cursor: help;
}
:deep(.tone-success) {
  color: var(--success);
}
:deep(.tone-danger) {
  color: var(--danger);
}
</style>