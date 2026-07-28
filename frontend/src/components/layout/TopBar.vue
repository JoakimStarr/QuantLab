<template>
  <header class="topbar">
    <!-- Left Section -->
    <div class="topbar-left">
      <button class="collapse-btn" @click="appStore.toggleSidebar" title="切换侧边栏">
        <el-icon :size="18">
          <Fold v-if="!appStore.sidebarCollapsed" />
          <Expand v-else />
        </el-icon>
      </button>

      <el-breadcrumb separator="/" class="breadcrumb">
        <el-breadcrumb-item to="/">
          <span class="breadcrumb-home">首页</span>
        </el-breadcrumb-item>
        <el-breadcrumb-item v-if="$route.meta.title">
          <span class="breadcrumb-current">{{ $route.meta.title }}</span>
        </el-breadcrumb-item>
      </el-breadcrumb>
    </div>

    <!-- Right Section -->
    <div class="topbar-right">
      <!-- Desktop Search -->
      <div class="search-container desktop-only">
        <el-input
          v-model="searchQuery"
          placeholder="搜索因子、策略..."
          prefix-icon="Search"
          size="small"
          clearable
          class="search-input"
          @keyup.enter="handleSearch"
        />
      </div>

      <!-- Actions -->
      <div class="topbar-actions">
        <!-- Mobile Search Button -->
        <button class="action-btn mobile-only" title="搜索" @click="showMobileSearch = true">
          <el-icon :size="18"><Search /></el-icon>
        </button>

        <!-- Notifications -->
        <el-badge :value="notificationCount" :hidden="notificationCount === 0" class="notification-badge">
          <button class="action-btn" title="通知">
            <el-icon :size="18"><Bell /></el-icon>
          </button>
        </el-badge>

        <!-- Help -->
        <button class="action-btn" title="帮助文档">
          <el-icon :size="18"><QuestionFilled /></el-icon>
        </button>

        <!-- Settings -->
        <button class="action-btn" title="系统设置">
          <el-icon :size="18"><Setting /></el-icon>
        </button>

        <!-- User -->
        <el-dropdown class="user-dropdown" trigger="click" @command="handleUserCommand">
          <button class="user-btn">
            <el-avatar :size="32" class="user-avatar">
              <el-icon :size="18"><User /></el-icon>
            </el-avatar>
            <span class="user-name">研究员</span>
            <el-icon class="dropdown-arrow"><ArrowDown /></el-icon>
          </button>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item command="profile">
                <el-icon><User /></el-icon>
                <span>个人中心</span>
              </el-dropdown-item>
              <el-dropdown-item command="settings">
                <el-icon><Setting /></el-icon>
                <span>系统设置</span>
              </el-dropdown-item>
              <el-dropdown-item divided command="logout">
                <el-icon><SwitchButton /></el-icon>
                <span>退出登录</span>
              </el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
      </div>
    </div>

    <!-- Mobile Search Dialog -->
    <el-dialog
      v-model="showMobileSearch"
      title="搜索"
      width="90%"
      class="mobile-search-dialog"
      :show-close="false"
    >
      <el-input
        v-model="searchQuery"
        placeholder="搜索因子、策略..."
        prefix-icon="Search"
        size="large"
        clearable
        autofocus
        @keyup.enter="handleMobileSearch"
      >
        <template #append>
          <el-button :icon="Search" @click="handleMobileSearch" />
        </template>
      </el-input>
    </el-dialog>
  </header>
</template>

<script setup>
import { ref } from 'vue'
import { useAppStore } from '@/stores/app'
import { ElMessage } from 'element-plus'

const appStore = useAppStore()

const searchQuery = ref('')
const notificationCount = ref(0)
const showMobileSearch = ref(false)

function handleSearch() {
  if (searchQuery.value.trim()) {
    ElMessage.info('全局搜索功能开发中')
  }
}

function handleMobileSearch() {
  handleSearch()
  showMobileSearch.value = false
}

function handleUserCommand(command) {
  switch (command) {
    case 'profile':
      ElMessage.info('个人中心')
      break
    case 'settings':
      ElMessage.info('系统设置')
      break
    case 'logout':
      ElMessage.success('已退出')
      break
  }
}
</script>

