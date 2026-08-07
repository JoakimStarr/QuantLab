<template>
  <div class="strategy-result">
    <!-- 回测结果内容（行内展开，单开折叠） -->
    <div class="strategy-result__head">
      <div class="strategy-result__title">
        <span class="cell-name">{{ strategy?.name }}</span>
        <span v-if="result" class="strategy-result__period">
          {{ result.start_date }} ~ {{ result.end_date }}
        </span>
      </div>
      <div class="strategy-result__actions">
        <el-button v-if="result" size="small" :loading="mcLoading" @click="runMonteCarlo">
          蒙特卡罗模拟
        </el-button>
        <el-button v-if="deletable" size="small" type="danger" plain @click.stop="$emit('delete')">删除该回测</el-button>
      </div>
    </div>
    <div v-loading="loading" class="result-overview">
      <!-- 资金与收益总览（big 期末资产 + 初始对比 + 总收益 + 年化收益） -->
      <div class="result-hero">
        <div class="result-hero__primary">
          <div class="result-hero__primary-label">期末资产</div>
          <div
            class="result-hero__primary-value"
            :class="currentValue >= initialCapital ? 'tone-success' : 'tone-danger'"
          >
            {{ fmtMoneyExact(currentValue) }}
          </div>
          <div class="result-hero__primary-sub">
            初始 {{ fmtMoneyExact(initialCapital) }}
            <span class="result-hero__delta" :class="profitDelta >= 0 ? 'tone-success' : 'tone-danger'">
              {{ profitDelta >= 0 ? '+' : '' }}{{ fmtMoneyExact(profitDelta) }}
            </span>
          </div>
        </div>
        <div class="result-hero__item" v-for="h in heroMetrics" :key="h.key">
          <div class="result-hero__item-label">
            {{ h.label }}
            <el-tooltip :content="h.tip" placement="top" :show-after="120">
              <span class="metric-hint">?</span>
            </el-tooltip>
          </div>
          <div class="result-hero__item-value" :class="h.tone">{{ h.value }}</div>
        </div>
      </div>

      <!-- 指标总览：平铺紧凑卡片，悬浮显示含义与计算公式 -->
      <div class="metrics-section">
        <MetricGrid :metrics="flatMetrics" />
      </div>

      <!-- 回测参数折叠收纳 -->
      <el-collapse v-model="paramsOpen" class="result-params-collapse">
        <el-collapse-item name="params">
          <template #title>
            <span class="result-params-collapse-title">回测参数与执行口径</span>
          </template>
          <div class="result-params">
            <div class="result-params__item">
              <span class="result-params__label">区间</span>
              <span class="result-params__value">{{ result.start_date }} ~ {{ result.end_date }}</span>
            </div>
            <div class="result-params__item">
              <span class="result-params__label">调仓频率</span>
              <span class="result-params__value">{{ rebalanceLabel }}</span>
            </div>
            <div class="result-params__item">
              <span class="result-params__label">topk/n_drop</span>
              <span class="result-params__value">{{ result.topk || '--' }}/{{ result.n_drop || '--' }}</span>
            </div>
            <div class="result-params__item">
              <span class="result-params__label">基准</span>
              <span class="result-params__value">{{ benchmarkLabel }}</span>
            </div>
            <div class="result-params__item">
              <span class="result-params__label">执行口径</span>
              <span class="result-params__value">{{ execConfigLabel }}</span>
            </div>
            <div class="result-params__item">
              <span class="result-params__label">交易笔数</span>
              <span class="result-params__value">{{ tradeCount }}</span>
            </div>
            <div class="result-params__item">
              <span class="result-params__label">换手率</span>
              <span class="result-params__value">{{ fmtNum(result.turnover, 3) }}</span>
            </div>
          </div>
        </el-collapse-item>
      </el-collapse>
    </div>

    <!-- 蒙特卡罗模拟：回测指标 bootstrap 置信区间 -->
    <SectionCard v-if="mcData" title="蒙特卡罗模拟" class="result-sub-card" compact>
      <template #extra>
        <span class="mc-note">
          Stationary Bootstrap ×{{ mcData.n_iter }}，块长 {{ mcData.block }} 交易日，
          {{ Number(mcData.ci_level) * 100 }}% 置信区间
        </span>
        <el-button size="small" :loading="mcLoading" @click="runMonteCarlo">重新运行</el-button>
      </template>
      <div class="mc-body">
        <div class="mc-grid">
          <div v-for="m in mcMetrics" :key="m.key" class="mc-card">
            <div class="mc-card__label">{{ m.label }}</div>
            <div class="mc-card__point" :class="m.tone">{{ m.point }}</div>
            <div class="mc-card__ci">CI [{{ m.lo }}, {{ m.hi }}]</div>
            <div v-if="m.sig" class="mc-card__sig" :class="'mc-card__sig--' + m.sig">
              {{ MC_SIG_LABELS[m.sig] }}
            </div>
          </div>
        </div>
        <div v-if="mcChartCategories.length" class="mc-chart">
          <div class="mc-chart__title">夏普比率重采样分布（{{ mcData.sharpe_samples.length }} 次）</div>
          <v-chart :option="mcChartOption" class="mc-chart__body" autoresize />
        </div>
        <p v-if="mcConclusion" class="mc-conclusion">{{ mcConclusion }}</p>
        <p class="mc-desc">
          对历史日收益做 block bootstrap 重采样并重算指标。置信区间越宽，说明该指标越不确定——
          点估计靠近 0 且区间横跨正负时，回测结果大概率是运气而非真实能力。
        </p>
      </div>
    </SectionCard>

    <!-- 净值走势 / K线图（按钮切换） -->
    <SectionCard :title="chartTitle" class="result-sub-card" compact>
      <template #extra>
        <el-radio-group v-model="chartView" size="small" class="chart-view-switch">
          <el-radio-button value="nav">净值走势</el-radio-button>
          <el-radio-button value="kline">K线图</el-radio-button>
        </el-radio-group>
        <div v-if="chartView === 'nav'" class="chart-legend">
          <span class="legend-item"> <span class="legend-line legend-line--solid"></span>策略净值 </span>
          <span class="legend-item"> <span class="legend-line legend-line--dashed"></span>基准净值 </span>
          <span v-if="hasTrades" class="legend-item legend-trade">
            <span class="legend-dot legend-dot--buy"></span>买入
          </span>
          <span v-if="hasTrades" class="legend-item legend-trade">
            <span class="legend-dot legend-dot--sell"></span>卖出
          </span>
        </div>
      </template>
      <template v-if="chartView === 'nav'">
        <v-chart v-if="hasChart" :option="chartOption" class="chart-body" autoresize />
        <el-empty v-else description="暂无净值数据" :image-size="64" />
      </template>
      <BacktestKLinePanel v-else :result="currentResult" />
    </SectionCard>

    <!-- 交易明细（全部交易日概览 + 逐笔成交） -->
    <SectionCard v-if="hasTrades" title="交易明细" class="result-sub-card trade-workspace" compact>
      <template #extra>
        <div class="chart-legend" style="gap: 16px">
          <span class="legend-item">
            <span
              class="trade-legend-dot"
              style="background: var(--danger)"
            ></span>
            买入 {{ tradeStats.buys }}
          </span>
          <span class="legend-item">
            <span
              class="trade-legend-dot"
              style="background: var(--success)"
            ></span>
            卖出 {{ tradeStats.sells }}
          </span>
          <span class="legend-item">总成交额 {{ fmtMoney(tradeStats.total) }}</span>
          <span class="legend-item">
            已实现盈亏
            <span :class="tradeStats.realized >= 0 ? 'text-success' : 'text-danger'">
              {{ fmtPnl(tradeStats.realized) }}
            </span>
          </span>
          <el-button size="small" @click="exportTrades">导出 CSV</el-button>
        </div>
      </template>

