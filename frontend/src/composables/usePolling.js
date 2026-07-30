import { onBeforeUnmount } from 'vue'

/**
 * 统一轮询 composable，自动管理 setInterval 生命周期，避免各页面手动清理遗漏。
 *
 * @param {Function} fn - 每次执行的异步函数（返回 truthy 表示可提前停止）
 * @param {number} interval - 间隔毫秒，默认 3000
 * @param {Object} opts - { maxAttempts, immediate }
 * @returns {{ start, stop, running }} 控制方法
 */
export function usePolling(fn, interval = 3000, opts = {}) {
  const { maxAttempts = Infinity, immediate = false } = opts
  let timer = null
  let attempts = 0

  function stop() {
    if (timer) {
      clearInterval(timer)
      timer = null
    }
    attempts = 0
  }

  function start() {
    stop()
    if (immediate) {
      Promise.resolve(fn()).catch(() => {})
    }
    timer = setInterval(async () => {
      attempts++
      if (attempts > maxAttempts) {
        stop()
        return
      }
      try {
        const stopFlag = await fn()
        if (stopFlag === true) stop()
      } catch (e) {
        /* 单次失败忽略，继续轮询 */
      }
    }, interval)
  }

  onBeforeUnmount(stop)
  return { start, stop, get running() { return timer !== null } }
}
