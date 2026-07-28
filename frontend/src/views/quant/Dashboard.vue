<template>
  <PageContainer>
    <!-- Hero Section -->
    <div class="hero-section mb-24">
      <div class="hero-content">
        <div class="hero-text">
          <h1 class="hero-title">量化策略研究平台</h1>
          <p class="hero-subtitle">AI驱动的因子挖掘与策略回测系统</p>
          <div class="hero-actions">
            <el-button type="primary" size="large" @click="$router.push('/quant/mining')">
              <el-icon class="mr-8"><MagicStick /></el-icon>
              开始AI挖掘
            </el-button>
            <el-button size="large" @click="$router.push('/quant/strategy')">
              <el-icon class="mr-8"><TrendCharts /></el-icon>
              策略回测
            </el-button>
          </div>
        </div>
        <div class="hero-stats">
          <div class="stat-item">
            <div class="stat-value">{{ factorCount }}</div>
            <div class="stat-label">因子总数</div>
          </div>
          <div class="stat-item">
            <div class="stat-value">{{ strategyCount }}</div>
            <div class="stat-label">策略数量</div>
          </div>
          <div class="stat-item">
            <div class="stat-value">{{ backtestCount }}</div>
            <div class="stat-label">回测记录</div>
          </div>
          <div class="stat-item">
            <div class="stat-value">{{ latestDataDate || '--' }}</div>
            <div class="stat-label">数据最新日期</div>
          </div>
        </div>
      </div>
      <div class="hero-illustration">
        <div class="chart-container">
          <v-chart :option="chartOption" autoresize class="chart" />
        </div>
      </div>
    </div>

    <!-- Quick Access -->
    <div class="quick-access mb-24">
      <div class="quick-card" @click="$router.push('/quant/factors')">
        <div class="quick-card__icon">
          <el-icon :size="32"><Coin /></el-icon>
        </div>
        <div class="quick-card__content">
          <div class="quick-card__title">因子库</div>
          <div class="quick-card__desc">管理和浏览所有因子</div>
        </div>
        <el-icon class="quick-card__arrow"><ArrowRight /></el-icon>
      </div>

      <div class="quick-card" @click="$router.push('/quant/strategy')">
        <div class="quick-card__icon">
          <el-icon :size="32"><TrendCharts /></el-icon>
        </div>
        <div class="quick-card__content">
          <div class="quick-card__title">策略回测</div>
          <div class="quick-card__desc">配置和运行策略回测</div>
        </div>
        <el-icon class="quick-card__arrow"><ArrowRight /></el-icon>
      </div>

      <div class="quick-card" @click="$router.push('/quant/mining')">
        <div class="quick-card__icon">
          <el-icon :size="32"><MagicStick /></el-icon>
        </div>
        <div class="quick-card__content">
          <div class="quick-card__title">AI因子挖掘</div>
          <div class="quick-card__desc">使用AI自动发现因子</div>
        </div>
        <el-icon class="quick-card__arrow"><ArrowRight /></el-icon>
      </div>

      <div class="quick-card" @click="$router.push('/quant/data')">
        <div class="quick-card__icon">
          <el-icon :size="32"><SetUp /></el-icon>
        </div>
        <div class="quick-card__content">
          <div class="quick-card__title">数据管理</div>
          <div class="quick-card__desc">管理和更新数据源</div>
        </div>
        <el-icon class="quick-card__arrow"><ArrowRight /></el-icon>
      </div>
    </div>

    <!-- Statistics Cards -->
    <el-row :gutter="16" class="mb-24">
      <el-col :xs="12" :sm="6">
        <StatCard label="因子库" :value="factorCount" suffix="个" icon="Coin" accent="#1a73e8" />
      </el-col>
      <el-col :xs="12" :sm="6">
        <StatCard label="策略数" :value="strategyCount" suffix="个" icon="TrendCharts" accent="#00b894" />
      </el-col>
      <el-col :xs="12" :sm="6">
        <StatCard label="挖掘任务" :value="miningCount" suffix="个" icon="MagicStick" accent="#f39c12" />
      </el-col>
      <el-col :xs="12" :sm="6">
        <StatCard label="回测记录" :value="backtestCount" suffix="条" icon="DataLine" accent="#e17055" />
      </el-col>
    </el-row>

    <!-- Recent Activity -->
    <el-row :gutter="16">
      <el-col :xs="24" :md="12">
        <SectionCard title="最近挖掘任务">
          <el-table :data="recentMining" size="small" stripe empty-text="暂无任务" max-height="300">
            <el-table-column label="类型" width="90" align="center">
              <template #default="{row}">
                <el-tag size="small" :type="typeTag(row.type)">{{ typeLabel[row.type] || row.type }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="状态" width="80" align="center">
              <template #default="{row}">
                <el-tag size="small" :type="statusTag(row.status)">{{ statusLabel[row.status] }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="通过/候选" width="100" align="center">
              <template #default="{row}">{{ row.candidates_passed ?? 0 }}/{{ row.candidates_generated ?? 0 }}</template>
            </el-table-column>
            <el-table-column prop="best_ic" label="最佳IC" width="80" align="center" />
            <el-table-column label="时间" min-width="140">
              <template #default="{row}">{{ (row.finished_at || row.created_at || '').slice(0,16) }}</template>
            </el-table-column>
          </el-table>
        </SectionCard>
      </el-col>
      <el-col :xs="24" :md="12">
        <SectionCard title="最近回测结果">
          <el-table :data="recentBacktests" size="small" stripe empty-text="暂无回测" max-height="300">
            <el-table-column prop="strategy_id" label="策略" width="60" align="center" />
            <el-table-column prop="sharpe" label="夏普" width="70" align="center" />
            <el-table-column label="年化" width="80" align="center">
              <template #default="{row}">{{ row.annual_return != null ? (row.annual_return * 100).toFixed(1) + '%' : '--' }}</template>
            </el-table-column>
            <el-table-column label="回撤" width="80" align="center">
              <template #default="{row}">{{ row.max_drawdown != null ? (row.max_drawdown * 100).toFixed(1) + '%' : '--' }}</template>
            </el-table-column>
            <el-table-column prop="calmar" label="卡玛" width="70" align="center" />
            <el-table-column label="区间" min-width="160">
              <template #default="{row}">{{ row.start_date }}~{{ row.end_date }}</template>
            </el-table-column>
          </el-table>
        </SectionCard>
      </el-col>
    </el-row>
  </PageContainer>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import VChart from 'vue-echarts'
import PageContainer from '@/components/common/PageContainer.vue'
import SectionCard from '@/components/common/SectionCard.vue'
import StatCard from '@/components/common/StatCard.vue'
import { listFactors } from '@/api/factor'
import { listStrategies, listAllBacktestResults } from '@/api/strategy'
import { listMiningTasks } from '@/api/mining'
import { getQlibStatus, getQuantDataStatus } from '@/api/quant'
import { useAppStore } from '@/stores/app'

const appStore = useAppStore()

const qlibAvailable = ref(false)
const factorCount = ref(0)
const strategyCount = ref(0)
const miningCount = ref(0)
const backtestCount = ref(0)
const recentMining = ref([])
const recentBacktests = ref([])
const latestDataDate = ref('')

const typeLabel = { llm: 'LLM', symbolic: '符号', text: '文本', automl: 'AutoML' }
const typeTag = (t) => ({ llm: 'success', symbolic: 'warning', text: 'info', automl: 'danger' }[t] || 'info')
const statusLabel = { pending: '等待', running: '运行', done: '完成', failed: '失败' }
const statusTag = (s) => ({ pending: 'info', running: 'warning', done: 'success', failed: 'danger' }[s] || 'info')

// Chart configuration
const chartOption = computed(() => ({
  grid: {
    top: 20,
    right: 20,
    bottom: 30,
    left: 50
  },
  xAxis: {
    type: 'category',
    data: ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'],
    axisLine: { lineStyle: { color: 'var(--border)' } },
    axisLabel: { color: 'var(--text-secondary)', fontSize: 11 }
  },
  yAxis: {
    type: 'value',
    axisLine: { show: false },
    axisTick: { show: false },
    axisLabel: { color: 'var(--text-secondary)', fontSize: 11 },
    splitLine: { lineStyle: { color: 'var(--border)', type: 'dashed' } }
  },
  series: [{
    data: [150, 230, 224, 218, 135, 147, 260],
    type: 'line',
    smooth: true,
    lineStyle: {
      color: 'var(--primary)',
      width: 3
    },
    areaStyle: {
      color: {
        type: 'linear',
        x: 0, y: 0, x2: 0, y2: 1,
        colorStops: [
          { offset: 0, color: 'rgba(26, 115, 232, 0.3)' },
          { offset: 1, color: 'rgba(26, 115, 232, 0.05)' }
        ]
      }
    },
    itemStyle: { color: 'var(--primary)' }
  }],
  tooltip: {
    trigger: 'axis',
    backgroundColor: 'var(--bg-card)',
    borderColor: 'var(--border)',
    textStyle: { color: 'var(--text-primary)' }
  }
}))

async function loadAll() {
  try {
    const [factors, strategies, mining, qlib, dataStatus] = await Promise.all([
      listFactors({ limit: 1 }),
      listStrategies(),
      listMiningTasks({ limit: 5 }),
      getQlibStatus(),
      getQuantDataStatus(),
    ])
    factorCount.value = factors?.total ?? 0
    strategyCount.value = strategies?.total ?? 0
    recentMining.value = mining?.items ?? []
    miningCount.value = mining?.total ?? 0
    qlibAvailable.value = qlib?.available ?? false
    const items = dataStatus?.items ?? []
    if (items.length) {
      const latest = items
        .filter(r => r.status === 'ok')
        .map(r => r.latest_date)
        .filter(Boolean)
        .sort()
        .reverse()[0]
      if (latest) latestDataDate.value = latest
    }
  } catch (e) {
    if (e !== 'cancel') ElMessage.error('加载首页数据失败')
  }
  try {
    const data = await listAllBacktestResults({ limit: 5 })
    recentBacktests.value = data?.items ?? []
    backtestCount.value = data?.total ?? 0
  } catch (e) {
    if (e !== 'cancel') ElMessage.error('加载回测数据失败')
  }
}

onMounted(loadAll)
</script>

<style scoped lang="scss">
// Hero Section
.hero-section {
  position: relative;
  background: var(--primary-gradient);
  border-radius: var(--radius-lg);
  padding: var(--space-2xl);
  display: flex;
  justify-content: space-between;
  align-items: center;
  min-height: 280px;
  overflow: hidden;
  box-shadow: var(--shadow-lg);

  @media (max-width: 767px) {
    flex-direction: column;
    padding: var(--space-lg);
    min-height: auto;
  }

  &::before {
    content: '';
    position: absolute;
    top: 0;
    left: 0;
    right: 0;
    bottom: 0;
    background: url('data:image/svg+xml,<svg width="60" height="60" viewBox="0 0 60 60" xmlns="http://www.w3.org/2000/svg"><g fill="none" fill-rule="evenodd"><g fill="%23ffffff" fill-opacity="0.05"><path d="M36 34v-4h-2v4h-4v2h4v4h2v-4h4v-2h-4zm0-30V0h-2v4h-4v2h4v4h2V6h4V4h-4zM6 34v-4H4v4H0v2h4v4h2v-4h4v-2H6zM6 4V0H4v4H0v2h4v4h2V6h4V4H6z"/></g></g></svg>');
    opacity: 0.3;
    animation: float 20s linear infinite;
  }
}

@keyframes float {
  0%, 100% { transform: translateY(0px); }
  50% { transform: translateY(-20px); }
}

.hero-content {
  flex: 1;
  z-index: 1;
  display: flex;
  flex-direction: column;
  gap: var(--space-lg);

  @media (max-width: 767px) {
    width: 100%;
  }
}

.hero-text {
  color: #fff;
}

.hero-title {
  font-size: var(--font-size-3xl);
  font-weight: var(--font-weight-bold);
  margin-bottom: var(--space-sm);
  letter-spacing: -0.5px;

  @media (max-width: 767px) {
    font-size: var(--font-size-2xl);
  }
}

.hero-subtitle {
  font-size: var(--font-size-lg);
  opacity: 0.9;
  margin-bottom: var(--space-lg);

  @media (max-width: 767px) {
    font-size: var(--font-size-base);
  }
}

.hero-actions {
  display: flex;
  gap: var(--space-md);

  @media (max-width: 767px) {
    flex-direction: column;
  }

  :deep(.el-button) {
    background: rgba(255, 255, 255, 0.15);
    border-color: rgba(255, 255, 255, 0.3);
    color: #fff;
    backdrop-filter: blur(10px);

    &:hover {
      background: rgba(255, 255, 255, 0.25);
      border-color: rgba(255, 255, 255, 0.5);
    }

    &.el-button--primary {
      background: #fff;
      border-color: #fff;
      color: var(--primary);

      &:hover {
        background: rgba(255, 255, 255, 0.9);
      }
    }
  }
}

.hero-stats {
  display: flex;
  gap: var(--space-lg);
  flex-wrap: wrap;

  @media (max-width: 767px) {
    gap: var(--space-md);
  }
}

.stat-item {
  text-align: center;
  color: #fff;
  min-width: 60px;
}

.stat-value {
  font-size: var(--font-size-xl);
  font-weight: var(--font-weight-bold);
  margin-bottom: var(--space-xs);
  white-space: nowrap;

  @media (max-width: 767px) {
    font-size: var(--font-size-lg);
  }
}

.stat-label {
  font-size: var(--font-size-sm);
  opacity: 0.8;
}

.hero-illustration {
  width: 300px;
  height: 200px;
  z-index: 1;

  @media (max-width: 767px) {
    display: none;
  }
}

.chart-container {
  width: 100%;
  height: 100%;
  background: rgba(255, 255, 255, 0.1);
  border-radius: var(--radius-md);
  padding: var(--space-md);
  backdrop-filter: blur(10px);
}

.chart {
  width: 100%;
  height: 100%;
}

// Quick Access
.quick-access {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: var(--space-md);

  @media (max-width: 1023px) {
    grid-template-columns: repeat(2, 1fr);
  }

  @media (max-width: 767px) {
    grid-template-columns: 1fr;
  }
}

.quick-card {
  display: flex;
  align-items: center;
  gap: var(--space-md);
  padding: var(--space-lg);
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  cursor: pointer;
  transition: all var(--duration-normal) var(--ease-out-expo);

  &:hover {
    transform: translateY(-4px);
    box-shadow: var(--shadow-md);
    border-color: var(--primary);

    .quick-card__icon {
      background: var(--primary-gradient);
      color: #fff;
    }

    .quick-card__arrow {
      transform: translateX(4px);
    }
  }
}

.quick-card__icon {
  width: 56px;
  height: 56px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: var(--radius-md);
  background: var(--primary-gradient-soft);
  color: var(--primary);
  flex-shrink: 0;
  transition: all var(--duration-normal) var(--ease-out-expo);
}

.quick-card__content {
  flex: 1;
}

.quick-card__title {
  font-size: var(--font-size-lg);
  font-weight: var(--font-weight-semibold);
  color: var(--text-primary);
  margin-bottom: var(--space-xs);
}

.quick-card__desc {
  font-size: var(--font-size-sm);
  color: var(--text-secondary);
}

.quick-card__arrow {
  color: var(--text-tertiary);
  transition: transform var(--duration-fast) var(--ease-in-out);
}
</style>