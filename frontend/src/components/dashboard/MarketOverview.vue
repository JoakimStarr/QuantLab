<template>
  <SectionCard title="市场概览" collapsible>
    <div v-if="loading" class="market-overview-grid">
      <div class="market-overview-card" v-for="i in 8" :key="i">
        <el-skeleton :rows="2" animated />
      </div>
    </div>
    <div class="market-overview-grid" v-else-if="items.length">
      <div
        class="market-overview-card"
        v-for="item in items"
        :key="item.code"
        :class="{ 'is-active': item.code === selected }"
        @click="$emit('update:selected', item.code)"
      >
        <div class="market-overview-card__name">{{ item.name }}</div>
        <div class="market-overview-card__price">{{ fmtPrice(item.price) }}</div>
        <div class="market-overview-card__pct" :class="pctClass(item.pct_change)">
          {{ fmtPct(item.pct_change) }}
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
  selected: { type: String, default: '' },
  loading: { type: Boolean, default: false }
})
defineEmits(['update:selected'])

function toNumber(v) {
  if (v === null || v === undefined || v === '') return null
  const n = Number(v)
  return Number.isNaN(n) ? null : n
}

function fmtPrice(v) {
  const n = toNumber(v)
  if (n === null) return '--'
  return n.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

function fmtPct(v) {
  const n = toNumber(v)
  if (n === null) return '--'
  return `${n > 0 ? '+' : ''}${n}%`
}

function pctClass(v) {
  const n = toNumber(v)
  if (n === null || n === 0) return ''
  return n > 0 ? 'is-up' : 'is-down'
}
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
  &.is-up { color: var(--chart-up); }
  &.is-down { color: var(--chart-down); }
}
</style>
