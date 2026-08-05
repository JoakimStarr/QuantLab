import { createApp } from 'vue'
import { createPinia } from 'pinia'
import 'element-plus/es/components/message/style/css'
import 'element-plus/es/components/loading/style/css'
import App from './App.vue'
import router from './router'
import { useAuthStore } from './stores/auth'
import { initAppConfig } from '@/config/app'
import { ElMessage } from 'element-plus/es/components/message/index'
// Element Plus 暗色主题变量（html.dark 下生效），须先于 global.scss 引入，
// 以便下方自定义 :root.dark 覆盖同优先级变量（品牌色/背景对齐自研 token）
import 'element-plus/theme-chalk/dark/css-vars.css'
import './styles/global.scss'

const app = createApp(App)
const pinia = createPinia()
app.use(pinia)
app.use(router)

// 全局错误边界：未捕获异常弹 toast 而非白屏
app.config.errorHandler = (err, instance, info) => {
  console.error('[Vue Error]', err, info)
  try {
    ElMessage.error('页面发生异常：' + (err?.message || String(err)))
  } catch (e) {
    console.error('[ErrorHandler] toast 失败:', e)
  }
}

// 立即挂载，避免白屏期间等待后端请求；
// 鉴权状态与应用配置改为挂载后后台并行加载（各自内部有兜底，不阻塞首屏）。
// 路由守卫会在首次导航时 await fetchStatus 完成鉴权判定。
const authStore = useAuthStore()
app.mount('#app')
authStore.fetchStatus()
initAppConfig()
