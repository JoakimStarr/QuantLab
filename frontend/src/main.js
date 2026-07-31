import { createApp } from 'vue'
import { createPinia } from 'pinia'
import 'element-plus/es/components/message/style/css'
import 'element-plus/es/components/loading/style/css'
import * as ElementPlusIconsVue from '@element-plus/icons-vue'
import App from './App.vue'
import router from './router'
import { useAuthStore } from './stores/auth'
import { initAppConfig } from '@/config/app'
import { ElMessage } from 'element-plus/es/components/message/index'
import './styles/global.scss'

// ECharts configuration
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { LineChart, BarChart, PieChart, CandlestickChart, RadarChart } from 'echarts/charts'
import { GridComponent, TooltipComponent, LegendComponent, TitleComponent, DataZoomComponent, AxisPointerComponent } from 'echarts/components'

// Register ECharts components globally
use([
  CanvasRenderer,
  LineChart,
  BarChart,
  PieChart,
  CandlestickChart,
  RadarChart,
  GridComponent,
  TooltipComponent,
  LegendComponent,
  TitleComponent,
  DataZoomComponent,
  AxisPointerComponent
])

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

for (const [key, component] of Object.entries(ElementPlusIconsVue)) {
  app.component(key, component)
}

// 挂载前并行加载鉴权状态与应用配置；
// 两个 Promise 均自带 finally 兜底，单项失败不阻塞启动
const authStore = useAuthStore()
Promise.allSettled([
  authStore.fetchStatus(),
  initAppConfig(),
]).finally(() => {
  app.mount('#app')
})
