<template>
  <div class="skeleton-loader">
    <el-skeleton :rows="rows" :animated="animated" />
  </div>
</template>

<script setup>
defineProps({
  rows: { type: Number, default: 5 },
  animated: { type: Boolean, default: true },
})
</script>

<style scoped>
.skeleton-loader {
  padding: 20px;
}
</style>