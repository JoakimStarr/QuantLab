<template>
  <SectionCard title="宏观指标" :compact="true" collapsible>
    <template #extra>
      <el-button size="small" link type="primary" @click="$router.push('/quant/macro')">详情</el-button>
    </template>
    <div v-if="loading" class="macro-grid">
      <div class="macro-cell" v-for="i in 8" :key="i">
        <el-skeleton :rows="2" animated />
      </div>
    </div>
    <div v-else-if="items.length" class="macro-grid">
      <div
        v-for="it in items"
        :key="it.indicator + '-' + it.field_name"
        class="macro-cell"
        :title="it.available_date + (it.prevDate ? '，较 ' + it.prevDate : '')"
      >
        <div class="macro-label">{{ it.label }}</div>
        <div class="macro-value" :class="trendClass(it.change)">
          {{ displayValue(it.value) }}
          <span v-if="it.unit" class="macro-unit">{{ it.unit }}</span>
        </div>
        <div v-if="hasChange(it.change)" class="macro-trend" :class="trendClass(it.change)">
          <el-icon v-if="it.change > 0" class="macro-trend__icon"><CaretTop /></el-icon>
          <el-icon v-else class="macro-trend__icon"><CaretBottom /></el-icon>
          <span>{{ fmtChange(it.change) }}</span>
        </div>
      </div>
    </div>
    <el-empty v-else :image-size="48" description="暂无宏观数据" />
  </SectionCard>
</template>

<script setup>
import { CaretTop, CaretBottom } from '@element-plus/icons-vue'
import SectionCard from '@/components/common/SectionCard.vue'

defineProps({
  items: { type: Array, default: () => [] },
  loading: { type: Boolean, default: false },
})

function hasChange(v) {
  return v !== null && v !== undefined && Number(v) !== 0 && !Number.isNaN(Number(v))
}

// 值显示：null/undefined/NaN → '--'（后端 NaN 日历日或缺失值不应展示为 NaN）
// 数值默认保留两位小数并去除多余尾零，避免长小数撑爆卡片
function displayValue(v) {
  if (v === null || v === undefined || v === '') return '--'
  const n = Number(v)
  if (Number.isNaN(n)) return '--'
  return String(Number(n.toFixed(2)))
}

function trendClass(v) {
  const n = Number(v)
  if (n === null || n === undefined || Number.isNaN(n) || n === 0) return ''
  return n > 0 ? 'is-up' : 'is-down'
}

function fmtChange(v) {
  const n = Number(v)
  if (Number.isNaN(n)) return ''
  return `${n > 0 ? '+' : ''}${Number(n.toFixed(4))}`
}
</script>

<style scoped lang="scss">
.macro-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(120px, 1fr));
  gap: 10px;
}
.macro-cell {
  padding: 12px 14px;
  min-width: 0;
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: 10px;
}
.macro-label {
  font-size: 11px;
  color: var(--text-tertiary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.macro-value {
  font-size: 15px;
  font-weight: 700;
  color: var(--text-primary);
  margin-top: 2px;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.macro-unit {
  font-size: 11px;
  font-weight: 400;
  color: var(--text-tertiary);
  margin-left: 2px;
}
.macro-value.is-up {
  color: var(--chart-up);
}
.macro-value.is-down {
  color: var(--chart-down);
}
.macro-trend {
  display: flex;
  align-items: center;
  gap: 2px;
  margin-top: 4px;
  font-size: 12px;
  font-weight: 500;
  font-variant-numeric: tabular-nums;
}
.macro-trend__icon {
  font-size: 13px;
}
.macro-trend.is-up {
  color: var(--chart-up);
}
.macro-trend.is-down {
  color: var(--chart-down);
}
</style>
