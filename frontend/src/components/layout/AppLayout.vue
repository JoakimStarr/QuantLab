<template>
  <div class="app-layout">
    <Sidebar class="desktop-only" />
    <div class="layout-main">
      <TopBar class="desktop-only" />
      <div class="app-mobile-header mobile-only">
        <router-link to="/" class="mobile-logo">
          <div class="mobile-logo-icon">
            <el-icon :size="18" color="#fff"><DataAnalysis /></el-icon>
          </div>
          <span class="mobile-logo-text">QuantLab</span>
        </router-link>
      </div>
      <main class="app-content">
        <router-view v-slot="{ Component }">
          <Transition name="page" mode="out-in">
            <component :is="Component" />
          </Transition>
        </router-view>
      </main>
      <footer class="app-footer desktop-only">
        QuantLab · 量化策略回测研究平台 · 仅供研究参考，不构成投资建议
      </footer>
      <MobileTabBar class="mobile-only" />
    </div>
  </div>
</template>

<script setup>
import Sidebar from './Sidebar.vue'
import TopBar from './TopBar.vue'
import MobileTabBar from './MobileTabBar.vue'
</script>

<style scoped lang="scss">
.app-layout {
  height: 100vh;
  display: flex;
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
  padding: var(--space-lg);
  background: var(--bg-page);

  @media (max-width: 767px) {
    padding: var(--space-md);
    padding-bottom: calc(56px + env(safe-area-inset-bottom) + var(--space-md));
  }
}

.app-footer {
  height: var(--footer-height);
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--bg-primary);
  border-top: 1px solid var(--border);
  font-size: var(--font-size-xs);
  color: var(--text-tertiary);
  flex-shrink: 0;
}

.app-mobile-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  height: 48px;
  padding: 0 var(--space-md);
  background: linear-gradient(135deg, #1e293b, #0f172a);
  flex-shrink: 0;
}

.mobile-logo {
  display: flex;
  align-items: center;
  gap: var(--space-sm);
  text-decoration: none;
}

.mobile-logo-icon {
  width: 28px;
  height: 28px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: var(--radius-sm);
  background: linear-gradient(135deg, #1a73e8, #6c5ce7);
}

.mobile-logo-text {
  font-size: var(--font-size-base);
  font-weight: 600;
  color: #fff;
}

.mobile-market-badge {
  display: flex;
  align-items: center;
  gap: 4px;
}

.market-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;

  &--morning,
  &--afternoon {
    background: var(--success);
  }

  &--lunch {
    background: var(--warning);
  }

  &--closed,
  &--weekend {
    background: rgba(255, 255, 255, 0.3);
  }
}

.mobile-market-text {
  font-size: 10px;
  color: rgba(255, 255, 255, 0.6);
}
</style>