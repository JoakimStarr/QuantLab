// 文件下载公共工具（blob 导出）
export function downloadBlob(blob, filename) {
  const url = window.URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  window.URL.revokeObjectURL(url)
}

// 便捷导出：调用返回 blob 的 API 并触发下载
// 用法：await exportFile(() => api.exportTrades(id), 'trades.csv')
export async function exportFile(requestFn, filename) {
  const res = await requestFn()
  downloadBlob(res?.data || res, filename)
}
