<template>
  <div class="empty-state">
    <div class="empty-state__icon">
      <el-icon :size="48" color="#c0c4cc">
        <component :is="icon" />
      </el-icon>
    </div>
    <p class="empty-state__text">{{ text }}</p>
    <slot />
  </div>
</template>

<script setup>
defineProps({
  icon: { type: String, default: 'InfoFilled' },
  text: { type: String, default: '暂无数据' }
})
</script>

<style scoped lang="scss">
.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: var(--space-2xl) var(--space-lg);
  animation: fadeIn 0.3s var(--ease-in-out);
}

.empty-state__text {
  font-size: var(--font-size-base);
  color: var(--text-tertiary);
  margin-top: var(--space-md);
}
</style>