<template>
  <el-select
    :model-value="modelValue"
    filterable
    remote
    allow-create
    reserve-keyword
    :placeholder="placeholder"
    :remote-method="debouncedQuery"
    :loading="loading"
    :clearable="clearable"
    style="width: 100%"
    @update:model-value="$emit('update:model-value', $event)"
  >
    <template #prefix v-if="$slots.prefix">
      <slot name="prefix" />
    </template>
    <el-option v-for="s in combinedOptions" :key="s.value" :label="s.label" :value="s.value">
      <span>{{ s.label }}</span>
      <span v-if="s.type" class="sym-type">{{ typeLabel(s.type) }}</span>
    </el-option>
  </el-select>
</template>

<script setup>
import { ref, computed } from 'vue'
import { searchStocks } from '@/api/quant'
import { debounce } from '@/utils/debounce'

const props = defineProps({
  modelValue: { type: String, default: '' },
  placeholder: { type: String, default: '输入名称/拼音/代码搜索' },
  debounceMs: { type: Number, default: 300 },
  limit: { type: Number, default: 10 },
  clearable: { type: Boolean, default: true },
  // 预置选项（用于回显默认选中项，远程搜索未触发前也可见）
  seedOptions: { type: Array, default: () => [] },
})

defineEmits(['update:model-value'])

const options = ref([])
const loading = ref(false)
let seq = 0

// 搜索中显示远程结果，否则显示预置选项（保证 modelValue 回显）
const combinedOptions = computed(() =>
  loading.value || options.value.length ? options.value : props.seedOptions
)

function typeLabel(t) {
  return { stock: '股票', index: '指数', etf: 'ETF' }[t] || ''
}

function query(query) {
  const q = (query || '').trim()
  const cur = ++seq
  // 空串不发请求：后端 q 必填(min_length=1)，空串会返回 422「参数校验失败」
  if (!q) {
    options.value = []
    loading.value = false
    return
  }
  loading.value = true
  searchStocks(q, props.limit)
    .then((res) => {
      if (cur !== seq) return // 丢弃过期结果
      options.value = (res?.items ?? []).map((s) => ({
        value: s.qlib_code || s.code,
        label: `${s.name} (${s.qlib_code || s.code})`,
        type: s.type,
      }))
    })
    .catch(() => {
      if (cur === seq) options.value = []
    })
    .finally(() => {
      if (cur === seq) loading.value = false
    })
}

// el-select remote-method 不内置 debounce，包一层避免每个键入字符都发请求
const debouncedQuery = debounce(query, props.debounceMs)
</script>

<style scoped>
.sym-type {
  float: right;
  margin-left: 12px;
  font-size: 12px;
  color: var(--text-tertiary, #8a9099);
}
</style>