<template>
  <nav class="mobile-tabbar">
    <router-link
      v-for="item in tabItems"
      :key="item.path"
      :to="item.path"
      class="tab-item"
      :class="{ 'tab-item--active': $route.path === item.path }"
    >
      <el-icon :size="20"><component :is="item.icon" /></el-icon>
      <span class="tab-label">{{ item.label }}</span>
    </router-link>
  </nav>
</template>

<script setup>
const tabItems = [
  { path: '/', label: '首页', icon: 'DataAnalysis' },
  { path: '/quant/factors', label: '因子', icon: 'Coin' },
  { path: '/quant/strategy', label: '回测', icon: 'TrendCharts' },
  { path: '/quant/mining', label: '挖掘', icon: 'MagicStick' },
  { path: '/quant/data', label: '数据', icon: 'SetUp' },
  { path: '/system/logs', label: '日志', icon: 'Document' },
]
</script>

<style scoped lang="scss">
.mobile-tabbar {
  position: fixed;
  bottom: 0;
  left: 0;
  right: 0;
  height: 56px;
  display: flex;
  align-items: center;
  justify-content: space-around;
  background: var(--bg-primary);
  border-top: 1px solid var(--border);
  z-index: 1000;
  padding-bottom: env(safe-area-inset-bottom);
}

.tab-item {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 2px;
  padding: 6px 12px;
  color: var(--text-tertiary);
  text-decoration: none;
  transition: color var(--duration-fast) var(--ease-in-out);
  -webkit-tap-highlight-color: transparent;

  &--active {
    color: var(--primary);
  }
}

.tab-label {
  font-size: var(--font-size-xs);
  line-height: 1;
}

// 桌面端隐藏移动底栏（>=768px）
@media (min-width: 768px) {
  .mobile-tabbar { display: none; }
}
</style>