<template>
  <div v-if="visible" class="learn-tip" role="note">
    <div class="learn-tip__icon">
      <el-icon :size="16"><Reading /></el-icon>
    </div>
    <div class="learn-tip__body">
      <span class="learn-tip__title">{{ title }}</span>
      <span class="learn-tip__desc">{{ desc }}</span>
      <router-link v-if="docSlug" :to="`/docs/${docSlug}`" class="learn-tip__link" @click="dismiss">
        了解更多 →
      </router-link>
    </div>
    <button class="learn-tip__close" aria-label="关闭提示" @click="dismiss">
      <el-icon :size="14"><Close /></el-icon>
    </button>
  </div>
</template>

<script setup>
// 学习研究平台教学提示：轻量、可关闭、localStorage 记忆（每处提示独立 key）。
// 全站统一视觉：浅主色底 + Reading 图标 + 可选文档深链。
import { ref } from 'vue'
import { Reading, Close } from '@element-plus/icons-vue'

const props = defineProps({
  // localStorage 记忆键（必填，建议按页面+主题命名，如 'learn_tip_mining_loop'）
  storageKey: { type: String, required: true },
  title: { type: String, required: true },
  desc: { type: String, required: true },
  // 技术文档 slug（可选）：显示"了解更多"深链
  docSlug: { type: String, default: '' },
})

const visible = ref(localStorage.getItem(props.storageKey) !== '1')

function dismiss() {
  visible.value = false
  localStorage.setItem(props.storageKey, '1')
}
</script>

<style scoped lang="scss">
.learn-tip {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 10px 14px;
  margin-bottom: 16px;
  border-radius: var(--radius-md);
  background: rgba(var(--primary-rgb), 0.05);
  border: 1px solid rgba(var(--primary-rgb), 0.15);
}

.learn-tip__icon {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border-radius: 8px;
  background: rgba(var(--primary-rgb), 0.1);
  color: var(--primary);
  flex-shrink: 0;
}

.learn-tip__body {
  flex: 1;
  min-width: 0;
  font-size: var(--font-size-sm);
  color: var(--text-secondary);
  line-height: 1.6;
}

.learn-tip__title {
  font-weight: var(--font-weight-medium);
  color: var(--text-primary);
  margin-right: 8px;
}

.learn-tip__link {
  margin-left: 8px;
  color: var(--primary);
  text-decoration: none;
  white-space: nowrap;

  &:hover {
    text-decoration: underline;
  }
}

.learn-tip__close {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  border: none;
  border-radius: 6px;
  background: transparent;
  color: var(--text-tertiary);
  cursor: pointer;
  flex-shrink: 0;
  transition: all 150ms var(--ease-in-out);

  &:hover {
    background: var(--bg-hover);
    color: var(--text-primary);
  }

  &:focus-visible {
    outline: 2px solid var(--primary);
    outline-offset: -2px;
  }
}
</style>
