<template>
  <svg
    v-if="points.length"
    :width="width"
    :height="height"
    :viewBox="`0 0 ${width} ${height}`"
    class="spark-line"
    role="img"
    aria-label="迷你趋势图"
  >
    <polyline :points="points" fill="none" :stroke="color" stroke-width="1.5"
              stroke-linejoin="round" stroke-linecap="round" />
    <circle :cx="lastPoint.x" :cy="lastPoint.y" r="2" :fill="color" />
  </svg>
</template>

<script setup>
// 通用 SVG 迷你趋势线（sparkline）：零依赖、适合表格内嵌。
// 数值自动归一化；单点退化为圆点。颜色默认主色 token，暗色自动适配。
import { computed } from 'vue'

const props = defineProps({
  values: { type: Array, default: () => [] },   // 数值序列（null/undefined 被过滤）
  width: { type: Number, default: 68 },
  height: { type: Number, default: 22 },
  color: { type: String, default: 'var(--primary)' },
})

const nums = computed(() => props.values.map(Number).filter((v) => Number.isFinite(v)))

const points = computed(() => {
  const { width, height } = props
  const n = nums.value.length
  if (!n) return ''
  const min = Math.min(...nums.value)
  const max = Math.max(...nums.value)
  const span = max - min
  const pad = 2
  const innerH = height - pad * 2
  // x：单点时居中；多点时均匀铺满
  const x = (i) => (n === 1 ? width / 2 : (i / (n - 1)) * (width - 4) + 2)
  const y = (v) => (span === 0 ? height / 2 : pad + innerH - ((v - min) / span) * innerH)
  return nums.value.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(' ')
})

const lastPoint = computed(() => {
  const n = nums.value.length
  if (!n) return { x: 0, y: 0 }
  const pts = points.value.split(' ')
  const [x, y] = pts[n - 1].split(',').map(Number)
  return { x, y }
})
</script>

<style scoped>
.spark-line {
  display: block;
  /* 占位对齐表格行高 */
  line-height: 1;
}
</style>
