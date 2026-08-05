<template>
  <section class="section-card" :class="{ 'section-card--compact': compact, 'section-card--collapsed': collapsed }">
    <div v-if="$slots.header || title" class="section-card__header">
      <div
        class="section-card__header-left"
        :class="{ 'section-card__header-left--clickable': collapsible }"
        @click="collapsible && toggle()"
      >
        <slot name="header">
          <h3 class="section-card__title">{{ title }}</h3>
          <span v-if="subtitle" class="section-card__subtitle">{{ subtitle }}</span>
        </slot>
      </div>
      <div v-if="$slots.extra || collapsible" class="section-card__header-extra">
        <slot name="extra" />
        <el-icon v-if="collapsible" class="section-card__collapse-icon" @click.stop="toggle()">
          <ArrowDown />
        </el-icon>
      </div>
    </div>
    <el-collapse-transition>
      <div v-show="!collapsed" class="section-card__body">
        <slot />
      </div>
    </el-collapse-transition>
  </section>
</template>

<script setup>
import { ref, watch } from 'vue'
import { ArrowDown } from '@element-plus/icons-vue'

const props = defineProps({
  title: { type: String, default: '' },
  subtitle: { type: String, default: '' },
  compact: { type: Boolean, default: false },
  collapsible: { type: Boolean, default: false },
  collapsed: { type: Boolean, default: false },
})
const emit = defineEmits(['update:collapsed'])

const collapsed = ref(props.collapsed)
watch(
  () => props.collapsed,
  (v) => {
    collapsed.value = v
  }
)

function toggle() {
  collapsed.value = !collapsed.value
  emit('update:collapsed', collapsed.value)
}
</script>

<style scoped lang="scss">
.section-card {
  background: var(--bg-card);
  border-radius: var(--radius-md);
  border: 1px solid var(--border);
  box-shadow: var(--shadow-card);
  margin-bottom: var(--space-md);
  animation: fadeInUp 0.5s var(--ease-out-expo) both;

  &--compact {
    .section-card__header {
      padding: var(--space-md) var(--space-lg);
    }
    .section-card__body {
      padding: var(--space-md) var(--space-lg);
    }
  }
}

.section-card__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: var(--space-md) var(--space-lg);
  border-bottom: 1px solid var(--border-light);
  flex-wrap: wrap;
  gap: var(--space-sm);
}

.section-card__header-left {
  display: flex;
  align-items: center;
  gap: var(--space-sm);
  min-width: 0;
}

.section-card__title {
  font-size: var(--font-size-lg);
  font-weight: 600;
  color: var(--text-primary);
  margin: 0;
}

.section-card__subtitle {
  font-size: var(--font-size-xs);
  color: var(--text-tertiary);
  white-space: nowrap;
}

.section-card__header-extra {
  display: flex;
  align-items: center;
  gap: var(--space-sm);
  flex-shrink: 0;
}

.section-card__header-left--clickable {
  cursor: pointer;
}

.section-card__collapse-icon {
  color: var(--text-tertiary);
  transition: transform var(--duration-fast) var(--ease-in-out);
  cursor: pointer;
}

.section-card--collapsed .section-card__collapse-icon {
  transform: rotate(-90deg);
}

.section-card__body {
  padding: var(--space-lg);
}
</style>
