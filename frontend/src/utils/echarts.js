// ECharts 按需注册模块：各图表组件顶部 `import '@/utils/echarts'`（副作用导入）即可。
// 从 main.js 移出后，vendor-echarts 仅在首个图表页面加载时才被拉取，
// 登录页/非图表页不再下载约 200KB（gzip）的 ECharts。
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { LineChart, BarChart, PieChart, CandlestickChart, RadarChart, HeatmapChart } from 'echarts/charts'
import {
  GridComponent,
  TooltipComponent,
  LegendComponent,
  TitleComponent,
  DataZoomComponent,
  AxisPointerComponent,
  MarkLineComponent,
  MarkAreaComponent,
  MarkPointComponent,
  VisualMapComponent,
} from 'echarts/components'

use([
  CanvasRenderer,
  LineChart,
  BarChart,
  PieChart,
  CandlestickChart,
  RadarChart,
  HeatmapChart,
  GridComponent,
  TooltipComponent,
  LegendComponent,
  TitleComponent,
  DataZoomComponent,
  AxisPointerComponent,
  MarkLineComponent,
  MarkAreaComponent,
  MarkPointComponent,
  VisualMapComponent,
])
