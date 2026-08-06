<template>
  <PageContainer narrow>
    <header class="page-header">
      <div class="page-header__lead">
        <h1 class="page-header__title">宏观指标</h1>
        <p class="page-header__subtitle">
          东财 + akshare 宏观数据（PMI/CPI/PPI/GDP/国债/Shibor/汇率等），同步后广播为 qlib 因子字段
        </p>
      </div>
    </header>

    <!-- 宏观指标：最新值 + 点击指标正面下方展开走势（按需加载） -->
    <SectionCard title="宏观指标" class="mb-6">
      <template #extra>
        <div class="snapshot-toolbar">
          <el-button size="small" @click="reloadAll" :loading="loading">刷新</el-button>
          <el-button size="small" type="primary" @click="doSync" :loading="syncing">
            {{ syncing ? '同步中...' : '同步宏观数据' }}
          </el-button>
        </div>
      </template>

      <div v-if="syncMessage" class="sync-message">{{ syncMessage }}</div>
      <div v-if="syncProgress" class="sync-progress">
        <div class="progress-header">
          <span class="progress-status">{{ syncProgress.message || '同步中...' }}</span>
          <span class="progress-pct">{{ (syncProgress.progress_pct || 0).toFixed(1) }}%</span>
        </div>
        <el-progress
          :percentage="syncProgress.progress_pct || 0"
          :status="syncProgress.status === 'failed' ? 'exception' : syncProgress.status === 'done' ? 'success' : ''"
          :stroke-width="12"
          :show-text="false"
        />
      </div>

      <template v-if="snapshotItems.length">
        <div v-for="group in snapshotGroups" :key="group.label" class="snapshot-group">
          <div class="snapshot-group-title">{{ group.label }}</div>
          <div class="snapshot-grid">
            <!-- 指标卡固定尺寸；点击后在卡片正下方展开占满整行的走势面板 -->
            <template v-for="opt in group.options" :key="opt.key">
              <div
                class="snapshot-cell"
                :class="{ 'snapshot-cell--active': isExpanded(opt) }"
                :title="opt.fields[0].item.available_date + (opt.fields[0].item.prevDate ? '，较 ' + opt.fields[0].item.prevDate : '')"
                @click="toggleExpand(opt, $event)"
              >
                <div class="snapshot-label">{{ opt.fields.map((f) => f.name).join(' / ') }}</div>
                <div class="snapshot-value" :class="trendClass(opt.fields[0].item.change)">
                  {{ opt.fields.map((f) => formatValue(f.item.value)).join(' / ') }}
                  <span v-if="opt.fields[0].item.unit" class="snapshot-unit">{{ opt.fields[0].item.unit }}</span>
                </div>
                <div class="snapshot-trends">
                  <span
                    v-for="f in trendFields(opt)"
                    :key="f.field"
                    class="snapshot-trend"
                    :class="trendClass(f.item.change)"
                  >
                    <el-icon v-if="f.item.change > 0"><CaretTop /></el-icon>
                    <el-icon v-else><CaretBottom /></el-icon>
                    {{ fmtChange(f.item.change) }}
                  </span>
                </div>
                <div class="snapshot-date">{{ opt.fields[0].item.available_date }}</div>
                <div class="snapshot-action">
                  <el-icon v-if="isExpanded(opt)"><CaretTop /></el-icon>
                  <el-icon v-else><CaretBottom /></el-icon>
                  {{ isExpanded(opt) ? '收起走势' : '查看走势' }}
                </div>
              </div>

              <!-- 走势面板：悬浮于被点击卡片正下方（占满整行，不影响其它卡片布局） -->
              <transition name="expand">
                <div v-if="expandedCard && isExpanded(opt)" class="snapshot-chart" :style="{ top: expandedTop + 'px' }">
                  <div class="snapshot-chart__head">
                    <div class="snapshot-chart__title">
                      <span class="snapshot-chart__name">{{ expandedCard.label }}</span>
                      <span class="snapshot-chart__fields">{{ (expandedCard.seriesFields || expandedCard.fields).map((f) => f.name).join(' / ') }}</span>
                    </div>
                    <el-radio-group v-model="timeRange" size="small">
                      <el-radio-button v-for="r in timeOptions" :key="r.key" :value="r.key">{{ r.label }}</el-radio-button>
                    </el-radio-group>
                  </div>
                  <div v-if="seriesLoading" class="chart-wrap">
                    <el-skeleton :rows="8" animated />
                  </div>
                  <v-chart v-else-if="seriesData.length" :option="chartOption" class="chart-macro" autoresize />
                  <el-empty v-else description="暂无数据，请先同步宏观指标" :image-size="64" />
                </div>
              </transition>
            </template>
          </div>
        </div>
      </template>
      <el-empty v-else description="暂无数据，请先同步宏观指标" :image-size="64" />
    </SectionCard>

    <!-- 同步状态（默认折叠） -->
    <SectionCard title="同步状态" class="mb-6" collapsible collapsed>
      <el-table :data="statusItems" size="small" empty-text="暂无数据">
        <el-table-column prop="indicator" label="指标" width="120" align="center" />
        <el-table-column prop="field_name" label="字段" width="120" align="center" />
        <el-table-column prop="count" label="记录数" width="100" align="right" />
        <el-table-column prop="latest_date" label="最新可用日" align="center" />
      </el-table>
    </SectionCard>
  </PageContainer>
