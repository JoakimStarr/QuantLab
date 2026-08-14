/**
 * 政策主题热度纯函数：从 [{date, topics:{topic:score}}] 序列构建图表数据。
 * 与 UI/框架解耦，便于单元测试。
 */

/**
 * 构建热力图矩阵数据 [[dateIdx, topicIdx, score], ...]。
 * @param {Array<{date: string, topics: Record<string, number>}>} items 按日期升序
 * @param {string[]} topics 主题列表（y 轴顺序，index 即 topicIdx）
 * @returns {Array<[number, number, number]>} 仅 score > 0 的格子入阵（稀疏日留白）
 */
export function buildTopicHeatMatrix(items, topics) {
  const data = []
  items.forEach((it, di) => {
    topics.forEach((topic, ti) => {
      const score = it.topics?.[topic]
      if (score != null && score > 0) data.push([di, ti, Number(score)])
    })
  })
  return data
}

/**
 * 构建主题累计热度序列（按日期累计 score）。
 * @param {Array<{date: string, topics: Record<string, number>}>} items 按日期升序
 * @param {string} topic
 * @returns {number[]} 每个日期的累计分数（保留 3 位小数）
 */
export function buildTopicCumulative(items, topic) {
  let acc = 0
  return items.map((it) => {
    const s = it.topics?.[topic]
    if (s != null && s > 0) acc += Number(s)
    return +acc.toFixed(3)
  })
}
