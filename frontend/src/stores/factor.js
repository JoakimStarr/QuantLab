import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { listFactors, addFactor, disableFactor, evaluateFactor } from '@/api/factor'

// 因子全局状态：列表缓存 + CRUD（5 分钟缓存，写操作后自动失效）
export const useFactorStore = defineStore('factor', () => {
  const factors = ref([])
  const loading = ref(false)
  const lastFetch = ref(null)

  const factorCount = computed(() => factors.value.length)
  const activeFactors = computed(() => factors.value.filter(f => f.status === 'active'))

  async function fetchList(force = false, params = { limit: 500 }) {
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

  async function create(data) {
    const res = await addFactor(data)
    invalidate()
    await fetchList(true)
    return res
  }

  async function remove(id) {
    const res = await disableFactor(id)
    invalidate()
    await fetchList(true)
    return res
  }

  async function evaluate(id, params) {
    const res = await evaluateFactor(id, params)
    invalidate()
    await fetchList(true)
    return res
  }

  return { factors, loading, factorCount, activeFactors, fetchList, invalidate, create, remove, evaluate }
})
