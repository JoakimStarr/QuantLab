<template>
  <div class="stat-card" :class="`stat-card--${tone}`">
    <div class="stat-card__head">
      <span class="stat-card__label">{{ label }}</span>
      <el-icon v-if="icon" class="stat-card__icon"><component :is="icon" /></el-icon>
    </div>
    <div class="stat-card__value">
      <el-skeleton v-if="loading" :rows="1" animated class="stat-card__skeleton" />
      <template v-else>
        <span class="stat-card__num">{{ displayValue }}</span>
        <span v-if="unit" class="stat-card__unit">{{ unit }}</span>
        <span v-if="hasTrend" class="stat-card__trend" :class="trend >= 0 ? 'stat-card__trend--up' : 'stat-card__trend--down'">
          {{ trendText }}
        </span>
      </template>
    </div>
    <div v-if="$slots.default" class="stat-card__extra">
      <slot />
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { fmtThousand } from '@/utils/format'

const props = defineProps({
  label: { type: String, required: true },
  value: { type: [String, Number], default: '--' },
  unit: { type: String, default: '' },
  // 涨跌趋势（+/- 数值，按百分比展示；正=▲ 红，负=▼ 绿，A股口径）
  trend: { type: Number, default: null },
  tone: {
    type: String,
    default: 'default',
    validator: (v) => ['default', 'success', 'danger', 'warning', 'info'].includes(v),
  },
  icon: { type: [Object, Function, String], default: null },
  loading: { type: Boolean, default: false },
})

const displayValue = computed(() =>
  typeof props.value === 'number' ? fmtThousand(props.value) : props.value || '--'
)

const hasTrend = computed(() => props.trend !== null && !Number.isNaN(Number(props.trend)))
const trendText = computed(() => {
  const n = Number(props.trend)
  return (n >= 0 ? '▲ ' : '▼ ') + Math.abs(n).toFixed(2) + '%'
})
</script>

<style scoped lang="scss">
.stat-card {
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-card);
  padding: var(--space-md) var(--space-lg);
  min-width: 0;
}

.stat-card__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-sm);
}

.stat-card__label {
  font-size: var(--font-size-sm);
  color: var(--text-tertiary);
}

.stat-card__icon {
  font-size: var(--font-size-xl);
  color: var(--primary);
  flex-shrink: 0;
}

.stat-card__value {
  margin-top: var(--space-sm);
  display: flex;
  align-items: baseline;
  gap: var(--space-xs);
  min-height: var(--space-xl);
}

.stat-card__skeleton {
  width: 60%;
}

.stat-card__num {
  font-size: var(--font-size-3xl);
  font-weight: var(--font-weight-semibold);
  color: var(--text-primary);
  font-variant-numeric: tabular-nums;
  line-height: 1.2;
}

.stat-card__unit {
  font-size: var(--font-size-sm);
  font-weight: var(--font-weight-medium);
  color: var(--text-tertiary);
}

.stat-card__trend {
  font-size: var(--font-size-xs);
  font-weight: var(--font-weight-medium);
  font-variant-numeric: tabular-nums;

  &--up {
    color: var(--chart-up);
  }
  &--down {
    color: var(--chart-down);
  }
}

.stat-card__extra {
  margin-top: var(--space-xs);
  font-size: var(--font-size-sm);
  color: var(--text-tertiary);
}

// tone 色彩作用于数值与图标（success=绿 / danger=红 / warning / info）
.stat-card--success .stat-card__num,
.stat-card--success .stat-card__icon {
  color: var(--success);
}
.stat-card--danger .stat-card__num,
.stat-card--danger .stat-card__icon {
  color: var(--danger);
}
.stat-card--warning .stat-card__num,
.stat-card--warning .stat-card__icon {
  color: var(--warning);
}
.stat-card--info .stat-card__num,
.stat-card--info .stat-card__icon {
  color: var(--info);
}
</style>
