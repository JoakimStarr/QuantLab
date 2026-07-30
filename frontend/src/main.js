import { createApp } from 'vue'
import { createPinia } from 'pinia'
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'
import * as ElementPlusIconsVue from '@element-plus/icons-vue'
import App from './App.vue'
import router from './router'
import { useAuthStore } from './stores/auth'
import { ElMessage } from 'element-plus'
import './styles/global.scss'

// ECharts configuration
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { LineChart, BarChart, PieChart, CandlestickChart } from 'echarts/charts'
import { GridComponent, TooltipComponent, LegendComponent, TitleComponent, DataZoomComponent, AxisPointerComponent } from 'echarts/components'

// Register ECharts components globally
use([
  CanvasRenderer,
  LineChart,
  BarChart,
  PieChart,
  CandlestickChart,
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
app.use(ElementPlus, {
  size: 'default'
})

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

// 挂载前探测鉴权状态，确保路由守卫拿到正确结果
const authStore = useAuthStore()
authStore.fetchStatus().finally(() => {
  app.mount('#app')
})
