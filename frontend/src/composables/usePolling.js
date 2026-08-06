import { onBeforeUnmount, ref } from 'vue'

// 通用轮询 composable（替代各页面手写 start/stop setInterval）
// 用法：
//   const { start, stop, isPolling } = usePolling(fetchFn, 5000)
//   onMounted(() => start())   // start({ immediate: false }) 可跳过首次立即执行
//   onBeforeUnmount 自动清理（无需手动）
export function usePolling(fn, interval = 5000, { immediate = true } = {}) {
  let timer = null
  const isPolling = ref(false)

  const stop = () => {
    if (timer) {
      clearInterval(timer)
      timer = null
    }
    isPolling.value = false
  }

  const start = (opts = {}) => {
    stop()
    const run = () => {
      try {
        fn()
      } catch (e) {
        // 轮询内的错误由调用方处理，这里静默避免中断定时器
      }
    }
    if (opts.immediate ?? immediate) run()
    timer = setInterval(run, opts.interval ?? interval)
    isPolling.value = true
  }

  onBeforeUnmount(stop)
  return { start, stop, isPolling }
}
