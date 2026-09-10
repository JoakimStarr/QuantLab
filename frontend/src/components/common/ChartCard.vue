<template>
  <SectionCard :title="title" :subtitle="subtitle">
    <div class="chart-card" :style="{ height: height + 'px' }">
      <div v-if="loading" v-loading="true" class="chart-card__state" />
      <EmptyState v-else-if="!hasData" :description="emptyText" size="sm" />
      <slot v-else />
    </div>
  </SectionCard>
</template>

<script setup>
import SectionCard from './SectionCard.vue'
import EmptyState from './EmptyState.vue'

defineProps({
  title: { type: String, default: '' },
  subtitle: { type: String, default: '' },
  loading: { type: Boolean, default: false },
  hasData: { type: Boolean, default: false },
  emptyText: { type: String, default: '暂无数据' },
  // 图表区高度（px），slot 内图表请铺满 100%
  height: { type: Number, default: 320 },
})
</script>

<style scoped lang="scss">
.chart-card {
  width: 100%;
}

.chart-card__state {
  width: 100%;
  height: 100%;
  min-height: var(--space-3xl);
}
</style>
