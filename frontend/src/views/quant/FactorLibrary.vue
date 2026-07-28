<template>
  <PageContainer>
    <div class="page-header mb-16">
      <h2 class="page-title">因子库</h2>
      <div class="page-actions">
        <el-tag :type="qlibAvailable ? 'success' : 'info'" size="small">
          qlib: {{ qlibAvailable ? '已就绪' : '未安装' }}
        </el-tag>
        <el-button @click="syncData" :loading="syncing" :disabled="!qlibAvailable">同步股票数据</el-button>
        <el-button @click="loadFactors" :icon="Refresh">刷新</el-button>
        <el-button type="success" @click="seedBuiltin" :loading="seeding">种子内置因子</el-button>
        <el-button type="primary" @click="showAdd = true" :icon="Plus">新增因子</el-button>
      </div>
    </div>

    <SectionCard class="mb-16">
      <div class="filter-bar">
        <el-select v-model="filter.category" placeholder="因子类别" clearable @change="loadFactors" style="width:140px">
          <el-option label="内置" value="builtin" />
          <el-option label="LLM生成" value="llm" />
          <el-option label="符号回归" value="symbolic" />
          <el-option label="文本因子" value="text" />
          <el-option label="AutoML" value="automl" />
        </el-select>
        <el-select v-model="filter.sort_by" @change="loadFactors" style="width:140px">
          <el-option label="按IC排序" value="ic" />
          <el-option label="按RankIC" value="rank_ic" />
          <el-option label="按ICIR" value="icir" />
        </el-select>
        <span class="filter-meta">共 {{ factors.length }} 个因子</span>
      </div>
    </SectionCard>

    <SectionCard title="因子列表">
      <el-table :data="factors" size="small" stripe empty-text="暂无因子，点击「种子内置因子」开始" max-height="640">
        <el-table-column prop="name" label="因子名称" width="160" />
        <el-table-column label="类别" width="100" align="center">
          <template #default="{row}">
            <el-tag size="small" :type="categoryTag(row.category)">{{ categoryLabel(row.category) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="expression" label="表达式" min-width="260" show-overflow-tooltip />
        <el-table-column prop="ic" label="IC" width="80" align="center" />
        <el-table-column prop="rank_ic" label="RankIC" width="90" align="center" />
        <el-table-column prop="icir" label="ICIR" width="80" align="center" />
        <el-table-column prop="turnover" label="换手" width="80" align="center" />
        <el-table-column label="状态" width="80" align="center">
          <template #default="{row}">
            <el-tag size="small" :type="row.status === 'active' ? 'success' : 'info'">
              {{ row.status === 'active' ? '启用' : '禁用' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="150" align="center" fixed="right">
          <template #default="{row}">
            <el-button size="small" link type="primary" @click="evaluate(row.id)" :disabled="!qlibAvailable">评价</el-button>
            <el-button size="small" link type="danger" @click="disable(row.id)" :disabled="row.status !== 'active'">禁用</el-button>
          </template>
        </el-table-column>
      </el-table>
    </SectionCard>

    <el-dialog v-model="showAdd" title="新增因子" width="520px">
      <el-form label-width="80px">
        <el-form-item label="名称">
          <el-input v-model="addForm.name" placeholder="如 momentum_20d" />
        </el-form-item>
        <el-form-item label="表达式">
          <el-input v-model="addForm.expression" type="textarea" :rows="3"
            placeholder="qlib 表达式，如 $close / Ref($close, 20) - 1" />
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="addForm.description" placeholder="选填" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showAdd = false">取消</el-button>
        <el-button type="primary" @click="doAdd" :loading="adding">提交</el-button>
      </template>
    </el-dialog>
  </PageContainer>
</template>

<script setup>
import { ref, reactive, onMounted, onBeforeUnmount } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Refresh } from '@element-plus/icons-vue'
import PageContainer from '@/components/common/PageContainer.vue'
import SectionCard from '@/components/common/SectionCard.vue'
import { listFactors, addFactor, disableFactor, evaluateFactor, seedBuiltinFactors } from '@/api/factor'
import { getQlibStatus, syncQuantData } from '@/api/quant'

const factors = ref([])
const qlibAvailable = ref(false)
const seeding = ref(false)
const adding = ref(false)
const syncing = ref(false)
const showAdd = ref(false)
const filter = reactive({ category: '', sort_by: 'ic' })
const addForm = reactive({ name: '', expression: '', description: '' })
let evalTimer = null

const categoryLabelMap = { builtin: '内置', llm: 'LLM', symbolic: '符号', text: '文本', automl: 'AutoML' }
const categoryLabel = (c) => categoryLabelMap[c] || c
const categoryTag = (c) => ({ builtin: 'primary', llm: 'success', symbolic: 'warning', text: 'info', automl: 'danger' }[c] || 'info')

async function loadFactors() {
  try {
    const data = await listFactors({ category: filter.category || undefined, sort_by: filter.sort_by, limit: 200 })
    factors.value = data?.items || []
  } catch (e) { if (e !== 'cancel') ElMessage.error('加载因子列表失败') }
}

async function loadQlib() {
  try {
    const data = await getQlibStatus()
    qlibAvailable.value = data?.available || false
  } catch (e) { if (e !== 'cancel') ElMessage.error('qlib 状态检查失败') }
}

async function syncData() {
  syncing.value = true
  try {
    await syncQuantData({})
    ElMessage.success('股票数据同步已提交（后台执行，耗时较长）')
  } catch (e) { if (e !== 'cancel') ElMessage.error('数据同步提交失败') } finally {
    syncing.value = false
  }
}

async function seedBuiltin() {
  seeding.value = true
  try {
    const data = await seedBuiltinFactors()
    if (data) {
      ElMessage.success(`已添加 ${data.added} 个，跳过 ${data.skipped} 个`)
    }
    loadFactors()
  } catch (e) { if (e !== 'cancel') ElMessage.error('种子内置因子失败') } finally {
    seeding.value = false
  }
}

async function doAdd() {
  if (!addForm.name || !addForm.expression) {
    ElMessage.warning('请填写名称和表达式')
    return
  }
  adding.value = true
  try {
    await addFactor({ name: addForm.name, expression: addForm.expression, description: addForm.description, category: 'builtin' })
    ElMessage.success('因子已添加')
    showAdd.value = false
    addForm.name = ''; addForm.expression = ''; addForm.description = ''
    loadFactors()
  } catch (e) { if (e !== 'cancel') ElMessage.error('添加因子失败') } finally {
    adding.value = false
  }
}

async function evaluate(id) {
  try {
    await evaluateFactor(id)
    ElMessage.success('因子评价已提交（后台执行），稍后刷新查看')
    evalTimer = setTimeout(loadFactors, 5000)
  } catch (e) { if (e !== 'cancel') ElMessage.error('因子评价提交失败') }
}

async function disable(id) {
  try {
    await ElMessageBox.confirm('确定禁用该因子？', '提示', { type: 'warning' })
    await disableFactor(id)
    ElMessage.success('已禁用')
    loadFactors()
  } catch (e) { if (e !== 'cancel') ElMessage.error('禁用因子失败') }
}

onMounted(() => { loadFactors(); loadQlib() })
onBeforeUnmount(() => { if (evalTimer) clearTimeout(evalTimer) })
</script>

<style scoped lang="scss">
.page-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: var(--space-md);
  animation: fadeInUp 0.5s var(--ease-out-expo);
}
.page-title { font-size: var(--font-size-2xl); font-weight: 700; }
.page-actions { display: flex; align-items: center; gap: var(--space-sm); }
.filter-bar { display: flex; align-items: center; gap: var(--space-md); }
.filter-meta { color: var(--text-secondary); font-size: var(--font-size-base); }
</style>