<div class="trade-workspace__body">
          <div class="trades-filters">
            <el-radio-group v-model="tradeView" size="small">
              <el-radio-button value="group">按日分组</el-radio-button>
              <el-radio-button value="flat">逐笔明细</el-radio-button>
            </el-radio-group>
            <el-radio-group v-model="tradeOrder" size="small">
              <el-radio-button value="desc">最近优先</el-radio-button>
              <el-radio-button value="asc">最早优先</el-radio-button>
            </el-radio-group>
            <el-radio-group v-model="tradeType" size="small">
              <el-radio-button value="all">全部</el-radio-button>
              <el-radio-button value="BUY">买入</el-radio-button>
              <el-radio-button value="SELL">卖出</el-radio-button>
            </el-radio-group>
            <el-input v-model="tradeCode" placeholder="搜索代码，如 SH600519" size="small" clearable style="width: 200px">
              <template #prefix>🔍</template>
            </el-input>
          </div>

          <div v-if="hasTrades">
            <!-- 按日分组视图 -->
            <el-table v-if="tradeView === 'group'" :data="pagedGroups" size="small" row-key="date">
              <el-table-column type="expand">
                <template #default="{ row }">
                  <div class="trade-group-detail">
                    <el-table :data="row.trades" size="small">
                      <el-table-column prop="date" label="日期" min-width="120">
                        <template #default="{ row: sub }">
                          <span class="trade-datetime">
                            <span class="trade-datetime__date">{{ String(sub.date).slice(0, 10) }}</span>
                            <span v-if="String(sub.date).length > 10" class="trade-datetime__time">
                              {{ String(sub.date).slice(11, 19) }}
                            </span>
                          </span>
                        </template>
                      </el-table-column>
                      <el-table-column label="动作" width="74" align="center">
                        <template #default="{ row: sub }">
                          <el-tag
                            :type="sub.action === 'BUY' ? 'danger' : 'success'"
                            size="small"
                            effect="dark"
                            disable-transitions
                          >
                            {{ sub.action === 'BUY' ? '买入' : '卖出' }}
                          </el-tag>
                        </template>
                      </el-table-column>
                      <el-table-column label="行为" width="84" align="center">
                        <template #default="{ row: sub }">
                          <el-tag
                            :type="behaviorTag(sub.behavior).type"
                            :effect="behaviorTag(sub.behavior).effect"
                            size="small"
                            disable-transitions
                          >
                            {{ sub.behavior }}
                          </el-tag>
                        </template>
                      </el-table-column>
                      <el-table-column prop="code" label="代码" min-width="120">
                        <template #default="{ row: sub }"><span class="cell-mono">{{ sub.code || '--' }}</span></template>
                      </el-table-column>
                      <el-table-column label="成交价" width="90" align="right">
                        <template #default="{ row: sub }"><span class="cell-mono cell-tnum">{{ fmtPrice(sub.price) }}</span></template>
                      </el-table-column>
                      <el-table-column label="数量" min-width="100" align="right">
                        <template #default="{ row: sub }"><span class="cell-mono cell-tnum">{{ fmtNum(sub.quantity, 0) }}</span></template>
                      </el-table-column>
                      <el-table-column label="成交金额" min-width="120" align="right">
                        <template #default="{ row: sub }"><span class="cell-mono cell-tnum">{{ fmtMoneyExact(sub.total) }}</span></template>
                      </el-table-column>
                      <el-table-column label="费用" width="100" align="right">
                        <template #default="{ row: sub }"><span class="cell-mono cell-tnum">{{ fmtMoneyExact(sub.cost) }}</span></template>
                      </el-table-column>
                      <el-table-column label="持仓" min-width="100" align="right">
                        <template #default="{ row: sub }"><span class="cell-mono cell-tnum">{{ fmtNum(sub.position, 0) }}</span></template>
                      </el-table-column>
                    </el-table>
                  </div>
                </template>
              </el-table-column>
              <el-table-column prop="date" label="交易日期" min-width="120" />
              <el-table-column label="买入" width="96" align="center">
                <template #default="{ row }"><span class="cell-tnum text-danger">{{ row.buys }}</span> 笔</template>
              </el-table-column>
              <el-table-column label="卖出" width="96" align="center">
                <template #default="{ row }"><span class="cell-tnum text-success">{{ row.sells }}</span> 笔</template>
              </el-table-column>
              <el-table-column label="当日成交额" min-width="130" align="right">
                <template #default="{ row }"><span class="cell-mono cell-tnum">{{ fmtMoneyExact(row.amount) }}</span></template>
              </el-table-column>
              <el-table-column label="当日已实现盈亏" min-width="130" align="right">
                <template #default="{ row }">
                  <span :class="['cell-mono', 'cell-tnum', row.pnl >= 0 ? 'text-success' : 'text-danger']">
                    {{ fmtPnl(row.pnl) }}
                  </span>
                </template>
              </el-table-column>
            </el-table>

            <!-- 逐笔明细视图 -->
            <el-table v-else :data="pagedTrades" size="small">
              <el-table-column label="#" type="index" :index="tradeIndexStart" width="56" align="center" />
              <el-table-column prop="date" label="日期" min-width="120">
                <template #default="{ row }">
                  <span class="trade-datetime">
                    <span class="trade-datetime__date">{{ String(row.date).slice(0, 10) }}</span>
                    <span v-if="String(row.date).length > 10" class="trade-datetime__time">
                      {{ String(row.date).slice(11, 19) }}
                    </span>
                  </span>
                </template>
              </el-table-column>
              <el-table-column label="动作" width="74" align="center">
                <template #default="{ row }">
                  <el-tag
                    :type="row.action === 'BUY' ? 'danger' : 'success'"
                    size="small"
                    effect="dark"
                    disable-transitions
                  >
                    {{ row.action === 'BUY' ? '买入' : '卖出' }}
                  </el-tag>
                </template>
              </el-table-column>
              <el-table-column label="行为" width="84" align="center">
                <template #default="{ row }">
                  <el-tag
                    :type="behaviorTag(row.behavior).type"
                    :effect="behaviorTag(row.behavior).effect"
                    size="small"
                    disable-transitions
                  >
                    {{ row.behavior }}
                  </el-tag>
                </template>
              </el-table-column>
              <el-table-column prop="code" label="代码" min-width="120">
                <template #default="{ row }"><span class="cell-code">{{ row.code || '--' }}</span></template>
              </el-table-column>
              <el-table-column label="成交价" width="90" align="right">
                <template #default="{ row }"><span class="cell-mono cell-tnum">{{ fmtPrice(row.price) }}</span></template>
              </el-table-column>
              <el-table-column label="数量" min-width="100" align="right">
                <template #default="{ row }"><span class="cell-mono cell-tnum">{{ fmtNum(row.quantity, 0) }}</span></template>
              </el-table-column>
              <el-table-column label="成交金额" min-width="120" align="right">
                <template #default="{ row }"><span class="cell-mono cell-tnum">{{ fmtMoneyExact(row.total) }}</span></template>
              </el-table-column>
              <el-table-column label="费用" width="90" align="right">
                <template #default="{ row }"><span class="cell-mono cell-tnum">{{ fmtMoneyExact(row.cost) }}</span></template>
              </el-table-column>
              <el-table-column label="成本价" min-width="90" align="right">
                <template #default="{ row }"><span class="cell-mono cell-tnum">{{ fmtPrice(row.avgCost) }}</span></template>
              </el-table-column>
              <el-table-column label="已实现盈亏" min-width="110" align="right">
                <template #default="{ row }">
                  <span :class="['cell-tnum', fmtPnlNum(row.pnl) >= 0 ? 'text-success' : 'text-danger']">
                    {{ fmtPnl(row.pnl) }}
                  </span>
                </template>
              </el-table-column>
              <el-table-column label="累计盈亏" min-width="110" align="right">
                <template #default="{ row }">
                  <span :class="['cell-tnum', fmtPnlNum(row.cumPnl) >= 0 ? 'text-success' : 'text-danger']">{{
                    fmtPnl(row.cumPnl)
                  }}</span>
                </template>
              </el-table-column>
              <el-table-column label="持仓" min-width="90" align="right">
                <template #default="{ row }"><span class="cell-mono cell-tnum">{{ fmtNum(row.position, 0) }}</span></template>
              </el-table-column>
              <el-table-column label="剩余资金" min-width="110" align="right">
                <template #default="{ row }"><span class="cell-mono cell-tnum">{{ fmtMoneyExact(row.cash) }}</span></template>
              </el-table-column>
            </el-table>

            <div class="trades-pagination">
              <el-pagination
                v-model:current-page="tradePage"
                v-model:page-size="tradePageSize"
                :total="tradeView === 'group' ? tradeGroupCount : filteredTrades.length"
                :page-sizes="[25, 50, 100, 200]"
                layout="total, sizes, prev, pager, next"
                background
              />
            </div>
          </div>

          <div v-else class="trades-empty">
            <el-empty description="暂无回测交易明细" :image-size="64" />
          </div>
      </div>
    </SectionCard>
  </div>
