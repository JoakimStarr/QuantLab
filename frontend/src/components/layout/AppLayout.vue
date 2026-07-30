<template>
  <div class="app-layout">
    <Sidebar />
    <div class="layout-main">
      <TopBar />
      <main class="app-content">
        <router-view v-slot="{ Component, route }">
          <Transition name="page" mode="out-in">
            <keep-alive :include="keepAliveNames">
              <component :is="Component" :key="route.fullPath" />
            </keep-alive>
          </Transition>
        </router-view>
      </main>
    </div>
    <MobileTabBar />
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import Sidebar from './Sidebar.vue'
import TopBar from './TopBar.vue'
import MobileTabBar from './MobileTabBar.vue'
import wsClient from '@/api/websocket'
import { useAuthStore } from '@/stores/auth'

const router = useRouter()
const authStore = useAuthStore()

// 需缓存的页面名（route.name 且 meta.keepAlive），组件名需与之匹配
const keepAliveNames = computed(() =>
  router.getRoutes()
    .filter(r => r.meta?.keepAlive && r.name)
    .map(r => r.name)
)

onMounted(() => {
  // 建立 WebSocket 连接（鉴权开启时附带 token）
  const token = authStore.authEnabled ? authStore.token : undefined
  wsClient.connect(token)
})

onUnmounted(() => {
  wsClient.close()
})
</script>

<style scoped lang="scss">
.app-layout {
  display: flex;
  height: 100vh;
  overflow: hidden;
}

.layout-main {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.app-content {
  flex: 1;
  overflow-y: auto;
  padding: 24px;
  background: var(--bg-page);
}
</style>