</template>

<script setup>
defineOptions({ name: 'QuantMacro' })
import { ref, computed, watch, onMounted, onBeforeUnmount } from 'vue'
import { ElMessage } from 'element-plus/es/components/message/index'
import { CaretTop, CaretBottom } from '@element-plus/icons-vue'
import PageContainer from '@/components/common/PageContainer.vue'
import SectionCard from '@/components/common/SectionCard.vue'
import VChart from 'vue-echarts'
import '@/utils/echarts'
import { syncMacro, getMacroIndicators, getMacroStatus, getMacroSnapshot } from '@/api/macro'
import { getSyncProgress } from '@/api/quant'
import { chartTheme, echartPalette as C } from '@/utils/chartTheme'
import { useThemeRev } from '@/composables/useChartTheme'

const themeRev = useThemeRev()

// 指标选项（与后端 MACRO_INDICATORS / AKSHARE_INDICATORS 一致）
// 一个选项可包含多个字段序列，会在同一张图里叠加展示（如制造/非制造 PMI）
const fieldOptions = [
  // 景气/价格
  {
    key: 'pmi',
    indicator: 'PMI',
    label: 'PMI',
    group: '景气/价格',
    markLine: 50,
    fields: [
      { field: 'pmi', name: '制造业PMI', color: C.blue },
      { field: 'pmi_nm', name: '非制造业PMI', color: C.orangeAlt },
    ],
  },
  {
    key: 'cpi',
    indicator: 'CPI',
    label: 'CPI同比(%)',
    group: '景气/价格',
    fields: [{ field: 'cpi', name: 'CPI同比', color: C.purple }],
  },
  {
    key: 'ppi',
    indicator: 'PPI',
    label: 'PPI同比(%)',
    group: '景气/价格',
    fields: [{ field: 'ppi', name: 'PPI同比', color: C.teal }],
  },
  {
    key: 'gdp',
    indicator: 'GDP',
    label: 'GDP同比(%)',
    group: '景气/价格',
    fields: [{ field: 'gdp', name: 'GDP同比', color: C.grass }],
  },
  // 利率
  {
    key: 'cn_trsy',
    indicator: 'TREASURY',
    label: '中债收益率',
    group: '利率',
    fields: [
      { field: 'trsy2y', name: '中债2Y', color: C.blue },
      { field: 'trsy5y', name: '中债5Y', color: C.green },
      { field: 'trsy10y', name: '中债10Y', color: C.gold },
      { field: 'trsy30y', name: '中债30Y', color: C.red },
    ],
  },
  {
    key: 'trsy_spread',
    indicator: 'TREASURY',
    label: '期限利差',
    group: '利率',
    fields: [{ field: 'trsy_spread_10y2y', name: '利差10Y-2Y', color: C.cyan }],
  },
  {
    key: 'us_trsy',
    indicator: 'TREASURY',
    label: '美债收益率',
    group: '利率',
    fields: [{ field: 'us_trsy10y', name: '美债10Y', color: C.forest }],
  },
  {
    key: 'shibor',
    indicator: 'SHIBOR',
    label: 'Shibor',
    group: '利率',
    fields: [
      { field: 'shibor_on', name: '隔夜', color: C.blue },
      { field: 'shibor_1w', name: '1周', color: C.green },
      { field: 'shibor_3m', name: '3月', color: C.gold },
      { field: 'shibor_1y', name: '1年', color: C.red },
    ],
  },
  {
    key: 'lpr',
    indicator: 'LPR',
    label: 'LPR',
    group: '利率',
    fields: [
      { field: 'lpr1y', name: 'LPR 1Y', color: C.blue },
      { field: 'lpr5y', name: 'LPR 5Y', color: C.orangeAlt },
    ],
  },
  {
    key: 'repofr',
    indicator: 'REPO_FR',
    label: '回购定盘利率',
    group: '利率',
    fields: [
      { field: 'fr001', name: 'FR001', color: C.blue },
      { field: 'fr007', name: 'FR007(≈R007)', color: C.orangeAlt },
      { field: 'fr014', name: 'FR014', color: C.teal },
    ],
  },
  {
    key: 'repofdr',
    indicator: 'REPO_FDR',
    label: '银银间回购',
    group: '利率',
    fields: [
      { field: 'fdr001', name: 'FDR001', color: C.blue },
      { field: 'fdr007', name: 'FDR007(≈DR007)', color: C.orangeAlt },
      { field: 'fdr014', name: 'FDR014', color: C.teal },
    ],
  },
  // 商品/汇率
  {
    key: 'commodity',
    indicator: 'COMMODITY',
    label: '大宗商品指数',
    group: '商品/汇率',
    fields: [{ field: 'commodity_idx', name: '商品价格指数', color: C.blue }],
  },
  {
    key: 'copper',
    indicator: 'COPPER',
    label: '沪铜',
    group: '商品/汇率',
    fields: [{ field: 'copper_close', name: '沪铜主力收盘价', color: C.red }],
  },
  {
    key: 'crude',
    indicator: 'CRUDE_OIL',
    label: '原油',
    group: '商品/汇率',
    fields: [{ field: 'crude_close', name: '原油SC主力', color: C.orange }],
  },
  {
    key: 'fx',
    indicator: 'FX',
    label: '人民币汇率',
    group: '商品/汇率',
    fields: [{ field: 'usdcny_mid', name: '美元中间价', color: C.blue }],
  },
  // 风险/情绪
  {
    key: 'ivix',
    indicator: 'IVIX',
    label: '波指iVIX',
    group: '风险/情绪',
    fields: [{ field: 'ivix', name: '50ETF波动率指数', color: C.red }],
  },
  {
    key: 'futif',
    indicator: 'FUTURES_IF',
    label: '股指期货IF',
    group: '风险/情绪',
    fields: [
      { field: 'if_close', name: 'IF主力收盘价', color: C.blue },
      { field: 'if_hold', name: 'IF持仓量', color: C.orangeAlt, axis: 'right' },
    ],
  },
  {
    key: 'futic',
    indicator: 'FUTURES_IC',
    label: '中证500期货',
    group: '风险/情绪',
    fields: [
      { field: 'ic_close', name: 'IC主力收盘价', color: C.blue },
      { field: 'ic_hold', name: 'IC持仓量', color: C.orangeAlt, axis: 'right' },
    ],
  },
  {
    key: 'futtf',
    indicator: 'FUTURES_TF',
    label: '国债期货',
    group: '风险/情绪',
    fields: [{ field: 'tf_close', name: 'TF主力收盘价', color: C.cyan }],
  },
  {
    key: 'gold',
    indicator: 'GOLD',
    label: '沪金',
    group: '风险/情绪',
    fields: [{ field: 'au_close', name: '沪金AU主力', color: C.gold }],
  },
  // 货币/信贷
  {
    key: 'moneysupply',
    indicator: 'MONEY_SUPPLY',
    label: '货币供应',
    group: '货币/信贷',
    fields: [
      { field: 'm0_yoy', name: 'M0同比', color: C.blue },
      { field: 'm1_yoy', name: 'M1同比', color: C.orangeAlt },
      { field: 'm2_yoy', name: 'M2同比', color: C.teal },
    ],
  },
  {
    key: 'socialfinance',
    indicator: 'SOCIAL_FINANCE',
    label: '社会融资',
    group: '货币/信贷',
    fields: [
      { field: 'social_finance', name: '社融增量', color: C.blue },
      { field: 'sf_rmb_loan', name: '社融-人民币贷款', color: C.orangeAlt },
    ],
  },
  {
    key: 'loan',
    indicator: 'LOAN',
    label: '新增贷款',
    group: '货币/信贷',
    fields: [
      { field: 'new_loan', name: '新增贷款', color: C.blue },
      { field: 'new_loan_yoy', name: '新增贷款同比', color: C.orangeAlt },
    ],
  },
  {
    key: 'margin',
    indicator: 'MARGIN',
    label: '两融余额',
    group: '货币/信贷',
    fields: [
      { field: 'margin_balance', name: '沪市两融余额', color: C.blue },
      { field: 'margin_balance_sz', name: '深市两融余额', color: C.orangeAlt },
    ],
  },
]

