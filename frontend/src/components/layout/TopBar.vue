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

      <!-- 全局同步入口：打开数据同步中心（运行中旋转 + 进度徽标） -->
      <button
        class="sync-btn"
        :class="{ 'is-running': syncStore.running }"
        :title="syncStore.running ? `同步中 ${syncStore.progressPct}%` : '数据同步中心'"
        @click="syncStore.open()"
      >
        <el-icon :size="18"><Refresh /></el-icon>
        <span v-if="syncStore.running" class="sync-btn__pct">{{ syncStore.progressPct }}%</span>
      </button>

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
              <span v-if="displayRole" class="user-identity__role">{{ displayRole }}</span>
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
import { Sunny, Moon, Fold, Expand, Refresh } from '@element-plus/icons-vue'
import { useAppStore } from '@/stores/app'
import { useAuthStore } from '@/stores/auth'
import { getQlibStatus } from '@/api/quant'
import { useSyncStore } from '@/stores/sync'

const appStore = useAppStore()
const authStore = useAuthStore()
const syncStore = useSyncStore()
const route = useRoute()
const router = useRouter()

const isDark = computed(() => appStore.theme === 'dark')
const sidebarCollapsed = computed(() => appStore.sidebarCollapsed)
const pageTitle = computed(() => route.meta.title || 'QuantLab')
const dataReady = ref(false)

const displayName = computed(
  () =>
    authStore.user?.email?.split('@')[0] ||
    authStore.user?.username ||
    authStore.user?.name ||
    '研究员'
)
const displayRole = computed(() =>
  authStore.user?.is_superuser ? '管理员' : authStore.user?.role || '研究员'
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
    await authStore.logout()
    ElMessage.success('已退出登录')
    router.push({ name: 'Login' })
  }
}

onMounted(() => {
  fetchTopbarStatus()
  // 全局同步中心常驻初始化：进度轮询 + 各域状态（单 timer，全站共享）
  syncStore.init()
})
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

// 全局同步按钮
.sync-btn {
  position: relative;
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

  &:focus-visible {
    outline: 2px solid var(--primary);
    outline-offset: -2px;
  }

  // 同步中：图标旋转 + 主色
  &.is-running {
    color: var(--primary);

    .el-icon {
      animation: sync-spin 1.2s linear infinite;
    }
  }
}

.sync-btn__pct {
  position: absolute;
  top: -4px;
  right: -8px;
  padding: 1px 5px;
  border-radius: var(--radius-full);
  background: var(--primary);
  color: var(--text-inverse);
  font-size: 10px;
  line-height: 1.4;
  font-family: var(--font-mono);
  pointer-events: none;
}

@keyframes sync-spin {
  from {
    transform: rotate(0deg);
  }
  to {
    transform: rotate(360deg);
  }
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