</template>

<script setup>
import { ref, computed, watch } from 'vue'
import { ElMessage } from 'element-plus/es/components/message/index'
import VChart from 'vue-echarts'
import '@/utils/echarts'
import SectionCard from '@/components/common/SectionCard.vue'
import MetricGrid from '@/components/common/MetricGrid.vue'
import BacktestKLinePanel from '@/components/quant/BacktestKLinePanel.vue'
import { navCurveOption } from '@/utils/chartOption'
import { fmtNum, fmtPct } from '@/utils/format'
import { downloadBlob } from '@/utils/download'
import { useThemeRev } from '@/composables/useChartTheme'
import { getMonteCarlo } from '@/api/strategy'

const props = defineProps({
  result: { type: Object, default: null },
  strategy: { type: Object, default: null },
  loading: { type: Boolean, default: false },
  deletable: { type: Boolean, default: true },
})
defineEmits(['delete'])

const themeRev = useThemeRev()

const currentResult = computed(() => props.result)

// === 蒙特卡罗模拟（指标 bootstrap 置信区间） ===
const mcLoading = ref(false)
const mcData = ref(null)

const MC_METRIC_LABELS = {
  sharpe: '夏普比率',
  sortino: '索提诺',
  calmar: '卡玛',
  cagr: '年化收益',
  annual_volatility: '年化波动率',
  max_drawdown: '最大回撤',
  win_rate: '胜率',
}
// 以百分比展示的指标（小数 → 百分比）
const MC_PCT_METRICS = new Set(['cagr', 'annual_volatility', 'max_drawdown', 'win_rate'])
// 0 是有意义阈值的指标（收益/风险类），对它们做"CI 是否横跨 0"的显著性判断
const MC_SIG_METRICS = new Set(['sharpe', 'sortino', 'calmar', 'cagr'])
const MC_SIG_LABELS = { positive: '显著为正', negative: '显著为负', insignificant: '不显著' }