// 时间范围（默认最近 5 年）
const timeOptions = [
  { key: '1Y', label: '1年' },
  { key: '3Y', label: '3年' },
  { key: '5Y', label: '5年' },
  { key: 'ALL', label: '全部' },
]

const timeRange = ref('5Y')
const loading = ref(false)
const syncing = ref(false)
const seriesLoading = ref(false)
const syncMessage = ref('')
const syncProgress = ref(null)
let progressTimer = null
const seriesData = ref([])
const statusItems = ref([])
const snapshotItems = ref([])

// 当前展开的走势卡片（点击最新值卡片后按需加载走势）
const expandedCard = ref(null)
// 悬浮面板距离所在分组顶部的偏移（由被点击卡片计算）
const expandedTop = ref(0)

function isExpanded(card) {
  return card != null && expandedCard.value != null && expandedCard.value.key === card.key
}
// 点击指标卡片：展开/收起其走势（切换时按需拉取）
function toggleExpand(card, e) {
  if (!card) return
  if (expandedCard.value && expandedCard.value.key === card.key) {
    expandedCard.value = null
    seriesData.value = []
  } else {
    const el = e && e.currentTarget
    if (el) expandedTop.value = el.offsetTop + el.offsetHeight + 12
    expandedCard.value = card
    seriesData.value = []
    loadSeries(card)
  }
}

