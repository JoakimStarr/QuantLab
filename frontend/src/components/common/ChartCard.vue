<template>
  <div class="chart-card" :class="{ 'chart-card--loading': loading }">
    <div v-if="$slots.header || title" class="chart-card__header">
      <span class="chart-card__title">{{ title }}</span>
      <slot name="header" />
    </div>
    <div class="chart-card__body" :style="{ height }">
      <div v-if="loading" class="chart-card__loading">
        <el-icon class="is-loading" :size="24"><Loading /></el-icon>
      </div>
      <div v-else ref="chartRef" class="chart-card__canvas" />
      <div v-if="empty && !loading" class="chart-card__empty">
        <el-empty :description="emptyText" :image-size="80" />
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted, nextTick, watch } from 'vue'

const props = defineProps({
  title: { type: String, default: '' },
  height: { type: String, default: '300px' },
  loading: { type: Boolean, default: false },
  empty: { type: Boolean, default: false },
  emptyText: { type: String, default: '暂无数据' },
  resize: { type: Boolean, default: true }
})

const emit = defineEmits(['chart-ready'])

const chartRef = ref(null)
let chartInstance = null

onMounted(() => {
  initChart()
})

onUnmounted(() => {
  if (chartInstance) {
    chartInstance.dispose()
    chartInstance = null
  }
  if (props.resize) {
    window.removeEventListener('resize', handleResize)
  }
})

function initChart() {
  nextTick(() => {
    if (!chartRef.value) return
    import('echarts').then(echarts => {
      chartInstance = echarts.init(chartRef.value)
      emit('chart-ready', chartInstance)
      if (props.resize) {
        window.addEventListener('resize', handleResize)
      }
    })
  })
}

function handleResize() {
  if (chartInstance) chartInstance.resize()
}

defineExpose({ chartInstance, resize: handleResize })
</script>

<style scoped lang="scss">
.chart-card {
  background: var(--bg-card);
  border-radius: var(--radius-md);
  border: 1px solid var(--border);
  box-shadow: var(--shadow-card);
  overflow: hidden;
}

.chart-card__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: var(--space-md) var(--space-lg);
  border-bottom: 1px solid var(--border-light);
}

.chart-card__title {
  font-size: var(--font-size-lg);
  font-weight: 600;
  color: var(--text-primary);
}

.chart-card__body {
  position: relative;
}

.chart-card__canvas {
  width: 100%;
  height: 100%;
}

.chart-card__loading {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--text-tertiary);
}

.chart-card__empty {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
}
</style>