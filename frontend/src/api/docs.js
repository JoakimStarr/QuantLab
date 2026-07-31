import request from '@/api/index'

// 文档列表（分组后的元数据列表）
export function listDocs() {
  return request({ url: '/docs', method: 'get' })
}

// 获取单个文档详情（含 markdown 原文）
export function getDoc(slug) {
  return request({ url: '/docs/' + encodeURIComponent(slug), method: 'get' })
}
