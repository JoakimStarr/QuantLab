<!--
  Docs.vue - 技术文档查看器

  使用 Docsify 4.x 开源库实现：
  - 自动从后端 API fetch markdown 渲染
  - 内置可点击目录（右侧浮动）
  - 滚动时自动高亮当前章节
  - 代码高亮、搜索、emoji 等插件开箱即用

  设计要点：
  - 通过 window.$docsify 配置项动态指定 homepage，切换文档无需重新初始化
  - 使用路由 hash (#/slug) 实现文档间跳转和浏览器前进/后退
  - Docsify 会自动 fetch /api/v1/docs/md/<slug> 拿 markdown
-->
<template>
  <div class="docs-page">
    <!-- 顶部工具栏 -->
    <div class="docs-toolbar">
      <el-select
        v-model="currentSlug"
        filterable
        placeholder="选择文档"
        size="default"
        class="docs-select"
        @change="onDocChange"
      >
        <el-option-group
          v-for="group in groupedDocs"
          :key="group.name"
          :label="group.name"
        >
          <el-option
            v-for="d in group.docs"
            :key="d.slug"
            :value="d.slug"
            :label="d.title"
          >
            <span style="float: left">{{ d.title }}</span>
            <span style="float: right; color: var(--el-text-color-secondary); font-size: 12px">
              {{ d.summary || '—' }}
            </span>
          </el-option>
        </el-option-group>
      </el-select>

      <div class="docs-toolbar-right">
        <el-button-group>
          <el-button :icon="ZoomOut" size="small" @click="zoomOut" title="缩小字体" />
          <el-button size="small" @click="resetZoom">{{ fontSizeLabel }}</el-button>
          <el-button :icon="ZoomIn" size="small" @click="zoomIn" title="放大字体" />
        </el-button-group>
        <el-button-group style="margin-left: 8px">
          <el-button
            :type="sidebarPos === 'left' ? 'primary' : ''"
            size="small"
            @click="sidebarPos = 'left'"
            title="目录在左"
          >⟸</el-button>
          <el-button
            :type="sidebarPos === 'right' ? 'primary' : ''"
            size="small"
            @click="sidebarPos = 'right'"
            title="目录在右"
          >⟹</el-button>
        </el-button-group>
      </div>
    </div>

    <!-- Docsify 渲染容器 -->
    <!-- Docsify 会接管这个 div 的内容。结构：
         <aside class="sidebar">   ← Docsify 内部生成
         <section class="content">  ← Docsify 内部生成（带自动目录）
         -->
    <div id="docsify-app" class="docsify-host" />
  </div>
</template>

<script setup>
import { ref, onMounted, onBeforeUnmount, computed, watch, nextTick } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ZoomIn, ZoomOut } from '@element-plus/icons-vue'
import { listDocs as fetchDocList } from '@/api/docs'

const route = useRoute()
const router = useRouter()

const docs = ref([])
const currentSlug = ref('')
const sidebarPos = ref(localStorage.getItem('docs_sidebar_pos') || 'right')
const fontSize = ref(parseInt(localStorage.getItem('docs_font_size') || '15', 10))
const fontSizeLabel = computed(() => `${fontSize.value}px`)

const groupedDocs = computed(() => {
  const groups = new Map()
  for (const d of docs.value) {
    if (!groups.has(d.group)) groups.set(d.group, [])
    groups.get(d.group).push(d)
  }
  return [...groups.entries()].map(([name, list]) => ({
    name,
    docs: list.sort((a, b) => a.order - b.order),
  }))
})

// ---------------------------------------------------------------------------
// Docsify 集成
// ---------------------------------------------------------------------------

// 基础 URL（前后端分离部署时改这里）
// Docsify 会在这个路径下 fetch <slug>.md
const DOCS_BASE = '/api/v1/docs/md'

