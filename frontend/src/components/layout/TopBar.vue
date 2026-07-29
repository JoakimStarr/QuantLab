<template>
  <header class="topbar">
    <!-- 左侧：当前页面标题 -->
    <div class="topbar-left">
      <h1 class="page-title">{{ pageTitle }}</h1>
    </div>

    <!-- 右侧：搜索 + 状态 + 主题 + 用户 -->
    <div class="topbar-right">
      <!-- 搜索框 -->
      <el-input
        v-model="searchQuery"
        class="search-input"
        placeholder="搜索因子、策略..."
        :prefix-icon="Search"
        @keyup.enter="handleSearch"
      />

      <!-- 状态徽章 -->
      <span class="status-badge">
        <span class="status-dot"></span>
        数据已就绪
      </span>

      <!-- 主题切换 -->
      <button
        class="theme-btn"
        :title="isDark ? '切换到亮色模式' : '切换到暗色模式'"
        @click="toggleTheme"
      >
        <el-icon :size="18">
          <Sunny v-if="isDark" />
          <Moon v-else />
        </el-icon>
      </button>

      <!-- 用户头像 -->
      <div class="user-avatar" title="研究员">Q</div>
    </div>
  </header>
</template>

<script setup>
import { ref, computed } from 'vue'
import { useRoute } from 'vue-router'
import { Search } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { useAppStore } from '@/stores/app'

const appStore = useAppStore()
const route = useRoute()

const isDark = computed(() => appStore.theme === 'dark')
const pageTitle = computed(() => route.meta.title || 'QuantLab')
const searchQuery = ref('')

function toggleTheme() {
  appStore.toggleTheme()
}

// 搜索：输入后回车提示开发中
function handleSearch() {
  if (searchQuery.value.trim()) {
    ElMessage.info('搜索功能开发中')
  }
}
</script>

<style scoped lang="scss">
.topbar {
  height: 56px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 24px;
  background: var(--bg-primary);
  border-bottom: 1px solid var(--border);
  flex-shrink: 0;
}

.topbar-left {
  display: flex;
  align-items: center;
}

.page-title {
  margin: 0;
  font-size: 15px;
  font-weight: var(--font-weight-semibold);
  color: var(--text-primary);
}

.topbar-right {
  display: flex;
  align-items: center;
  gap: 16px;
}

// 搜索框
.search-input {
  width: 320px;

  :deep(.el-input__wrapper) {
    height: 36px;
    background-color: var(--bg-secondary) !important;
    border-radius: 8px !important;
    box-shadow: none !important;
    padding: 0 12px;
  }

  :deep(.el-input__inner) {
    font-size: 13px;

    &::placeholder {
      color: var(--text-placeholder);
    }
  }

  :deep(.el-input__prefix) {
    color: var(--text-tertiary);
  }
}

// 状态徽章
.status-badge {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 10px;
  border-radius: var(--radius-full);
  background: var(--bg-hover);
  font-size: 12px;
  color: var(--text-secondary);
  white-space: nowrap;
}

.status-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--success);
  flex-shrink: 0;
}

// 主题切换按钮
.theme-btn {
  width: 36px;
  height: 36px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 8px;
  background: transparent;
  border: 1px solid transparent;
  color: var(--text-secondary);
  cursor: pointer;
  transition: all 150ms var(--ease-in-out);

  &:hover {
    background: var(--bg-hover);
    color: var(--primary);
  }
}

// 用户头像
.user-avatar {
  width: 36px;
  height: 36px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 50%;
  background: rgba(var(--primary-rgb), 0.1);
  color: var(--primary);
  font-size: 14px;
  font-weight: var(--font-weight-semibold);
  cursor: pointer;
  user-select: none;
}
</style>
