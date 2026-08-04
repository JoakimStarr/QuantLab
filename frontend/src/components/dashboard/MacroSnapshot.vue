<template>
  <SectionCard title="宏观指标" :compact="true">
    <template #extra>
      <el-button size="small" link type="primary" @click="$router.push('/quant/macro')">详情</el-button>
    </template>
    <div v-if="items.length" class="macro-grid">
      <div v-for="it in items" :key="it.indicator + '-' + it.field_name" class="macro-cell" :title="it.available_date">
        <div class="macro-label">{{ it.label }}</div>
        <div class="macro-value">
          {{ it.value != null ? it.value : '--' }}
          <span v-if="it.unit" class="macro-unit">{{ it.unit }}</span>
        </div>
      </div>
    </div>
    <el-empty v-else :image-size="48" description="暂无宏观数据" />
  </SectionCard>
</template>

<script setup>
import SectionCard from '@/components/common/SectionCard.vue'

defineProps({
  items: { type: Array, default: () => [] }
})
</script>

<style scoped lang="scss">
.macro-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(110px, 1fr));
  gap: 10px;
}
.macro-cell {
  padding: 10px 12px;
  background: var(--bg-tertiary, #f5f7fa);
  border-radius: 8px;
}
.macro-label { font-size: 11px; color: var(--text-tertiary); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.macro-value { font-size: 18px; font-weight: 700; color: var(--text-primary); margin-top: 2px; font-variant-numeric: tabular-nums; }
.macro-unit { font-size: 11px; font-weight: 400; color: var(--text-tertiary); margin-left: 2px; }
</style>
