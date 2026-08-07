// K 线图表共用基建：技术指标计算 + ECharts option 工厂。
// 供 Dashboard 的 KLineChart 与 回测 K 线面板共用，保证指标口径与图形样式一致。
import { chartTheme } from './chartTheme'

// === 技术指标计算（纯函数，KLineChart 与回测 K 线面板共用） ===

export function calcMA(data, period) {
  const result = []
  for (let i = 0; i < data.length; i++) {
    if (i < period - 1) {
      result.push(null)
      continue
    }
    let sum = 0
    for (let j = 0; j < period; j++) sum += data[i - j].close
    result.push(sum / period)
  }
  return result
}

export function calcEMA(data, period) {
  const result = []
  const k = 2 / (period + 1)
  let ema = null
  for (let i = 0; i < data.length; i++) {
    ema = i === 0 ? data[i].close : data[i].close * k + ema * (1 - k)
    result.push(ema)
  }
  return result
}

export function calcMACD(data, short = 12, long = 26, signal = 9) {
  const emaShort = calcEMA(data, short)
  const emaLong = calcEMA(data, long)
  const dif = data.map((_, i) => emaShort[i] - emaLong[i])
  const dea = []
  const k = 2 / (signal + 1)
  let prev = 0
  for (let i = 0; i < dif.length; i++) {
    prev = i === 0 ? dif[0] : dif[i] * k + prev * (1 - k)
    dea.push(prev)
  }
  const macd = dif.map((d, i) => (d - dea[i]) * 2)
  return { dif, dea, macd }
}

export function calcKDJ(data, n = 9, m1 = 3, m2 = 3) {
  let prevK = 50,
    prevD = 50
  const k = [],
    d = [],
    j = []
  for (let i = 0; i < data.length; i++) {
    let hn = -Infinity,
      ln = Infinity
    for (let p = Math.max(0, i - n + 1); p <= i; p++) {
      if (data[p].high > hn) hn = data[p].high
      if (data[p].low < ln) ln = data[p].low
    }
    const rsv = hn === ln ? 0 : ((data[i].close - ln) / (hn - ln)) * 100
    const curK = ((m1 - 1) / m1) * prevK + (1 / m1) * rsv
    const curD = ((m2 - 1) / m2) * prevD + (1 / m2) * curK
    const curJ = 3 * curK - 2 * curD
    k.push(curK)
    d.push(curD)
    j.push(curJ)
    prevK = curK
    prevD = curD
  }
  return { k, d, j }
}

// 将回测成交记录按日期聚合为 K 线下标散点，用于在价格图上叠加买卖点。
// trades 元素需含 date 与 action（BUY/SELL）。
// 返回 { buy: [[i, price, count]...], sell: [[i, price, count]...] }
export function buildTradeMarks(klineItems, trades) {
  const dates = klineItems.map((k) => String(k.date).slice(0, 10))
  const idx = new Map(dates.map((d, i) => [d, i]))
  const agg = {}
  for (const t of trades || []) {
    const d = String(t.date || '').slice(0, 10)
    const i = idx.get(d)
    if (i == null) continue
    const key = t.action === 'BUY' ? 'buy' : 'sell'
    agg[d] = agg[d] || { i, buy: 0, sell: 0 }
    agg[d][key]++
  }
  const buy = []
  const sell = []
  for (const d in agg) {
    const { i, buy: b, sell: s } = agg[d]
    const price = Number(klineItems[i]?.close)
    if (Number.isNaN(price)) continue
    if (b) buy.push([i, price, b])
    if (s) sell.push([i, price, s])
  }
  return { buy, sell }
}

// ---------- ECharts option 工厂 ----------

