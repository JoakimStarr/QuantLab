<template>
  <div class="empty-state">
    <el-empty :description="description" :image-size="imageSize">
      <template v-if="$slots.action || $slots.default" #default>
        <div class="empty-state__action">
          <slot name="action"><slot /></slot>
        </div>
      </template>
    </el-empty>
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  description: { type: String, default: '暂无数据' },
  // sm=64 / md=96 / lg=120（对应各页面的 image-size 惯例）
  size: {
    type: String,
    default: 'md',
    validator: (v) => ['sm', 'md', 'lg'].includes(v),
  },
})

const SIZE_MAP = { sm: 64, md: 96, lg: 120 }
const imageSize = computed(() => SIZE_MAP[props.size])
</script>

<style scoped lang="scss">
.empty-state {
  display: flex;
  justify-content: center;
  padding: var(--space-sm) 0;
}

.empty-state__action {
  margin-top: var(--space-xs);
}
</style>
