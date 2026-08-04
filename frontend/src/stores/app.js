import { defineStore } from 'pinia'
import { ref, watch } from 'vue'

export const useAppStore = defineStore('app', () => {
  const loading = ref(false)
  const sidebarCollapsed = ref(false)
  const theme = ref(localStorage.getItem('theme') || 'light')
  // 主题版本号：每次主题切换 +1，供图表 computed 建立依赖以重读 CSS 变量刷新
  const themeRev = ref(0)

  // Watch theme changes and apply to document
  watch(theme, (newTheme) => {
    const root = document.documentElement
    if (newTheme === 'dark') {
      root.classList.add('dark')
    } else {
      root.classList.remove('dark')
    }
    localStorage.setItem('theme', newTheme)
    themeRev.value++
  }, { immediate: true })

  function toggleSidebar() {
    sidebarCollapsed.value = !sidebarCollapsed.value
  }

  function toggleTheme() {
    theme.value = theme.value === 'light' ? 'dark' : 'light'
  }

  function setTheme(newTheme) {
    theme.value = newTheme
  }

  return {
    loading,
    sidebarCollapsed,
    theme,
    themeRev,
    toggleSidebar,
    toggleTheme,
    setTheme,
  }
})
