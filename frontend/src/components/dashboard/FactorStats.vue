<template>
  <SectionCard title="因子统计" collapsible>
    <div class="factor-stats">
      <div class="factor-stats__total">
        <span class="factor-stats__total-label">因子总数</span>
        <span class="factor-stats__total-value">{{ total }}</span>
      </div>
      <div class="factor-stats__bars">
        <div class="factor-stats__bar" v-for="item in sourceBars" :key="item.key">
          <div class="factor-stats__bar-label">
            <span class="badge" :class="`badge--${item.badge}`">{{ item.label }}</span>
            <span class="num">{{ item.count }}</span>
          </div>
          <div class="factor-stats__bar-track">
            <div
              class="factor-stats__bar-fill"
              :class="`badge--${item.badge}`"
              :style="{ width: item.percent + '%' }"
            />
          </div>
        </div>
      </div>
    </div>
  </SectionCard>
</template>

<script setup>
import { computed } from 'vue'
import SectionCard from '@/components/common/SectionCard.vue'

const props = defineProps({
  total: { type: Number, default: 0 },
  bySource: { type: Object, default: () => ({}) },
})

const sourceMeta = [
  { key: 'builtin', label: '内置', badge: 'primary' },
  { key: 'llm', label: 'LLM', badge: 'success' },
  { key: 'symbolic', label: '符号', badge: 'warning' },
  { key: 'text', label: '文本', badge: 'info' },
  { key: 'automl', label: 'AutoML', badge: 'danger' },
]

const sourceBars = computed(() => {
  const total = props.total || 1
  return sourceMeta.map((m) => {
    const count = props.bySource?.[m.key] ?? 0
    return { ...m, count, percent: Math.round((count / total) * 100) }
  })
})
</script>

<style scoped lang="scss">
.factor-stats {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.factor-stats__total {
  display: flex;
  align-items: baseline;
  gap: 12px;
  padding-bottom: 12px;
  border-bottom: 1px solid var(--border-light);
}
.factor-stats__total-label {
  font-size: 14px;
  color: var(--text-tertiary);
}
.factor-stats__total-value {
  font-size: 28px;
  font-weight: 600;
  color: var(--text-primary);
  font-variant-numeric: tabular-nums;
}
.factor-stats__bars {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.factor-stats__bar {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.factor-stats__bar-label {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 13px;
  color: var(--text-secondary);
}
.factor-stats__bar-track {
  height: 6px;
  background: var(--bg-tertiary);
  border-radius: 3px;
  overflow: hidden;
}
.factor-stats__bar-fill {
  height: 100%;
  border-radius: 3px;
  transition: width 0.3s;
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
  background: var(--primary-soft);
  color: var(--primary);
}
.badge--success {
  background: var(--success-soft);
  color: var(--success);
}
.badge--warning {
  background: var(--warning-soft);
  color: var(--warning);
}
.badge--info {
  background: var(--info-soft);
  color: var(--info);
}
.badge--danger {
  background: var(--danger-soft);
  color: var(--danger);
}
.factor-stats__bar-fill.badge--primary {
  background: var(--primary);
}
.factor-stats__bar-fill.badge--success {
  background: var(--success);
}
.factor-stats__bar-fill.badge--warning {
  background: var(--warning);
}
.factor-stats__bar-fill.badge--info {
  background: var(--info);
}
.factor-stats__bar-fill.badge--danger {
  background: var(--danger);
}
.num {
  font-family: var(--font-mono);
  font-variant-numeric: tabular-nums;
}
</style>
