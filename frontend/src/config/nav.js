// 导航菜单单一数据源：桌面侧栏（Sidebar）与移动底栏（MobileTabBar）共用。
// path/icon 只在这里维护，避免多份手写列表漂移；title 与 shortLabel 各自消费。
// navGroups 为分组结构（侧栏按组渲染小标题）；navItems 为其平铺派生，供旧消费方使用。

export const navGroups = [
  {
    title: '研究',
    items: [
      { path: '/', title: '研究首页', icon: 'DataAnalysis' },
      { path: '/quant/daily-report', title: '每日晨报', icon: 'Sunrise' },
      { path: '/quant/factor-library', title: '因子库', icon: 'Coin' },
      { path: '/quant/strategy', title: '策略回测', icon: 'TrendCharts' },
      { path: '/quant/strategy-library', title: '策略库', icon: 'Collection' },
      { path: '/quant/mining', title: 'AI因子挖掘', icon: 'MagicStick' },
    ],
  },
  {
    title: '数据',
    items: [
      { path: '/quant/data', title: '数据管理', icon: 'SetUp' },
      { path: '/quant/macro', title: '宏观指标', icon: 'Odometer' },
      { path: '/quant/policy', title: '政策风向', icon: 'Postcard' },
    ],
  },
  {
    title: '系统',
    items: [
      { path: '/docs', title: '技术文档', icon: 'Reading' },
      { path: '/system/logs', title: '日志管理', icon: 'Document' },
      { path: '/system/settings', title: '系统设置', icon: 'Setting' },
    ],
  },
]

// 平铺导航项（由分组派生，保持既有消费方兼容）
export const navItems = navGroups.flatMap((group) => group.items)

// 移动底栏直达 tab 子集（4 个高频入口，其余项收纳进"更多"抽屉）
const mobileTabPaths = ['/', '/quant/factor-library', '/quant/strategy', '/quant/data']
const mobileLabels = {
  '/': '首页',
  '/quant/factor-library': '因子',
  '/quant/strategy': '回测',
  '/quant/data': '数据',
}

export const mobileTabs = mobileTabPaths.map((path) => {
  const item = navItems.find((it) => it.path === path)
  return { path, icon: item.icon, label: mobileLabels[path] }
})

// "更多"抽屉内的其余导航项（复用 navItems 的 title/icon）
export const mobileMoreItems = navItems
  .filter((it) => !mobileTabPaths.includes(it.path))
  .map((it) => ({ path: it.path, title: it.title, icon: it.icon }))
