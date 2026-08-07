// 净值曲线 ECharts option 工厂：策略净值 + 基准净值两条线。
// 多页面（策略库回测、回测结果详情）共用，消除大量近似重复的 option 脚手架。
// 差异点通过参数注入：yAxis 小数位、tooltip formatter、以及可选的事件散点/花式 series。
import { chartTheme, withAlpha } from './chartTheme'

export function navCurveOption({
  dates = [],
  portfolio = [],
  benchmark = [],
  yDigits = 2,
  tooltipFormatter,
  extraSeries = [],
}) {
  return {
    grid: { top: 20, right: 24, bottom: 30, left: 50 },
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'line', snap: true },
      backgroundColor: chartTheme.bgCard(),
      borderColor: chartTheme.border(),
      textStyle: { color: chartTheme.textPrimary() },
      formatter:
        tooltipFormatter ||
        ((params) => {
          const list = Array.isArray(params) ? params : [params]
          const lines = list.map(
            (p) => `${p.marker} ${p.seriesName}: <b>${Number(p.value).toFixed(yDigits)}</b>`
          )
          return `${list[0].axisValue}<br/>${lines.join('<br/>')}`
        }),
    },
    xAxis: {
      type: 'category',
      data: dates,
      boundaryGap: false,
      axisLine: { lineStyle: { color: chartTheme.border() } },
      axisTick: { show: false },
      axisLabel: { color: chartTheme.axisText(), fontSize: 11, hideOverlap: true },
    },
    yAxis: {
      type: 'value',
      scale: true,
      axisLine: { show: false },
      axisTick: { show: false },
      axisLabel: { color: chartTheme.axisText(), fontSize: 11, formatter: (v) => Number(v).toFixed(yDigits) },
      splitLine: { lineStyle: { color: chartTheme.border(), type: 'dashed' } },
    },
    series: [
      {
        name: '策略净值',
        data: portfolio,
        type: 'line',
        smooth: true,
        showSymbol: false,
        connectNulls: true,
        emphasis: { disabled: true },
        lineStyle: { color: chartTheme.primary(), width: 2 },
        areaStyle: { color: withAlpha(chartTheme.primary(), 0.08) },
        itemStyle: { color: chartTheme.primary() },
      },
      {
        name: '基准净值',
        data: benchmark,
        type: 'line',
        smooth: true,
        showSymbol: false,
        connectNulls: true,
        emphasis: { disabled: true },
        lineStyle: { color: chartTheme.axisText(), width: 1.5, type: 'dashed' },
        itemStyle: { color: chartTheme.axisText() },
      },
      ...extraSeries,
    ],
  }
}