// 时间范围切换后，若已有展开的指标则重新拉取
watch(timeRange, () => {
  if (expandedCard.value) loadSeries(expandedCard.value)
})

// 刷新：重拉状态 + 快照，并重载当前展开的走势
async function reloadAll() {
  loading.value = true
  try {
    await loadStatus()
    if (expandedCard.value) await loadSeries(expandedCard.value)
  } finally {
    loading.value = false
  }
}

// 快照卡片：按「指标 option」合并（如 PMI 的制造业/非制造业合成一张卡），再按分类分组展示
const snapshotGroups = computed(() => {
  const order = ['景气/价格', '利率', '商品/汇率', '风险/情绪', '货币/信贷']
  const byField = new Map(snapshotItems.value.map((it) => [it.field_name, it]))
  const byGroup = {}
  for (const opt of fieldOptions) {
    const fields = (opt.fields ?? [])
      .map((f) => ({ field: f.field, name: f.name, item: byField.get(f.field) }))
      .filter((x) => x.item)
    if (!fields.length) continue
    const g = opt.group || '其他'
    // 每张卡片最多展示 2 个字段，超出部分拆成独立卡片；
    // 属于同一族（如中债所有期限）的卡片共用 seriesFields，点击任一张一起展开整族走势
    const seriesFields = fields
    for (let i = 0; i < fields.length; i += 2) {
      ;(byGroup[g] = byGroup[g] || []).push({
        key: i === 0 ? opt.key : `${opt.key}_${i / 2}`,
        label: opt.label,
        indicator: opt.indicator,
        group: opt.group,
        fields: fields.slice(i, i + 2),
        seriesFields,
      })
    }
  }
  const groups = order.filter((g) => byGroup[g]).map((g) => ({ label: g, options: byGroup[g] }))
  const rest = Object.keys(byGroup).filter((g) => !order.includes(g))
  for (const g of rest) groups.push({ label: g, options: byGroup[g] })
  return groups
})

