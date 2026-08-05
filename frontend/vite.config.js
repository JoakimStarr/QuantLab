import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import AutoImport from 'unplugin-auto-import/vite'
import Components from 'unplugin-vue-components/vite'
import { ElementPlusResolver } from 'unplugin-vue-components/resolvers'
import { readFileSync } from 'fs'
import { resolve } from 'path'

// Element Plus 按需导入优化：
// 官方 ElementPlusResolver 对 v2 组件/指令从 barrel (`element-plus/es`) 导入，
// 而 barrel 聚合了全部组件，Rollup 无法有效 tree-shake，导致全量组件进包。
// 这里解析 barrel 的 import 语句构建 ElXxx -> 目录 映射，把 from 改写为子路径
// `element-plus/es/components/{dir}/index`，使每个组件只引入自身模块。
function buildElementPlusMap() {
  try {
    const barrel = readFileSync(
      resolve(process.cwd(), 'node_modules/element-plus/es/index.mjs'),
      'utf-8'
    )
    const map = {}
    const re = /import\s*\{([^}]+)\}\s*from\s*"\.\/components\/([^/]+)\/index\.mjs"/g
    let m
    while ((m = re.exec(barrel)) !== null) {
      const dir = m[2]
      for (const n of m[1].split(',')) {
        const nm = n.trim()
        if (nm.startsWith('El')) map[nm] = dir
      }
    }
    return map
  } catch {
    return {}
  }
}

const EP_MAP = buildElementPlusMap()

function lookupElementPlusDir(name) {
  if (!name) return null
  if (EP_MAP[name]) return EP_MAP[name]
  // 指令/服务式导出名（如 ElLoadingDirective / ElLoadingService）不在 barrel 中，
  // 去掉后缀后查找对应组件目录
  for (const suffix of ['Directive', 'Service']) {
    if (name.endsWith(suffix)) {
      const base = name.slice(0, -suffix.length)
      if (EP_MAP[base]) return EP_MAP[base]
    }
  }
  return null
}

function ElementPlusOnDemandResolver() {
  const base = ElementPlusResolver()
  return base.map((r) => ({
    type: r.type,
    resolve: async (name) => {
      const res = await r.resolve(name)
      if (!res) return res
      const fromBarrel = res.from === 'element-plus/es' || res.from === 'element-plus/lib'
      if (!fromBarrel) return res
      const dir = lookupElementPlusDir(res.name)
      if (!dir) return res // 未在映射中找到，保留 barrel（安全兜底）
      return { ...res, from: `element-plus/es/components/${dir}/index` }
    },
  }))
}

export default defineConfig({
  plugins: [
    vue(),
    AutoImport({ resolvers: [...ElementPlusOnDemandResolver()] }),
    Components({ resolvers: [...ElementPlusOnDemandResolver()] })
  ],
  resolve: {
    alias: { '@': resolve(__dirname, 'src') }
  },
  css: {
    preprocessorOptions: {
      scss: {
        api: 'modern-compiler',
        silenceDeprecations: ['legacy-js-api'],
      },
    },
  },
  server: {
    port: 3000,
    // 允许 cloudflared 快速隧道域名访问 dev server（Vite 5.3+ 默认拦截非 localhost Host）
    allowedHosts: ['.trycloudflare.com'],
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true
      },
      '/ws': {
        target: 'ws://localhost:8000',
        ws: true,
        changeOrigin: true
      }
    }
  },
  build: {
    rollupOptions: {
      output: {
        // 函数形式 manualChunks：按 node_modules 路径分组，避免对象形式解析包入口
        // 导致 barrel 被强制纳入图、破坏 tree-shaking。
        manualChunks(id) {
          if (!id.includes('node_modules/')) return
          if (id.includes('element-plus') || id.includes('@element-plus/icons-vue')) return 'vendor-element'
          if (id.includes('/echarts/') || id.includes('vue-echarts') || id.includes('/zrender/')) return 'vendor-echarts'
          if (id.includes('/vue/') || id.includes('vue-router') || id.includes('/pinia/') || id.includes('@vue/')) return 'vendor-vue'
          if (id.includes('/axios/') || id.includes('/dayjs/')) return 'vendor-utils'
        }
      }
    }
  }
})
