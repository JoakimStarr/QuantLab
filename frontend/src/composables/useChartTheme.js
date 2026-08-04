import { computed } from 'vue'
import { useAppStore } from '@/stores/app'

// 主题版本号：图表 computed 依赖它，切换主题后重新求值并重读 --chart-* CSS 变量。
// 用法：在 computed 顶部调用 `void themeRev.value` 建立响应式依赖。
export function useThemeRev() {
  const appStore = useAppStore()
  return computed(() => appStore.themeRev)
}