function rangeStartDate() {
  if (timeRange.value === 'ALL') return null
  const start = new Date()
  switch (timeRange.value) {
    case '1Y':
      start.setFullYear(start.getFullYear() - 1)
      break
    case '3Y':
      start.setFullYear(start.getFullYear() - 3)
      break
    case '5Y':
      start.setFullYear(start.getFullYear() - 5)
      break
    default:
      return null
  }
  return start.toISOString().slice(0, 10)
}

// 数值显示：千分位 + 最多 2 位小数（大数如两融余额不再一长串）
function formatValue(v) {
  if (v == null) return '--'
  const n = Number(v)
  if (!Number.isFinite(n)) return '--'
  return n.toLocaleString('zh-CN', { maximumFractionDigits: 2 })
}

// 荣枯线穿越点图标（向上/向下箭头）
const RISING_ICON = 'path://M448 96 L832 512 L608 512 L608 928 L608 928 L288 928 L288 512 L64 512 Z'
const FALLING_ICON = 'path://M448 896 L832 480 L608 480 L608 64 L608 64 L288 64 L288 480 L64 480 Z'

// 计算某序列相对基准线的穿越点（越过 50 的拐点）
function crossingPoints(points, base) {
  const pts = []
  for (let i = 1; i < points.length; i++) {
    const p0 = points[i - 1]
    const p1 = points[i]
    if (p0.value == null || p1.value == null) continue
    const d0 = p0.value - base
    const d1 = p1.value - base
    if (d0 === 0 || d1 === 0) continue
    if (d0 * d1 < 0) {
      const rising = d1 > 0
      pts.push({
        coord: [p1.date, base],
        symbol: rising ? RISING_ICON : FALLING_ICON,
        symbolSize: 10,
        symbolOffset: [0, rising ? 8 : -8],
        itemStyle: { color: rising ? chartTheme.up() : chartTheme.down() },
        label: { show: false },
      })
    }
  }
  return pts
}