function ensureDocsifyAssets() {
  // 动态注入 Docsify CSS + JS（仅一次）
  if (document.getElementById('docsify-css')) return

  const css = document.createElement('link')
  css.id = 'docsify-css'
  css.rel = 'stylesheet'
  // 使用本地 npm 包路径（vite 打包后会引用 node_modules 中的资源）
  css.href = '/docsify/themes/vue.css'
  document.head.appendChild(css)

  // 自定义样式覆盖 Docsify 默认外观
  const customCss = document.createElement('style')
  customCss.id = 'docsify-custom-css'
  customCss.textContent = `
    /* 让 Docsify 适配 Element Plus 主题色 */
    :root {
      --docsifytabs--bg: var(--el-bg-color);
      --docsifytabs--tab-background-active: var(--el-color-primary-light-9);
      --code-theme-background: var(--el-fill-color-light);
    }
    .docsify-host { background: var(--el-bg-color-page); }
    .docsify-host .markdown-section {
      max-width: 100%;
      padding: 24px 32px;
      color: var(--el-text-color-primary);
      font-size: ${fontSize.value}px;
      line-height: 1.7;
    }
    .docsify-host .markdown-section h1,
    .docsify-host .markdown-section h2,
    .docsify-host .markdown-section h3 {
      color: var(--el-text-color-primary);
      font-weight: 600;
    }
    .docsify-host .markdown-section h2 {
      border-bottom: 1px solid var(--el-border-color-lighter);
      padding-bottom: 8px;
      margin-top: 32px;
    }
    .docsify-host .markdown-section code {
      background: var(--el-fill-color-light);
      color: #d63384;
      padding: 2px 6px;
      border-radius: 4px;
      font-size: 0.92em;
    }
    .docsify-host .markdown-section pre {
      background: #1e1e1e;
      color: #d4d4d4;
      border-radius: 6px;
      padding: 16px;
      overflow-x: auto;
    }
    .docsify-host .markdown-section pre code {
      background: transparent;
      color: inherit;
      padding: 0;
    }
    .docsify-host .markdown-section a {
      color: var(--el-color-primary);
    }
    .docsify-host .markdown-section blockquote {
      border-left: 4px solid var(--el-color-primary-light-5);
      background: var(--el-color-primary-light-9);
      padding: 12px 16px;
      border-radius: 0 4px 4px 0;
    }
    .docsify-host .markdown-section table {
      border-collapse: collapse;
      width: 100%;
      margin: 16px 0;
    }
    .docsify-host .markdown-section table th,
    .docsify-host .markdown-section table td {
      border: 1px solid var(--el-border-color-lighter);
      padding: 8px 12px;
      text-align: left;
    }
    .docsify-host .markdown-section table th {
      background: var(--el-fill-color-light);
      font-weight: 600;
    }

    /* 侧边栏 */
    .docsify-host .sidebar {
      background: var(--el-bg-color);
      border-right: 1px solid var(--el-border-color-lighter);
    }
    .docsify-host .sidebar > h1 {
      padding: 16px 20px 8px;
      margin: 0;
      font-size: 18px;
      font-weight: 600;
      color: var(--el-color-primary);
    }
    .docsify-host .sidebar-nav {
      padding: 0 12px;
    }
    .docsify-host .sidebar-nav p,
    .docsify-host .sidebar-nav strong {
      color: var(--el-text-color-secondary);
      padding: 12px 8px 4px;
      font-size: 12px;
      font-weight: 600;
      letter-spacing: 0.5px;
      text-transform: uppercase;
    }
    .docsify-host .sidebar-nav li {
      margin: 2px 0;
    }
    .docsify-host .sidebar-nav li a {
      display: block;
      padding: 6px 12px;
      border-radius: 4px;
      color: var(--el-text-color-regular);
      text-decoration: none;
      transition: all 0.2s;
    }
    .docsify-host .sidebar-nav li a:hover {
      background: var(--el-color-primary-light-9);
      color: var(--el-color-primary);
    }
    .docsify-host .sidebar-nav li.active > a {
      background: var(--el-color-primary-light-8);
      color: var(--el-color-primary);
      font-weight: 500;
    }

    /* 目录（侧边栏右侧） */
    .docsify-host .sidebar-toggle {
      background: var(--el-color-primary);
    }

    /* 暗色主题适配 */
    html.dark .docsify-host .markdown-section code {
      background: #2d2d2d;
      color: #ff79c6;
    }
    html.dark .docsify-host .markdown-section pre {
      background: #1e1e1e;
    }
    html.dark .docsify-host .sidebar {
      background: #1e1e1e;
      border-right-color: #333;
    }
  `
  document.head.appendChild(customCss)
}

let docsifyLoaded = null

// 每次进入页面都强制重新加载 Docsify 脚本（用时间戳去缓存）
// 避免 Vue Router 切换后 Docsify IIFE 不重跑导致空容器
function loadDocsify(force = false) {
  if (docsifyLoaded && !force) return docsifyLoaded

  // 移除已存在的脚本和插件，避免重复实例化
  const oldScript = document.getElementById('docsify-js')
  if (oldScript) oldScript.remove()
  document.querySelectorAll('script[data-docsify-plugin]').forEach((el) => el.remove())

  docsifyLoaded = new Promise((resolve, reject) => {
    // ⚠️ 关键：必须在加载 docsify.min.js 之前就设置好 window.$docsify
    // 因为 Docsify 脚本是 IIFE，加载时立即执行构造函数检查挂载点元素
    // eslint-disable-next-line no-undef
    window.$docsify = {
      // 挂载点（必须用 #docsify-app，因为我们有这个 id 的 div）
      el: '#docsify-app',
      // Docsify 会 fetch `${basePath}${homepage}` 拿首页 markdown
      basePath: DOCS_BASE,
      homepage: 'README.md',
      // 侧边栏 + 顶部导航（自动 fetch 并渲染）
      loadSidebar: '_sidebar.md',
      loadNavbar: '_navbar.md',
      // 目录最大层级
      subMaxLevel: 3,
      // hash 路由（#/slug，便于分享 URL）
      routerMode: 'hash',
      // 别名：让 Docsify 把 #/ 路由到 README.md
      alias: {
        '/.*/_sidebar.md': '/_sidebar.md',
        '/.*/_navbar.md': '/_navbar.md',
      },
      // 全文搜索
      search: {
        paths: 'auto',
        placeholder: '搜索文档...',
        noData: '没有找到结果',
        depth: 6,
      },
      name: 'QuantLab 技术文档',
      repo: 'https://github.com/JoakimStarr/QuantLab',
      auto2top: true,
    }

    // Docsify 4 主脚本（加时间戳去缓存，强制重跑 IIFE）
    const script = document.createElement('script')
    script.id = 'docsify-js'
    script.src = `/docsify/lib/docsify.min.js?t=${Date.now()}`
    script.onload = () => {
      // 搜索插件（也加时间戳）
      const searchPlugin = document.createElement('script')
      searchPlugin.id = 'docsify-search-js'
      searchPlugin.setAttribute('data-docsify-plugin', 'search')
      searchPlugin.src = `/docsify/lib/plugins/search.min.js?t=${Date.now()}`
      searchPlugin.onload = () => resolve()
      searchPlugin.onerror = () => resolve() // 搜索插件失败不阻塞
      document.head.appendChild(searchPlugin)
    }
    script.onerror = reject
    document.head.appendChild(script)
  })
  return docsifyLoaded
}