function mcFmt(v, key) {
  if (v == null || Number.isNaN(Number(v))) return '--'
  return MC_PCT_METRICS.has(key) ? fmtPct(v, 1) : fmtNum(v, 2)
}

const mcMetrics = computed(() => {
  const m = mcData.value?.metrics || {}
  return Object.keys(MC_METRIC_LABELS).map((key) => {
    const v = m[key] || {}
    const p = v.point
    let sig = null
    if (MC_SIG_METRICS.has(key) && v.lo != null && v.hi != null) {
      sig = v.lo > 0 ? 'positive' : v.hi < 0 ? 'negative' : 'insignificant'
    }
    return {
      key,
      label: MC_METRIC_LABELS[key],
      point: mcFmt(p, key),
      lo: mcFmt(v.lo, key),
      hi: mcFmt(v.hi, key),
      sig,
      tone: p > 0 ? 'tone-success' : p < 0 ? 'tone-danger' : '',
    }
  })
})

// 结论行：以夏普比率 CI 是否横跨 0 做一句话总结
const mcConclusion = computed(() => {
  const sh = mcData.value?.metrics?.sharpe || {}
  if (sh.lo == null || sh.hi == null) return ''
  if (sh.lo > 0) return '夏普比率 90% CI 全部为正，策略表现显著区别于随机。'
  if (sh.hi < 0) return '夏普比率 90% CI 全部为负，策略显著跑输随机（存在负 Alpha）。'
  return '夏普比率 90% CI 横跨 0，当前表现与随机无法区分——回测结果大概率靠运气。'
})

// Sharpe 重采样分布直方图
const mcChartCategories = computed(() => (mcData.value ? mcChartBins.value.map((b) => b.label) : []))
const mcChartCounts = computed(() => (mcData.value ? mcChartBins.value.map((b) => b.count) : []))
const mcChartBins = computed(() => {
  const samples = mcData.value?.sharpe_samples || []
  const BINS = 30
  if (!samples.length) return []
  const min = Math.min(...samples)
  const max = Math.max(...samples)
  const width = (max - min) / BINS || 1
  const counts = new Array(BINS).fill(0)
  for (const s of samples) {
    const idx = Math.min(BINS - 1, Math.max(0, Math.floor((s - min) / width)))
    counts[idx]++
  }
  return counts.map((count, i) => ({
    label: (min + i * width).toFixed(2),
    count,
  }))
})

const mcChartOption = computed(() => {
  void themeRev.value
  return {
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    grid: { left: 44, right: 12, top: 20, bottom: 28 },
    xAxis: { type: 'category', data: mcChartCategories.value, axisLabel: { fontSize: 10, rotate: 0 } },
    yAxis: { type: 'value', name: '频次', axisLabel: { fontSize: 10 } },
    series: [
      {
        type: 'bar',
        data: mcChartCounts.value,
        barWidth: '85%',
        itemStyle: { color: 'var(--primary)', borderRadius: [2, 2, 0, 0] },
      },
    ],
  }
})

async function runMonteCarlo() {
  const id = currentResult.value?.id
  if (id == null) return
  mcLoading.value = true
  try {
    mcData.value = await getMonteCarlo(id, {})
  } catch (e) {
    if (e !== 'cancel') ElMessage.error('蒙特卡罗模拟失败')
  } finally {
    mcLoading.value = false
  }
}

// 切换回测结果时清空旧 MC 数据
watch(
  () => currentResult.value?.id,
  () => {
    mcData.value = null
  }
)

const paramsOpen = ref([])
// 规则/信号型策略（策略库，result 带 template 字段）默认显示 K 线图：
// 单股全仓买卖的净值走势与价格高度重叠，直接看价格 + 买卖点 + 指标线更直观；
// 因子组合回测保持默认净值走势。两者均可手动切换。
const chartView = ref(props.result?.template ? 'kline' : 'nav') // 'nav' 净值走势 / 'kline' K线图
const tradeType = ref('all')
const tradeCode = ref('')
const tradePage = ref(1)
const tradePageSize = ref(25)
const tradeView = ref('group') // 'group' 按日折叠 / 'flat' 逐笔
const tradeOrder = ref('desc') // 'desc' 最近优先 / 'asc' 最早优先

const hasTrades = computed(() => {
  const t = currentResult.value?.trades
  return Array.isArray(t) && t.length > 0
})

const tradeStats = computed(() => {
  const t = currentResult.value?.trades || []
  const buys = t.filter((x) => x.action === 'BUY').length
  const sells = t.filter((x) => x.action === 'SELL').length
  const total = t.reduce((s, x) => s + (Number(x.total) || 0), 0)
  const realized = enrichedTrades.value.length ? enrichedTrades.value[enrichedTrades.value.length - 1].cumPnl : 0
  const tradeDays = new Set(t.map((x) => String(x.date || '').slice(0, 10)).filter(Boolean)).size
  return { buys, sells, total, realized, tradeDays }
})

