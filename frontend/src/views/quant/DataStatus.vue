<template>
  <PageContainer narrow>
    <div class="page-header mb-16">
      <h2 class="page-title">数据管理</h2>
    </div>

    <SectionCard class="mb-16">
      <div class="data-header">
        <div>
          <div class="data-title">qlib 数据源</div>
          <div class="data-meta">
            状态：{{ qlib.available ? '就绪' : '未就绪' }} |
            数据目录：{{ qlib.provider_uri || '--' }}
          </div>
        </div>
        <el-button type="primary" @click="syncData" :loading="syncing" :disabled="!qlib.available">
          同步股票数据
        </el-button>
      </div>
    </SectionCard>

    <SectionCard title="数据状态">
      <el-table :data="statusList" size="small" stripe empty-text="暂无数据" max-height="400">
        <el-table-column prop="universe" label="股票池" width="120" align="center" />
        <el-table-column prop="latest_date" label="最新日期" width="130" align="center" />
        <el-table-column prop="stock_count" label="股票数" width="100" align="center" />
        <el-table-column prop="row_count" label="记录数" width="100" align="center" />
        <el-table-column label="状态" width="100" align="center">
          <template #default="{row}">
            <el-tag size="small" :type="row.status === 'ok' ? 'success' : row.status === 'syncing' ? 'warning' : 'danger'">
              {{ row.status }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="last_error" label="错误" min-width="200" show-overflow-tooltip />
        <el-table-column label="更新时间" width="160" align="center">
          <template #default="{row}">{{ (row.last_updated || '').slice(0,19) }}</template>
        </el-table-column>
      </el-table>
    </SectionCard>
  </PageContainer>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import PageContainer from '@/components/common/PageContainer.vue'
import SectionCard from '@/components/common/SectionCard.vue'
import { getQuantDataStatus, syncQuantData, getQlibStatus } from '@/api/quant'

const statusList = ref([])
const syncing = ref(false)
const qlib = reactive({ available: false, provider_uri: '' })

async function loadStatus() {
  try {
    const data = await getQuantDataStatus()
    statusList.value = data?.items || []
  } catch {}
}

async function loadQlib() {
  try {
    const data = await getQlibStatus()
    qlib.available = data?.available || false
    qlib.provider_uri = data?.provider_uri || ''
  } catch {}
}

async function syncData() {
  syncing.value = true
  try {
    await syncQuantData({})
    ElMessage.success('数据同步已提交（后台执行）')
    setTimeout(loadStatus, 5000)
  } catch {} finally {
    syncing.value = false
  }
}

onMounted(() => { loadStatus(); loadQlib() })
</script>

<style scoped lang="scss">
.page-header { animation: fadeInUp 0.5s var(--ease-out-expo); }
.page-title { font-size: var(--font-size-2xl); font-weight: 700; }
.data-header { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: var(--space-md); }
.data-title { font-size: var(--font-size-lg); font-weight: 600; }
.data-meta { font-size: var(--font-size-base); color: var(--text-secondary); margin-top: 4px; }
</style>
