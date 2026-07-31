<template>
  <div class="docs-layout">
    <!-- 顶部工具栏：文档切换 + 字号控制 -->
    <div class="docs-toolbar">
      <div class="docs-toolbar-left">
        <el-select
          v-model="currentSlug"
          @change="handleSelect"
          placeholder="选择文档"
          class="docs-select"
        >
          <el-option-group v-for="group in groupedDocs" :key="group.name" :label="group.name">
            <el-option v-for="doc in group.docs" :key="doc.slug" :label="doc.title" :value="doc.slug" />
          </el-option-group>
        </el-select>
        <span v-if="currentDoc?.summary" class="docs-summary">{{ currentDoc.summary }}</span>
      </div>
      <div class="docs-toolbar-right">
        <el-tooltip :content="tocPosition === 'right' ? '目录靠左' : '目录靠右'" placement="bottom">
          <el-button size="small" circle @click="toggleTocPosition">
            <el-icon>
              <ArrowLeft v-if="tocPosition === 'right'" />
              <ArrowRight v-else />
            </el-icon>
          </el-button>
        </el-tooltip>
        <span class="font-size-label">{{ fontSize }}px</span>
        <el-button-group>
          <el-button size="small" :disabled="fontSize <= 12" @click="changeFontSize(-2)">A-</el-button>
          <el-button size="small" :disabled="fontSize >= 22" @click="changeFontSize(2)">A+</el-button>
        </el-button-group>
      </div>
    </div>

    <el-divider />

    <div v-if="loading" class="docs-loading">
      <el-icon class="is-loading"><Loading /></el-icon>
      <span style="margin-left: 8px">加载中...</span>
    </div>
    <el-empty v-else-if="!currentDoc" description="暂无文档" />
    <div v-else class="docs-main" :class="{ 'toc-left': tocPosition === 'left' }">
      <article class="markdown-body" v-html="renderedContent" />
      <!-- 浮动目录：仅 h2/h3，h1 作为文档标题不进目录；位置由 tocPosition 决定 -->
      <aside v-if="toc.length > 0" class="docs-toc">
        <div class="docs-toc-title">目录</div>
        <ul class="docs-toc-list">
          <li v-for="item in toc" :key="item.id" :class="{ active: activeId === item.id }">
            <a class="toc-h2" href="javascript:void(0)" @click.prevent="scrollToHeading(item.id)">{{ item.text }}</a>
            <ul v-if="item.children?.length">
              <li v-for="child in item.children" :key="child.id" :class="{ active: activeId === child.id }">
                <a class="toc-h3" href="javascript:void(0)" @click.prevent="scrollToHeading(child.id)">{{ child.text }}</a>
              </li>
            </ul>
          </li>
        </ul>
      </aside>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, watch, nextTick } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import MarkdownIt from 'markdown-it'
import hljs from 'highlight.js/lib/core'
import javascript from 'highlight.js/lib/languages/javascript'
import python from 'highlight.js/lib/languages/python'
import json from 'highlight.js/lib/languages/json'
import bash from 'highlight.js/lib/languages/bash'
import xml from 'highlight.js/lib/languages/xml'
import css from 'highlight.js/lib/languages/css'
import scss from 'highlight.js/lib/languages/scss'
import yaml from 'highlight.js/lib/languages/yaml'
import sql from 'highlight.js/lib/languages/sql'
import 'highlight.js/styles/github.css'
import { listDocs, getDoc } from '@/api/docs'

// 仅注册常用语言，未识别语言走 md.utils.escapeHtml 兜底（避免 highlight 抛错）
hljs.registerLanguage('javascript', javascript)
hljs.registerLanguage('js', javascript)
hljs.registerLanguage('python', python)
hljs.registerLanguage('py', python)
hljs.registerLanguage('json', json)
hljs.registerLanguage('bash', bash)
hljs.registerLanguage('sh', bash)
hljs.registerLanguage('shell', bash)
hljs.registerLanguage('xml', xml)
hljs.registerLanguage('html', xml)
hljs.registerLanguage('css', css)
hljs.registerLanguage('scss', scss)
hljs.registerLanguage('yaml', yaml)
hljs.registerLanguage('yml', yaml)
hljs.registerLanguage('sql', sql)

