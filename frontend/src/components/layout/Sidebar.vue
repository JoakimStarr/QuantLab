<template>
  <aside
    class="sidebar"
    :class="{
      'sidebar--collapsed': appStore.sidebarCollapsed,
      'sidebar--dark': isDark
    }"
  >
    <!-- Logo -->
    <router-link to="/" class="sidebar-logo">
      <div class="logo-icon">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
          <path d="M3 3V21H21V3H3Z" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
          <path d="M9 9L15 15M15 9L9 15" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
        </svg>
      </div>
      <transition name="logo-fade">
        <span v-show="!appStore.sidebarCollapsed" class="logo-text">QuantLab</span>
      </transition>
    </router-link>

    <!-- Menu -->
    <nav class="sidebar-nav">
      <el-menu
        :default-active="$route.path"
        router
        :collapse="appStore.sidebarCollapsed"
        class="sidebar-menu"
      >
        <el-menu-item v-for="item in menuItems" :key="item.path" :index="item.path">
          <el-icon><component :is="item.icon" /></el-icon>
          <template #title>{{ item.title }}</template>
        </el-menu-item>
      </el-menu>
    </nav>

    <!-- Footer -->
    <div class="sidebar-footer">
      <transition name="fade">
        <div v-show="!appStore.sidebarCollapsed" class="footer-content">
          <button @click="toggleTheme" class="theme-toggle" :title="isDark ? '切换到亮色模式' : '切换到暗色模式'">
            <el-icon :size="16">
              <Sunny v-if="isDark" />
              <Moon v-else />
            </el-icon>
          </button>
          <div class="version-badge">v2.1.0</div>
        </div>
      </transition>
    </div>
  </aside>
</template>

<script setup>
import { computed } from 'vue'
import { useAppStore } from '@/stores/app'

const appStore = useAppStore()

const isDark = computed(() => appStore.theme === 'dark')

const menuItems = [
  { path: '/', title: '研究首页', icon: 'DataAnalysis' },
  { path: '/quant/factors', title: '因子库', icon: 'Coin' },
  { path: '/quant/strategy', title: '策略回测', icon: 'TrendCharts' },
  { path: '/quant/mining', title: 'AI因子挖掘', icon: 'MagicStick' },
  { path: '/quant/data', title: '数据管理', icon: 'SetUp' },
]

function toggleTheme() {
  appStore.toggleTheme()
}
</script>

<style scoped lang="scss">
.sidebar {
  width: var(--sidebar-width);
  background: var(--bg-primary);
  border-right: 1px solid var(--border);
  display: flex;
  flex-direction: column;
  flex-shrink: 0;
  overflow: hidden;
  transition: width var(--duration-normal) var(--ease-out-expo),
              background-color var(--duration-normal) var(--ease-in-out);

  &--collapsed {
    width: var(--sidebar-collapsed-width);
  }

  &--dark {
    background: linear-gradient(180deg, #1e293b 0%, #0f172a 100%);
    border-right-color: rgba(255, 255, 255, 0.06);
  }
}

// Logo
.sidebar-logo {
  display: flex;
  align-items: center;
  gap: var(--space-sm);
  padding: var(--space-md);
  text-decoration: none;
  flex-shrink: 0;
  overflow: hidden;
  transition: padding var(--duration-normal) var(--ease-out-expo);

  .sidebar--collapsed & {
    padding: var(--space-md) var(--space-sm);
    justify-content: center;
  }
}

.logo-icon {
  width: 36px;
  height: 36px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: var(--radius-md);
  background: var(--primary-gradient);
  color: #fff;
  flex-shrink: 0;
  transition: transform var(--duration-fast) var(--ease-spring);

  .sidebar-logo:hover & {
    transform: scale(1.05) rotate(5deg);
  }
}

.logo-text {
  font-size: var(--font-size-lg);
  font-weight: var(--font-weight-bold);
  color: var(--text-primary);
  white-space: nowrap;
  letter-spacing: 0.3px;

  .sidebar--dark & {
    color: #fff;
  }
}

// Navigation
.sidebar-nav {
  flex: 1;
  overflow-y: auto;
  overflow-x: hidden;
  padding: var(--space-sm) 0;

  // Custom scrollbar
  &::-webkit-scrollbar {
    width: 4px;
  }

  &::-webkit-scrollbar-thumb {
    background: var(--border);
    border-radius: var(--radius-full);

    &:hover {
      background: var(--text-tertiary);
    }
  }
}

.sidebar-menu {
  border-right: none !important;
  background: transparent !important;

  :deep(.el-menu-item) {
    height: 44px;
    line-height: 44px;
    margin: 2px var(--space-sm);
    border-radius: var(--radius-md);
    color: var(--text-secondary) !important;
    font-weight: var(--font-weight-medium);
    transition: all var(--duration-fast) var(--ease-in-out);
    position: relative;

    &:hover {
      background: var(--bg-hover) !important;
      color: var(--text-primary) !important;
    }

    &.is-active {
      color: var(--primary) !important;
      background: var(--primary-gradient-soft) !important;
      font-weight: var(--font-weight-semibold);

      &::before {
        content: '';
        position: absolute;
        left: 0;
        top: 50%;
        transform: translateY(-50%);
        width: 3px;
        height: 24px;
        background: var(--primary);
        border-radius: 0 3px 3px 0;
      }
    }

    .el-icon {
      font-size: 18px;
    }
  }

  .sidebar--dark :deep(.el-menu-item) {
    color: rgba(255, 255, 255, 0.7) !important;

    &:hover {
      background: rgba(255, 255, 255, 0.05) !important;
      color: rgba(255, 255, 255, 0.95) !important;
    }

    &.is-active {
      color: #fff !important;
      background: rgba(var(--primary-rgb), 0.2) !important;
    }
  }
}

// Footer
.sidebar-footer {
  padding: var(--space-sm) var(--space-sm);
  border-top: 1px solid var(--border);
  flex-shrink: 0;

  .sidebar--dark & {
    border-top-color: rgba(255, 255, 255, 0.06);
  }
}

.footer-content {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: var(--space-xs) var(--space-sm);
}

.theme-toggle {
  width: 32px;
  height: 32px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: var(--radius-md);
  background: var(--bg-hover);
  border: 1px solid var(--border);
  color: var(--text-secondary);
  cursor: pointer;
  transition: all var(--duration-fast) var(--ease-in-out);

  &:hover {
    background: var(--bg-active);
    color: var(--primary);
    transform: rotate(20deg);
  }

  .sidebar--dark & {
    background: rgba(255, 255, 255, 0.05);
    border-color: rgba(255, 255, 255, 0.1);
    color: rgba(255, 255, 255, 0.7);

    &:hover {
      background: rgba(255, 255, 255, 0.1);
      color: #fff;
    }
  }
}

.version-badge {
  font-size: var(--font-size-xs);
  color: var(--text-tertiary);
  font-family: var(--font-mono);
  padding: var(--space-xs) var(--space-sm);

  .sidebar--dark & {
    color: rgba(255, 255, 255, 0.4);
  }
}

// Animations
.logo-fade-enter-active,
.logo-fade-leave-active {
  transition: opacity var(--duration-fast) var(--ease-in-out);
}

.logo-fade-enter-from,
.logo-fade-leave-to {
  opacity: 0;
}

.fade-enter-active,
.fade-leave-active {
  transition: opacity var(--duration-fast) var(--ease-in-out);
}

.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}
</style>