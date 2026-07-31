<template>
  <div class="login-page">
    <div class="login-card">
      <div class="login-header">
        <div class="logo">Q</div>
        <h1>QuantLab</h1>
        <p>量化策略回测研究平台</p>
      </div>
      <el-form @submit.prevent="onLogin" class="login-form">
        <el-form-item>
          <el-input
            v-model="password"
            type="password"
            placeholder="请输入访问口令"
            size="large"
            show-password
            @keyup.enter="onLogin"
          >
            <template #prefix><el-icon><Lock /></el-icon></template>
          </el-input>
        </el-form-item>
        <el-button
          type="primary"
          size="large"
          class="login-btn"
          :loading="loading"
          @click="onLogin"
        >
          登 录
        </el-button>
      </el-form>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { ElMessage } from 'element-plus/es/components/message/index'
import { Lock } from '@element-plus/icons-vue'
import { useAuthStore } from '@/stores/auth'

const router = useRouter()
const route = useRoute()
const authStore = useAuthStore()

const password = ref('')
const loading = ref(false)

async function onLogin() {
  if (!password.value) {
    ElMessage.warning('请输入口令')
    return
  }
  loading.value = true
  try {
    await authStore.login(password.value)
    ElMessage.success('登录成功')
    const redirect = route.query.redirect || '/'
    router.replace(redirect)
  } catch (e) {
    // 拦截器已提示，这里兜底
  } finally {
    loading.value = false
  }
}
</script>

<style scoped lang="scss">
.login-page {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--primary-gradient);
  padding: var(--space-lg);
}

.login-card {
  width: 100%;
  max-width: 380px;
  background: var(--bg-card);
  border-radius: 16px;
  padding: 40px 32px;
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.18);
}

.login-header {
  text-align: center;
  margin-bottom: 32px;

  .logo {
    width: 56px;
    height: 56px;
    margin: 0 auto 16px;
    border-radius: 14px;
    background: var(--primary-gradient);
    color: #fff;
    font-size: 28px;
    font-weight: 700;
    line-height: 56px;
  }
  h1 {
    font-size: 22px;
    font-weight: 700;
    color: var(--text-primary);
    margin: 0 0 4px;
  }
  p {
    font-size: var(--font-size-base);
    color: var(--text-secondary);
    margin: 0;
  }
}

.login-btn {
  width: 100%;
  margin-top: 8px;
}
</style>
