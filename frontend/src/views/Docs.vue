<!--
  Docs.vue - 技术文档查看器

  使用 markdown-it + highlight.js 渲染 Markdown 文档：
  - 从后端 API 获取文档列表和内容
  - markdown-it 渲染 HTML，highlight.js 代码高亮
  - 自动生成 TOC 侧边栏（h1/h2/h3）
  - 滚动时自动高亮当前章节
  - 支持字体缩放、侧边栏位置切换
-->
<template>
  <div class="docs-page">
    <!-- 顶部工具栏 -->
    <div class="docs-toolbar">
      <el-select
        v-model="categoryFilter"
        size="default"
        class="docs-cat-select"
        placeholder="分类筛选"
      >
        <el-option label="全部分类" value="all" />
        <el-option v-for="g in groupNames" :key="g" :label="g" :value="g" />
      </el-select>
      <el-select
        v-model="currentSlug"
        filterable
        placeholder="选择文档"
        size="default"
        class="docs-select"
        @change="onDocChange"
      >
        <el-option-group
          v-for="group in filteredGroupedDocs"
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
        <el-button
          :type="readingMode ? 'primary' : ''"
          :icon="View"
          size="small"
          style="margin-left: 8px"
          title="阅读模式（隐藏目录）"
          @click="readingMode = !readingMode"
        >{{ readingMode ? '退出阅读' : '阅读模式' }}</el-button>
      </div>
    </div>

    <!-- 主内容区域：侧边栏 + 正文 -->
    <div class="docs-body" :class="[`sidebar--${sidebarPos}`, { 'docs-body--reading': readingMode }]">
      <!-- TOC 侧边栏 -->
      <aside class="docs-sidebar">
        <div class="toc-title">目录</div>
        <nav class="toc-nav">
          <div
            v-for="item in visibleToc"
            :key="item.id"
            :class="['toc-row', `toc-h${item.level}`, { 'toc-row--active': activeHeading === item.id }]"
          >
            <span
              v-if="item.children.length"
              class="toc-caret"
              :class="{ 'toc-caret--open': !collapsedSections.has(item.id) }"
              @click="toggleTocSection(item.id)"
            >▸</span>
            <a
              class="toc-item"
              :href="'#' + item.id"
              @click.prevent="scrollToHeading(item.id)"
            >{{ item.text }}</a>
          </div>
        </nav>
        <div v-if="!toc.length" class="toc-empty">无标题结构</div>
      </aside>

      <!-- 文档正文 -->
      <main
        class="docs-content"
        ref="contentRef"
        :style="{ fontSize: fontSize + 'px' }"
        v-loading="loading"
        @scroll="onContentScroll"
        @click="onContentClick"
      >
        <div v-if="!doc" class="docs-empty">
          <el-icon :size="48" color="var(--el-text-color-placeholder)"><Document /></el-icon>
          <p>请从上方选择一篇文档</p>
        </div>
        <template v-else>
          <el-alert
            v-if="loadError"
            :title="loadError"
            type="error"
            show-icon
            :closable="true"
            @close="loadError = ''"
            class="docs-error"
          />
          <div class="doc-meta">
            <span class="doc-meta__group">{{ doc.group || '未分组' }}</span>
            <span v-if="doc.summary" class="doc-meta__summary">{{ doc.summary }}</span>
            <span class="doc-meta__count">约 {{ docWordCount }} 字</span>
          </div>
          <div class="markdown-body" v-html="renderedHtml" />
          <nav class="doc-nav">
            <el-button
              v-if="docNav.prev"
              link
              @click="onDocChange(docNav.prev.slug)"
              title="上一篇"
            >⟵ {{ docNav.prev.title }}</el-button>
            <span v-else class="doc-nav__placeholder" />
            <el-button
              v-if="docNav.next"
              link
              @click="onDocChange(docNav.next.slug)"
              title="下一篇"
            >{{ docNav.next.title }} ⟶</el-button>
            <span v-else class="doc-nav__placeholder" />
          </nav>
        </template>
      </main>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onBeforeUnmount, watch, nextTick } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ZoomIn, ZoomOut, Document, View } from '@element-plus/icons-vue'
