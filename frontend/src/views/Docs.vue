<template>
  <PageContainer>
    <div class="docs-header">
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
    <el-divider />
    <div v-if="loading" class="docs-loading">
      <el-icon class="is-loading"><Loading /></el-icon>
      <span style="margin-left: 8px">加载中...</span>
    </div>
    <el-empty v-else-if="!currentDoc" description="暂无文档" />
    <article v-else class="markdown-body" v-html="renderedContent" />
  </PageContainer>
</template>

<script setup>
import { ref, computed, onMounted, watch } from 'vue'
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
import PageContainer from '@/components/common/PageContainer.vue'

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
  } catch (err) {
    console.error('[Docs] 内容加载失败', err)
    currentDoc.value = null
  } finally {
    loading.value = false
  }
}

function handleSelect(slug) {
  if (!slug || slug === route.params.slug) return
  router.push({ name: 'Docs', params: { slug } })
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
</script>

<style scoped lang="scss">
.docs-header {
  display: flex;
  align-items: center;
  gap: 16px;
  flex-wrap: wrap;
  padding: 8px 0;
  animation: fadeInUp 0.5s var(--ease-out-expo);
}

.docs-select {
  width: 300px;
}

.docs-summary {
  font-size: 13px;
  color: var(--el-text-color-secondary);
}

.docs-loading {
  display: flex;
  justify-content: center;
  align-items: center;
  padding: 80px 0;
  color: var(--el-text-color-secondary);
}

.markdown-body {
  max-width: 900px;
  margin: 0 auto;
  line-height: 1.6;
  color: #24292e;
  word-wrap: break-word;
  padding: 0 16px 48px;
}

.markdown-body :deep(h1),
.markdown-body :deep(h2),
.markdown-body :deep(h3),
.markdown-body :deep(h4) {
  margin-top: 24px;
  margin-bottom: 16px;
  font-weight: 600;
  line-height: 1.25;
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
</style>