async function initDocsify() {
  // 1. 必须等 #docsify-app 元素进入 DOM 再加载脚本
  await nextTick()
  // 2. 清空容器（避免 Vue 复用组件时残留的 Docsify 内部 DOM）
  const container = document.getElementById('docsify-app')
  if (container) container.innerHTML = ''
  // 3. 强制重新加载 Docsify（IIFE 重新跑，挂载到当前容器）
  await loadDocsify(true)
}

async function loadDocs() {
  try {
    const res = await fetchDocList()
    // 后端 list_docs_api 返回 ApiResponse(ok=True, data={"docs": [...]})
    // axios 拦截器通常会 unwrap res.data，所以 res 即 ApiResponse
    docs.value = res?.data?.docs || res?.docs || []
    // 默认进入路由中的 slug 或第一个文档
    const querySlug = route.query.slug
    if (querySlug && docs.value.some((d) => d.slug === querySlug)) {
      currentSlug.value = querySlug
    } else if (docs.value.length) {
      currentSlug.value = 'README'  // 默认进 README
    }
    router.replace({ query: { slug: currentSlug.value } })
  } catch (e) {
    console.error('[Docs] load docs failed:', e)
  }
}

function onDocChange(slug) {
  // 切换文档：让 Docsify 跳到新路由
  router.replace({ query: { slug } })
  // Docsify 用 hash 路由，地址 #/slug
  window.location.hash = `#/${slug}`
}

watch(
  () => route.query.slug,
  (slug) => {
    if (slug && slug !== currentSlug.value) {
      currentSlug.value = slug
      // 同步 Docsify 路由
      window.location.hash = `#/${slug}`
    }
  },
)

watch(sidebarPos, (v) => localStorage.setItem('docs_sidebar_pos', v))

function zoomIn() {
  fontSize.value = Math.min(22, fontSize.value + 1)
  applyFontSize()
}
function zoomOut() {
  fontSize.value = Math.max(12, fontSize.value - 1)
  applyFontSize()
}
function resetZoom() {
  fontSize.value = 15
  applyFontSize()
}
function applyFontSize() {
  localStorage.setItem('docs_font_size', String(fontSize.value))
  document.querySelectorAll('.docsify-host .markdown-section').forEach((el) => {
    el.style.fontSize = `${fontSize.value}px`
  })
}

onMounted(async () => {
  await loadDocs()
  ensureDocsifyAssets()
  await initDocsify()
})

onBeforeUnmount(() => {
  // 清理 Docsify 注入的脚本与容器内容，避免重复实例化导致卡顿闪烁
  const oldScript = document.getElementById('docsify-js')
  if (oldScript) oldScript.remove()
  document.querySelectorAll('script[data-docsify-plugin]').forEach((el) => el.remove())
  const container = document.getElementById('docsify-app')
  if (container) container.innerHTML = ''
  // 重置缓存标志，下次进入会强制重新加载
  docsifyLoaded = null
})
</script>

<style scoped>
.docs-page {
  display: flex;
  flex-direction: column;
  height: calc(100vh - 120px);
  background: var(--el-bg-color-page);
}

.docs-toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 12px 24px;
  border-bottom: 1px solid var(--el-border-color-lighter);
  background: var(--el-bg-color);
}

.docs-select {
  width: 360px;
}

.docs-toolbar-right {
  display: flex;
  align-items: center;
}

.docsify-host {
  flex: 1;
  /* Docsify 内部用 position absolute 控制侧栏；这里给容器一个固定布局 */
  position: relative;
  overflow: hidden;
}

/* Docsify 内部结构覆盖：把 Docsify 的 .content 撑满容器 */
.docsify-host :deep(.content) {
  position: absolute;
  top: 0;
  bottom: 0;
  right: 0;
  left: 0;
  overflow-y: auto;
  padding-top: 0;
}
.docsify-host :deep(.sidebar) {
  position: absolute;
  top: 0;
  bottom: 0;
  width: 260px;
  overflow-y: auto;
}
</style>