import { listDocs as fetchDocList, getDoc } from '@/api/docs'
import MarkdownIt from 'markdown-it'
// highlight.js 按需注册：仅文档实际用到的语言，避免全量 384 语言打进分包（~1MB）
import hljs from 'highlight.js/lib/core'
import json from 'highlight.js/lib/languages/json'
import python from 'highlight.js/lib/languages/python'
import sql from 'highlight.js/lib/languages/sql'
import bash from 'highlight.js/lib/languages/bash'
import yaml from 'highlight.js/lib/languages/yaml'
import markdown from 'highlight.js/lib/languages/markdown'
import ini from 'highlight.js/lib/languages/ini'
import 'highlight.js/styles/github-dark.css'

hljs.registerLanguage('json', json)
hljs.registerLanguage('python', python)
hljs.registerLanguage('py', python)
hljs.registerLanguage('sql', sql)
hljs.registerLanguage('bash', bash)
hljs.registerLanguage('shell', bash)
hljs.registerLanguage('sh', bash)
hljs.registerLanguage('yaml', yaml)
hljs.registerLanguage('yml', yaml)
hljs.registerLanguage('markdown', markdown)
hljs.registerLanguage('md', markdown)
hljs.registerLanguage('ini', ini)
hljs.registerLanguage('env', ini)
hljs.registerLanguage('toml', ini)

const route = useRoute()
const router = useRouter()

const docs = ref([])
const doc = ref(null)
const currentSlug = ref('')
const sidebarPos = ref(localStorage.getItem('docs_sidebar_pos') || 'right')
const readingMode = ref(localStorage.getItem('docs_reading_mode') === '1')
const fontSize = ref(parseInt(localStorage.getItem('docs_font_size') || '15', 10))
const fontSizeLabel = computed(() => `${fontSize.value}px`)
const contentRef = ref(null)
const toc = ref([])
const activeHeading = ref('')
const loading = ref(false)
const loadError = ref('')
const categoryFilter = ref(
  route.query.category || localStorage.getItem('docs_category') || 'all'
)
// 已加载文档缓存：Map<slug, doc>，重复切换秒开；模块级，跨路由往返保留
const docCache = new Map()

// markdown-it 实例（带 highlight.js 代码高亮）
const md = new MarkdownIt({
  html: true,
  linkify: true,
  typographer: true,
  breaks: true,
  highlight(str, lang) {
    if (lang && hljs.getLanguage(lang)) {
      try {
        return '<pre class="hljs"><code>' + hljs.highlight(str, { language: lang, ignoreIllegals: true }).value + '</code></pre>'
      } catch { /* fall through */ }
    }
    // 没有语言或高亮失败，用通用转义
    return '<pre class="hljs"><code>' + md.utils.escapeHtml(str) + '</code></pre>'
  },
})

// ============================================================
// 标题锚点 id 生成（渲染器与 TOC 必须共用同一套规则，否则点击定位失效）
// 注意：markdown-it 默认不给标题渲染 id 属性，必须注入；否则
// TOC 点击 scrollToHeading 的 querySelector 找不到目标节点。
// ============================================================

