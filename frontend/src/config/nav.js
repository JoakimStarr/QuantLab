// 导航菜单单一数据源：桌面侧栏（Sidebar）与移动底栏（MobileTabBar）共用。
// path/icon 只在这里维护，避免多份手写列表漂移；title 与 shortLabel 各自消费。
export const navItems = [
  { path: '/', title: '研究首页', icon: 'DataAnalysis' },
  { path: '/quant/factors', title: '因子库', icon: 'Coin' },
  { path: '/quant/strategy', title: '策略回测', icon: 'TrendCharts' },
  { path: '/quant/strategy-library', title: '策略库', icon: 'Collection' },
  { path: '/quant/mining', title: 'AI因子挖掘', icon: 'MagicStick' },
  { path: '/quant/data', title: '数据管理', icon: 'SetUp' },
  { path: '/quant/macro', title: '宏观指标', icon: 'Odometer' },
  { path: '/docs', title: '技术文档', icon: 'Reading' },
  { path: '/system/logs', title: '日志管理', icon: 'Document' },
]

// 移动底栏展示的导航子集（path 与 icon 复用 navItems，仅覆盖简短标签）
const mobileTabPaths = ['/', '/quant/factors', '/quant/strategy', '/quant/mining', '/quant/data', '/system/logs']
const mobileLabels = {
  '/': '首页',
  '/quant/factors': '因子',
  '/quant/strategy': '回测',
  '/quant/mining': '挖掘',
  '/quant/data': '数据',
  '/system/logs': '日志',
}

export const mobileTabs = mobileTabPaths.map((path) => {
  const item = navItems.find((it) => it.path === path)
  return { path, icon: item.icon, label: mobileLabels[path] }
})