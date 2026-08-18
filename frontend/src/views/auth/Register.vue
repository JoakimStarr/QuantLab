<template>
  <div class="login-page">
    <div class="login-card">
      <div class="login-header">
        <div class="logo">Q</div>
        <h1>注册 QuantLab</h1>
        <p>创建账号，开始量化研究</p>
      </div>
      <el-form @submit.prevent="onRegister" class="login-form">
        <el-form-item>
          <el-input
            v-model="email"
            type="email"
            placeholder="请输入邮箱"
            size="large"
            autocomplete="username"
          >
            <template #prefix><el-icon><User /></el-icon></template>
          </el-input>
        </el-form-item>
        <el-form-item>
          <el-input
            v-model="password"
            type="password"
            placeholder="密码（至少 8 位，需一定强度）"
            size="large"
            show-password
            autocomplete="new-password"
          >
            <template #prefix><el-icon><Lock /></el-icon></template>
          </el-input>
        </el-form-item>
        <el-form-item>
          <el-input
            v-model="confirm"
            type="password"
            placeholder="确认密码"
            size="large"
            show-password
            autocomplete="new-password"
            @keyup.enter="onRegister"
          >
            <template #prefix><el-icon><Lock /></el-icon></template>
          </el-input>
        </el-form-item>
        <el-button type="primary" size="large" class="login-btn" :loading="loading" @click="onRegister">
          注 册
        </el-button>
        <div class="login-footer">
          已有账号？<el-link type="primary" :underline="false" @click="router.push('/login')">去登录</el-link>
        </div>
      </el-form>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus/es/components/message/index'
import { Lock, User } from '@element-plus/icons-vue'
import { useAuthStore } from '@/stores/auth'

const router = useRouter()
const authStore = useAuthStore()

const email = ref('')
const password = ref('')
const confirm = ref('')
const loading = ref(false)

async function onRegister() {
  if (!email.value || !password.value) {
    ElMessage.warning('请输入邮箱和密码')
    return
  }
  if (password.value.length < 8) {
    ElMessage.warning('密码至少 8 位')
    return
  }
  if (password.value !== confirm.value) {
    ElMessage.warning('两次输入的密码不一致')
    return
  }
  loading.value = true
  try {
    await authStore.register(email.value, password.value)
    ElMessage.success('注册成功，已自动登录')
    router.replace('/')
  } catch {
    // 拦截器已提示（密码强度不足 / 邮箱已注册等）
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
    color: var(--text-inverse);
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

.login-footer {
  margin-top: 16px;
  text-align: center;
  font-size: var(--font-size-sm);
  color: var(--text-secondary);
}
</style>