// 清洗标题里的行内 markdown 标记（粗体/斜体/链接/行内代码），
// 使"markdown 原文"与"渲染后文本"生成一致的 slug。
function cleanHeadingText(text) {
  return text
    .replace(/!?\[([^\]]*)\]\([^)]*\)/g, '$1') // 链接/图片 → 文字
    .replace(/`([^`]*)`/g, '$1')               // 行内代码 → 文字
    .replace(/\*\*([^*]+)\*\*/g, '$1')         // 粗体 → 文字
    .replace(/\*([^*]+)\*/g, '$1')             // 斜体 → 文字
}

// 与 markdown-it 渲染一致的 slugify（仅保留字母数字与中文，其他转 -）
function slugifyHeading(text) {
  return text
    .toLowerCase()
    .replace(/[^\w\u4e00-\u9fff]+/g, '-')
    .replace(/^-+|-+$/g, '')
}

// 生成带去重计数的 id 生成器：重复标题追加 -1/-2（markdown-it-anchor 同款行为）
function makeHeadingIdGenerator() {
  const used = new Set()
  const gen = (text) => {
    const base = slugifyHeading(cleanHeadingText(text)) || 'heading'
    let id = base
    let i = 1
    while (used.has(id)) id = `${base}-${i++}`
    used.add(id)
    return id
  }
  gen.used = used
  return gen
}

// 渲染器侧的 id 生成器（每次渲染前重置，与 generateToc 从零对齐）
const nextHeadingId = makeHeadingIdGenerator()

// 注入 heading id：markdown-it 默认不生成，TOC 定位依赖它
md.renderer.rules.heading_open = (tokens, idx, options, env, self) => {
  const token = tokens[idx]
  const inline = tokens[idx + 1]
  let text = ''
  if (inline && inline.children) {
    text = inline.children
      .filter((c) => c.type === 'text' || c.type === 'code_inline')
      .map((c) => c.content)
      .join('')
  }
  token.attrSet('id', nextHeadingId(text))
  return self.renderToken(tokens, idx, options)
}

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

// 分类筛选：全部 / 按分组（百科·基础 / 百科·AI / 架构 / ...）
const groupNames = computed(() => groupedDocs.value.map(g => g.name))
const filteredGroupedDocs = computed(() =>
  categoryFilter.value === 'all'
    ? groupedDocs.value
    : groupedDocs.value.filter(g => g.name === categoryFilter.value)
)

// 切换分类时，若当前文档不在该分类下，跳到该分类第一篇
watch(categoryFilter, (val) => {
  // 持久化：URL query + localStorage，刷新后保持当前分类
  localStorage.setItem('docs_category', val || 'all')
  router.replace({ query: { ...route.query, category: val === 'all' ? undefined : val } })
  const groups = filteredGroupedDocs.value
  if (!groups.length) return
  const first = groups[0]?.docs?.[0]
  const inSet = groups.some(g => g.docs.some(d => d.slug === currentSlug.value))
  if (!inSet && first) {
    currentSlug.value = first.slug
    router.replace({ query: { slug: first.slug, ...(val === 'all' ? {} : { category: val }) } })
    loadDoc(first.slug)
  }
})

const renderedHtml = computed(() => {
  if (!doc.value) return ''
  // 重置渲染器侧 id 去重计数，与 generateToc 从零对齐
  nextHeadingId.used.clear()
  return md.render(doc.value.content)
})

// 解析 TOC 从 markdown 原文（比从 DOM 解析更可靠）
function generateToc(content) {
  if (!content) return []
  const items = []
  const nextId = makeHeadingIdGenerator()
  const lines = content.split(/\r?\n/)
  let inFence = false

  for (const line of lines) {
    if (/^\s*(```|~~~)/.test(line)) {
      inFence = !inFence
      continue
    }
    if (inFence) continue

    const match = /^(#{1,4})\s+(.+)$/.exec(line)
    if (!match) continue

    const level = match[1].length
    const rawText = match[2].trim()
    items.push({ level, text: cleanHeadingText(rawText), id: nextId(rawText) })
  }
  return items
}

// 滚动到指定标题
function scrollToHeading(id) {
  if (!contentRef.value) return
  const el = contentRef.value.querySelector('#' + CSS.escape(id))
  if (el) {
    // 计算偏移让标题位于视口上方一点
    const offset = el.getBoundingClientRect().top - contentRef.value.getBoundingClientRect().top - 16
    contentRef.value.scrollBy({ top: offset, behavior: 'smooth' })
  }
}

// 滚动时高亮当前章节
function onContentScroll() {
  if (!contentRef.value || !toc.value.length) return
  const headings = toc.value
    .map((item) => contentRef.value.querySelector('#' + CSS.escape(item.id)))
    .filter(Boolean)

  // 从底部往上找第一个可见的标题
  const scrollTop = contentRef.value.scrollTop
  const containerTop = contentRef.value.getBoundingClientRect().top + 80 // 偏移量

  let active = headings[0]
  for (const el of headings) {
    const rect = el.getBoundingClientRect()
    if (rect.top <= containerTop) {
      active = el
    }
  }
  activeHeading.value = active ? active.id : ''
}

// 监听文档加载完成后滚动到 hash 指定的标题
function scrollToHash() {
  const hash = route.hash.replace('#', '')
  if (hash) {
    nextTick(() => scrollToHeading(hash))
  }
}

async function loadDoc(slug) {
  if (!slug) return
  // 已缓存：直接展示（秒开），不触发 loading
  if (docCache.has(slug)) {
    doc.value = docCache.get(slug)
    toc.value = generateToc(doc.value?.content || '')
    collapsedSections.value = new Set()
    await nextTick()
    scrollToHash()
    return
  }
  loading.value = true
  loadError.value = ''
  try {
    const res = await getDoc(slug)
    const d = res?.data || res
    docCache.set(slug, d)
    doc.value = d
    toc.value = generateToc(doc.value?.content || '')
    collapsedSections.value = new Set()
    await nextTick()
    scrollToHash()
  } catch (e) {
    console.error('[Docs] load doc failed:', e)
    loadError.value = '加载文档失败：' + (e?.message || e)
  } finally {
    loading.value = false
  }
}

