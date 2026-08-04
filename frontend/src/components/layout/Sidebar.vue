<template>
  <aside class="sidebar">
    <!-- Logo 区 -->
    <div class="sidebar-logo">
      <router-link to="/" class="logo-link">
        <el-icon :size="20" class="logo-icon"><DataAnalysis /></el-icon>
        <span class="logo-text">{{ appName }}</span>
      </router-link>
      <span class="logo-version">v{{ appVersion }}</span>
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
        <el-icon :size="18" class="nav-icon"><component :is="item.icon" /></el-icon>
        <span class="nav-label">{{ item.title }}</span>
      </router-link>
    </nav>

    <!-- 底部：主题切换 + 版本号 -->
    <div class="sidebar-footer">
      <button
        class="theme-toggle"
        :title="isDark ? '切换到亮色模式' : '切换到暗色模式'"
        @click="toggleTheme"
      >
        <el-icon :size="16">
          <Sunny v-if="isDark" />
          <Moon v-else />
        </el-icon>
      </button>
      <span class="footer-version">v{{ appVersion }}</span>
    </div>
  </aside>
</template>

<script setup>
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { useAppStore } from '@/stores/app'
import { getVersion, getAppName } from '@/config/app'

const appStore = useAppStore()
const route = useRoute()

const isDark = computed(() => appStore.theme === 'dark')

// 从全局配置读取版本与名称（main.js 已 await initAppConfig）
const appVersion = computed(() => getVersion())
const appName = computed(() => getAppName())

// 导航菜单项（与路由配置保持一致）
const menuItems = [
  { path: '/', title: '研究首页', icon: 'DataAnalysis' },
  { path: '/quant/factors', title: '因子库', icon: 'Coin' },
  { path: '/quant/factor-compare', title: '因子对比', icon: 'DataLine' },
  { path: '/quant/strategy', title: '策略回测', icon: 'TrendCharts' },
  { path: '/quant/strategy-library', title: '策略库', icon: 'Collection' },
  { path: '/quant/backtest-compare', title: '回测对比', icon: 'Histogram' },
  { path: '/quant/mining', title: 'AI因子挖掘', icon: 'MagicStick' },
  { path: '/quant/data', title: '数据管理', icon: 'SetUp' },
  { path: '/quant/macro', title: '宏观指标', icon: 'Odometer' },
  { path: '/docs', title: '技术文档', icon: 'Reading' },
  { path: '/system/logs', title: '日志管理', icon: 'Document' },
]

// /docs 用前缀匹配，使 /docs/data-layer 也高亮"文档"项
function isActive(item) {
  if (item.path === '/docs') return route.path.startsWith('/docs')
  return route.path === item.path
}

function toggleTheme() {
  appStore.toggleTheme()
}
</script>

<style scoped lang="scss">
.sidebar {
  width: 220px;
  display: flex;
  flex-direction: column;
  flex-shrink: 0;
  background: var(--bg-primary);
  border-right: 1px solid var(--border);
  overflow: hidden;
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

.theme-toggle {
  width: 32px;
  height: 32px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 8px;
  background: var(--bg-hover);
  border: 1px solid transparent;
  color: var(--text-secondary);
  cursor: pointer;
  transition: all 150ms var(--ease-in-out);

  &:hover {
    background: var(--bg-active);
    color: var(--primary);
  }
}

.footer-version {
  font-size: var(--font-size-xs);
  color: var(--text-tertiary);
  font-family: var(--font-mono);
}
</style>
