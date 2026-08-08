// 通过字符串名称动态渲染的 Element Plus 图标映射。
// Vue 3 <script setup> 中动态 :is="'IconName'" 无法解析局部导入组件，
// 需要在此把用到的图标显式映射为组件对象，从而避免在 main.js 全量注册
// 约 290 个图标（约 300KB，会拖慢首屏）。新增导航/动态图标时在此登记。
import {
  DataAnalysis,
  Coin,
  TrendCharts,
  Collection,
  MagicStick,
  SetUp,
  Odometer,
  Reading,
  Document,
  Postcard,
} from '@element-plus/icons-vue'

const iconMap = {
  DataAnalysis,
  Coin,
  TrendCharts,
  Collection,
  MagicStick,
  SetUp,
  Odometer,
  Reading,
  Document,
  Postcard,
}

export function resolveIcon(name) {
  return iconMap[name]
}

export default iconMap
