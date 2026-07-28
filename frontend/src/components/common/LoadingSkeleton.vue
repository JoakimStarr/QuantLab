<template>
  <div class="loading-skeleton" :class="`loading-skeleton--${type}`">
    <template v-if="type === 'card'">
      <div v-for="i in count" :key="i" class="skeleton-card">
        <div class="skeleton-card__icon" />
        <div class="skeleton-card__lines">
          <div class="skeleton-line skeleton-line--short" />
          <div class="skeleton-line skeleton-line--long" />
        </div>
      </div>
    </template>
    <template v-else-if="type === 'table'">
      <div class="skeleton-table">
        <div class="skeleton-table__header">
          <div v-for="i in 4" :key="i" class="skeleton-line" />
        </div>
        <div v-for="i in Math.min(count, 5)" :key="i" class="skeleton-table__row">
          <div v-for="j in 4" :key="j" class="skeleton-line" />
        </div>
      </div>
    </template>
    <template v-else>
      <div v-for="i in count" :key="i" class="skeleton-line skeleton-line--full" />
    </template>
  </div>
</template>

<script setup>
defineProps({
  type: { type: String, default: 'card' },
  count: { type: Number, default: 3 }
})
</script>

<style scoped lang="scss">
.skeleton-line {
  height: 14px;
  border-radius: 7px;
  background: linear-gradient(90deg, var(--bg-tertiary) 25%, var(--border-light) 50%, var(--bg-tertiary) 75%);
  background-size: 200% 100%;
  animation: shimmer 1.5s ease-in-out infinite;

  &--short { width: 40%; }
  &--long { width: 80%; }
  &--full { width: 100%; margin-bottom: 8px; }
}

.skeleton-card {
  display: flex;
  align-items: center;
  gap: var(--space-md);
  padding: var(--space-lg);
  background: var(--bg-card);
  border-radius: var(--radius-md);
  border: 1px solid var(--border);
  margin-bottom: var(--space-sm);

  &__icon {
    width: 48px;
    height: 48px;
    border-radius: var(--radius-md);
    background: var(--bg-tertiary);
    flex-shrink: 0;
    animation: pulse 2s ease-in-out infinite;
  }

  &__lines {
    flex: 1;
    display: flex;
    flex-direction: column;
    gap: 8px;
  }
}

.skeleton-table {
  &__header {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: var(--space-md);
    padding: var(--space-md);
    border-bottom: 1px solid var(--border);
  }

  &__row {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: var(--space-md);
    padding: var(--space-md);
    border-bottom: 1px solid var(--border-light);
  }
}
</style>