const chartOption = computed(() => {
  void themeRev.value
  // 合并所有序列的日期作为 x 轴（同一指标内多字段通常同日发布）
  const dateSet = new Set()
  for (const s of seriesData.value) {
    for (const p of s.points) dateSet.add(p.date)
  }
  const dates = [...dateSet].sort()
  const opt = expandedCard.value

  // PMI 荣枯线（50）与扩张/收缩背景分区：上方淡绿、下方淡红
  const markArea =
    opt?.markLine != null
      ? (() => {
          const vals = seriesData.value.flatMap((s) => s.points.map((p) => p.value)).filter((v) => v != null)
          const lo = Math.min(opt.markLine, ...(vals.length ? vals : [opt.markLine]))
          const hi = Math.max(opt.markLine, ...(vals.length ? vals : [opt.markLine]))
          return {
            silent: true,
            data: [
              [
                { yAxis: opt.markLine, itemStyle: { color: chartTheme.areaAbove() } },
                { yAxis: hi, itemStyle: { color: chartTheme.areaAbove() } },
              ],
              [
                { yAxis: lo, itemStyle: { color: chartTheme.areaBelow() } },
                { yAxis: opt.markLine, itemStyle: { color: chartTheme.areaBelow() } },
              ],
            ],
          }
        })()
      : undefined

  const series = seriesData.value.map((s) => {
    const valByDate = new Map(s.points.map((p) => [p.date, p.value]))
    const fieldCfg = opt?.fields.find((x) => x.field === s.field)
    const cfg = {
      name: s.name,
      type: 'line',
      smooth: true,
      symbol: 'circle',
      symbolSize: 5,
      data: dates.map((d) => (valByDate.has(d) ? valByDate.get(d) : null)),
      lineStyle: { width: 2, color: s.color },
      itemStyle: { color: s.color },
    }
    // 量纲差异大的字段（如持仓量 vs 收盘价）放右轴，避免价格线被压平
    if (fieldCfg?.axis === 'right') cfg.yAxisIndex = 1
    if (seriesData.value.length === 1) cfg.areaStyle = { opacity: 0.12, color: s.color }
    const crosses = opt?.markLine != null ? crossingPoints(s.points, opt.markLine) : []
    if (crosses.length) cfg.markPoint = { symbol: 'pin', data: crosses, silent: true }
    return cfg
  })

  // 荣枯线作为独立序列展示在图例（可开关），不再在线上绘制文字
  if (opt?.markLine != null) {
    series.push({
      name: `荣枯线 ${opt.markLine}`,
      type: 'line',
      symbol: 'none',
      data: dates.map(() => opt.markLine),
      lineStyle: { type: 'dashed', color: chartTheme.baseline(), width: 2 },
      itemStyle: { color: chartTheme.baseline() },
      tooltip: { show: false },
      silent: true,
      z: 0,
      markArea: JSON.parse(JSON.stringify(markArea)),
    })
  }

  return {
    tooltip: { trigger: 'axis' },
    textStyle: { color: chartTheme.axisText() },
    legend: { top: 0, left: 8, textStyle: { color: chartTheme.axisText() } },
    grid: { left: 48, right: 48, top: 40, bottom: 40 },
    xAxis: { type: 'category', data: dates, axisLabel: { fontSize: 11, color: chartTheme.axisText() } },
    yAxis: opt?.fields.some((f) => f.axis === 'right')
      ? [
          { type: 'value', scale: true, axisLabel: { color: chartTheme.axisText() } },
          { type: 'value', scale: true, axisLabel: { color: chartTheme.axisText() }, splitLine: { show: false } },
        ]
      : { type: 'value', scale: true, axisLabel: { color: chartTheme.axisText() } },
    series,
  }
})

// 按需加载某指标走势（未指定时重载当前已展开的指标）
async function loadSeries(opt) {
  const target = opt || expandedCard.value
  if (!target) {
    seriesData.value = []
    return
  }
  seriesLoading.value = true
  try {
    const startDate = rangeStartDate()
    const fields = target.seriesFields || target.fields
    const series = await Promise.all(
      fields.map(async (f) => {
        const res = await getMacroIndicators({ indicator: target.indicator, field: f.field })
        const items = res?.items ?? []
        return {
          field: f.field,
          name: f.name,
          color: f.color,
          points: items
            .filter((d) => !startDate || d.available_date >= startDate)
            .map((d) => ({ date: d.available_date, value: d.value })),
        }
      })
    )
    seriesData.value = series
  } catch {
    seriesData.value = []
  } finally {
    seriesLoading.value = false
  }
}

