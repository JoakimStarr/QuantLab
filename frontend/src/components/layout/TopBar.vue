<template>
  <header class="topbar">
    <!-- 左侧：折叠按钮 + 当前页面标题 -->
    <div class="topbar-left">
      <button
        class="sidebar-toggle desktop-only"
        :title="sidebarCollapsed ? '展开侧边栏' : '收起侧边栏'"
        @click="toggleSidebar"
      >
        <el-icon :size="18">
          <Expand v-if="sidebarCollapsed" />
          <Fold v-else />
        </el-icon>
      </button>
      <h1 class="page-title">{{ pageTitle }}</h1>
    </div>

    <!-- 右侧：状态 + 主题 + 用户 -->
    <div class="topbar-right">
      <!-- 状态徽章：动态反映 qlib 数据可用性 -->
      <span class="status-badge" :class="{ 'is-error': !dataReady }">
        <span class="status-dot"></span>
        {{ dataReady ? '数据已就绪' : '数据未就绪' }}
      </span>

      <!-- 主题切换 -->
      <button class="theme-btn" :title="isDark ? '切换到亮色模式' : '切换到暗色模式'" @click="toggleTheme">
        <el-icon :size="18">
          <Sunny v-if="isDark" />
          <Moon v-else />
        </el-icon>
      </button>

      <!-- 用户头像：开启鉴权时下拉展示账号与登出；否则纯占位 -->
      <el-dropdown v-if="authStore.authEnabled" trigger="click" @command="onUserCommand">
        <div class="user-avatar" :title="displayName">{{ displayName.charAt(0).toUpperCase() }}</div>
        <template #dropdown>
          <el-dropdown-menu>
            <div class="user-identity">
              <span class="user-identity__name">{{ displayName }}</span>
              <span v-if="authStore.user?.role" class="user-identity__role">{{ authStore.user.role }}</span>
            </div>
            <el-dropdown-item divided command="logout">退出登录</el-dropdown-item>
          </el-dropdown-menu>
        </template>
      </el-dropdown>
      <div v-else class="user-avatar" title="研究员">Q</div>
    </div>
  </header>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus/es/components/message/index'
import { Sunny, Moon, Fold, Expand } from '@element-plus/icons-vue'
import { useAppStore } from '@/stores/app'
import { useAuthStore } from '@/stores/auth'
import { getQlibStatus } from '@/api/quant'

const appStore = useAppStore()
const authStore = useAuthStore()
const route = useRoute()
const router = useRouter()

const isDark = computed(() => appStore.theme === 'dark')
const sidebarCollapsed = computed(() => appStore.sidebarCollapsed)
const pageTitle = computed(() => route.meta.title || 'QuantLab')
const dataReady = ref(false)

const displayName = computed(
  () => authStore.user?.username || authStore.user?.name || '研究员'
)

function toggleTheme() {
  appStore.toggleTheme()
}

function toggleSidebar() {
  appStore.toggleSidebar()
}

// 拉取当前用户和 qlib 状态
async function fetchTopbarStatus() {
  if (authStore.authEnabled && authStore.isAuthenticated) {
    await authStore.fetchUser()
  }
  await fetchDataStatus()
}

async function fetchDataStatus() {
  try {
    const data = await getQlibStatus()
    dataReady.value = !!(data?.available ?? data?.qlib_available ?? data?.ok)
  } catch (e) {
    dataReady.value = false
  }
}

// 用户下拉指令
async function onUserCommand(command) {
  if (command === 'logout') {
    authStore.logout()
    ElMessage.success('已退出登录')
    router.push({ name: 'Login', query: { redirect: route.fullPath } })
  }
}

onMounted(fetchTopbarStatus)
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
  gap: 12px;
}

// 侧栏折叠按钮（仅桌面显示）
.sidebar-toggle {
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
  transition:
    background var(--duration-fast) var(--ease-in-out),
    color var(--duration-fast) var(--ease-in-out);

  &:hover {
    background: var(--bg-hover);
    color: var(--primary);
  }
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

  &.is-error .status-dot {
    background: var(--danger);
  }
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

:deep(.el-dropdown) {
  outline: none;
}

// 用户身份信息（下拉菜单头部）
.user-identity {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 6px 16px 8px;
  border-bottom: 1px solid var(--border);

  &__name {
    font-size: var(--font-size-base);
    font-weight: var(--font-weight-semibold);
    color: var(--text-primary);
  }

  &__role {
    font-size: var(--font-size-xs);
    color: var(--text-tertiary);
  }
}
</style>
