import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { getAuthStatus, login as loginApi, register as registerApi, logout as logoutApi, getMe } from '@/api/auth'

const TOKEN_KEY = 'auth_token'

export const useAuthStore = defineStore('auth', () => {
  const token = ref(localStorage.getItem(TOKEN_KEY) || '')
  const authEnabled = ref(false)
  const user = ref(null)
  const statusLoaded = ref(false)

  const isAuthenticated = computed(() => !!token.value)

  /** 探测后端鉴权是否开启（单例：并发调用共享同一次请求） */
  let statusPromise = null
  function fetchStatus() {
    if (!statusPromise) {
      statusPromise = (async () => {
        try {
          const data = await getAuthStatus()
          authEnabled.value = data?.auth_enabled ?? false
        } catch (e) {
          // 探测失败时保守地认为未开启，避免本地开发被锁死
          authEnabled.value = false
        } finally {
          statusLoaded.value = true
        }
      })()
    }
    return statusPromise
  }

  /** 登录（邮箱 + 密码，成功后落 token 与用户信息） */
  async function login(email, password) {
    const data = await loginApi(email, password)
    token.value = data.token
    user.value = data.user || null
    localStorage.setItem(TOKEN_KEY, data.token)
    return data
  }

  /** 注册即登录（后端注册成功直接签发 token） */
  async function register(email, password) {
    const data = await registerApi(email, password)
    token.value = data.token
    user.value = data.user || null
    localStorage.setItem(TOKEN_KEY, data.token)
    return data
  }

  /** 拉取当前用户 */
  async function fetchUser() {
    if (!token.value) return null
    try {
      user.value = await getMe()
    } catch (e) {
      logout()
    }
    return user.value
  }

  /** 登出：通知后端审计（失败不阻塞），再清理本地状态 */
  async function logout() {
    try {
      await logoutApi()
    } catch {
      /* 后端不可达时仍继续本地登出 */
    }
    token.value = ''
    user.value = null
    localStorage.removeItem(TOKEN_KEY)
  }

  /** 鉴权是否需要拦截（未开启或已登录均放行） */
  const needAuth = computed(() => authEnabled.value && !token.value)

  return {
    token,
    authEnabled,
    user,
    statusLoaded,
    isAuthenticated,
    needAuth,
    fetchStatus,
    login,
    register,
    fetchUser,
    logout,
  }
})
