<template>
  <div v-if="hasError" class="error-boundary">
    <el-result icon="error" title="页面出错了" :sub-title="errorMessage">
      <template #extra>
        <el-button type="primary" @click="reload">重新加载</el-button>
        <el-button @click="goBack">返回上一页</el-button>
      </template>
    </el-result>
  </div>
  <slot v-else />
</template>

<script setup>
import { ref, onErrorCaptured } from 'vue'
import { useRouter } from 'vue-router'

const router = useRouter()
const hasError = ref(false)
const errorMessage = ref('')

onErrorCaptured((err) => {
  hasError.value = true
  errorMessage.value = err.message || '未知错误'
  console.error('ErrorBoundary caught:', err)
  return false
})

function reload() {
  hasError.value = false
  errorMessage.value = ''
  window.location.reload()
}

function goBack() {
  hasError.value = false
  errorMessage.value = ''
  router.back()
}
</script>

<style scoped>
.error-boundary {
  display: flex;
  justify-content: center;
  align-items: center;
  min-height: 400px;
}
</style>