<style scoped lang="scss">
.topbar {
  height: var(--topbar-height);
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 var(--space-lg);
  background: var(--bg-primary);
  border-bottom: 1px solid var(--border);
  flex-shrink: 0;
  transition: background-color var(--duration-normal) var(--ease-in-out),
              border-color var(--duration-normal) var(--ease-in-out);
}

// Left Section
.topbar-left {
  display: flex;
  align-items: center;
  gap: var(--space-md);
}

.collapse-btn {
  width: 36px;
  height: 36px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: var(--radius-md);
  background: transparent;
  border: 1px solid transparent;
  color: var(--text-secondary);
  cursor: pointer;
  transition: all var(--duration-fast) var(--ease-in-out);

  &:hover {
    background: var(--bg-hover);
    color: var(--primary);
    border-color: var(--border);
  }

  &:active {
    transform: scale(0.95);
  }
}

.breadcrumb {
  :deep(.el-breadcrumb__inner),
  :deep(.el-breadcrumb__separator) {
    font-size: var(--font-size-sm);
  }
}

.breadcrumb-home {
  color: var(--text-secondary);
  font-size: var(--font-size-sm);
  transition: color var(--duration-fast) var(--ease-in-out);

  &:hover {
    color: var(--primary);
  }
}

.breadcrumb-current {
  color: var(--text-primary);
  font-weight: var(--font-weight-medium);
  font-size: var(--font-size-sm);
}

// Right Section
.topbar-right {
  display: flex;
  align-items: center;
  gap: var(--space-md);
}

// Search
.search-container {
  position: relative;
}

.search-input {
  width: 280px;

  :deep(.el-input__wrapper) {
    background-color: var(--bg-secondary) !important;
    border-radius: var(--radius-full) !important;
    padding: 0 var(--space-md);
  }

  :deep(.el-input__inner) {
    &::placeholder {
      color: var(--text-placeholder);
    }
  }
}

// Actions
.topbar-actions {
  display: flex;
  align-items: center;
  gap: var(--space-sm);
}

.action-btn {
  width: 36px;
  height: 36px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: var(--radius-md);
  background: transparent;
  border: 1px solid transparent;
  color: var(--text-secondary);
  cursor: pointer;
  transition: all var(--duration-fast) var(--ease-in-out);

  &:hover {
    background: var(--bg-hover);
    color: var(--primary);
    border-color: var(--border);
  }

  &:active {
    transform: scale(0.95);
  }
}

.notification-badge {
  :deep(.el-badge__content) {
    border: none;
  }
}

// User
.user-dropdown {
  margin-left: var(--space-sm);
}

.user-btn {
  display: flex;
  align-items: center;
  gap: var(--space-sm);
  padding: var(--space-xs) var(--space-sm);
  border-radius: var(--radius-md);
  background: transparent;
  border: 1px solid transparent;
  color: var(--text-primary);
  cursor: pointer;
  transition: all var(--duration-fast) var(--ease-in-out);

  &:hover {
    background: var(--bg-hover);
    border-color: var(--border);
  }
}

.user-avatar {
  background: var(--primary-gradient);
  color: #fff;
  font-size: var(--font-size-sm);
}

.user-name {
  font-size: var(--font-size-sm);
  font-weight: var(--font-weight-medium);
  color: var(--text-primary);

  @media (max-width: 767px) {
    display: none;
  }
}

.dropdown-arrow {
  color: var(--text-tertiary);
  font-size: 12px;
  transition: transform var(--duration-fast) var(--ease-in-out);

  .user-btn:hover & {
    transform: rotate(180deg);
  }

  @media (max-width: 767px) {
    display: none;
  }
}

// Mobile Search Dialog
.mobile-search-dialog {
  :deep(.el-dialog__header) {
    display: none;
  }

  :deep(.el-dialog__body) {
    padding: var(--space-lg);
  }

  :deep(.el-input__wrapper) {
    border-radius: var(--radius-md);
  }
}

// Dropdown Menu
:deep(.el-dropdown-menu__item) {
  display: flex;
  align-items: center;
  gap: var(--space-sm);
  padding: var(--space-sm) var(--space-md);
  font-size: var(--font-size-sm);

  .el-icon {
    font-size: 16px;
  }
}
</style>
