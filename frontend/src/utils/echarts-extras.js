// ECharts 扩展类型按需注册：仅被 K 线 / 雷达 / 热力图页面引用。
// 不使用这些图表的页面只需 `import '@/utils/echarts'`（核心模块），
// 避免为 Candlestick/Radar/Heatmap 支付注册成本。
// 注意：因 vite.config manualChunks 将所有 echarts 源码归入 vendor-echarts 共享 chunk，
// 下载体积不变，本拆分的收益限于运行时注册与依赖显式化。
import { use } from 'echarts/core'
import { CandlestickChart, RadarChart, HeatmapChart } from 'echarts/charts'
import { VisualMapComponent } from 'echarts/components'

use([CandlestickChart, RadarChart, HeatmapChart, VisualMapComponent])