// 生成交易日/成交量 + 可选技术指标 + 可选买卖点 + 策略指标线叠加的 K 线 option。
export function klineChartOption({
  items = [],
  activeIndicators = [],
  buyPoints = [],
  sellPoints = [],
  volumeUnit = '万股',
  strategyIndicator = null,
}) {
  const data = items
  if (!data.length) return {}
  const dates = data.map((k) => k.date)
  const ohlc = data.map((k) => [k.open, k.close, k.low, k.high])
  const volumes = data.map((k) => (Number(k.volume) || 0) / (volumeUnit === '亿股' ? 100000000 : 10000))

  const showMA = activeIndicators.includes('MA')
  const showEMA = activeIndicators.includes('EMA')
  const showMACD = activeIndicators.includes('MACD')
  const showKDJ = activeIndicators.includes('KDJ')
  // 策略指标线：价格域（grid=main）叠主图，振荡域（grid=sub）占一个副图
  const strategyMainLines = (strategyIndicator?.lines || []).filter((l) => l.grid === 'main')
  const strategySubLines = (strategyIndicator?.lines || []).filter((l) => l.grid === 'sub')
  const extraSubs = (showMACD ? 1 : 0) + (showKDJ ? 1 : 0) + (strategySubLines.length ? 1 : 0)

  // 存在买卖点时，dataZoom 默认范围覆盖所有买卖点（留 5% 边距），
  // 否则 485 天 K 线默认从 60% 缩放开始，前面的买卖点会落在缩略区外"看不见"
  let zoomStart = data.length <= 80 ? 0 : 60
  const allPoints = [...buyPoints, ...sellPoints]
  if (allPoints.length) {
    const idxs = allPoints.map((p) => p[0])
    const minIdx = Math.min(...idxs)
    const maxIdx = Math.max(...idxs)
    const pad = Math.max(5, Math.ceil((maxIdx - minIdx) * 0.05))
    zoomStart = Math.max(0, Math.round(((minIdx - pad) / data.length) * 100))
  }

  let grids, xAxes, yAxes, zoomIndices
  if (extraSubs === 0) {
    grids = [
      { left: '10%', right: '4%', top: '8%', height: '55%' },
      { left: '10%', right: '4%', top: '70%', height: '18%' },
    ]
  } else if (extraSubs === 1) {
    grids = [
      { left: '10%', right: '4%', top: '6%', height: '46%' },
      { left: '10%', right: '4%', top: '56%', height: '14%' },
      { left: '10%', right: '4%', top: '74%', height: '14%' },
    ]
  } else if (extraSubs === 2) {
    grids = [
      { left: '10%', right: '4%', top: '5%', height: '38%' },
      { left: '10%', right: '4%', top: '47%', height: '12%' },
      { left: '10%', right: '4%', top: '63%', height: '12%' },
      { left: '10%', right: '4%', top: '79%', height: '12%' },
    ]
  } else {
    grids = [
      { left: '10%', right: '4%', top: '4%', height: '32%' },
      { left: '10%', right: '4%', top: '40%', height: '10%' },
      { left: '10%', right: '4%', top: '54%', height: '10%' },
      { left: '10%', right: '4%', top: '68%', height: '10%' },
      { left: '10%', right: '4%', top: '82%', height: '10%' },
    ]
  }
  xAxes = grids.map((_, i) => ({
    type: 'category',
    gridIndex: i,
    data: dates,
    scale: true,
    boundaryGap: true,
    axisLine: { onZero: false },
    splitLine: { show: i === 0 },
    min: 'dataMin',
    max: 'dataMax',
  }))
  // 0 号轴为主图价格；1 号轴为成交量，显示带单位的刻度；其余为指标副图
  yAxes = grids.map((_, i) => {
    if (i === 0) return { scale: true, splitArea: { show: true } }
    return {
      gridIndex: i,
      splitNumber: 2,
      axisLabel: i === 1 ? { formatter: (value) => `${value}${volumeUnit}` } : { show: false },
    }
  })
  zoomIndices = grids.map((_, i) => i)

  const legendData = ['日K', `成交量(${volumeUnit})`]

  // 买卖点：用 markPoint 标注在 K 线上（coord = [日期, 价格]），买入红 B、卖出绿 S
  const markPoints = []
  for (const p of buyPoints) {
    markPoints.push({
      coord: [dates[p[0]], p[1]],
      value: p[2],
      name: '买入',
      label: { formatter: 'B', color: '#d24545' },
      itemStyle: { color: 'rgba(210, 69, 69, 0.95)', borderColor: '#fff', borderWidth: 1 },
    })
  }
  for (const p of sellPoints) {
    markPoints.push({
      coord: [dates[p[0]], p[1]],
      value: p[2],
      name: '卖出',
      label: { formatter: 'S', color: '#1f9d6b' },
      itemStyle: { color: 'rgba(31, 157, 107, 0.95)', borderColor: '#fff', borderWidth: 1 },
    })
  }

  const series = [
    {
      name: '日K',
      type: 'candlestick',
      data: ohlc,
      itemStyle: {
        color: chartTheme.up(),
        color0: chartTheme.down(),
        borderColor: chartTheme.up(),
        borderColor0: chartTheme.down(),
      },
      ...(markPoints.length
        ? {
            markPoint: {
              symbol: 'circle',
              symbolSize: 8,
              label: { show: true, position: 'top', fontWeight: 700, fontSize: 13 },
              tooltip: {
                formatter: (param) => {
                  const d = param.data || {}
                  return `${d.name}: <b>${d.value ?? 0}</b> 笔`
                },
              },
              data: markPoints,
              z: 10,
            },
          }
        : {}),
    },
    {
      name: `成交量(${volumeUnit})`,
      type: 'bar',
      xAxisIndex: 1,
      yAxisIndex: 1,
      data: volumes,
      itemStyle: { color: chartTheme.volume() },
    },
  ]

  if (showMA) {
    const maColors = {
      MA5: chartTheme.ma5(),
      MA10: chartTheme.ma10(),
      MA20: chartTheme.ma20(),
      MA60: chartTheme.ma60(),
    }
    const maData = { MA5: calcMA(data, 5), MA10: calcMA(data, 10), MA20: calcMA(data, 20), MA60: calcMA(data, 60) }
    Object.entries(maData).forEach(([key, vals]) => {
      legendData.push(key)
      series.push({
        name: key,
        type: 'line',
        data: vals,
        smooth: true,
        showSymbol: false,
        lineStyle: { width: 1, color: maColors[key] },
        itemStyle: { color: maColors[key] },
      })
    })
  }

  if (showEMA) {
    const emaColors = { EMA12: chartTheme.ema12(), EMA26: chartTheme.ema26() }
    const emaData = { EMA12: calcEMA(data, 12), EMA26: calcEMA(data, 26) }
    Object.entries(emaData).forEach(([key, vals]) => {
      legendData.push(key)
      series.push({
        name: key,
        type: 'line',
        data: vals,
        smooth: true,
        showSymbol: false,
        lineStyle: { width: 1, color: emaColors[key] },
        itemStyle: { color: emaColors[key] },
      })
    })
  }

  if (showMACD) {
    const gi = 2
    const { dif, dea, macd } = calcMACD(data)
    legendData.push('DIF', 'DEA', 'MACD')
    series.push(
      {
        name: 'DIF',
        type: 'line',
        xAxisIndex: gi,
        yAxisIndex: gi,
        data: dif,
        showSymbol: false,
        lineStyle: { width: 1, color: chartTheme.dif() },
        itemStyle: { color: chartTheme.dif() },
      },
      {
        name: 'DEA',
        type: 'line',
        xAxisIndex: gi,
        yAxisIndex: gi,
        data: dea,
        showSymbol: false,
        lineStyle: { width: 1, color: chartTheme.dea() },
        itemStyle: { color: chartTheme.dea() },
      },
      {
        name: 'MACD',
        type: 'bar',
        xAxisIndex: gi,
        yAxisIndex: gi,
        data: macd.map((v) => ({ value: v, itemStyle: { color: v >= 0 ? chartTheme.up() : chartTheme.down() } })),
      }
    )
  }

  if (showKDJ) {
    const gi = showMACD ? 3 : 2
    const { k, d, j } = calcKDJ(data)
    legendData.push('K', 'D', 'J')
    series.push(
      {
        name: 'K',
        type: 'line',
        xAxisIndex: gi,
        yAxisIndex: gi,
        data: k,
        showSymbol: false,
        lineStyle: { width: 1, color: chartTheme.k() },
        itemStyle: { color: chartTheme.k() },
      },
      {
        name: 'D',
        type: 'line',
        xAxisIndex: gi,
        yAxisIndex: gi,
        data: d,
        showSymbol: false,
        lineStyle: { width: 1, color: chartTheme.d() },
        itemStyle: { color: chartTheme.d() },
      },
      {
        name: 'J',
        type: 'line',
        xAxisIndex: gi,
        yAxisIndex: gi,
        data: j,
        showSymbol: false,
        lineStyle: { width: 1, color: chartTheme.j() },
        itemStyle: { color: chartTheme.j() },
      }
    )
  }

  // 策略指标线：价格域叠在主图（grid 0），振荡域叠在最后新增的副图
  for (const l of strategyMainLines) {
    legendData.push(l.name)
    series.push({
      name: l.name,
      type: 'line',
      data: l.data,
      smooth: true,
      showSymbol: false,
      lineStyle: { width: 1.2, color: l.color },
      itemStyle: { color: l.color },
    })
  }
  if (strategySubLines.length) {
    const gi = grids.length - 1
    for (const l of strategySubLines) {
      legendData.push(l.name)
      // RSI 副图叠加 0/30/70/100 超买超卖参考刻度线
      const isRSI = l.key === 'rsi' || String(l.name || '').startsWith('RSI')
      series.push({
        name: l.name,
        type: 'line',
        xAxisIndex: gi,
        yAxisIndex: gi,
        data: l.data,
        smooth: true,
        showSymbol: false,
        lineStyle: { width: 1.2, color: l.color },
        itemStyle: { color: l.color },
        ...(isRSI
          ? {
              markLine: {
                symbol: 'none',
                silent: true,
                label: {
                  show: true,
                  position: 'start',
                  fontSize: 10,
                  color: 'rgba(128,128,128,0.9)',
                  formatter: '{c}',
                },
                lineStyle: { type: 'dashed', width: 1, color: 'rgba(128,128,128,0.45)' },
                data: [0, 30, 70, 100].map((v) => ({ yAxis: v, name: String(v) })),
              },
            }
          : {}),
      })
    }
  }

  // 买卖点：markPoint 标注在 K 线图上（见 candlestick 系列的 markPoint 配置）

  return {
    animation: true,
    textStyle: { color: chartTheme.axisText() },
    legend: { data: legendData, top: 0, type: 'scroll', width: '100%', textStyle: { color: chartTheme.axisText() } },
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'cross' },
      formatter(params) {
        const items = Array.isArray(params) ? params : [params]
        if (!items.length) return ''
        const lines = [items[0].axisValueLabel || '']
        for (const item of items) {
if (item.seriesName === '日K' && Array.isArray(item.data)) {
            const [base, open, close, low, high] = item.data
            const cur = data[item.dataIndex]
            const prev = data[item.dataIndex - 1]
            const pct = prev && cur ? ((Number(cur.close) - Number(prev.close)) / Number(prev.close)) * 100 : null
            const pctTxt = pct !== null ? `${pct >= 0 ? '+' : ''}${pct.toFixed(2)}%` : ''
            const fmt = (v) => (v == null || Number.isNaN(Number(v)) ? '--' : Number(v).toFixed(2))
            lines.push(`${item.marker}涨跌幅：${pctTxt}`)
            lines.push(`${item.marker}开 ${fmt(open)}，收 ${fmt(close)}，${pctTxt}`)
            lines.push(`${item.marker}低 ${fmt(low)}，高 ${fmt(high)}`)
            continue
          }
          if (item.seriesName.startsWith('成交量(')) {
            const num = Number(item.data) || 0
            lines.push(`${item.marker}${item.seriesName}：${num.toFixed(num >= 100 ? 0 : 2)}${volumeUnit}`)
            continue
          }
          if (item.seriesType === 'scatter') {
            const count = item.value?.[2] ?? 0
            lines.push(`${item.marker}${item.seriesName}: <b>${count}</b> 笔`)
            continue
          }
          // 指标线/柱（MA/EMA/MACD/KDJ）保留两位小数，避免长小数溢出
          const raw = item.data && typeof item.data === 'object' ? item.data.value : item.data
          const num = raw == null || raw === '' ? NaN : Number(raw)
          lines.push(
            item.seriesType === 'line' || item.seriesType === 'bar'
              ? `${item.marker}${item.seriesName}：${Number.isNaN(num) ? '--' : num.toFixed(2)}`
              : `${item.marker}${item.seriesName}：${item.data}`
          )
        }
        return lines.join('<br/>')
      },
    },
    axisPointer: { link: { xAxisIndex: 'all' } },
    grid: grids,
    xAxis: xAxes,
    yAxis: yAxes,
    dataZoom: [
      { type: 'inside', xAxisIndex: zoomIndices, start: zoomStart, end: 100 },
      {
        show: true,
        type: 'slider',
        xAxisIndex: zoomIndices,
        bottom: 6,
        height: 20,
        start: zoomStart,
        end: 100,
        textStyle: { color: chartTheme.axisText() },
      },
    ],
    series,
  }
}