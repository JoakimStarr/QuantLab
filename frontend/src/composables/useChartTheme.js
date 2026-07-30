import { computed, watch } from 'vue'
import { useAppStore } from '@/stores/app'

// 暗色/亮色 ECharts 色值（Canvas 不支持 CSS 变量，需读取实际色值）
const LIGHT = {
  bg: '#ffffff',
  border: '#e3e9f1',
  text: '#16213a',
  textSecondary: '#5b6b85',
  textTertiary: '#8493ab',
  grid: '#eef2f7',
  axisLine: '#e3e9f1',
  primary: '#1f4ba0',
}

const DARK = {
  bg: '#161f33',
  border: '#1a2540',
  text: '#e8edf5',
  textSecondary: '#8493ab',
  textTertiary: '#5b6b85',
  grid: '#1a2540',
  axisLine: '#2a3855',
  primary: '#4f7fc8',
}

/**
 * ECharts 主题色 composable：从 appStore.theme 读取实际色值，
 * 并监听主题变化触发 onChange，便于图表 setOption 更新。
 *
 * @param {Function} [onChange] - 主题变化回调，接收新色值对象
 * @returns {{ isDark: ComputedRef<boolean>, chartColors: ComputedRef<object> }}
 */
export function useChartTheme(onChange) {
  const appStore = useAppStore()
  const isDark = computed(() => appStore.theme === 'dark')
  const chartColors = computed(() => (isDark.value ? DARK : LIGHT))

  if (onChange) {
    watch(chartColors, (c) => onChange(c), { deep: true })
  }
  return { isDark, chartColors }
}
