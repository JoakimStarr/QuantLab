// 统一图表配色：从 CSS 变量读取，随亮/暗主题自动切换，
// ECharts 序列只需引用 chartTheme.* 即可，无需感知主题。
function read(name, fallback = '') {
  if (typeof window === 'undefined' || !window.getComputedStyle) return fallback
  return window.getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback
}

export const chartTheme = {
  up: () => read('--chart-up', '#ef232a'),
  down: () => read('--chart-down', '#14b143'),
  volume: () => read('--chart-volume', '#7fbbea'),
  ma5: () => read('--chart-ma5', '#ffaa00'),
  ma10: () => read('--chart-ma10', '#ff55ff'),
  ma20: () => read('--chart-ma20', '#00bfff'),
  ma60: () => read('--chart-ma60', '#a5a9b5'),
  ema12: () => read('--chart-ema12', '#e6a23c'),
  ema26: () => read('--chart-ema26', '#a23ce6'),
  dif: () => read('--chart-dif', '#ffaa00'),
  dea: () => read('--chart-dea', '#ff55ff'),
  k: () => read('--chart-k', '#ffaa00'),
  d: () => read('--chart-d', '#ff55ff'),
  j: () => read('--chart-j', '#00bfff'),
  axisText: () => read('--chart-axis-text', '#5b6b85'),
  neutral: () => read('--chart-neutral', '#909399'),
  line: () => read('--chart-line', '#9db4d4'),
  baseline: () => read('--chart-baseline', '#d4380d'),
  areaAbove: () => read('--chart-area-above', 'rgba(82,196,26,0.08)'),
  areaBelow: () => read('--chart-area-below', 'rgba(245,34,45,0.06)'),
  palette: (n) =>
    read(
      `--chart-p${n}`,
      ['#5470c6', '#fa8c16', '#722ed1', '#13c2c2', '#52c41a', '#9333ea', '#0891b2', '#be185d'][(n - 1) % 8]
    ),
  // 语义色（对齐 CSS 变量）
  primary: () => read('--primary', '#1f4ba0'),
  success: () => read('--success', '#1f9d6b'),
  danger: () => read('--danger', '#d24545'),
  warning: () => read('--warning', '#c8801c'),
  info: () => read('--info', '#2f7dc2'),
  textPrimary: () => read('--text-primary', '#16213a'),
  textSecondary: () => read('--text-secondary', '#5b6b85'),
  textTertiary: () => read('--text-tertiary', '#8493ab'),
  bgCard: () => read('--bg-card', '#ffffff'),
  bgTertiary: () => read('--bg-tertiary', '#eef2f6'),
  border: () => read('--border', '#e3e9f1'),
  successSoft: () => read('--success-soft', 'rgba(31,157,107,0.1)'),
}

// 类别序列固定调色板（ECharts 默认配色，多序列图表如宏观指标使用，
// 作为唯一来源，避免各视图重复硬编码）
export const echartPalette = {
  blue: '#5470c6',
  green: '#91cc75',
  gold: '#fac858',
  red: '#ee6666',
  cyan: '#73c0de',
  forest: '#3ba272',
  orange: '#fc8452',
  violet: '#9a60b4',
  pink: '#ea7ccc',
  orangeAlt: '#fa8c16',
  teal: '#13c2c2',
  purple: '#722ed1',
  grass: '#52c41a',
  grape: '#8e44ad',
}

// 分层分组 Q1（红）→ Qn（绿）渐变，最多支持 10 分组
export const quantileGradient = [
  '#d03b3b',
  '#e8853a',
  '#d4b73a',
  '#7ab83a',
  '#2a9d4a',
  '#1f9d6b',
  '#1f7a4a',
  '#155f3a',
  '#0e4a2e',
  '#08351f',
]

// 一次性读取所有图表颜色（用于非响应式场景，如初始化即固定的系列色）
export function snapshotChartTheme() {
  const t = {}
  for (const [key, fn] of Object.entries(chartTheme)) t[key] = fn()
  return t
}

// 将 hex 颜色转为带透明度的 rgba（供 ECharts itemStyle/areaStyle 使用）
export function withAlpha(hex, alpha) {
  const h = String(hex || '').replace('#', '')
  const full =
    h.length === 3
      ? h
          .split('')
          .map((c) => c + c)
          .join('')
      : h
  const num = parseInt(full, 16)
  if (Number.isNaN(num) || full.length !== 6) return hex
  return `rgba(${(num >> 16) & 255}, ${(num >> 8) & 255}, ${num & 255}, ${alpha})`
}
