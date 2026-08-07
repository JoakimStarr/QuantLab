<template>
  <aside class="sidebar" :class="{ 'sidebar--collapsed': sidebarCollapsed }">
    <!-- Logo 区 -->
    <div class="sidebar-logo">
      <router-link to="/" class="logo-link">
        <el-icon :size="20" class="logo-icon"><DataAnalysis /></el-icon>
        <span v-if="!sidebarCollapsed" class="logo-text">{{ appName }}</span>
      </router-link>
      <span v-if="!sidebarCollapsed" class="logo-version">v{{ appVersion }}</span>
    </div>

    <!-- 导航菜单 -->
    <nav class="sidebar-nav">
      <router-link
        v-for="item in menuItems"
        :key="item.path"
        :to="item.path"
        class="nav-item"
        :class="{ 'nav-item--active': isActive(item) }"
      >
        <el-icon :size="18" class="nav-icon"><component :is="resolveIcon(item.icon)" /></el-icon>
        <span v-if="!sidebarCollapsed" class="nav-label">{{ item.title }}</span>
      </router-link>
    </nav>

    <!-- 底部：版本号 -->
    <div class="sidebar-footer">
      <span class="footer-version">v{{ appVersion }}</span>
    </div>
  </aside>
</template>

<script setup>
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { DataAnalysis } from '@element-plus/icons-vue'
import { useAppStore } from '@/stores/app'
import { getVersion, getAppName } from '@/config/app'
import { resolveIcon } from '@/utils/icons'
import { navItems } from '@/config/nav'

const appStore = useAppStore()
const route = useRoute()

const sidebarCollapsed = computed(() => appStore.sidebarCollapsed)

// 从全局配置读取版本与名称（initAppConfig 挂载后异步加载，ref 响应式自动更新）
const appVersion = computed(() => getVersion())
const appName = computed(() => getAppName())

// 导航菜单项（来自单一数据源 src/config/nav.js）
const menuItems = navItems

// /docs 用前缀匹配，使 /docs/data-layer 也高亮"文档"项
function isActive(item) {
  if (item.path === '/docs') return route.path.startsWith('/docs')
  return route.path === item.path
}
</script>

<style scoped lang="scss">
.sidebar {
  width: var(--sidebar-width);
  display: flex;
  flex-direction: column;
  flex-shrink: 0;
  background: var(--bg-primary);
  border-right: 1px solid var(--border);
  overflow: hidden;
  transition: width 160ms var(--ease-in-out);
  will-change: width;
}

// 折叠态：收窄为纯图标栏
.sidebar--collapsed {
  width: var(--sidebar-collapsed-width);

  .sidebar-logo {
    justify-content: center;
  }

  .nav-item {
    justify-content: center;
    padding: 0;
    border-left: none;
  }

  .sidebar-footer {
    justify-content: center;
  }
}

// 移动端隐藏侧栏，交由 MobileTabBar 承接导航
@media (max-width: 767px) {
  .sidebar {
    display: none;
  }
}

// Logo 区
.sidebar-logo {
  height: 56px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 16px;
  flex-shrink: 0;
}

.logo-link {
  display: flex;
  align-items: center;
  gap: 10px;
  text-decoration: none;
}

.logo-icon {
  color: var(--primary);
}

.logo-text {
  font-size: 15px;
  font-weight: var(--font-weight-semibold);
  color: var(--text-primary);
  letter-spacing: 0.3px;
}

.logo-version {
  font-size: var(--font-size-xs);
  color: var(--text-tertiary);
}

// 导航菜单
.sidebar-nav {
  flex: 1;
  overflow-y: auto;
  overflow-x: hidden;
  padding: 8px 0;

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

.nav-item {
  display: flex;
  align-items: center;
  gap: 12px;
  height: 40px;
  margin: 0 8px;
  padding: 0 12px;
  border-radius: 8px;
  font-size: 14px;
  color: var(--text-secondary);
  text-decoration: none;
  border-left: 3px solid transparent;
  transition: all 150ms var(--ease-in-out);
  cursor: pointer;

  &:hover {
    background: var(--bg-hover);
    color: var(--text-primary);
  }

  // 选中态三件套：背景 + 主色文字 + 左侧主色边条
  &--active {
    background: rgba(var(--primary-rgb), 0.08);
    color: var(--primary);
    font-weight: var(--font-weight-medium);
    border-left: 3px solid var(--primary);
  }
}

.nav-icon {
  flex-shrink: 0;
}

.nav-label {
  white-space: nowrap;
}

// 底部
.sidebar-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 16px;
  border-top: 1px solid var(--border);
  flex-shrink: 0;
}

.footer-version {
  font-size: var(--font-size-xs);
  color: var(--text-tertiary);
  font-family: var(--font-mono);
}
</style>
