import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { listStrategies, listAllBacktestResults, listBacktestResults } from '@/api/strategy'

// 策略全局状态：策略列表 + 回测结果（5 分钟缓存）
export const useStrategyStore = defineStore('strategy', () => {
  const strategies = ref([])
  const backtestResults = ref([])
  const loading = ref(false)
  const lastFetch = ref(null)

  const strategyCount = computed(() => strategies.value.length)
  const activeStrategies = computed(() =>
    strategies.value.filter(s => !s.archived && s.status !== 'archived')
  )

  async function fetchStrategies(force = false, params = {}) {
    if (!force && strategies.value.length > 0 && lastFetch.value) {
      const age = Date.now() - lastFetch.value
      if (age < 5 * 60 * 1000) return strategies.value
    }
    loading.value = true
    try {
      const res = await listStrategies(params)
      strategies.value = res?.items ?? []
      lastFetch.value = Date.now()
    } finally {
      loading.value = false
    }
  }

  async function fetchBacktests(strategyId, params) {
    const res = strategyId
      ? await listBacktestResults(strategyId, params)
      : await listAllBacktestResults(params)
    backtestResults.value = res?.items ?? []
    return backtestResults.value
  }

  function invalidate() {
    lastFetch.value = null
  }

  return {
    strategies,
    backtestResults,
    loading,
    strategyCount,
    activeStrategies,
    fetchStrategies,
    fetchBacktests,
    invalidate
  }
})
