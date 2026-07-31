// 项目配置：从后端 /api/v1/config 动态加载版本等信息
// 使用：在 main.js 调用 initAppConfig() 后，所有页面通过 window.APP_CONFIG.version 访问
// 注意：本项目 src/api/index.js 的拦截器会做响应解包（直接返回 data.data），
//      因此下面用 axios 原生实例 request' 的 get / post，避免误导。

import request from '@/api/index'

const DEFAULTS = {
  name: 'QuantLab',
  version: '0.0.0',
  description: '',
  api_version: 'v1',
}

// 单例 Promise：多次调用复用同一次请求
let configPromise = null

/**
 * 初始化全局 APP 配置。应在 app.mount 之前调用。
 * 失败回落到默认值，不阻断启动。
 */
export async function initAppConfig() {
  if (configPromise) return configPromise
  configPromise = (async () => {
    try {
      const data = await request({ url: '/config', method: 'get' })
      window.APP_CONFIG = { ...DEFAULTS, ...(data || {}) }
    } catch (err) {
      console.warn('[appConfig] 加载失败，使用默认值', err)
      window.APP_CONFIG = { ...DEFAULTS }
    }
    return window.APP_CONFIG
  })()
  return configPromise
}

/** 获取应用版本，未初始化时安全降级 */
export function getVersion() {
  return window.APP_CONFIG?.version || DEFAULTS.version
}

/** 获取应用名称 */
export function getAppName() {
  return window.APP_CONFIG?.name || DEFAULTS.name
}

/** 获取后端 API 版本 */
export function getApiVersion() {
  return window.APP_CONFIG?.api_version || DEFAULTS.api_version
}