const md = new MarkdownIt({
  html: true,
  linkify: true,
  typographer: true,
  highlight(str, lang) {
    if (lang && hljs.getLanguage(lang)) {
      try {
        return '<pre class="hljs"><code>' +
          hljs.highlight(str, { language: lang, ignoreIllegals: true }).value +
          '</code></pre>'
      } catch (_) {
        // 兜底走默认
      }
    }
    return '<pre class="hljs"><code>' + md.utils.escapeHtml(str) + '</code></pre>'
  },
})

const route = useRoute()
const router = useRouter()

const docs = ref([])
const currentDoc = ref(null)
const currentSlug = ref('')
const loading = ref(false)

// 右侧目录（TOC）：仅含 h2 / h3，h1 作为文档标题不进目录
const toc = ref([]) // [{ id, text, level, children: [...] }]
const activeId = ref('')

// 字号控制：默认 16px，范围 12-22px，步进 2px，localStorage 持久化
const FONT_KEY = 'quantlab:docs:fontsize'
const fontSize = ref(parseInt(localStorage.getItem(FONT_KEY)) || 16)

function changeFontSize(delta) {
  const newSize = Math.max(12, Math.min(22, fontSize.value + delta))
  fontSize.value = newSize
  localStorage.setItem(FONT_KEY, String(newSize))
}

// 通过 CSS 变量驱动正文字号，不影响顶部工具栏与目录
watch(fontSize, (val) => {
  document.documentElement.style.setProperty('--docs-font-size', `${val}px`)
}, { immediate: true })

// 目录位置：left / right，默认 right（保持原行为），localStorage 持久化
const TOC_POS_KEY = 'quantlab:docs:toc-position'
const savedPos = localStorage.getItem(TOC_POS_KEY)
const tocPosition = ref(savedPos === 'left' || savedPos === 'right' ? savedPos : 'right')

function toggleTocPosition() {
  tocPosition.value = tocPosition.value === 'right' ? 'left' : 'right'
  localStorage.setItem(TOC_POS_KEY, tocPosition.value)
}

const groupedDocs = computed(() => {
  const groups = {}
  for (const doc of docs.value) {
    const g = doc.group || '默认'
    if (!groups[g]) groups[g] = []
    groups[g].push(doc)
  }
  return Object.entries(groups)
    .map(([name, list]) => ({ name, docs: list }))
    .sort((a, b) => {
      const aOrder = Math.min(...a.docs.map(d => d.order ?? 9999))
      const bOrder = Math.min(...b.docs.map(d => d.order ?? 9999))
      return aOrder - bOrder
    })
})

const renderedContent = computed(() => {
  if (!currentDoc.value?.content) return ''
  return md.render(currentDoc.value.content)
})

async function loadDocs() {
  try {
    const res = await listDocs()
    // 后端约定：data.data 已是解包 payload，结构 { docs: [...] } 或直接数组
    const list = Array.isArray(res) ? res : (res?.docs || [])
    docs.value = list
  } catch (err) {
    console.error('[Docs] 列表加载失败', err)
    docs.value = []
  }
}

async function loadDoc(slug) {
  if (!slug) return
  loading.value = true
  try {
    currentDoc.value = await getDoc(slug)
    // 滚动到顶部
    window.scrollTo({ top: 0 })
    // 文档切换后重新提取目录
    extractToc()
  } catch (err) {
    console.error('[Docs] 内容加载失败', err)
    currentDoc.value = null
    toc.value = []
  } finally {
    loading.value = false
  }
}

function handleSelect(slug) {
  if (!slug || slug === route.params.slug) return
  router.push({ name: 'Docs', params: { slug } })
}

