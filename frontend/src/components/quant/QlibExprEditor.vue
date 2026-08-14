<template>
  <div class="qlib-expr-editor">
    <div ref="host" class="qlib-expr-editor__host"></div>
    <div v-if="inlineError" class="qlib-expr-editor__error">{{ inlineError }}</div>
  </div>
</template>

<script setup>
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { EditorState } from '@codemirror/state'
import { EditorView, keymap, placeholder } from '@codemirror/view'
import {
  HighlightStyle,
  StreamLanguage,
  bracketMatching,
  syntaxHighlighting,
} from '@codemirror/language'
import { tags as t } from '@lezer/highlight'
import {
  autocompletion,
  closeBrackets,
  closeBracketsKeymap,
  completionKeymap,
} from '@codemirror/autocomplete'
import { defaultKeymap, history, historyKeymap } from '@codemirror/commands'
import { getExpressionSchema } from '@/api/factor'

const props = defineProps({
  modelValue: { type: String, default: '' },
  placeholderText: { type: String, default: 'qlib 表达式，如 $close / Ref($close, 20) - 1' },
})
const emit = defineEmits(['update:modelValue'])

const host = ref(null)
const inlineError = ref('')
let view = null

// --- schema 模块级缓存：多次打开对话框只拉一次 ---
let schemaCache = null
let schemaPromise = null
function loadSchema() {
  if (schemaCache) return Promise.resolve(schemaCache)
  if (!schemaPromise) {
    schemaPromise = getExpressionSchema()
      .then((res) => {
        // 注意：axios 拦截器已解包 ApiResponse，res 就是 {ops, fields} 本体
        schemaCache = res ?? null
        return schemaCache
      })
      .catch(() => {
        schemaCache = null
        return null
      })
  }
  return schemaPromise
}

// --- 内联校验：对齐后端 validate_expression 的两条高频规则 ---
function checkExpression(expr) {
  if (!expr) return ''
  const opens = (expr.match(/\(/g) || []).length
  const closes = (expr.match(/\)/g) || []).length
  if (opens > closes) return '括号未闭合'
  if (opens < closes) return '存在多余的右括号 )'
  if (/Ref\s*\([^)]*,\s*-\d/.test(expr)) return '禁止负数 Ref（未来数据 → look-ahead bias）'
  return ''
}

// --- 语法高亮：$字段 / 算子 / 数字 / 运算符分色（颜色走 CSS 变量适配明暗主题） ---
const qlibHighlight = syntaxHighlighting(
  HighlightStyle.define([
    { tag: t.variableName, color: 'var(--qlib-expr-field, #d19a66)' },
    { tag: t.function, color: 'var(--qlib-expr-func, #4078c0)' },
    { tag: t.atom, color: 'var(--qlib-expr-func, #4078c0)' },
    { tag: t.number, color: 'var(--qlib-expr-num, #50a14f)' },
    { tag: t.operator, color: 'var(--qlib-expr-op, #a626a4)' },
    { tag: t.paren, color: 'var(--qlib-expr-paren, #9d9d9d)' },
    { tag: t.separator, color: 'var(--qlib-expr-paren, #9d9d9d)' },
  ]),
)

// qlib 表达式近似 Python 语法（$field 用 variableName 单独着色）
const qlibLang = StreamLanguage.define({
  token(stream) {
    if (stream.eatSpace()) return null
    if (stream.match(/^\$[A-Za-z_][A-Za-z0-9_]*/)) return 'variableName'
    if (stream.match(/^\d+(\.\d+)?([eE][+-]?\d+)?/)) return 'number'
    if (stream.match(/^[A-Za-z_][A-Za-z0-9_]*/)) {
      // qlib 算子全部大写开头，字段/变量小写 → 用大小写区分函数与变量
      const word = stream.string.slice(stream.start, stream.pos)
      return /^[A-Z]/.test(word) ? 'function' : 'atom'
    }
    if (stream.match(/^[()]/)) return 'paren'
    if (stream.match(/^,/)) return 'separator'
    if (stream.match(/^[+\-*/%^<>=]/)) return 'operator'
    stream.next()
    return null
  },
})

