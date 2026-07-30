<template>
  <div class="not-found">
    <div class="nf-code">404</div>
    <p class="nf-text">页面不存在</p>
    <el-button type="primary" @click="$router.replace('/')">返回首页</el-button>
  </div>
</template>

<script setup>
defineOptions({ name: 'NotFound' })
</script>

<style scoped lang="scss">
.not-found {
  min-height: 60vh;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 16px;
}
.nf-code {
  font-size: 72px;
  font-weight: 700;
  color: var(--primary);
  line-height: 1;
}
.nf-text {
  font-size: 16px;
  color: var(--text-secondary);
  margin: 0 0 8px;
}
</style>
