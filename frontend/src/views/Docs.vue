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

    <!-- 主内容区域：侧边栏 + 正文 -->
    <div class="docs-body" :class="`sidebar--${sidebarPos}`">
      <!-- TOC 侧边栏 -->
      <aside class="docs-sidebar">
        <div class="toc-title">目录</div>
        <nav class="toc-nav">
          <a
            v-for="item in toc"
            :key="item.id"
            :href="'#' + item.id"
            :class="['toc-item', `toc-h${item.level}`, { 'toc-item--active': activeHeading === item.id }]"
            @click.prevent="scrollToHeading(item.id)"
          >{{ item.text }}</a>
        </nav>
        <div v-if="!toc.length" class="toc-empty">无标题结构</div>
      </aside>

      <!-- 文档正文 -->
      <main class="docs-content" ref="contentRef" :style="{ fontSize: fontSize + 'px' }" @scroll="onContentScroll">
        <div v-if="!doc" class="docs-empty">
          <el-icon :size="48" color="var(--el-text-color-placeholder)"><Document /></el-icon>
          <p>请从上方选择一篇文档</p>
        </div>
        <div v-else class="markdown-body" v-html="renderedHtml" />
      </main>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onBeforeUnmount, watch, nextTick } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ZoomIn, ZoomOut, Document } from '@element-plus/icons-vue'
import { listDocs as fetchDocList, getDoc } from '@/api/docs'
import MarkdownIt from 'markdown-it'
import hljs from 'highlight.js'
import 'highlight.js/styles/github-dark.css'

const route = useRoute()
const router = useRouter()

const docs = ref([])
const doc = ref(null)
const currentSlug = ref('')
const sidebarPos = ref(localStorage.getItem('docs_sidebar_pos') || 'right')
const fontSize = ref(parseInt(localStorage.getItem('docs_font_size') || '15', 10))
const fontSizeLabel = computed(() => `${fontSize.value}px`)
const contentRef = ref(null)
const toc = ref([])
const activeHeading = ref('')
const loading = ref(false)

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

    const match = /^(#{1,3})\s+(.+)$/.exec(line)
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
  loading.value = true
  try {
    const res = await getDoc(slug)
    doc.value = res?.data || res
    toc.value = generateToc(doc.value?.content || '')
    await nextTick()
    scrollToHash()
  } catch (e) {
    console.error('[Docs] load doc failed:', e)
    doc.value = null
    toc.value = []
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
  white-space: nowrap;
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
  background: #2d2d2d;
  color: #ff79c6;
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

.toc-item {
  display: block;
  padding: 5px 16px;
  color: var(--el-text-color-regular);
  text-decoration: none;
  font-size: 13px;
  line-height: 1.4;
  border-left: 3px solid transparent;
  transition: all 0.15s;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  cursor: pointer;
}

.toc-item:hover {
  background: var(--el-color-primary-light-9);
  color: var(--el-color-primary);
}

.toc-item--active {
  color: var(--el-color-primary);
  border-left-color: var(--el-color-primary);
  background: var(--el-color-primary-light-9);
  font-weight: 500;
}

.toc-h2 { padding-left: 28px; }
.toc-h3 { padding-left: 40px; }

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
</style>