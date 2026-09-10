// ECharts 按需注册模块：各图表组件顶部 `import '@/utils/echarts'`（副作用导入）即可。
// 从 main.js 移出后，vendor-echarts 仅在首个图表页面加载时才被拉取，
// 登录页/非图表页不再下载约 200KB（gzip）的 ECharts。
// Candlestick/Radar/Heatmap（及 VisualMap）在 utils/echarts-extras.js 按需注册，
// 仅 K 线 / 雷达 / 热力图页面需要额外 `import '@/utils/echarts-extras'`。
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { LineChart, BarChart, PieChart } from 'echarts/charts'
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
} from 'echarts/components'

use([
  CanvasRenderer,
  LineChart,
  BarChart,
  PieChart,
  GridComponent,
  TooltipComponent,
  LegendComponent,
  TitleComponent,
  DataZoomComponent,
  AxisPointerComponent,
  MarkLineComponent,
  MarkAreaComponent,
  MarkPointComponent,
])