async function loadDocs() {
  try {
    const res = await fetchDocList()
    docs.value = res?.data?.docs || res?.docs || []
    // 默认进入路由中的 slug 或第一个文档
    const querySlug = route.query.slug
    if (querySlug && docs.value.some((d) => d.slug === querySlug)) {
      currentSlug.value = querySlug
    } else if (docs.value.length) {
      currentSlug.value = docs.value[0].slug
    }
    if (currentSlug.value) {
      await loadDoc(currentSlug.value)
    }
  } catch (e) {
    console.error('[Docs] load docs list failed:', e)
  }
}

function onDocChange(slug) {
  router.replace({ query: { slug } })
  loadDoc(slug)
}

// === 上/下一篇导航（按分组内 order 顺序）===
const docNav = computed(() => {
  const flat = groupedDocs.value.flatMap(g => g.docs)
  const idx = flat.findIndex(d => d.slug === currentSlug.value)
  if (idx < 0) return { prev: null, next: null }
  return {
    prev: flat[idx - 1] || null,
    next: flat[idx + 1] || null,
  }
})

// === 文档元信息（字数估算）===
const docWordCount = computed(() => {
  if (!doc.value?.content) return 0
  const text = doc.value.content
    .replace(/```[\s\S]*?```/g, '')
    .replace(/`[^`]*`/g, '')
    .replace(/!?\[([^\]]*)\]\([^)]*\)/g, '$1')
    .replace(/[#>*_\-|]/g, '')
    .trim()
  return text.length
})

// === TOC 折叠：按层级嵌套，h2 可展开/收起 ===
const tocTree = computed(() => {
  const tree = []
  const stack = []
  for (const item of toc.value) {
    const node = { ...item, children: [] }
    while (stack.length && stack[stack.length - 1].level >= item.level) stack.pop()
    if (stack.length) stack[stack.length - 1].children.push(node)
    else tree.push(node)
    stack.push(node)
  }
  return tree
})
const collapsedSections = ref(new Set())
function toggleTocSection(id) {
  const s = new Set(collapsedSections.value)
  if (s.has(id)) s.delete(id)
  else s.add(id)
  collapsedSections.value = s
}
const visibleToc = computed(() => {
  const out = []
  const walk = (nodes) => {
    for (const n of nodes) {
      out.push(n)
      if (n.children.length && !collapsedSections.value.has(n.id)) walk(n.children)
    }
  }
  walk(tocTree.value)
  return out
})

// === 代码块复制按钮（渲染后注入 + 事件委托）===
watch(renderedHtml, async () => {
  await nextTick()
  const root = contentRef.value?.querySelector('.markdown-body')
  if (!root) return
  root.querySelectorAll('pre').forEach((pre) => {
    if (pre.querySelector('.code-copy-btn')) return
    const btn = document.createElement('button')
    btn.type = 'button'
    btn.className = 'code-copy-btn'
    btn.textContent = '复制'
    pre.appendChild(btn)
  })
})

function onContentClick(e) {
  const btn = e.target.closest?.('.code-copy-btn')
  if (!btn) return
  const pre = btn.closest('pre')
  const text = pre?.querySelector('code')?.textContent || ''
  if (!text) return
  navigator.clipboard?.writeText(text).then(() => {
    btn.textContent = '已复制'
    setTimeout(() => { btn.textContent = '复制' }, 1500)
  }).catch(() => { btn.textContent = '复制失败' })
}

watch(readingMode, (v) => localStorage.setItem('docs_reading_mode', v ? '1' : '0'))

watch(
  () => route.query.slug,
  (slug) => {
    if (slug && slug !== currentSlug.value) {
      currentSlug.value = slug
      loadDoc(slug)
    }
  },
)

watch(sidebarPos, (v) => localStorage.setItem('docs_sidebar_pos', v))

function zoomIn() {
  fontSize.value = Math.min(22, fontSize.value + 1)
  localStorage.setItem('docs_font_size', String(fontSize.value))
}
function zoomOut() {
  fontSize.value = Math.max(12, fontSize.value - 1)
  localStorage.setItem('docs_font_size', String(fontSize.value))
}
function resetZoom() {
  fontSize.value = 15
  localStorage.setItem('docs_font_size', String(fontSize.value))
}

onMounted(async () => {
  await loadDocs()
})

onBeforeUnmount(() => {
  // 清理
  doc.value = null
  toc.value = []
})
</script>

<style>
/* ============================================================
   markdown-body 样式（非 scoped，因为 v-html 渲染的内容不受 scoped 影响）
   ============================================================ */
.markdown-body {
  max-width: 960px;
  margin: 0 auto;
  padding: 24px 32px 80px;
  color: var(--el-text-color-primary);
  line-height: 1.7;
  word-wrap: break-word;
}

.markdown-body h1,
.markdown-body h2,
.markdown-body h3,
.markdown-body h4 {
  margin-top: 24px;
  margin-bottom: 12px;
  font-weight: 600;
  color: var(--el-text-color-primary);
  line-height: 1.3;
  scroll-margin-top: 20px;
}

.markdown-body h1 { font-size: 1.8em; margin-top: 0; padding-bottom: 8px; border-bottom: 1px solid var(--el-border-color-lighter); }
.markdown-body h2 { font-size: 1.45em; border-bottom: 1px solid var(--el-border-color-lighter); padding-bottom: 6px; }
.markdown-body h3 { font-size: 1.2em; }
.markdown-body h4 { font-size: 1.05em; }

.markdown-body p { margin: 8px 0; }
.markdown-body ul, .markdown-body ol { padding-left: 24px; margin: 8px 0; }
.markdown-body li { margin: 4px 0; }

.markdown-body a {
  color: var(--el-color-primary);
  text-decoration: none;
}
.markdown-body a:hover { text-decoration: underline; }

.markdown-body blockquote {
  margin: 16px 0;
  padding: 12px 16px;
  border-left: 4px solid var(--el-color-primary-light-5);
  background: var(--el-color-primary-light-9);
  border-radius: 0 4px 4px 0;
  color: var(--el-text-color-regular);
}
.markdown-body blockquote p { margin: 4px 0; }

.markdown-body table {
  display: block;
  overflow-x: auto;
  border-collapse: collapse;
  width: 100%;
  margin: 16px 0;
  font-size: 0.95em;
}
.markdown-body table th,
.markdown-body table td {
  border: 1px solid var(--el-border-color-lighter);
  padding: 8px 12px;
  text-align: left;
  white-space: normal;
  word-break: break-word;
}
.markdown-body table th {
  background: var(--el-fill-color-light);
  font-weight: 600;
}
.markdown-body table tr:nth-child(even) {
  background: var(--el-fill-color-lighter);
}

.markdown-body code {
  background: var(--el-fill-color-light);
  color: #d63384;
  padding: 2px 6px;
  border-radius: 4px;
  font-size: 0.92em;
  font-family: 'SF Mono', 'Fira Code', 'Fira Mono', 'Roboto Mono', monospace;
}

.markdown-body pre {
  margin: 16px 0;
  border-radius: 6px;
  overflow: hidden;
  position: relative;
}

.markdown-body .code-copy-btn {
  position: absolute;
  top: 8px;
  right: 8px;
  z-index: 2;
  padding: 2px 10px;
  font-size: 12px;
  line-height: 1.6;
  color: var(--el-text-color-secondary);
  background: rgba(255, 255, 255, 0.08);
  border: 1px solid rgba(255, 255, 255, 0.16);
  border-radius: 4px;
  cursor: pointer;
  opacity: 0;
  transition: opacity 0.15s;
  font-family: var(--el-font-family);
}
.markdown-body pre:hover .code-copy-btn {
  opacity: 1;
}
.markdown-body pre .code-copy-btn:hover {
  color: #fff;
  background: rgba(255, 255, 255, 0.16);
}
.markdown-body pre code {
  display: block;
  padding: 16px;
  overflow-x: auto;
  background: #1e1e1e;
  color: #d4d4d4;
  font-size: 0.9em;
  line-height: 1.5;
  tab-size: 2;
}

.markdown-body img {
  max-width: 100%;
  border-radius: 4px;
}

.markdown-body hr {
  border: none;
  border-top: 1px solid var(--el-border-color-lighter);
  margin: 24px 0;
}

.markdown-body :not(pre) > code {
  background: var(--el-fill-color-light);
  padding: 2px 6px;
  border-radius: 4px;
  font-size: 0.92em;
  color: #d63384;
}

/* 暗色主题适配 */
html.dark .markdown-body code {
  background: var(--el-fill-color-light);
  color: var(--el-color-primary-light-5);
}
html.dark .markdown-body table th {
  background: #2d2d2d;
}
html.dark .markdown-body table tr:nth-child(even) {
  background: #252525;
}
html.dark .markdown-body blockquote {
  background: #1e2a3a;
  border-left-color: #409eff;
}
</style>

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
  flex-shrink: 0;
}

.docs-cat-select { width: 170px; }
.docs-select {
  width: 360px;
}

.docs-toolbar-right {
  display: flex;
  align-items: center;
}

/* ============================================================
   主内容区：flex 布局，侧边栏 + 正文
   ============================================================ */
.docs-body {
  display: flex;
  flex: 1;
  overflow: hidden;
}

/* 侧边栏在左（默认） */
.docs-body.sidebar--left {
  flex-direction: row;
}

/* 侧边栏在右 */
.docs-body.sidebar--right {
  flex-direction: row-reverse;
}

/* ============================================================
   TOC 侧边栏
   ============================================================ */
.docs-sidebar {
  width: 240px;
  flex-shrink: 0;
  overflow-y: auto;
  background: var(--el-bg-color);
  border-right: 1px solid var(--el-border-color-lighter);
  padding: 20px 0;
}
.docs-body.sidebar--right .docs-sidebar {
  border-right: none;
  border-left: 1px solid var(--el-border-color-lighter);
}

.toc-title {
  padding: 0 16px 12px;
  font-size: 13px;
  font-weight: 600;
  color: var(--el-text-color-secondary);
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

.toc-nav {
  display: flex;
  flex-direction: column;
}

.toc-row {
  display: flex;
  align-items: flex-start;
  border-left: 3px solid transparent;
  transition: background 0.15s;
  position: relative;
}
.toc-row:hover {
  background: var(--el-color-primary-light-9);
}
.toc-row--active {
  background: var(--el-color-primary-light-9);
  border-left-color: var(--el-color-primary);
}
.toc-row--active .toc-item {
  color: var(--el-color-primary);
  font-weight: 500;
}

.toc-caret {
  flex-shrink: 0;
  width: 16px;
  text-align: center;
  color: var(--el-text-color-secondary);
  cursor: pointer;
  padding-top: 6px;
  font-size: 10px;
  transition: transform 0.15s;
  user-select: none;
}
.toc-caret--open {
  transform: rotate(90deg);
}

.toc-item {
  flex: 1;
  min-width: 0;
  display: block;
  padding: 5px 12px;
  color: var(--el-text-color-regular);
  text-decoration: none;
  font-size: 13px;
  line-height: 1.4;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  cursor: pointer;
}

.toc-item:hover {
  color: var(--el-color-primary);
}

.toc-h2 { padding-left: 4px; }
.toc-h3 { padding-left: 20px; }
.toc-h4 { padding-left: 36px; }

.toc-empty {
  padding: 16px;
  color: var(--el-text-color-placeholder);
  font-size: 13px;
  text-align: center;
}

/* ============================================================
   文档正文
   ============================================================ */
.docs-content {
  flex: 1;
  overflow-y: auto;
  background: var(--el-bg-color);
}

.docs-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 100%;
  gap: 16px;
  color: var(--el-text-color-placeholder);
}

/* 阅读模式：隐藏 TOC 侧边栏 */
.docs-body--reading .docs-sidebar {
  display: none;
}

/* 加载失败提示 */
.docs-error {
  margin: 16px 24px 0;
}

/* 文档元信息条 */
.doc-meta {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
  max-width: 960px;
  margin: 16px auto 0;
  padding: 0 32px;
  color: var(--el-text-color-secondary);
  font-size: 13px;
}
.doc-meta__group {
  display: inline-block;
  padding: 2px 10px;
  border-radius: 999px;
  background: var(--el-color-primary-light-9);
  color: var(--el-color-primary);
  font-weight: 500;
  font-size: 12px;
}
.doc-meta__summary {
  flex: 1;
  min-width: 200px;
  color: var(--el-text-color-regular);
}
.doc-meta__count {
  white-space: nowrap;
  font-variant-numeric: tabular-nums;
}

/* 上/下一篇导航 */
.doc-nav {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  max-width: 960px;
  margin: 0 auto;
  padding: 24px 32px 40px;
  font-size: 13px;
}
.doc-nav :deep(.el-button + .el-button) {
  margin-left: 0;
}
.doc-nav__placeholder {
  flex: 1;
}
</style>