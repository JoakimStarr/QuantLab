// 轻量 TTL 缓存：基于 sessionStorage（会话内刷新不丢），带过期时间。
// 用于首页统计等重量级接口：进入时先渲染缓存，后台静默刷新。
const KEY_PREFIX = 'quantlab_cache_'

export function getCached(key) {
  try {
    const raw = sessionStorage.getItem(KEY_PREFIX + key)
    if (!raw) return null
    const entry = JSON.parse(raw)
    if (Date.now() > entry.expire) {
      sessionStorage.removeItem(KEY_PREFIX + key)
      return null
    }
    return entry.value
  } catch {
    return null
  }
}

export function setCache(key, value, ttlMs) {
  try {
    sessionStorage.setItem(KEY_PREFIX + key, JSON.stringify({ expire: Date.now() + ttlMs, value }))
  } catch {
    // 隐私模式/配额不足时静默降级为不缓存
  }
}

export function clearCache(key) {
  try {
    sessionStorage.removeItem(KEY_PREFIX + key)
  } catch {
    // ignore
  }
}