// 逐笔补充"行为"（建仓/加仓/减仓/清仓）与累计持仓。
// 注意：qlib topk-dropout 调仓日会"整仓位卖旧+买新"，而落库顺序是 (date, action, code)，
// 即同一天先 BUY 后 SELL；若按原始顺序累计，会把新旧仓位叠加，持仓虚高、行为错乱。
// 因此同一标的、同一天内强制先处理 SELL 再处理 BUY，让"卖旧→清仓、买新→建仓"语义正确，
// 持仓列始终等于当日结束后的真实净持仓。
const enrichedTrades = computed(() => {
  const t = currentResult.value?.trades || []
  const orderKey = (x) => `${x.date || ''}|${x.code || '__single__'}|${x.action === 'SELL' ? 0 : 1}`
  const sorted = [...t].sort((a, b) => {
    const ka = orderKey(a)
    const kb = orderKey(b)
    return ka < kb ? -1 : ka > kb ? 1 : 0
  })
  const lots = {} // code -> { shares, costBasis }
  const startCapital =
    currentResult.value?.initial_capital ??
    currentResult.value?.metrics?.initial_capital ??
    0
  let cash = startCapital
  let cumPnl = 0
  return sorted.map((x) => {
    const key = String(x.code || '__single__')
    const lot = lots[key] || { shares: 0, costBasis: 0 }
    const prevShares = lot.shares
    const qty = Number(x.quantity) || 0
    const price = Number(x.price) || 0
    const total = Number(x.total) || 0
    const cost = Number(x.cost) || 0
    let behavior = x.action === 'BUY' ? '买入' : '卖出'
    let avgCost = 0
    let pnl = 0
    if (x.action === 'BUY') {
      behavior = prevShares > 0 ? '加仓' : '建仓'
      lot.shares += qty
      lot.costBasis += total + cost
      avgCost = lot.shares > 0 ? lot.costBasis / lot.shares : 0
    } else {
      behavior = prevShares - qty > 0 ? '减仓' : '清仓'
      avgCost = prevShares > 0 ? lot.costBasis / prevShares : price
      pnl = (price - avgCost) * qty - cost
      lot.shares = Math.max(0, prevShares - qty)
      lot.costBasis = Math.max(0, lot.costBasis - avgCost * Math.min(qty, prevShares))
    }
    lots[key] = lot
    const nextPos = lot.shares
    cash += x.action === 'BUY' ? -(total + cost) : total - cost
    cumPnl += pnl
    return { ...x, behavior, position: nextPos, cash, avgCost, pnl, cumPnl }
  })
})

const behaviorTag = (behavior) => {
  switch (behavior) {
    case '建仓':
      return { type: 'danger', effect: 'light' }
    case '加仓':
      return { type: 'danger', effect: 'plain' }
    case '减仓':
      return { type: 'success', effect: 'plain' }
    case '清仓':
      return { type: 'success', effect: 'light' }
    default:
      return { type: 'info', effect: 'plain' }
  }
}

const filteredTrades = computed(() => {
  let list = enrichedTrades.value
  if (tradeType.value !== 'all') {
    list = list.filter((x) => x.action === tradeType.value)
  }
  if (tradeCode.value) {
    const kw = tradeCode.value.trim().toUpperCase()
    if (kw)
      list = list.filter((x) =>
        String(x.code || '')
          .toUpperCase()
          .includes(kw)
      )
  }
  const sorted = [...list]
  if (tradeOrder.value === 'desc') sorted.reverse()
  else sorted.sort((a, b) => String(a.date || '').localeCompare(String(b.date || '')))
  return sorted
})

// 按调仓日分组（默认视图）：同一天先 SELL 后 BUY，组内直接复用 enriched 行的行为/盈亏
const tradeGroups = computed(() => {
  const groups = []
  const byDate = new Map()
  for (const row of filteredTrades.value) {
    const d = String(row.date || '').slice(0, 10)
    if (!byDate.has(d)) {
      const g = { date: d, trades: [], buys: 0, sells: 0, amount: 0, pnl: 0 }
      byDate.set(d, g)
      groups.push(g)
    }
    const g = byDate.get(d)
    g.trades.push(row)
    g.amount += Number(row.total) || 0
    g.pnl += Number(row.pnl) || 0
    if (row.action === 'BUY') g.buys++
    else g.sells++
  }
  return groups
})

// 分组视图分页（按日期页）
const pagedGroups = computed(() => {
  const start = (tradePage.value - 1) * tradePageSize.value
  return tradeGroups.value.slice(start, start + tradePageSize.value)
})

// 分组视图总条数（供分页 total 切换）
const tradeGroupCount = computed(() => tradeGroups.value.length)

// 净值图时间线：按日期聚合买卖笔数，映射到净值曲线下标
const tradeTimeline = computed(() => {
  const c = currentResult.value?.nav_curve || {}
  const dates = c.dates || []
  const nav = c.portfolio || []
  if (!dates.length) return { buy: [], sell: [] }
  const idx = new Map()
  dates.forEach((d, i) => idx.set(String(d).slice(0, 10), i))
  const agg = {}
  for (const x of enrichedTrades.value) {
    const d = String(x.date || '').slice(0, 10)
    const i = idx.get(d)
    if (i == null) continue
    agg[d] = agg[d] || { i, buys: 0, sells: 0 }
    if (x.action === 'BUY') agg[d].buys++
    else agg[d].sells++
  }
  const buy = []
  const sell = []
  for (const d in agg) {
    const { i, buys, sells } = agg[d]
    const base = Number(nav[i])
    if (isNaN(base)) continue
    if (buys) buy.push([i, base - 0.01, buys])
    if (sells) sell.push([i, base + 0.01, sells])
  }
  return { buy, sell }
})

// 分页展示：过滤器/视图/排序变化时重置到第 1 页
watch([tradeType, tradeCode, tradeView, tradeOrder], () => {
  tradePage.value = 1
})

