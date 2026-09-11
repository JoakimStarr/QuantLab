import { createApp } from 'vue'
import { createPinia } from 'pinia'
import 'element-plus/es/components/message/style/css'
import 'element-plus/es/components/loading/style/css'
import App from './App.vue'
import router from './router'
import vStagger from './directives/vStagger'
import { useAuthStore } from './stores/auth'
import { initAppConfig } from '@/config/app'
import { ElMessage } from 'element-plus/es/components/message/index'
import { reportFrontendError } from './api/logs'
// Element Plus 暗色主题变量（html.dark 下生效），须先于 global.scss 引入，
// 以便下方自定义 :root.dark 覆盖同优先级变量（品牌色/背景对齐自研 token）
import 'element-plus/theme-chalk/dark/css-vars.css'
import './styles/global.scss'

// ---------------- 前端错误上报（节流：同 route 每 10s 最多 3 条，防风暴） ----------------
const REPORT_WINDOW_MS = 10_000
const REPORT_MAX_PER_WINDOW = 3
// route -> [timestamp, ...]（本窗口内的上报时间戳）
const reportTimestamps = new Map()

function currentRoute() {
  try {
    return router.currentRoute.value?.fullPath || window.location.pathname
  } catch (e) {
    return window.location.pathname
  }
}

function reportError(err, extra = {}) {
  const route = currentRoute()
  const now = Date.now()
  const stamps = (reportTimestamps.get(route) || []).filter((t) => now - t < REPORT_WINDOW_MS)
  if (stamps.length >= REPORT_MAX_PER_WINDOW) return
  stamps.push(now)
  reportTimestamps.set(route, stamps)

  const message = err?.message || String(err || '未知错误')
  const stack = err?.stack || ''
  reportFrontendError({ message, stack, route, level: 'error' }).catch(() => {
    // 上报失败静默：日志通道不可用时不能引发新的错误风暴
  })
}

const app = createApp(App)
const pinia = createPinia()
app.use(pinia)
app.use(router)
// 列表入场 stagger 全局指令（v-stagger）
app.directive('stagger', vStagger)

// 全局错误边界：未捕获异常弹 toast 而非白屏，同时上报后端落 error.log
app.config.errorHandler = (err, instance, info) => {
  console.error('[Vue Error]', err, info)
  reportError(err, { info })
  try {
    ElMessage.error('页面发生异常：' + (err?.message || String(err)))
  } catch (e) {
    console.error('[ErrorHandler] toast 失败:', e)
  }
}

// 未捕获的 Promise 拒绝同样上报（节流规则同上）
if (typeof window !== 'undefined') {
  window.addEventListener('unhandledrejection', (event) => {
    const reason = event?.reason
    console.debug('[Unhandled Rejection]', reason)
    reportError(reason instanceof Error ? reason : new Error(String(reason)))
  })
}

// 立即挂载，避免白屏期间等待后端请求；
// 鉴权状态与应用配置改为挂载后后台并行加载（各自内部有兜底，不阻塞首屏）。
// 路由守卫会在首次导航时 await fetchStatus 完成鉴权判定。
const authStore = useAuthStore()
app.mount('#app')
authStore.fetchStatus()
initAppConfig()
