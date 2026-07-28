<template>
  <section class="section-card" :class="{ 'section-card--compact': compact }">
    <div v-if="$slots.header || title" class="section-card__header">
      <slot name="header">
        <div class="section-card__header-left">
          <h3 class="section-card__title">{{ title }}</h3>
          <span v-if="subtitle" class="section-card__subtitle">{{ subtitle }}</span>
        </div>
        <div v-if="$slots.extra" class="section-card__header-extra">
          <slot name="extra" />
        </div>
      </slot>
    </div>
    <div class="section-card__body">
      <slot />
    </div>
  </section>
</template>

<script setup>
defineProps({
  title: { type: String, default: '' },
  subtitle: { type: String, default: '' },
  compact: { type: Boolean, default: false }
})
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

.section-card__body {
  padding: var(--space-lg);
}
</style>