async function loadStatus() {
  try {
    const res = await getMacroStatus()
    statusItems.value = res?.items ?? []
    // 快照：每个指标字段的最新一条 + 环比变化（最新值 - 上一条值），来自轻量 /macro/snapshot
    const labelMap = {}
    const groupMap = {}
    for (const opt of fieldOptions) {
      for (const f of opt.fields) {
        labelMap[f.field] = f.name
        groupMap[f.field] = opt.group
      }
    }
    const snap = await getMacroSnapshot()
    snapshotItems.value = (snap?.items ?? []).map((it) => {
      const latestVal = it.latest_value != null ? Number(it.latest_value) : null
      const prevVal = it.prev_value != null ? Number(it.prev_value) : null
      const change =
        prevVal != null && latestVal != null && !Number.isNaN(latestVal) && !Number.isNaN(prevVal)
          ? latestVal - prevVal
          : null
      return {
        indicator: it.indicator,
        field_name: it.field_name,
        value: it.latest_value,
        unit: it.unit,
        available_date: it.latest_date,
        prevDate: it.prev_date,
        label: labelMap[it.field_name] || it.field_name,
        group: groupMap[it.field_name] || '其他',
        change,
      }
    })
  } catch {
    statusItems.value = []
    snapshotItems.value = []
  }
}

function hasChange(v) {
  return v !== null && v !== undefined && Number(v) !== 0
}

function trendFields(opt) {
  return opt.fields.filter((f) => hasChange(f.item.change))
}

function trendClass(v) {
  const n = Number(v)
  if (n === null || n === undefined || Number.isNaN(n) || n === 0) return ''
  return n > 0 ? 'is-up' : 'is-down'
}

function fmtChange(v) {
  const n = Number(v)
  if (Number.isNaN(n)) return ''
  return `${n > 0 ? '+' : ''}${Number(n.toFixed(2))}`
}

async function doSync() {
  syncing.value = true
  syncMessage.value = ''
  try {
    await syncMacro()
    syncMessage.value = '同步已提交（后台执行），进度见下方。'
    ElMessage.success('宏观同步已提交')
    startMacroProgressPolling()
    // 兜底：即使进度未显示，也刷新一次状态
    setTimeout(() => {
      loadStatus()
      loadSeries()
    }, 5000)
  } catch (e) {
    if (e !== 'cancel') ElMessage.error('宏观同步提交失败: ' + (e?.message || e))
  } finally {
    syncing.value = false
  }
}

// 轮询宏观同步进度（共享 /quant/data/sync-progress，kind=macro / data_source=eastmoney）
function startMacroProgressPolling() {
  if (progressTimer) clearInterval(progressTimer)
  syncProgress.value = null
  let nullCount = 0
  const poll = async () => {
    try {
      const data = await getSyncProgress()
      if (data && (data.kind === 'macro' || data.data_source === 'eastmoney')) {
        nullCount = 0
        syncProgress.value = data
        if (data.status === 'done' || data.status === 'failed') {
          clearInterval(progressTimer)
          progressTimer = null
          if (data.status === 'done') ElMessage.success('宏观同步完成')
          else ElMessage.error('宏观同步失败: ' + (data.error || '未知错误'))
          setTimeout(() => {
            syncProgress.value = null
          }, 2000)
          loadStatus()
          loadSeries()
        }
      } else if (data) {
        // 其他任务（如 baostock）占用进度文件，非本页关注
        nullCount = 0
        syncProgress.value = null
      } else {
        nullCount += 1
        if (nullCount > 20) {
          clearInterval(progressTimer)
          progressTimer = null
          syncProgress.value = null
        }
      }
    } catch (e) {
      // 忽略瞬时错误，继续轮询
    }
  }
  poll()
  progressTimer = setInterval(poll, 1000)
}

onMounted(() => {
  loadStatus()
})

onBeforeUnmount(() => {
  if (progressTimer) clearInterval(progressTimer)
})
</script>

<style scoped lang="scss">
.page-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: var(--space-md);
  margin-bottom: var(--space-lg);
  animation: fadeInUp 0.5s var(--ease-out-expo);

  &__lead {
    flex: 1;
    min-width: 0;
  }
  &__title {
    font-size: var(--font-size-2xl);
    font-weight: 700;
    color: var(--text-primary);
    margin: 0 0 var(--space-xs);
  }
  &__subtitle {
    font-size: var(--font-size-sm);
    color: var(--text-secondary);
  }
}

