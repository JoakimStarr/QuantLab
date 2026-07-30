<template>
  <SectionCard title="市场概览">
    <div class="market-overview-grid" v-if="items.length">
      <div
        class="market-overview-card"
        v-for="item in items"
        :key="item.code"
        :class="{ 'is-active': item.code === selected }"
        @click="$emit('update:selected', item.code)"
      >
        <div class="market-overview-card__name">{{ item.name }}</div>
        <div class="market-overview-card__price">{{ item.price }}</div>
        <div class="market-overview-card__pct" :class="item.pct_change >= 0 ? 'is-up' : 'is-down'">
          {{ item.pct_change >= 0 ? '+' : '' }}{{ item.pct_change }}%
        </div>
      </div>
    </div>
    <el-empty v-else description="暂无行情" :image-size="60" />
  </SectionCard>
</template>

<script setup>
import SectionCard from '@/components/common/SectionCard.vue'

defineProps({
  items: { type: Array, default: () => [] },
  selected: { type: String, default: '' }
})
defineEmits(['update:selected'])
</script>

<style scoped lang="scss">
.market-overview-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(140px, 1fr));
  gap: 12px;
}
.market-overview-card {
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 14px 16px;
  cursor: pointer;
  transition: all var(--duration-fast) var(--ease-in-out);
  &:hover { border-color: var(--primary); }
  &.is-active { border-color: var(--primary); box-shadow: 0 0 0 1px var(--primary); }
}
.market-overview-card__name { font-size: 13px; color: var(--text-tertiary); }
.market-overview-card__price {
  font-size: 20px; font-weight: 600; color: var(--text-primary);
  font-variant-numeric: tabular-nums; margin-top: 4px;
}
.market-overview-card__pct {
  font-size: 13px; font-weight: 500; font-variant-numeric: tabular-nums; margin-top: 2px;
  &.is-up { color: #ef232a; }
  &.is-down { color: #14b143; }
}
</style>
