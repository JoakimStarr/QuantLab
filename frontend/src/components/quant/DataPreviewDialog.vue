<template>
  <el-dialog v-model="visible" title="数据预览" width="80%" :close-on-click-modal="true">
    <div class="preview-toolbar">
      <el-input
        v-model="codeInput"
        placeholder="输入股票代码如 sh600000 或股票池 csi300"
        style="width: 260px"
        @keyup.enter="load(codeInput)"
      />
      <el-button @click="load(codeInput)" size="small">查询</el-button>
      <el-button @click="load()" size="small">最近数据</el-button>
      <span class="preview-hint-inline">
        {{ previewCode ? '当前: ' + previewCode : '最近数据' }}
        ({{ rows.length }} 条)
      </span>
    </div>
    <el-table :data="rows" size="small" stripe max-height="500" v-loading="loading">
      <el-table-column
        v-for="col in columns"
        :key="col"
        :prop="col"
        :label="col"
        min-width="120"
        show-overflow-tooltip
      />
      <template #empty>
        <div class="preview-empty">
          <p v-if="!loading">暂无数据，请检查股票代码是否正确，或尝试输入其他代码</p>
          <p v-else>加载中...</p>
        </div>
      </template>
    </el-table>
  </el-dialog>
</template>

<script setup>
import { ref, computed } from 'vue'
import { ElMessage } from 'element-plus/es/components/message/index'
import { getDataPreview } from '@/api/quant'

const visible = ref(false)
const rows = ref([])
const loading = ref(false)
const code = ref('')
const codeInput = ref('')

const columns = computed(() => {
  if (!rows.value.length) return []
  return Object.keys(rows.value[0])
})

async function load(c) {
  code.value = c || ''
  codeInput.value = c || ''
  visible.value = true
  loading.value = true
  try {
    const data = await getDataPreview(c, 20)
    rows.value = data?.items || data || []
  } catch (e) {
    if (e !== 'cancel') ElMessage.error('数据预览加载失败')
    rows.value = []
  } finally {
    loading.value = false
  }
}

function open(c) {
  load(c || '')
}

defineExpose({ open })
</script>

<style scoped lang="scss">
.preview-toolbar {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
  margin-bottom: 12px;
}

.preview-hint-inline {
  font-size: var(--font-size-sm);
  color: var(--text-secondary);
  margin-left: auto;
}

.preview-empty {
  color: var(--text-tertiary);
  font-size: var(--font-size-sm);
}
</style>