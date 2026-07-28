<template>
  <div
    class="stat-card"
    :style="{ '--card-accent': accent }"
    @click="$emit('click')"
  >
    <div class="stat-card__icon">
      <el-icon :size="32"><component :is="icon" /></el-icon>
    </div>
    <div class="stat-card__info">
      <div class="stat-card__value">
        <span ref="valueRef">{{ displayValue }}</span>
        <span v-if="suffix" class="stat-card__suffix">{{ suffix }}</span>
      </div>
      <div class="stat-card__label">{{ label }}</div>
    </div>
    <div v-if="trend != null" class="stat-card__trend" :class="trend >= 0 ? 'is-up' : 'is-down'">
      <el-icon :size="12">
        <Top v-if="trend >= 0" />
        <Bottom v-else />
      </el-icon>
      <span>{{ Math.abs(trend).toFixed(1) }}%</span>
    </div>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'

const props = defineProps({
  icon: { type: String, required: true },
  value: { type: [String, Number], default: '--' },
  label: { type: String, default: '' },
  suffix: { type: String, default: '' },
  accent: { type: String, default: 'var(--primary)' },
  trend: { type: Number, default: null },
  animate: { type: Boolean, default: true }
})

defineEmits(['click'])

const valueRef = ref(null)

const displayValue = computed(() => {
  if (props.value === '--' || props.value == null) return '--'
  return props.value
})
</script>

<style scoped lang="scss">
.stat-card {
  display: flex;
  align-items: center;
  gap: var(--space-md);
  padding: var(--space-lg);
  background: var(--bg-card);
  border-radius: var(--radius-md);
  border: 1px solid var(--border);
  box-shadow: var(--shadow-card);
  cursor: default;
  transition: all var(--duration-normal) var(--ease-out-expo);
  position: relative;
  overflow: hidden;

  &::before {
    content: '';
    position: absolute;
    left: 0;
    top: 0;
    bottom: 0;
    width: 3px;
    background: var(--card-accent);
    border-radius: 0 3px 3px 0;
  }

  @media (hover: hover) {
    &:hover {
      transform: translateY(-4px);
      box-shadow: 0 12px 32px rgba(0, 0, 0, 0.1);
    }
  }
}

.stat-card__icon {
  width: 56px;
  height: 56px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: var(--radius-md);
  background: var(--primary-gradient-soft);
  color: var(--card-accent);
  flex-shrink: 0;
}

.stat-card__info {
  flex: 1;
  min-width: 0;
}

.stat-card__value {
  font-size: var(--font-size-2xl);
  font-weight: 700;
  line-height: 1.2;
  color: var(--text-primary);
  animation: countUp 0.5s var(--ease-out-expo);
}

.stat-card__suffix {
  font-size: var(--font-size-sm);
  font-weight: 400;
  color: var(--text-secondary);
  margin-left: 2px;
}

.stat-card__label {
  font-size: var(--font-size-sm);
  color: var(--text-secondary);
  margin-top: 4px;
}

.stat-card__trend {
  display: flex;
  align-items: center;
  gap: 2px;
  font-size: var(--font-size-sm);
  font-weight: 500;
  padding: 2px 8px;
  border-radius: var(--radius-sm);
  flex-shrink: 0;

  &.is-up {
    color: var(--danger);
    background: rgba(225, 112, 85, 0.1);
  }

  &.is-down {
    color: var(--success);
    background: rgba(0, 184, 148, 0.1);
  }
}
</style>