// 序号起始值（供 # 列真实序号）
const tradeIndexStart = computed(() => (tradePage.value - 1) * tradePageSize.value + 1)

const pagedTrades = computed(() => {
  const start = (tradePage.value - 1) * tradePageSize.value
  return filteredTrades.value.slice(start, start + tradePageSize.value)
})

function fmtMoney(v) {
  if (v == null || isNaN(v)) return '--'
  const n = Number(v)
  if (Math.abs(n) >= 1e8) return (n / 1e8).toFixed(2) + '亿'
  if (Math.abs(n) >= 1e4) return (n / 1e4).toFixed(0) + '万'
  return n.toFixed(0)
}

// 逐笔金额精确展示（千分位 + 2 位小数）
function fmtMoneyExact(v) {
  if (v == null || isNaN(v)) return '--'
  return Number(v).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

// 盈亏带正负号展示（千分位，2 位小数）
function fmtPnlNum(v) {
  const n = Number(v)
  return isNaN(n) ? 0 : n
}
function fmtPnl(v) {
  const n = fmtPnlNum(v)
  const sign = n > 0 ? '+' : ''
  return sign + n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

// 成本价/成交价展示
function fmtPrice(v) {
  if (v == null || isNaN(v)) return '--'
  return Number(v).toFixed(2)
}

async function exportTrades() {
  try {
    const id = currentResult.value?.id
    if (id == null) return
    const { exportTrades } = await import('@/api/quant')
    const res = await exportTrades(id)
    downloadBlob(res?.data || res, `backtest_${id}_trades.csv`)
  } catch (e) {
    if (e !== 'cancel') ElMessage.error('导出交易明细失败')
  }
}

// === 指标卡（含含义与计算公式提示） ===
const METRIC_TIPS = {
  total: '总收益率 = 期末净值 / 期初净值 − 1，衡量整个回测区间的累计收益。',
  annual: '年化收益 = (1 + 总收益) ^ (365 / 回测天数) − 1，将累计收益折算为年化水平。',
  sharpe: '夏普比率 = (年化收益 − 无风险利率) / 年化波动率，衡量每承担一单位波动获得的超额回报。',
  excess: '超额收益 = 策略总收益 − 基准总收益，衡量相对基准的跑赢幅度。',
  benchmark: '基准收益 = 基准指数同期累计收益（如沪深300）。',
  volatility: '年化波动率 = 日收益标准差 × √252，衡量收益的波动程度。',
  max_drawdown: '最大回撤 = min(净值/前期最高净值 − 1)，衡量回测期间的最大亏损幅度。',
  sortino: '索提诺比率 = (年化收益 − 无风险利率) / 下行波动率，只惩罚下跌方向的波动。',
  calmar: '卡玛比率 = 年化收益 / 最大回撤绝对值，衡量收益与回撤的平衡。',
  win_rate: '胜率 = 盈利交易笔数 / 总交易笔数。',
  profit: '盈亏额 = 期末资金 − 初始资金，整个回测区间的绝对盈亏金额。',
}

// 核心指标：期末资产旁展示总收益 / 年化收益 / 夏普
const heroMetrics = computed(() => {
  const m = currentResult.value || {}
  const ar = m.annual_return
  const tr = totalReturn.value
  return [
    { key: 'total', label: '总收益', value: fmtPct(tr), tone: tr > 0 ? 'tone-success' : tr < 0 ? 'tone-danger' : '', tip: METRIC_TIPS.total },
    { key: 'annual', label: '年化收益', value: fmtPct(ar), tone: ar > 0 ? 'tone-success' : ar < 0 ? 'tone-danger' : '', tip: METRIC_TIPS.annual },
    { key: 'sharpe', label: '夏普比率', value: fmtNum(m.sharpe), tone: '', tip: METRIC_TIPS.sharpe },
  ]
})

// 指标总览：平铺紧凑（不分区），悬浮显示含义与公式
const flatMetrics = computed(() => {
  const m = currentResult.value || {}
  const er = m.excess_return
  const sig = (v) => (v > 0 ? 'tone-success' : v < 0 ? 'tone-danger' : '')
  return [
    { label: '超额收益', value: fmtPct(er), tone: sig(er), tip: METRIC_TIPS.excess },
    { label: '基准收益', value: fmtPct(m.benchmark_return), tone: '', tip: METRIC_TIPS.benchmark },
    { label: '年化波动率', value: fmtPct(m.annual_volatility), tone: '', tip: METRIC_TIPS.volatility },
    { label: '最大回撤', value: fmtPct(m.max_drawdown), tone: 'tone-danger', tip: METRIC_TIPS.max_drawdown },
    { label: '索提诺比率', value: fmtNum(m.sortino), tone: '', tip: METRIC_TIPS.sortino },
    { label: '卡玛比率', value: fmtNum(m.calmar), tone: '', tip: METRIC_TIPS.calmar },
    { label: '胜率', value: fmtPct(m.win_rate, 1), tone: '', tip: METRIC_TIPS.win_rate },
  ]
})

// 总收益率：区间累计收益 = 净值曲线最后一个点 - 1（曲线已归一化到 1.0）
const totalReturn = computed(() => {
  const p = currentResult.value?.nav_curve?.portfolio
  if (!Array.isArray(p) || !p.length) return null
  const last = Number(p[p.length - 1])
  return isNaN(last) ? null : last - 1
})

const DEFAULT_CAPITAL = 100000000 // 与后端 config.quant.initial_capital 默认一致（1 亿）

const initialCapital = computed(() => {
  const c = currentResult.value?.initial_capital ?? currentResult.value?.metrics?.initial_capital
  const n = Number(c)
  return isNaN(n) || n <= 0 ? DEFAULT_CAPITAL : n
})

const currentValue = computed(() => {
  const tr = totalReturn.value
  if (tr == null) return initialCapital.value
  return initialCapital.value * (1 + tr)
})

// 盈亏额 = 期末资金 − 初始资金
const profitDelta = computed(() => currentValue.value - initialCapital.value)

const rebalanceLabel = computed(() => {
  const map = { day: '每日', week: '每周', month: '每月' }
  return map[currentResult.value?.rebalance_freq] || currentResult.value?.rebalance_freq || '--'
})

// 基准：v2.4.1 起回测结果持久化 benchmark 快照，优先取结果自带值，
// 兼容旧数据（结果无 benchmark 时回退到策略当前值）
const benchmarkLabel = computed(() => {
  return currentResult.value?.benchmark || props.strategy?.benchmark || '--'
})

const tradeCount = computed(() => {
  const t = currentResult.value?.trades
  return Array.isArray(t) ? t.length : 0
})

// 回测执行口径（整手/成交价/滑点/费率）展示
const execConfigLabel = computed(() => {
  const c = currentResult.value?.metrics?.exec_config
  if (!c) return '--'
  const lot = c.trade_unit === 1 ? '任意整数股' : `整手${c.trade_unit === 'default(100)' ? 100 : c.trade_unit}股`
  const price = c.deal_price === 'open' ? 'T+1开盘' : 'T+1收盘'
  const slip = c.slippage_bps ? `${c.slippage_bps}bps` : '无滑点'
  return `${lot} / ${price} / 滑点${slip} / 费${(c.cost_buy * 1000).toFixed(1)}‰-${(c.cost_sell * 1000).toFixed(1)}‰`
})

// === 净值曲线数据 ===
const hasChart = computed(() => {
  const c = currentResult.value?.nav_curve
  return !!(c && c.dates && c.portfolio)
})

const chartTitle = computed(() => (chartView.value === 'nav' ? '净值走势' : 'K线图'))

const chartOption = computed(() => {
  void themeRev.value
  const c = currentResult.value?.nav_curve || {}
  return navCurveOption({
    dates: c.dates || [],
    portfolio: c.portfolio || [],
    benchmark: c.benchmark || [],
    yDigits: 1,
    tooltipFormatter: (params) => {
      const arr = Array.isArray(params) ? params : [params]
      const lines = arr.map((p) => {
        if (p.seriesType === 'scatter') {
          const count = p.value?.[2] ?? 0
          return `${p.marker} ${p.seriesName}: <b>${count}</b> 笔`
        }
        return `${p.marker} ${p.seriesName}: <b>${Number(p.value).toFixed(2)}</b>`
      })
      return `${params[0].axisValue}<br/>${lines.join('<br/>')}`
    },
    extraSeries: [
      // 交易时间线：买入标在净值线下方，卖出标在上方
      {
        name: '买入',
        data: tradeTimeline.value.buy,
        type: 'scatter',
        symbol: 'triangle',
        symbolSize: 8,
        itemStyle: { color: 'rgba(210, 69, 69, 0.9)', borderColor: '#fff', borderWidth: 1 },
        z: 10,
      },
      {
        name: '卖出',
        data: tradeTimeline.value.sell,
        type: 'scatter',
        symbol: 'triangle',
        symbolRotate: 180,
        symbolSize: 8,
        itemStyle: { color: 'rgba(31, 157, 107, 0.9)', borderColor: '#fff', borderWidth: 1 },
        z: 10,
      },
    ],
  })
})
</script>

<style scoped lang="scss">
.cell-name {
  font-weight: var(--font-weight-medium);
  color: var(--text-primary);
}
.cell-mono {
  font-family: var(--font-mono);
  color: var(--text-secondary);
}
.cell-tnum {
  font-variant-numeric: tabular-nums;
}
.cell-code {
  font-family: var(--font-mono);
  color: var(--text-secondary);
}
.text-success {
  color: var(--success, #1f9d6b);
}
.text-danger {
  color: var(--danger, #d24545);
}

.strategy-result {
  padding: 4px 8px 16px;
  background: var(--bg-card);

  &__head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    flex-wrap: wrap;
    margin-bottom: 12px;
    padding-bottom: 12px;
    border-bottom: 1px solid var(--border-light);
  }

  &__title {
    display: flex;
    align-items: center;
    gap: 10px;
    flex-wrap: wrap;
  }

  &__period {
    font-size: 12px;
    color: var(--text-secondary);
    background: var(--bg-tertiary);
    border: 1px solid var(--border-light);
    padding: 2px 10px;
    border-radius: var(--radius-full);
    font-variant-numeric: tabular-nums;
  }

  &__actions {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-wrap: wrap;
  }
}
.result-overview {
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  padding: 14px 16px 16px;
  margin-bottom: 12px;
}
.result-sub-card {
  margin-top: 12px;

  & + & {
    margin-top: 12px;
  }
}

// 回测指标区
.result-hero {
  display: grid;
  grid-template-columns: minmax(0, 0.9fr) repeat(3, minmax(120px, 176px));
  gap: var(--space-sm);
  margin-bottom: 12px;

  &__primary {
    background: var(--bg-tertiary);
    border: 1px solid var(--border);
    border-left: 3px solid var(--primary);
    border-radius: var(--radius-lg);
    padding: 10px 12px;
    display: flex;
    flex-direction: column;
    justify-content: center;
    gap: 1px;
    min-width: 0;
  }

  &__primary-label {
    font-size: var(--font-size-xs, 12px);
    color: var(--text-tertiary);
    letter-spacing: 0.04em;
  }

  &__primary-value {
    font-size: 18px;
    font-weight: 600;
    font-variant-numeric: tabular-nums;
    line-height: 1.25;
  }

  &__primary-sub {
    font-size: 12px;
    color: var(--text-tertiary);
    font-variant-numeric: tabular-nums;
  }

  &__delta {
    margin-left: 8px;
    font-size: 12px;
    font-weight: 600;
    padding: 1px 8px;
    border-radius: var(--radius-full);
    background: rgba(31, 157, 107, 0.12);
    color: var(--success);
    font-variant-numeric: tabular-nums;
  }
  &__delta.tone-danger {
    background: rgba(210, 69, 69, 0.12);
    color: var(--danger);
  }

  &__item {
    background: var(--bg-tertiary, #f5f7fa);
    border-radius: 8px;
    padding: 12px 14px;
    display: flex;
    flex-direction: column;
    justify-content: center;
    gap: 4px;
    min-width: 0;
  }

  &__item-label {
    font-size: var(--font-size-xs, 12px);
    color: var(--text-tertiary);
  }

  &__item-value {
    font-size: 18px;
    font-weight: 700;
    font-variant-numeric: tabular-nums;
  }
}

// 指标悬浮提示
.metrics-section {
  margin-bottom: 12px;
}
.result-params-collapse {
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  overflow: hidden;
  margin-bottom: 0;

  :deep(.el-collapse-item__header) {
    font-size: 13px;
    color: var(--text-secondary);
    padding: 0 4px;
  }
}
.result-params-collapse-title {
  font-size: 13px;
  color: var(--text-secondary);
  font-weight: 500;
}

.result-params {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
  gap: 12px 16px;
  padding: 12px 16px;
  margin-bottom: 0;
  background: var(--bg-secondary);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  min-height: 44px;

  &__item {
    display: flex;
    flex-direction: column;
    gap: 2px;
    min-width: 0;
  }

  &__label {
    font-size: var(--font-size-xs, 12px);
    color: var(--text-tertiary, #8a9099);
  }

  &__value {
    font-size: 13px;
    font-weight: 600;
    color: var(--text-primary, #1f2329);
    font-variant-numeric: tabular-nums;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
}
.tone-success {
  color: var(--success);
}
.tone-danger {
  color: var(--danger);
}

// 蒙特卡罗模拟面板
.mc-note {
  font-size: 12px;
  color: var(--text-secondary);
  font-variant-numeric: tabular-nums;
}
.mc-body {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.mc-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
  gap: 12px;
}
.mc-card {
  padding: 10px 12px;
  background: var(--bg-tertiary, #f5f7fa);
  border-radius: 8px;

  &__label {
    font-size: 12px;
    color: var(--text-tertiary);
    margin-bottom: 2px;
  }

  &__point {
    font-size: 16px;
    font-weight: 700;
    font-variant-numeric: tabular-nums;
    color: var(--text-primary);
  }

  &__ci {
    font-size: 12px;
    color: var(--text-secondary);
    font-variant-numeric: tabular-nums;
  }

  &__sig {
    display: inline-block;
    margin-top: 4px;
    padding: 1px 8px;
    border-radius: var(--radius-full);
    font-size: 11px;
    font-weight: 600;

    &--positive {
      background: rgba(31, 157, 107, 0.12);
      color: var(--success);
    }

    &--negative,
    &--insignificant {
      background: rgba(210, 69, 69, 0.12);
      color: var(--danger);
    }
  }
}
.mc-conclusion {
  margin: 0;
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary);
  padding: 8px 12px;
  border-radius: var(--radius-sm);
  background: var(--bg-secondary);
  border-left: 3px solid var(--primary);
}
.mc-chart {
  &__title {
    font-size: 13px;
    color: var(--text-secondary);
    margin-bottom: 4px;
  }

  &__body {
    width: 100%;
    height: 180px;
  }
}
.mc-desc {
  font-size: 12px;
  color: var(--text-tertiary);
  line-height: 1.6;
  margin: 0;
}

// 净值曲线卡
.chart-view-switch {
  margin-right: 12px;
  vertical-align: middle;
}
.chart-legend {
  display: flex;
  align-items: center;
  gap: var(--space-lg);
  font-size: var(--font-size-base);
  color: var(--text-tertiary);
}
.legend-item {
  display: inline-flex;
  align-items: center;
  gap: var(--space-xs);
}
.legend-line {
  display: inline-block;
  width: 16px;
  height: 2px;
}
.legend-line--solid {
  background: var(--primary);
}
.legend-line--dashed {
  border-top: 1.5px dashed var(--text-tertiary);
  height: 0;
}
.legend-dot {
  display: inline-block;
  width: 8px;
  height: 8px;
  margin-right: 5px;
  border-radius: 2px;
  border: 1px solid #fff;
}
.legend-dot--buy {
  background: rgba(210, 69, 69, 0.9);
}
.legend-dot--sell {
  background: rgba(31, 157, 107, 0.9);
}
.trade-legend-dot {
  display: inline-block;
  width: 10px;
  height: 10px;
  margin-right: 6px;
  border-radius: 50%;
}
.chart-body {
  width: 100%;
  height: 260px;
}

/* 交易明细（回测动作与行为） */
.trades-filters {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 12px;
  flex-wrap: wrap;
}

.trade-group-detail {
  padding: 4px 0 8px 40px;
}

.trade-datetime {
  display: flex;
  flex-direction: column;
  line-height: 1.4;

  &__date {
    color: var(--text-primary, #1f2329);
    font-variant-numeric: tabular-nums;
  }

  &__time {
    font-size: 11px;
    color: var(--text-tertiary, #8a9099);
    font-variant-numeric: tabular-nums;
  }
}

.trades-pagination {
  display: flex;
  justify-content: flex-end;
  margin-top: 12px;
}

/* 交易明细 · 收益日历（合并双栏） */
.trade-workspace {
  &__body {
    display: flex;
    flex-direction: column;
    gap: 12px;
  }

  :deep(.el-table) {
    --el-table-border-color: var(--border-light, #eef2f7);
    --el-table-header-bg-color: var(--bg-tertiary, #f1f4f9);
    --el-table-header-text-color: var(--text-secondary, #5b6b85);
    --el-table-row-hover-bg-color: var(--bg-hover, rgba(22, 33, 58, 0.04));

    th.el-table__cell {
      font-weight: 600;
    }
  }
}
      .trades-empty {
  display: flex;
  justify-content: center;
  padding: 8px 0;
}
</style>