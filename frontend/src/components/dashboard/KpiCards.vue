<template>
  <section class="kpi-section">
    <section class="kpi-grid">
      <template v-if="loading">
        <div class="kpi-card" v-for="i in 4" :key="i">
          <el-skeleton :rows="2" animated />
        </div>
      </template>
      <template v-else>
        <div
          class="kpi-card"
          :class="{ 'kpi-card--clickable': card.to }"
          v-for="card in kpiCards"
          :key="card.key"
          :role="card.to ? 'link' : undefined"
          :tabindex="card.to ? 0 : undefined"
          @click="card.to && go(card.to)"
          @keydown.enter="card.to && go(card.to)"
        >
          <div class="kpi-card__label">{{ card.label }}</div>
          <div class="kpi-card__value">{{ card.value }}</div>
          <div class="kpi-card__sub">{{ card.sub }}</div>
          <el-icon class="kpi-card__icon" :style="{ color: card.iconColor }"><component :is="card.icon" /></el-icon>
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
      iconColor: 'var(--primary)',
      to: 'FactorLibrary',
    },
    {
      key: 'strategy',
      label: '策略数量',
      value: animatedValues.value.strategy,
      sub: `活跃 ${activeStrategies} / 归档 ${archivedStrategies}`,
      icon: TrendCharts,
      iconColor: 'var(--success)',
      to: 'QuantStrategy',
    },
    {
      key: 'mining',
      label: '挖掘任务',
      value: animatedValues.value.mining,
      sub: `今日 ${todayMining} / 运行中 ${runningMining}`,
      icon: MagicStick,
      iconColor: 'var(--warning)',
      to: 'Mining',
    },
    {
      key: 'backtest',
      label: '回测记录',
      value: animatedValues.value.backtest,
      sub: `近7日 ${last7dBacktests}`,
      icon: DataAnalysis,
      iconColor: 'var(--danger)',
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
  gap: 16px;
  @media (max-width: 768px) {
    grid-template-columns: repeat(2, 1fr);
  }
}
.kpi-card {
  position: relative;
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 20px;
}
.kpi-card--clickable {
  cursor: pointer;
  transition:
    border-color 0.2s,
    box-shadow 0.2s,
    transform 0.2s;
}
.kpi-card--clickable:hover {
  border-color: var(--primary);
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.12);
  transform: translateY(-2px);
}
.kpi-card--clickable:focus-visible {
  outline: 2px solid var(--primary);
  outline-offset: 2px;
}
.kpi-card__label {
  font-size: 14px;
  color: var(--text-tertiary);
}
.kpi-card__value {
  font-size: 32px;
  font-weight: 600;
  color: var(--text-primary);
  font-variant-numeric: tabular-nums;
  line-height: 1.2;
  margin-top: 8px;
}
.kpi-card__sub {
  font-size: 12px;
  color: var(--text-tertiary);
  margin-top: 6px;
}
.kpi-card__icon {
  position: absolute;
  top: 20px;
  right: 20px;
  font-size: 22px;
}
.kpi-freshness {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-top: 12px;
  padding: 8px 4px;
}
.kpi-freshness__label {
  font-size: 12px;
  color: var(--text-tertiary);
  white-space: nowrap;
}
.kpi-freshness__text {
  font-size: 12px;
  color: var(--text-tertiary);
  white-space: nowrap;
}
.kpi-freshness :deep(.el-progress) {
  flex: 1;
}
</style>
