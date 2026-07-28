<template>
  <PageContainer>
    <div class="page-header mb-16">
      <h2 class="page-title">AI 因子挖掘</h2>
      <div class="page-actions">
        <el-tag :type="qlibAvailable ? 'success' : 'info'" size="small">qlib: {{ qlibAvailable ? '就绪' : '未安装' }}</el-tag>
        <el-button @click="loadTasks" :icon="Refresh">刷新</el-button>
      </div>
    </div>

    <SectionCard title="挖掘路线" class="mb-16">
      <div class="mining-grid">
        <div class="mining-card">
          <div class="mining-title">① LLM 生成因子</div>
          <div class="mining-desc">大模型产出 qlib 因子表达式，沙箱校验 + IC 评价后入库</div>
          <el-input-number v-model="llmN" :min="3" :max="30" size="small" />
          <el-button type="primary" @click="startLlm" :disabled="!qlibAvailable">启动</el-button>
        </div>
        <div class="mining-card">
          <div class="mining-title">② 符号回归</div>
          <div class="mining-desc">gplearn 遗传规划搜索因子表达式空间</div>
          <el-button type="primary" @click="startSymbolic" :disabled="!qlibAvailable">启动</el-button>
        </div>
        <div class="mining-card">
          <div class="mining-title">③ 文本因子</div>
          <div class="mining-desc">新闻情绪信号构造因子（复用 news_service + LLM）</div>
          <el-button type="primary" @click="startText" :disabled="!qlibAvailable">启动</el-button>
        </div>
        <div class="mining-card">
          <div class="mining-title">④ AutoML 组合</div>
          <div class="mining-desc">lightgbm 学习多因子最优组合</div>
          <el-select v-model="automlFactorIds" multiple filterable placeholder="选择因子" size="small" style="width:100%">
            <el-option v-for="f in factorOptions" :key="f.id" :label="f.name" :value="f.id" />
          </el-select>
          <el-button type="primary" @click="startAutoml" :disabled="!qlibAvailable || !automlFactorIds.length">启动</el-button>
        </div>
      </div>
    </SectionCard>

    <SectionCard title="挖掘任务">
      <el-table :data="tasks" size="small" stripe empty-text="暂无挖掘任务" max-height="520">
        <el-table-column prop="id" label="ID" width="60" align="center" />
        <el-table-column label="类型" width="100" align="center">
          <template #default="{row}">
            <el-tag size="small" :type="typeTag(row.type)">{{ typeLabel(row.type) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="100" align="center">
          <template #default="{row}">
            <el-tag size="small" :type="statusTag(row.status)">{{ statusLabel(row.status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="候选/通过" width="110" align="center">
          <template #default="{row}">{{ row.candidates_generated ?? 0 }} / {{ row.candidates_passed ?? 0 }}</template>
        </el-table-column>
        <el-table-column prop="best_ic" label="最佳IC" width="90" align="center" />
        <el-table-column label="产出因子" min-width="140">
          <template #default="{row}">{{ row.result_factor_ids?.length ? row.result_factor_ids.join(', ') : '--' }}</template>
        </el-table-column>
        <el-table-column prop="error" label="错误" min-width="160" show-overflow-tooltip />
        <el-table-column label="时间" width="150" align="center">
          <template #default="{row}">{{ row.finished_at || row.started_at || row.created_at || '--' }}</template>
        </el-table-column>
      </el-table>
    </SectionCard>
  </PageContainer>
</template>

<script setup>
import { ref, onMounted, onBeforeUnmount } from 'vue'
import { ElMessage } from 'element-plus'
import { Refresh } from '@element-plus/icons-vue'
import PageContainer from '@/components/common/PageContainer.vue'
import SectionCard from '@/components/common/SectionCard.vue'
import { mineLlm, mineSymbolic, mineText, mineAutoml, listMiningTasks } from '@/api/mining'
import { listFactors } from '@/api/factor'
import { getQlibStatus } from '@/api/quant'

const tasks = ref([])
const factorOptions = ref([])
const qlibAvailable = ref(false)
const llmN = ref(10)
const automlFactorIds = ref([])
let pollTimer = null

const typeLabel = { llm: 'LLM', symbolic: '符号回归', text: '文本', automl: 'AutoML' }
const typeTag = (t) => ({ llm: 'success', symbolic: 'warning', text: 'info', automl: 'danger' }[t] || '')
const statusLabel = { pending: '等待', running: '运行中', done: '完成', failed: '失败' }
const statusTag = (s) => ({ pending: 'info', running: 'warning', done: 'success', failed: 'danger' }[s] || '')

async function loadTasks() {
  try {
    const data = await listMiningTasks({ limit: 50 })
    tasks.value = data?.items || []
  } catch {}
}

async function loadFactors() {
  try {
    const data = await listFactors({ status: 'active', limit: 200 })
    factorOptions.value = data?.items || []
  } catch {}
}

async function loadQlib() {
  try { const data = await getQlibStatus(); qlibAvailable.value = data?.available || false } catch {}
}

async function startLlm() {
  try { const data = await mineLlm({ n_candidates: llmN }); ElMessage.success(`LLM 挖掘任务 #${data.task_id} 已提交`); loadTasks() } catch {}
}

async function startSymbolic() {
  try { const data = await mineSymbolic(); ElMessage.success(`符号回归任务 #${data.task_id} 已提交`); loadTasks() } catch {}
}

async function startText() {
  try { const data = await mineText(); ElMessage.success(`文本因子任务 #${data.task_id} 已提交`); loadTasks() } catch {}
}

async function startAutoml() {
  try { const data = await mineAutoml({ factor_ids: automlFactorIds.value }); ElMessage.success(`AutoML 任务 #${data.task_id} 已提交`); loadTasks() } catch {}
}

onMounted(() => {
  loadTasks(); loadFactors(); loadQlib()
  pollTimer = setInterval(loadTasks, 5000)
})
onBeforeUnmount(() => { if (pollTimer) clearInterval(pollTimer) })
</script>

<style scoped lang="scss">
.page-header { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: var(--space-md); animation: fadeInUp 0.5s var(--ease-out-expo); }
.page-title { font-size: var(--font-size-2xl); font-weight: 700; }
.page-actions { display: flex; align-items: center; gap: var(--space-sm); }
.mining-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: var(--space-md); }
.mining-card { display: flex; flex-direction: column; gap: var(--space-sm); padding: var(--space-md); border: 1px solid var(--border-color, #e4e7ed); border-radius: var(--radius-sm); }
.mining-title { font-size: var(--font-size-lg); font-weight: 600; }
.mining-desc { font-size: var(--font-size-base); color: var(--text-secondary); min-height: 40px; }
</style>
