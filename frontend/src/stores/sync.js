// 数据同步中心：全站唯一的同步调度与状态枢纽。
// 设计约定：
// - 8 大数据域在此注册（名称/说明/history 来源），SyncCenter 与页面按钮共用，避免多份列表漂移
// - 进度轮询全局单 timer：运行中 1s、空闲 15s 自适应降频（全站只此一份，页面不再各自轮询）
// - 各域单域同步统一经 syncDomain(key) 分发，提示文案统一「已提交，后台执行」
import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { ElMessage } from 'element-plus/es/components/message/index'
import {
  syncFullData,
  syncIndices,
  syncEtfData,
  syncFundamental,
  syncExternalMarket,
  eodSync,
  getSyncProgress,
  getSyncHistory,
} from '@/api/quant'
import { syncMacro, syncGlobalMacro } from '@/api/macro'
import { syncPolicy } from '@/api/policy'

// 数据域注册表：sources 用于在 sync-history 中匹配该域最近一次成功记录
export const DATA_DOMAINS = [
  { key: 'stock', name: 'A股行情', desc: '增量补齐最新交易日', icon: 'Histogram', sources: ['baostock', 'eod', 'chenditc', 'akshare'] },
  { key: 'indices', name: '指数', desc: 'baostock 主源 / akshare 兜底', icon: 'DataLine', sources: ['indices'] },
  { key: 'etf', name: 'ETF', desc: 'baostock 增量拉取', icon: 'Coin', sources: ['etf'] },
  { key: 'macro', name: '国内宏观', desc: '东财 + akshare 注册表', icon: 'Odometer', sources: ['macro', 'eastmoney'] },
  { key: 'globalMacro', name: '全球宏观', desc: 'FRED / CFTC / EIA', icon: 'Place', sources: [] },
  { key: 'fundamental', name: '财报数据', desc: '逐股增量提取', icon: 'Tickets', sources: ['fundamental'] },
  { key: 'external', name: '外盘行情', desc: '美股 / 港股 / 商品', icon: 'Sunrise', sources: ['external'] },
  { key: 'policy', name: '政策新闻', desc: '新闻联播文本入库', icon: 'Postcard', sources: ['policy'] },
]

// 单域同步动作：均以后台任务方式提交，返回后由全局轮询接管进度
const DOMAIN_SYNCERS = {
  stock: () => eodSync('all', 5, true, 'baostock'),
  indices: () => syncIndices(),
  etf: () => syncEtfData(2, 'baostock'),
  macro: () => syncMacro(),
  globalMacro: () => syncGlobalMacro(),
  fundamental: () => syncFundamental(false),
  external: () => syncExternalMarket(),
  policy: () => syncPolicy(),
}

export const useSyncStore = defineStore('sync', () => {
  // ---- 状态 ----
  const progress = ref(null) // /sync-progress 实时进度（message/progress_pct/status/kind）
  const lastSync = ref({}) // { [domainKey]: { time, status } } 各域最近同步记录
  const domainBusy = ref({}) // { [domainKey]: boolean } 单域按钮 loading
  const fullSyncing = ref(false) // 一键全同步按钮 loading
  const drawerOpen = ref(false) // SyncCenter 抽屉开关（全局唯一实例）

  let timer = null
  let idleTicks = 0
  let wasRunning = false

  const running = computed(() => progress.value?.status === 'running')
  const progressPct = computed(() => Math.floor(progress.value?.progress_pct || 0))

  // ---- 进度轮询（单 timer：running 1s / 空闲每 15s 探测一次） ----
  async function fetchProgress() {
    try {
      const p = await getSyncProgress()
      progress.value = p ?? null
    } catch {
      progress.value = null
    }
  }

  function onProgressTransition() {
    // running -> 终态：提示结果并刷新各域最近同步时间
    if (wasRunning && !running.value && progress.value) {
      if (progress.value.status === 'done') {
        ElMessage.success(progress.value.message || '同步完成')
      } else if (progress.value.status === 'failed') {
        ElMessage.error(progress.value.error || progress.value.message || '同步失败')
      }
      refreshLastSync()
    }
    wasRunning = running.value
  }

  function startPolling() {
    if (timer) return
    timer = setInterval(async () => {
      if (running.value) {
        idleTicks = 0
        await fetchProgress()
      } else if (idleTicks % 30 === 0) {
        await fetchProgress()
        idleTicks = 0
      }
      idleTicks += 1
      onProgressTransition()
    }, 1000)
  }

  // ---- 各域最近同步时间（sync-history 按 data_source 匹配） ----
  async function refreshLastSync() {
    try {
      const data = await getSyncHistory(200)
      const items = data?.items || []
      const next = {}
      for (const d of DATA_DOMAINS) {
        if (!d.sources.length) continue
        const hit = items.find((it) => d.sources.includes(it.data_source))
        if (hit) next[d.key] = { time: hit.finished_at || hit.started_at, status: hit.status }
      }
      lastSync.value = next
    } catch {
      // 静默失败：状态展示属增强信息，不阻断主流程
    }
  }

  // ---- 同步动作 ----
  async function startFullSync(years = 5, refreshMisc = false) {
    if (fullSyncing.value || running.value) return
    fullSyncing.value = true
    try {
      await syncFullData(years, 'all', refreshMisc)
      ElMessage.success(`一键全同步已提交（回填 ${years} 年），后台执行中`)
      wasRunning = true
      await fetchProgress()
    } catch (e) {
      if (e !== 'cancel') ElMessage.error('同步提交失败：' + (e?.message || e))
    } finally {
      fullSyncing.value = false
    }
  }

  async function syncDomain(key) {
    const domain = DATA_DOMAINS.find((d) => d.key === key)
    const fn = DOMAIN_SYNCERS[key]
    if (!domain || !fn) return
    if (domainBusy.value[key] || running.value) return
    domainBusy.value[key] = true
    try {
      await fn()
      ElMessage.success(`${domain.name}同步已提交，后台执行中`)
    } catch (e) {
      if (e !== 'cancel') ElMessage.error(`${domain.name}同步提交失败`)
    } finally {
      domainBusy.value[key] = false
    }
  }

  // ---- 抽屉 ----
  function open() {
    drawerOpen.value = true
    refreshLastSync()
    fetchProgress()
  }

  function close() {
    drawerOpen.value = false
  }

  // App 常驻组件（TopBar）挂载时调用一次
  function init() {
    fetchProgress()
    refreshLastSync()
    startPolling()
  }

  return {
    progress,
    lastSync,
    domainBusy,
    fullSyncing,
    drawerOpen,
    running,
    progressPct,
    init,
    open,
    close,
    startFullSync,
    syncDomain,
    refreshLastSync,
  }
})
