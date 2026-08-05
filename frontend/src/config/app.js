// 项目配置：从后端 /api/v1/config 动态加载版本等信息
// 使用：在 main.js 调用 initAppConfig() 后，通过 getVersion()/getAppName() 访问。
// 配置保存在响应式 ref 中，挂载后再异步加载也能触发依赖方（如 Sidebar 版本号）更新。
// 注意：本项目 src/api/index.js 的拦截器会做响应解包（直接返回 data.data），
//      因此下面用 axios 原生实例 request' 的 get / post，避免误导。

import { ref } from 'vue'
import request from '@/api/index'

const DEFAULTS = {
  name: 'QuantLab',
  version: '0.0.0',
  description: '',
  api_version: 'v1',
}

const config = ref({ ...DEFAULTS })

// 单例 Promise：多次调用复用同一次请求
let configPromise = null

/**
 * 初始化全局 APP 配置。可在 app.mount 之前或之后调用。
 * 失败回落到默认值，不阻断启动。
 */
export async function initAppConfig() {
  if (configPromise) return configPromise
  configPromise = (async () => {
    try {
      const data = await request({ url: '/config', method: 'get' })
      config.value = { ...DEFAULTS, ...(data || {}) }
    } catch (err) {
      console.warn('[appConfig] 加载失败，使用默认值', err)
      config.value = { ...DEFAULTS }
    }
    window.APP_CONFIG = config.value
    return config.value
  })()
  return configPromise
}

/** 获取应用版本，未初始化时安全降级 */
export function getVersion() {
  return config.value.version || DEFAULTS.version
}

/** 获取应用名称 */
export function getAppName() {
  return config.value.name || DEFAULTS.name
}

/** 获取后端 API 版本 */
export function getApiVersion() {
  return config.value.api_version || DEFAULTS.api_version
}
