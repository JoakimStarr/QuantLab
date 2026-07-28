import { defineStore } from 'pinia'
import { ref, watch } from 'vue'

export const useAppStore = defineStore('app', () => {
  const loading = ref(false)
  const sidebarCollapsed = ref(false)
  const theme = ref(localStorage.getItem('theme') || 'light')

  // Watch theme changes and apply to document
  watch(theme, (newTheme) => {
    const root = document.documentElement
    if (newTheme === 'dark') {
      root.classList.add('dark')
    } else {
      root.classList.remove('dark')
    }
    localStorage.setItem('theme', newTheme)
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
    toggleSidebar,
    toggleTheme,
    setTheme,
  }
})
