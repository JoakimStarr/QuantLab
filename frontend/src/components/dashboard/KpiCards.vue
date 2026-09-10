<template>
  <section class="kpi-section">
    <section class="kpi-grid">
      <template v-if="loading">
        <div class="kpi-item" v-for="i in 4" :key="i">
          <StatCard :label="'加载中'" loading />
        </div>
      </template>
      <template v-else>
        <div
          class="kpi-item"
          :class="{ 'kpi-item--clickable': card.to }"
          v-for="card in kpiCards"
          :key="card.key"
          :role="card.to ? 'link' : undefined"
          :tabindex="card.to ? 0 : undefined"
          @click="card.to && go(card.to)"
          @keydown.enter="card.to && go(card.to)"
        >
          <StatCard :label="card.label" :value="card.value" :tone="card.tone" :icon="card.icon">
            {{ card.sub }}
          </StatCard>
        </div>
      </template>
    </section>
    <div v-if="freshnessText || freshnessPercent > 0" class="kpi-freshness">
      <span class="kpi-freshness__label">数据新鲜度</span>
      <el-progress :percentage="freshnessPercent" :status="freshnessStatus" :stroke-width="6" />
      <span v-if="freshnessText" class="kpi-freshness__text">{{ freshnessText }}</span>
    </div>
  </section>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { Coin, TrendCharts, MagicStick, DataAnalysis } from '@element-plus/icons-vue'
import { isToday, isWithinDays } from './utils'
import StatCard from '@/components/common/StatCard.vue'

const router = useRouter()
function go(name) {
  router.push({ name })
}

const props = defineProps({
  stats: { type: Object, default: () => ({}) },
  loading: { type: Boolean, default: false },
  dataStatus: { type: Object, default: () => ({}) },
})

// KPI 数字滚动动画
const animatedValues = ref({ factor: 0, strategy: 0, mining: 0, backtest: 0 })
function animate(targets) {
  const from = { ...animatedValues.value }
  const duration = 600
  const start = performance.now()
  const tick = (now) => {
    const p = Math.min(1, (now - start) / duration)
    const e = 1 - Math.pow(1 - p, 3)
    for (const k of Object.keys(targets)) {
      animatedValues.value[k] = Math.round(from[k] + (targets[k] - from[k]) * e)
    }
    if (p < 1) requestAnimationFrame(tick)
  }
  requestAnimationFrame(tick)
}
watch(
  () => ({
    factor: props.stats?.factorTotal ?? 0,
    strategy: props.stats?.strategies?.length ?? 0,
    mining: props.stats?.miningTotal ?? 0,
    backtest: props.stats?.backtestTotal ?? 0,
  }),
  (targets) => animate(targets),
  { deep: true, immediate: true }
)

const kpiCards = computed(() => {
  const s = props.stats || {}
  const strategies = s.strategies || []
  const recentMining = s.recentMining || []
  const recentBacktests = s.recentBacktests || []
  const factorBySource = s.factorBySource || { builtin: 0, llm: 0, symbolic: 0 }
  const activeStrategies = strategies.filter((x) => !x.archived && x.status !== 'archived').length
  const archivedStrategies = strategies.length - activeStrategies
  const todayMining = recentMining.filter((t) => isToday(t.created_at)).length
  const runningMining = recentMining.filter((t) => t.status === 'running').length
  const last7dBacktests = recentBacktests.filter((b) => isWithinDays(b.created_at || b.end_date, 7)).length

  return [
    {
      key: 'factor',
      label: '因子总数',
      value: animatedValues.value.factor,
      sub: `内置 ${factorBySource.builtin} / LLM ${factorBySource.llm} / 符号 ${factorBySource.symbolic}`,
      icon: Coin,
      tone: 'default',
      to: 'FactorLibraryV2',
    },
    {
      key: 'strategy',
      label: '策略数量',
      value: animatedValues.value.strategy,
      sub: `活跃 ${activeStrategies} / 归档 ${archivedStrategies}`,
      icon: TrendCharts,
      tone: 'success',
      to: 'QuantStrategy',
    },
    {
      key: 'mining',
      label: '挖掘任务',
      value: animatedValues.value.mining,
      sub: `今日 ${todayMining} / 运行中 ${runningMining}`,
      icon: MagicStick,
      tone: 'warning',
      to: 'Mining',
    },
    {
      key: 'backtest',
      label: '回测记录',
      value: animatedValues.value.backtest,
      sub: `近7日 ${last7dBacktests}`,
      icon: DataAnalysis,
      tone: 'danger',
      to: 'QuantStrategy',
    },
  ]
})

// 数据新鲜度：基于 dataStatus 字段（后端如未返回则不展示）
const freshnessPercent = computed(() => {
  const s = props.dataStatus || {}
  if (s.coverage != null) return Math.min(100, Math.max(0, Math.round(Number(s.coverage) * 100)))
  if (s.completeness != null) return Math.min(100, Math.max(0, Math.round(Number(s.completeness) * 100)))
  if (s.last_synced) return 100
  return 0
})
const freshnessStatus = computed(() => {
  const p = freshnessPercent.value
  if (p >= 80) return 'success'
  if (p >= 40) return 'warning'
  return 'exception'
})
const freshnessText = computed(() => {
  const s = props.dataStatus || {}
  if (s.last_synced) return `最近同步: ${String(s.last_synced).slice(0, 16)}`
  return ''
})
</script>

<style scoped lang="scss">
.kpi-section {
  width: 100%;
}
.kpi-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: var(--space-md);
  @media (max-width: 768px) {
    grid-template-columns: repeat(2, 1fr);
  }
}
.kpi-item {
  min-width: 0;
}
.kpi-item--clickable {
  cursor: pointer;
  border-radius: var(--radius-lg);

  & > :deep(.stat-card) {
    transition:
      border-color var(--duration-fast) var(--ease-in-out),
      box-shadow var(--duration-fast) var(--ease-in-out),
      transform var(--duration-fast) var(--ease-in-out);
  }
  &:hover > :deep(.stat-card) {
    border-color: var(--primary);
    box-shadow: var(--shadow-md);
    transform: translateY(-2px);
  }
  &:focus-visible {
    outline: 2px solid var(--primary);
    outline-offset: 2px;
  }
}
.kpi-freshness {
  display: flex;
  align-items: center;
  gap: var(--space-12);
  margin-top: var(--space-12);
  padding: var(--space-sm) var(--space-xs);
}
.kpi-freshness__label {
  font-size: var(--font-size-sm);
  color: var(--text-tertiary);
  white-space: nowrap;
}
.kpi-freshness__text {
  font-size: var(--font-size-sm);
  color: var(--text-tertiary);
  white-space: nowrap;
}
.kpi-freshness :deep(.el-progress) {
  flex: 1;
}
</style>
