// 用 @vue/compiler-sfc 真实解析每个 .vue，捕获模板语法错误
// 用法: node vue_parse_check.mjs
// 集成到 package.json 的 scripts.check:vue
import { parse } from '@vue/compiler-sfc'
import fs from 'node:fs'
import path from 'node:path'

const ROOT = 'src'
let total = 0, errored = 0
const errs = []

function walk(dir) {
  for (const name of fs.readdirSync(dir)) {
    const full = path.join(dir, name)
    const st = fs.statSync(full)
    if (st.isDirectory()) walk(full)
    else if (name.endsWith('.vue')) check(full)
  }
}

function check(file) {
  total++
  const src = fs.readFileSync(file, 'utf8')
  const { descriptor, errors } = parse(src, { filename: file })
  if (errors && errors.length) {
    for (const e of errors) {
      const loc = e.loc?.start ? `:${e.loc.start.line}:${e.loc.start.column}` : ''
      errs.push(`${file}${loc}  [${e.code || 'ERR'}] ${e.message}`)
    }
    errored++
  }
}

walk(ROOT)
console.log(`扫描 ${total} 个 .vue 文件`)
if (errs.length) {
  console.log(`\n发现 ${errs.length} 个解析错误（${errored} 个文件）：\n`)
  for (const e of errs) console.log('  ' + e)
  process.exit(1)
} else {
  console.log('✓ 全部通过模板解析检查')
}
