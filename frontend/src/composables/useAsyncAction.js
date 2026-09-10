// 包装 async 操作的 loading + try/finally 样板。
// 用法：const { run, loading } = useAsyncAction(fn, { onError })
//
// 错误策略（重要）：默认静默吞掉异常 —— 本项目 axios 拦截器已统一弹错误提示，
// 再走 ElMessage.error 会双重提示；需要自定义文案时传 onError。
import { ref } from 'vue'

export function useAsyncAction(fn, { onError } = {}) {
  const loading = ref(false)

  async function run(...args) {
    loading.value = true
    try {
      return await fn(...args)
    } catch (e) {
      if (onError) onError(e)
    } finally {
      loading.value = false
    }
  }

  return { run, loading }
}
