import { describe, it, expect } from 'vitest'
import { buildTopicHeatMatrix, buildTopicCumulative } from '../src/utils/policyTopics'

describe('buildTopicHeatMatrix', () => {
  it('按 dateIdx/topicIdx 铺开 score>0 的格子', () => {
    const items = [
      { date: '2026-01-01', topics: { 人工智能: 0.8, 新能源: 0.4 } },
      { date: '2026-01-02', topics: { 人工智能: 0.6 } },
    ]
    const topics = ['人工智能', '新能源']
    expect(buildTopicHeatMatrix(items, topics)).toEqual([
      [0, 0, 0.8],
      [0, 1, 0.4],
      [1, 0, 0.6],
    ])
  })

  it('稀疏日（主题缺席）不产生格子', () => {
    const items = [
      { date: '2026-01-01', topics: { 人工智能: 0.5 } },
      { date: '2026-01-02', topics: {} },
    ]
    const topics = ['人工智能']
    expect(buildTopicHeatMatrix(items, topics)).toEqual([[0, 0, 0.5]])
  })

  it('score 为 0 / null / undefined 不入格', () => {
    const items = [
      { date: '2026-01-01', topics: { a: 0, b: null, c: undefined, d: 0.3 } },
    ]
    const topics = ['a', 'b', 'c', 'd']
    expect(buildTopicHeatMatrix(items, topics)).toEqual([[0, 3, 0.3]])
  })

  it('字符串 score 转 number', () => {
    const items = [{ date: '2026-01-01', topics: { a: '0.75' } }]
    expect(buildTopicHeatMatrix(items, ['a'])).toEqual([[0, 0, 0.75]])
  })

  it('空 items / 空 topics 返回空数组', () => {
    expect(buildTopicHeatMatrix([], ['a'])).toEqual([])
    expect(buildTopicHeatMatrix([{ date: 'x', topics: { a: 0.5 } }], [])).toEqual([])
  })
})

describe('buildTopicCumulative', () => {
  it('按日期累计，缺席日保持上一累计值', () => {
    const items = [
      { date: '2026-01-01', topics: { a: 0.3 } },
      { date: '2026-01-02', topics: {} },
      { date: '2026-01-03', topics: { a: 0.5 } },
    ]
    expect(buildTopicCumulative(items, 'a')).toEqual([0.3, 0.3, 0.8])
  })

  it('全缺席返回全 0', () => {
    const items = [{ date: '2026-01-01', topics: {} }, { date: '2026-01-02', topics: { b: 1 } }]
    expect(buildTopicCumulative(items, 'a')).toEqual([0, 0])
  })

  it('结果保留 3 位小数', () => {
    const items = [
      { date: 'd1', topics: { a: 0.12345 } },
      { date: 'd2', topics: { a: 0.001 } },
    ]
    expect(buildTopicCumulative(items, 'a')).toEqual([0.123, 0.124])
  })
})
