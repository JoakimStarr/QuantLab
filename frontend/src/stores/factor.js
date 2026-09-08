import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { ElMessage } from 'element-plus/es/components/message/index'
import {
  listFactors,
  addFactor,
  disableFactor,
  evaluateFactor,
  createFactorEvalJob,
  listFactorEvalJobs,
} from '@/api/factor'

// 因子全局状态：列表缓存 + CRUD（5 分钟缓存，写操作后自动失效）
export const useFactorStore = defineStore('factor', () => {
  const factors = ref([])
  const loading = ref(false)
  const lastFetch = ref(null)

  const factorCount = computed(() => factors.value.length)
  const activeFactors = computed(() => factors.value.filter((f) => f.status === 'active'))

  async function fetchList(force = false, params = { limit: 500, status: '' }) {
    if (!force && factors.value.length > 0 && lastFetch.value) {
      const age = Date.now() - lastFetch.value
      if (age < 5 * 60 * 1000) return factors.value
    }
    loading.value = true
    try {
      const res = await listFactors(params)
      factors.value = res?.items ?? []
      lastFetch.value = Date.now()
    } finally {
      loading.value = false
    }
  }

  function invalidate() {
    lastFetch.value = null
  }

  // 写操作后的统一刷新：失效缓存 + 重新拉取（等价于 fetchList(true)，去掉三连冗余）
  async function refresh() {
    invalidate()
    await fetchList()
  }

  async function create(data) {
    const res = await addFactor(data)
    await refresh()
    return res
  }

  async function remove(id) {
    const res = await disableFactor(id)
    await refresh()
    return res
  }

  async function evaluate(id, params) {
    const res = await evaluateFactor(id, params)
    await refresh()
    return res
  }

  function sleep(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms))
  }

  // 后台轮询至没有 running/pending 的评价 job（store 跨页存活，组件可离开页面）
  async function waitEvalSettled(timeoutMs = 45 * 60 * 1000) {
    const deadline = Date.now() + timeoutMs
    while (Date.now() < deadline) {
      try {
        const data = await listFactorEvalJobs(10)
        const items = data?.items || []
        const running = items.filter((j) => j.status === 'pending' || j.status === 'running')
        if (!running.length) return items
      } catch {
        // 轮询失败继续重试
      }
      await sleep(3000)
    }
    return null
  }

  // 提交后台评价 job（单因子/批量补算通用）：立即返回，worker 子进程逐个评价；
  // 等待全部终态后失效缓存（调用页面再自行 refreshList）
  async function submitEval(factorIds, params = {}) {
    const job = await createFactorEvalJob(factorIds, params)
    ElMessage.success(job?.message || '评价任务已提交，后台计算中…')
    try {
      const settled = await waitEvalSettled()
      invalidate()
      const failed = (settled || []).some((j) => j.status === 'failed')
      const cancelled = (settled || []).some((j) => j.status === 'cancelled')
      if (failed) ElMessage.warning('部分评价任务失败，请查看评价任务列表或日志')
      else if (cancelled) ElMessage.info('评价任务已取消')
      else if (settled) ElMessage.success('评价完成，列表已更新')
    } catch {
      // 静默：列表仍可通过手动刷新
    }
    return job
  }

  return {
    factors,
    loading,
    factorCount,
    activeFactors,
    fetchList,
    invalidate,
    create,
    remove,
    evaluate,
    refresh,
    submitEval,
  }
})
