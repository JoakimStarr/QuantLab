import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { getAuthStatus, login as loginApi, getMe } from '@/api/auth'

const TOKEN_KEY = 'auth_token'

export const useAuthStore = defineStore('auth', () => {
  const token = ref(localStorage.getItem(TOKEN_KEY) || '')
  const authEnabled = ref(false)
  const user = ref(null)
  const statusLoaded = ref(false)

  const isAuthenticated = computed(() => !!token.value)

  /** 探测后端鉴权是否开启 */
  async function fetchStatus() {
    try {
      const data = await getAuthStatus()
      authEnabled.value = data?.auth_enabled ?? false
    } catch (e) {
      // 探测失败时保守地认为未开启，避免本地开发被锁死
      authEnabled.value = false
    } finally {
      statusLoaded.value = true
    }
  }

  /** 登录 */
  async function login(password) {
    const data = await loginApi(password)
    token.value = data.token
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

  /** 登出 */
  function logout() {
    token.value = ''
    user.value = null
    localStorage.removeItem(TOKEN_KEY)
  }

  /** 鉴权是否需要拦截（未开启或已登录均放行） */
  const needAuth = computed(() => authEnabled.value && !token.value)

  return {
    token, authEnabled, user, statusLoaded,
    isAuthenticated, needAuth,
    fetchStatus, login, fetchUser, logout,
  }
})
