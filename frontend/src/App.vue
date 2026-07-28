<template>
  <router-view />
</template>

<script setup>
import { onMounted } from 'vue'
import { useAppStore } from '@/stores/app'

const appStore = useAppStore()

// Initialize theme on app mount
onMounted(() => {
  // Theme is already initialized in the store's watch
  // This ensures no flash of unstyled content
  const savedTheme = localStorage.getItem('theme')
  if (savedTheme) {
    appStore.setTheme(savedTheme)
  }
})
</script>