// 从渲染后的 markdown 提取 h2/h3，构造层级目录
function extractToc() {
  nextTick(() => {
    const article = document.querySelector('.markdown-body')
    if (!article) { toc.value = []; return }
    const headings = article.querySelectorAll('h2, h3')
    const items = []
    let currentH2 = null
    const usedIds = {} // 记录每个 id 出现次数，保证锚点 id 唯一
    headings.forEach((h, idx) => {
      // 给每个标题补 id（用于锚点定位）
      if (!h.id) {
        let baseId = h.textContent.trim().toLowerCase()
          .replace(/[^\w\u4e00-\u9fa5]+/g, '-')
          .replace(/^-|-$/g, '')
        // 处理后为空（纯标点等）用索引兜底，避免空 id 导致 getElementById 失败
        if (!baseId) baseId = `heading-${idx}`
        // 重名标题加序号后缀，保证 DOM id 唯一
        if (usedIds[baseId]) {
          usedIds[baseId]++
          baseId = `${baseId}-${usedIds[baseId]}`
        } else {
          usedIds[baseId] = 1
        }
        h.id = baseId
      }
      const item = { id: h.id, text: h.textContent.trim(), level: parseInt(h.tagName[1]) }
      if (h.tagName === 'H2') {
        currentH2 = { ...item, children: [] }
        items.push(currentH2)
      } else if (h.tagName === 'H3' && currentH2) {
        currentH2.children.push(item)
      } else {
        // 没有父级 h2 的 h3 直接平铺
        items.push(item)
      }
    })
    toc.value = items
    setupScrollSpy()
  })
}

// 滚动监听：IntersectionObserver 高亮当前章节
let observer = null
// 点击 TOC 定位滚动期间暂停 observer，避免平滑滚动过程中 activeId 跳变闪烁
let scrollSpyPaused = false
function setupScrollSpy() {
  if (observer) observer.disconnect()
  observer = new IntersectionObserver((entries) => {
    // 滚动定位期间不更新高亮
    if (scrollSpyPaused) return
    // 找到当前在视口内的标题
    const visible = entries.filter(e => e.isIntersecting)
    if (visible.length > 0) {
      activeId.value = visible[0].target.id
    }
  }, { rootMargin: '-80px 0px -70% 0px' }) // 顶部偏移 80px，底部 70% 不可见才算离开

  nextTick(() => {
    document.querySelectorAll('.markdown-body h2, .markdown-body h3').forEach(h => {
      observer.observe(h)
    })
  })
}

function scrollToHeading(id) {
  const el = document.getElementById(id)
  if (!el) {
    // 找不到目标标题时给出告警，便于排查 id 生成问题
    console.warn('[Docs] scrollToHeading: 未找到目标标题, id =', id)
    return
  }
  // 平滑滚动期间暂停 observer，避免高亮跳变
  scrollSpyPaused = true
  const top = el.getBoundingClientRect().top + window.pageYOffset - 80 // 顶部留 80px 偏移
  window.scrollTo({ top, behavior: 'smooth' })
  activeId.value = id
  // 平滑滚动动画约 600-800ms，结束后恢复 observer
  window.setTimeout(() => { scrollSpyPaused = false }, 800)
}

onMounted(async () => {
  await loadDocs()
  const slug = route.params.slug || docs.value[0]?.slug
  if (slug) {
    currentSlug.value = slug
    if (!route.params.slug) {
      router.replace({ name: 'Docs', params: { slug } })
    }
    await loadDoc(slug)
  }
})

watch(() => route.params.slug, (newSlug) => {
  if (newSlug && newSlug !== currentSlug.value) {
    currentSlug.value = newSlug
    loadDoc(newSlug)
  }
})

// 组件卸载时断开观察者，避免内存泄漏
onUnmounted(() => {
  if (observer) observer.disconnect()
})
</script>

<style scoped lang="scss">
.docs-layout {
  max-width: 1400px;
  margin: 0 auto;
  padding: 24px 32px 48px;
}

.docs-toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 16px;
  flex-wrap: wrap;
  padding: 8px 0;
  animation: fadeInUp 0.5s var(--ease-out-expo, ease-out);
}

.docs-toolbar-left {
  display: flex;
  align-items: center;
  gap: 16px;
  flex-wrap: wrap;
}

.docs-toolbar-right {
  display: flex;
  align-items: center;
  gap: 8px;
}

.docs-select {
  width: 300px;
}

.docs-summary {
  font-size: 13px;
  color: var(--el-text-color-secondary);
}

.font-size-label {
  font-size: 13px;
  color: var(--el-text-color-secondary);
  min-width: 40px;
  text-align: right;
}

.docs-loading {
  display: flex;
  justify-content: center;
  align-items: center;
  padding: 80px 0;
  color: var(--el-text-color-secondary);
}

