<template>
  <header class="topbar">
    <!-- 左侧：当前页面标题 -->
    <div class="topbar-left">
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

      <!-- 用户头像 -->
      <div class="user-avatar" title="研究员">Q</div>
    </div>
  </header>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { Sunny, Moon } from '@element-plus/icons-vue'
import { useAppStore } from '@/stores/app'
import { getQlibStatus } from '@/api/quant'

const appStore = useAppStore()
const route = useRoute()

const isDark = computed(() => appStore.theme === 'dark')
const pageTitle = computed(() => route.meta.title || 'QuantLab')
const dataReady = ref(false)

function toggleTheme() {
  appStore.toggleTheme()
}

// 动态读取 qlib 数据可用性，反映真实状态
async function fetchDataStatus() {
  try {
    const data = await getQlibStatus()
    dataReady.value = !!(data?.available ?? data?.qlib_available ?? data?.ok)
  } catch (e) {
    dataReady.value = false
  }
}

onMounted(fetchDataStatus)
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
</style>
