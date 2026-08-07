<template>
  <header class="page-header">
    <div class="page-header__lead">
      <h1 class="page-header__title">{{ title }}</h1>
      <p v-if="subtitle" class="page-header__subtitle">{{ subtitle }}</p>
    </div>
    <div v-if="$slots.actions" class="page-header__actions">
      <slot name="actions" />
    </div>
  </header>
</template>

<script setup>
defineProps({
  title: { type: String, required: true },
  subtitle: { type: String, default: '' },
})
</script>

<style lang="scss">
.page-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: var(--space-md);
  margin-bottom: var(--space-lg);
  animation: fadeInUp 0.5s var(--ease-out-expo) both;

  &__lead {
    flex: 1;
    min-width: 0;
  }
  &__title {
    font-size: var(--font-size-2xl);
    font-weight: 700;
    color: var(--text-primary);
    margin: 0 0 var(--space-xs);
    line-height: var(--line-height-tight);
  }
  &__subtitle {
    font-size: var(--font-size-sm);
    color: var(--text-secondary);
  }
  &__actions {
    display: flex;
    align-items: center;
    gap: var(--space-sm);
  }
  &__count {
    font-size: var(--font-size-sm);
    color: var(--text-tertiary);
    font-variant-numeric: tabular-nums;
    white-space: nowrap;
  }
}
</style>