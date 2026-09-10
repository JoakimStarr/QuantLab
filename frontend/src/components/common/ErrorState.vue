<template>
  <div class="error-state">
    <el-alert type="error" :closable="false" show-icon>
      <template #title>{{ title }}</template>
      <div v-if="description" class="error-state__desc">{{ description }}</div>
      <div v-if="retryable || $slots.action" class="error-state__actions">
        <slot name="action">
          <el-button size="small" type="primary" plain @click="emit('retry')">重试</el-button>
        </slot>
      </div>
    </el-alert>
  </div>
</template>

<script setup>
defineProps({
  title: { type: String, default: '加载失败' },
  description: { type: String, default: '' },
  retryable: { type: Boolean, default: true },
})

const emit = defineEmits(['retry'])
</script>

<style scoped lang="scss">
.error-state__desc {
  font-size: var(--font-size-sm);
  color: var(--text-secondary);
}

.error-state__actions {
  margin-top: var(--space-sm);
}
</style>