const editorTheme = EditorView.theme({
  '&': {
    fontSize: '13px',
    width: '100%',
    boxSizing: 'border-box',
    backgroundColor: 'var(--el-fill-color-blank, #fff)',
    // 与 el-input 一致：用 inset box-shadow 画边框（1px、圆角 4px），focus 换主色
    boxShadow: '0 0 0 1px var(--el-border-color, #dcdfe6) inset',
    borderRadius: '4px',
    transition: 'box-shadow .2s',
    cursor: 'text',
  },
  '&:hover': {
    boxShadow: '0 0 0 1px var(--el-border-color-hover, #c0c4cc) inset',
  },
  '&.cm-focused': {
    boxShadow: '0 0 0 1px var(--el-color-primary, #409eff) inset',
    outline: 'none',
  },
  '.cm-content': {
    fontFamily: "'JetBrains Mono', 'Fira Code', ui-monospace, SFMono-Regular, Consolas, monospace",
    fontSize: '13px',
    lineHeight: '1.7',
    padding: '7px 11px',
    minHeight: '64px',
    caretColor: 'var(--el-text-color-primary, #303133)',
  },
  '.cm-scroller': { maxHeight: '180px' },
  '.cm-line': { padding: '0 2px' },
  '.cm-placeholder': { color: 'var(--el-text-color-placeholder, #a8abb2)' },
  '.cm-cursor': { borderLeftColor: 'var(--el-text-color-primary, #303133)' },
  '.cm-selectionBackground': { backgroundColor: 'var(--el-color-primary-light-7, #d4e7fb) !important' },
  '.cm-tooltip': { fontSize: '12px' },
})

function buildExtensions(schema) {
  const fieldOptions = (schema?.fields || []).map((f) => ({
    label: f.name,
    type: 'variable',
    detail: f.category || '',
    info: f.description || undefined,
    boost: 100,
  }))
  const opOptions = (schema?.ops || []).map((o) => ({
    label: o.name,
    type: 'function',
    info: o.description || undefined,
    boost: 90,
    apply: `${o.name}(`,
  }))

  return [
    qlibLang,
    qlibHighlight,
    bracketMatching(),
    closeBrackets(),
    history(),
    editorTheme,
    placeholder(props.placeholderText),
    EditorView.lineWrapping,
    autocompletion({
      activateOnTyping: true,
      override: [
        (context) => {
          // $ 开头 → 字段补全
          const dollar = context.matchBefore(/\$[A-Za-z_]*$/)
          if (dollar) {
            return { from: dollar.from, options: fieldOptions, validFor: /^\$[A-Za-z_]*$/ }
          }
          // 普通标识符 → 算子补全
          const ident = context.matchBefore(/[A-Za-z_][A-Za-z0-9_]*$/)
          if (ident) {
            return { from: ident.from, options: opOptions, validFor: /^[A-Za-z_][A-Za-z0-9_]*$/ }
          }
          return null
        },
      ],
    }),
    keymap.of([...closeBracketsKeymap, ...completionKeymap, ...historyKeymap, ...defaultKeymap]),
    EditorView.updateListener.of((update) => {
      if (update.docChanged) {
        const val = update.state.doc.toString()
        emit('update:modelValue', val)
        inlineError.value = checkExpression(val)
      }
    }),
  ]
}

function createView(schema) {
  view = new EditorView({
    state: EditorState.create({
      doc: props.modelValue,
      extensions: buildExtensions(schema),
    }),
    parent: host.value,
  })
}

onMounted(async () => {
  const schema = await loadSchema()
  createView(schema)
})

watch(
  () => props.modelValue,
  (val) => {
    if (view && val !== view.state.doc.toString()) {
      view.dispatch({ changes: { from: 0, to: view.state.doc.length, insert: val ?? '' } })
    }
  },
)

onBeforeUnmount(() => {
  view?.destroy()
  view = null
})
</script>

<style scoped>
.qlib-expr-editor {
  width: 100%;
}
.qlib-expr-editor__host {
  width: 100%;
}
.qlib-expr-editor__error {
  margin-top: 4px;
  font-size: 12px;
  line-height: 1.4;
  color: var(--el-color-danger, #f56c6c);
}
</style>

<style>
/* 暗色主题下表达式 token 配色（fallback 已内置亮色值） */
html.dark .qlib-expr-editor {
  --qlib-expr-field: #e5c07b;
  --qlib-expr-func: #61afef;
  --qlib-expr-num: #98c379;
  --qlib-expr-op: #c678dd;
  --qlib-expr-paren: #7f848e;
}
</style>