.snapshot-toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.sync-message {
  margin-top: 12px;
  font-size: 13px;
  color: var(--text-secondary);
}

.sync-progress {
  margin-top: 14px;
  padding: 12px 14px;
  background: var(--bg-tertiary, #f5f7fa);
  border-radius: 8px;
}
.progress-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
}
.progress-status {
  font-size: 13px;
  color: var(--text-primary);
}
.progress-pct {
  font-size: 13px;
  font-weight: 600;
  color: var(--primary);
  font-variant-numeric: tabular-nums;
}

.chart-wrap {
  min-height: 200px;
}
.chart-macro {
  height: 340px;
}

.snapshot-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
  gap: 12px;
  align-items: start;
}
.snapshot-group {
  position: relative;
}
.snapshot-group + .snapshot-group {
  margin-top: 20px;
}
.snapshot-group-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-secondary);
  margin-bottom: 10px;
}
.snapshot-cell {
  padding: 12px 14px;
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg, 12px);
  cursor: pointer;
  user-select: none;
  transition: box-shadow 0.2s ease, border-color 0.2s ease, transform 0.2s ease;

  &:hover {
    border-color: var(--primary-light);
    box-shadow: var(--shadow-sm);
    transform: translateY(-1px);
  }

  &--active {
    border-color: var(--primary);
    box-shadow: 0 0 0 1px var(--primary);
  }
}
.snapshot-label {
  font-size: 12px;
  color: var(--text-tertiary);
  margin-bottom: 6px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.snapshot-value {
  font-size: 20px;
  font-weight: 600;
  color: var(--text-primary);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}
.snapshot-trends {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
  margin-top: 4px;
  min-height: 18px;
}
.snapshot-trend {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  font-size: 12px;
  font-weight: 500;
  font-variant-numeric: tabular-nums;

  .el-icon {
    font-size: 12px;
  }
}
.snapshot-unit {
  font-size: 12px;
  font-weight: 400;
  color: var(--text-tertiary);
  margin-left: 2px;
}
.snapshot-value.is-up,
.snapshot-trend.is-up {
  color: var(--chart-up);
}
.snapshot-value.is-down,
.snapshot-trend.is-down {
  color: var(--chart-down);
}
.snapshot-date {
  font-size: 12px;
  color: var(--text-tertiary);
  margin-top: 4px;
}
.snapshot-action {
  display: flex;
  align-items: center;
  gap: 3px;
  margin-top: 8px;
  padding-top: 8px;
  border-top: 1px dashed var(--border-light);
  font-size: 12px;
  color: var(--text-tertiary);

  .el-icon {
    font-size: 12px;
  }
}
.snapshot-cell--active .snapshot-action {
  color: var(--primary);
}

/* 展开走势面板：悬浮于被点击卡片正下方，占满整行，不参与布局（右侧卡片完全不动） */
.snapshot-chart {
  position: absolute;
  left: 0;
  right: 0;
  top: 0;
  z-index: 9999;
  padding: 14px 16px;
  background: var(--bg-secondary);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg, 12px);
  box-shadow: var(--shadow-lg, 0 10px 30px rgba(0, 0, 0, 0.16));

  &__head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    flex-wrap: wrap;
    margin-bottom: 12px;
  }

  &__title {
    display: flex;
    align-items: baseline;
    gap: 10px;
    flex-wrap: wrap;
  }

  &__name {
    font-size: 15px;
    font-weight: 600;
    color: var(--text-primary);
  }

  &__fields {
    font-size: 12px;
    color: var(--text-tertiary);
  }
}

/* 展开/收起过渡 */
.expand-enter-active,
.expand-leave-active {
  transition: opacity 0.25s ease, transform 0.25s ease;
}
.expand-enter-from,
.expand-leave-to {
  opacity: 0;
  transform: translateY(-6px);
}

.mb-6 {
  margin-bottom: 24px;
}

@keyframes fadeInUp {
  from {
    opacity: 0;
    transform: translateY(8px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}
</style>