.docs-main {
  display: grid;
  grid-template-columns: 1fr 260px;
  gap: 32px;
  align-items: start;
}

// 目录靠左：翻转列宽顺序，并用 order 让 aside 落到第一列(260px)、article 落到第二列(1fr)
.docs-main.toc-left {
  grid-template-columns: 260px 1fr;

  .markdown-body {
    order: 2;
  }

  .docs-toc {
    order: 1;
  }
}

.markdown-body {
  max-width: 880px;
  margin: 0 auto;
  line-height: 1.6;
  color: #24292e;
  word-wrap: break-word;
  padding: 0 16px 48px;
  font-size: var(--docs-font-size, 16px);
}

.markdown-body :deep(h1),
.markdown-body :deep(h2),
.markdown-body :deep(h3),
.markdown-body :deep(h4) {
  margin-top: 24px;
  margin-bottom: 16px;
  font-weight: 600;
  line-height: 1.25;
  scroll-margin-top: 80px; // 锚点跳转时顶部留 80px
}

.markdown-body :deep(h1) {
  font-size: 2em;
  border-bottom: 1px solid #eaecef;
  padding-bottom: 0.3em;
}

.markdown-body :deep(h2) {
  font-size: 1.5em;
  border-bottom: 1px solid #eaecef;
  padding-bottom: 0.3em;
}

.markdown-body :deep(pre) {
  background: #f6f8fa;
  border-radius: 6px;
  padding: 16px;
  overflow: auto;
  font-size: 85%;
  line-height: 1.45;
}

.markdown-body :deep(code) {
  background: rgba(27, 31, 35, 0.05);
  border-radius: 3px;
  padding: 0.2em 0.4em;
  font-size: 85%;
}

.markdown-body :deep(pre code) {
  background: transparent;
  padding: 0;
}

.markdown-body :deep(table) {
  border-collapse: collapse;
  width: 100%;
  margin: 16px 0;
}

.markdown-body :deep(table th),
.markdown-body :deep(table td) {
  border: 1px solid #dfe2e5;
  padding: 6px 13px;
}

.markdown-body :deep(table tr:nth-child(2n)) {
  background: #f6f8fa;
}

.markdown-body :deep(blockquote) {
  border-left: 4px solid #dfe2e5;
  color: #6a737d;
  padding: 0 1em;
  margin: 16px 0;
}

.markdown-body :deep(a) {
  color: var(--primary, #409eff);
  text-decoration: none;

  &:hover {
    text-decoration: underline;
  }
}

.markdown-body :deep(img) {
  max-width: 100%;
}

// 右侧浮动目录
.docs-toc {
  position: sticky;
  top: 80px;
  max-height: calc(100vh - 120px);
  overflow-y: auto;
  padding: 0 4px;
}

.docs-toc-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--el-text-color-secondary);
  margin-bottom: 12px;
  letter-spacing: 0.5px;
}

.docs-toc-list {
  list-style: none;
  padding: 0;
  margin: 0;

  // 嵌套 h3 列表
  ul {
    list-style: none;
    padding-left: 14px;
    margin: 0;
  }

  li {
    margin: 0;
  }

  a {
    display: block;
    padding: 6px 8px;
    font-size: 13px;
    line-height: 1.5;
    color: var(--el-text-color-secondary);
    cursor: pointer;
    border-left: 2px solid transparent;
    transition: color 0.2s, border-color 0.2s, background 0.2s;

    &:hover {
      color: var(--primary, #409eff);
      background: var(--el-fill-color-light, #f5f7fa);
    }
  }

  .toc-h3 {
    font-size: 12px;
  }

  // 当前章节高亮
  li.active > a {
    color: var(--primary, #409eff);
    font-weight: 600;
    border-left-color: var(--primary, #409eff);
  }
}

// 窄屏隐藏 TOC，正文撑满
@media (max-width: 1200px) {
  // 需同时覆盖 toc-left（双类选择器优先级高于单类，否则左侧会残留 260px 空列）
  .docs-main,
  .docs-main.toc-left {
    grid-template-columns: 1fr;

    .markdown-body,
    .docs-toc {
      order: 0;
    }
  }

  .docs-toc {
    display: none;
  }

  .markdown-body {
    margin: 0 auto;
  }
}
</style>
