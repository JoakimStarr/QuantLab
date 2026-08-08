<template>
  <PageContainer narrow>
    <PageHeader
      title="宏观指标"
      subtitle="东财 + akshare 宏观数据（PMI/CPI/PPI/GDP/国债/Shibor/汇率等），同步后广播为 qlib 因子字段"
    />

    <!-- 宏观指标：最新值 + 点击指标正面下方展开走势（按需加载） -->
    <SectionCard title="宏观指标" class="macro-card mb-6">
      <template #extra>
        <div class="snapshot-toolbar">
          <el-button size="small" @click="reloadAll" :loading="loading">刷新</el-button>
          <el-button size="small" type="primary" @click="doSync" :loading="syncing">
            {{ syncing ? '同步中...' : '同步宏观数据' }}
          </el-button>
        </div>
      </template>

      <div v-if="syncMessage" class="sync-message">{{ syncMessage }}</div>

      <template v-if="snapshotItems.length">
        <div v-for="group in snapshotGroups" :key="group.label" class="snapshot-group">
          <div class="snapshot-group-title">{{ group.label }}</div>
          <div class="snapshot-grid">
            <!-- 指标卡固定尺寸；点击后在其所在行下方展开占满整行的走势面板（结构化布局，天然不错位） -->
            <template v-for="opt in group.options" :key="opt.key">
              <div
                class="snapshot-cell"
                :class="{ 'snapshot-cell--active': isExpanded(opt) }"
                :title="opt.fields[0].item.available_date + (opt.fields[0].item.prevDate ? '，较 ' + opt.fields[0].item.prevDate : '')"
                @click="toggleExpand(opt, $event)"
              >
                <div class="snapshot-label">{{ cardTitle(opt) }}</div>
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

              <!-- 走势面板：绝对定位悬浮于被点击卡所在行下方，覆盖后续行，不参与网格布局（右侧同行卡片保持原位） -->
              <transition name="expand">
                <div v-if="isExpanded(opt)" class="snapshot-chart" :style="{ top: expandedTop + 'px' }">
                  <div class="snapshot-chart__head">
                    <div class="snapshot-chart__title">
                      <span class="snapshot-chart__name">{{ expandedCard.label }}</span>
                      <span class="snapshot-chart__fields">{{ (expandedCard.seriesFields || expandedCard.fields).map((f) => f.name).join(' / ') }}</span>
                    </div>
                    <el-radio-group v-model="timeRange" size="small">
                      <el-radio-button v-for="r in timeOptions" :key="r.key" :value="r.key">{{ r.label }}</el-radio-button>
                    </el-radio-group>
                  </div>
                  <div v-if="expandedCard.desc" class="snapshot-chart__desc">{{ expandedCard.desc }}</div>
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
import PageHeader from '@/components/common/PageHeader.vue'
import SectionCard from '@/components/common/SectionCard.vue'
import VChart from 'vue-echarts'
import '@/utils/echarts'
import { syncMacro, getMacroIndicators, getMacroStatus, getMacroSnapshot } from '@/api/macro'
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
    desc: '景气先行指标，< 50 经济收缩、> 50 扩张；见底回升常领先股市 1-3 个月。',
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
    desc: '通胀 > 3% → 央行可能加息 → 债市利空；< 1% 暗示通缩、降息空间打开。',
    group: '景气/价格',
    fields: [{ field: 'cpi', name: 'CPI同比', color: C.purple }],
  },
  {
    key: 'ppi',
    indicator: 'PPI',
    label: 'PPI同比(%)',
    desc: '上游涨价挤压中下游利润；CPI-PPI 剪刀差用于判断利润在上下游分配。',
    group: '景气/价格',
    fields: [{ field: 'ppi', name: 'PPI同比', color: C.teal }],
  },
  {
    key: 'gdp',
    indicator: 'GDP',
    label: 'GDP同比(%)',
    desc: '经济周期定位的最终锚；低于潜在增速 → 宽松预期、高于潜在增速 → 警惕过热。',
    group: '景气/价格',
    fields: [{ field: 'gdp', name: 'GDP同比', color: C.grass }],
  },
  // 利率
  {
    key: 'cn_trsy',
    indicator: 'TREASURY',
    label: '中债收益率',
    desc: '无风险利率锚，股债性价比切换信号；10Y 下行利好成长股估值。',
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
    desc: '收益率曲线形态；倒挂 → 衰退预警，陡峭化 → 复苏预期。',
    group: '利率',
    fields: [{ field: 'trsy_spread_10y2y', name: '利差10Y-2Y', color: C.cyan }],
  },
  {
    key: 'us_trsy',
    indicator: 'TREASURY',
    label: '美债收益率',
    desc: '全球资产定价之锚；上行 → 美元强、新兴市场承压、北向流出。',
    group: '利率',
    fields: [{ field: 'us_trsy10y', name: '美债10Y', color: C.forest }],
  },
  {
    key: 'shibor',
    indicator: 'SHIBOR',
    label: 'Shibor',
    desc: '银行间流动性；隔夜飙升 → 资金紧张、股市利空；持续低位 → 流动性宽松。',
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
    desc: '实体融资成本基准；1Y 降利好短贷，5Y 降利好地产链与可选消费。',
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
    desc: '全市场质押式回购；FR007≈R007，衡量非银机构融资压力。',
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
    desc: 'DR 系列，存款类机构资金价格；DR007 是央行观察的利率走廊。',
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
    desc: '通胀与需求侧领先指标；持续上行 → 资源股利好，快速回落 → 需求担忧。',
    group: '商品/汇率',
    fields: [{ field: 'commodity_idx', name: '商品价格指数', color: C.blue }],
  },
  {
    key: 'copper',
    indicator: 'COPPER',
    label: '沪铜',
    desc: '"铜博士"，全球需求晴雨表；上行 + 库存去化 → 经济复苏预期。',
    group: '商品/汇率',
    fields: [{ field: 'copper_close', name: '沪铜主力收盘价', color: C.red }],
  },
  {
    key: 'crude',
    indicator: 'CRUDE_OIL',
    label: '原油',
    desc: '大宗商品之锚；上行 → 化工/航运成本与通胀压力，下行 → 利好航空/物流。',
    group: '商品/汇率',
    fields: [{ field: 'crude_close', name: '原油SC主力', color: C.orange }],
  },
  {
    key: 'fx',
    indicator: 'FX',
    label: '人民币汇率',
    desc: '央行每日发布中间价；贬值利好出口但输入通胀，升值利好进口。',
    group: '商品/汇率',
    fields: [{ field: 'usdcny_mid', name: '美元中间价', color: C.blue }],
  },
  // 风险/情绪
  {
    key: 'ivix',
    indicator: 'IVIX',
    label: '波指iVIX',
    desc: '市场恐慌情绪；> 30 警惕风险，< 20 过度乐观，底部反转领先大盘 1-2 周。',
    group: '风险/情绪',
    fields: [{ field: 'ivix', name: '50ETF波动率指数', color: C.red }],
  },
  {
    key: 'futif',
    indicator: 'FUTURES_IF',
    label: '股指期货IF',
    desc: '沪深300期货；基差/升贴水反映多空预期，持仓量变化反映机构倾向。',
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
    desc: '中小盘情绪指标；IC 升贴水走阔 → 恐慌，收敛 → 情绪修复。',
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
    desc: '5Y 国债期货；TF 涨 → 预期利率下行、债牛，利率方向领先信号。',
    group: '风险/情绪',
    fields: [{ field: 'tf_close', name: 'TF主力收盘价', color: C.cyan }],
  },
  {
    key: 'gold',
    indicator: 'GOLD',
    label: '沪金',
    desc: '与实际利率反向；避险急涨 → 风险资产承压，金价新高警惕滞胀。',
    group: '风险/情绪',
    fields: [{ field: 'au_close', name: '沪金AU主力', color: C.gold }],
  },
  // 市场热度（乐咕乐股/新浪，非东财；北向资金 2024-08 停更已移除 HSGT）
  {
    key: 'marketPE',
    indicator: 'MARKET_PE',
    label: '全A估值分位',
    desc: '全市场市盈率中位数 TTM + 历史分位；分位 > 0.8 偏贵、< 0.2 便宜，估值温度计。',
    group: '市场热度',
    fields: [
      { field: 'pe_mid_ttm', name: '全A市盈率TTM(中位数)', color: C.blue },
      { field: 'pe_tt_quant_hist', name: '历史分位数', color: C.orangeAlt, axis: 'right' },
    ],
  },
  {
    key: 'shPE',
    indicator: 'MARKET_PE_SH',
    label: '上证市盈率',
    desc: '上证平均市盈率（周频）；历史区间约 13-25，极端低位=机会区、高位=风险区。',
    group: '市场热度',
    fields: [{ field: 'pe_sh', name: '上证平均市盈率', color: C.teal }],
  },
  {
    key: 'shPB',
    indicator: 'MARKET_PB',
    label: '上证市净率',
    desc: '上证平均市净率 + 中位数；历史低位约 1.2-1.5，高位约 3.5+。',
    group: '市场热度',
    fields: [
      { field: 'pb_sh_mid', name: '市净率中位数', color: C.blue },
      { field: 'pb_sh', name: '平均市净率', color: C.orangeAlt, axis: 'right' },
    ],
  },
  {
    key: 'hs300PE',
    indicator: 'HS300_PE',
    label: '沪深300估值',
    desc: '沪深300 滚动/静态市盈率；权重股估值温度，与股指期货 IF 呼应。',
    group: '市场热度',
    fields: [
      { field: 'hs300_pe_ttm', name: '滚动市盈率', color: C.blue },
      { field: 'hs300_pe_std', name: '静态市盈率', color: C.orangeAlt, axis: 'right' },
    ],
  },
  {
    key: 'divYield',
    indicator: 'MARKET_DIV',
    label: '上证股息率',
    desc: '上证A股整体股息率；> 3% 吸引长线配置资金，股债性价比的重要参照。',
    group: '市场热度',
    fields: [{ field: 'div_yield_sh', name: '股息率', color: C.gold }],
  },
  {
    key: 'shIndex',
    indicator: 'SH_INDEX',
    label: '上证指数量能',
    desc: '上证指数收盘点位 + 成交量；放量上攻可信，缩量新高存疑。',
    group: '市场热度',
    fields: [
      { field: 'sh_idx_close', name: '上证收盘', color: C.blue },
      { field: 'sh_idx_vol', name: '成交量', color: C.orangeAlt, axis: 'right' },
    ],
  },
  {
    key: 'congestion',
    indicator: 'MARKET_CONG',
    label: '市场拥挤度',
    desc: 'A股拥挤度（乐咕，0~1）；数据发布滞后约 2 个月，高位拥挤 → 上涨空间透支。',
    group: '市场热度',
    fields: [{ field: 'congestion', name: '拥挤度', color: C.red }],
  },
  // 货币/信贷
  {
    key: 'moneysupply',
    indicator: 'MONEY_SUPPLY',
    label: 'M0/M1/M2同比',
    cardTitle: 'M0/M1/M2同比',
    desc: 'M1-M2 增速差 → 资金活化程度；M1 增速快 → 股市可能上涨。',
    group: '货币/信贷',
    cardFields: 3,
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
    desc: '新增社融超预期 → 流动性宽松 → 利好股市。',
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
    desc: '实体融资需求；短贷大增常为票据冲量，中长贷回升 → 实体投资恢复。',
    group: '货币/信贷',
    fields: [
      { field: 'new_loan', name: '新增贷款', color: C.blue },
      { field: 'new_loan_yoy', name: '新增贷款同比', color: C.orangeAlt, axis: 'right' },
    ],
  },
  {
    key: 'margin',
    indicator: 'MARGIN',
    label: '两融余额',
    desc: '场内杠杆资金；> 2 万亿偏热、< 1.5 万亿偏冷。',
    group: '货币/信贷',
    fields: [
      { field: 'margin_balance', name: '沪市两融余额', color: C.blue },
      { field: 'margin_balance_sz', name: '深市两融余额', color: C.orangeAlt, indicator: 'MARGIN_SZ' },
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
const seriesData = ref([])
const statusItems = ref([])
const snapshotItems = ref([])

// 当前展开的走势卡片（点击最新值卡片后按需加载走势）
const expandedCard = ref(null)
// 悬浮面板距离所在分组顶部的偏移（由被点击卡片测量，滚动/缩放时重算）
const expandedTop = ref(0)
const activeCell = ref(null)

function isExpanded(card) {
  return card != null && expandedCard.value != null && expandedCard.value.key === card.key
}

// 卡片标题：显式 cardTitle（如 "M0/M1/M2同比"）优先，否则字段名拼接
function cardTitle(opt) {
  return opt.cardTitle || opt.fields.map((f) => f.name).join(' / ')
}

// 测量被点击卡片相对其分组的垂直位置，作为悬浮面板的 top（不动其它卡片布局）
function measurePanelTop() {
  if (!activeCell.value) return
  expandedTop.value = activeCell.value.offsetTop + activeCell.value.offsetHeight + 12
}

// 点击指标卡片：展开/收起其走势（切换时按需拉取；面板绝对定位覆盖后续行）
function toggleExpand(card, e) {
  if (!card) return
  if (expandedCard.value && expandedCard.value.key === card.key) {
    expandedCard.value = null
    activeCell.value = null
    seriesData.value = []
  } else {
    activeCell.value = e && e.currentTarget
    measurePanelTop()
    expandedCard.value = card
    seriesData.value = []
    loadSeries(card)
  }
}

// 窗口缩放时重定位悬浮面板，避免静止偏移
function onPanelResize() {
  measurePanelTop()
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
  const order = ['景气/价格', '利率', '商品/汇率', '风险/情绪', '市场热度', '货币/信贷']
  const byField = new Map(snapshotItems.value.map((it) => [it.field_name, it]))
  const byGroup = {}
  for (const opt of fieldOptions) {
    const fields = (opt.fields ?? [])
      .map((f) => ({ ...f, item: byField.get(f.field) }))
      .filter((x) => x.item)
    if (!fields.length) continue
    const g = opt.group || '其他'
    // 每张卡片最多展示 cardFields 个字段（默认 2），超出部分拆成独立卡片；
    // 属于同一族（如中债所有期限）的卡片共用 seriesFields，点击任一张一起展开整族走势
    const seriesFields = fields
    const step = opt.cardFields || 2
    for (let i = 0; i < fields.length; i += step) {
      ;(byGroup[g] = byGroup[g] || []).push({
        key: i === 0 ? opt.key : `${opt.key}_${i / step}`,
        label: opt.label,
        cardTitle: opt.cardTitle,
        indicator: opt.indicator,
        group: opt.group,
        desc: opt.desc,
        fields: fields.slice(i, i + step),
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
      unit: s.unit || '',
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

  // 某序列所在轴（右轴字段在 axis 1，其余 0）
  const axisIndexOf = (s) => {
    const fc = opt?.fields.find((x) => x.field === s.field)
    return fc?.axis === 'right' ? 1 : 0
  }
  // 按轴聚合单位：同轴序列单位一致才显示（如整轴是同比%则轴标带%）
  const axisUnit = (idx) => {
    const units = seriesData.value.filter((s) => axisIndexOf(s) === idx).map((s) => s.unit)
    return units.length && units.every((u) => u === units[0]) ? units[0] : ''
  }
  const axisLabelWithUnit = (idx) => {
    const u = axisUnit(idx)
    if (!u) return { color: chartTheme.axisText() }
    return { color: chartTheme.axisText(), formatter: (v) => (v == null ? v : `${v}${u}`) }
  }

  const yAxisOpt = opt?.fields.some((f) => f.axis === 'right')
    ? [
        { type: 'value', scale: true, axisLabel: axisLabelWithUnit(0) },
        { type: 'value', scale: true, axisLabel: axisLabelWithUnit(1), splitLine: { show: false } },
      ]
    : { type: 'value', scale: true, axisLabel: axisLabelWithUnit(0) }

  return {
    // 悬停十字线数值带单位（% 等），如 "49.6%"
    tooltip: {
      trigger: 'axis',
      formatter: (params) => {
        const list = Array.isArray(params) ? params : [params]
        const head = list[0]?.axisValue ?? ''
        const rows = list
          .filter((p) => p.value != null && p.value !== '' && p.value !== '-')
          .map((p) => {
            const unit = series[p.seriesIndex]?.unit ?? ''
            return `${p.marker}${p.seriesName}: ${Number(p.value).toFixed(2)}${unit}`
          })
        return [head, ...rows].join('<br/>')
      },
    },
    textStyle: { color: chartTheme.axisText() },
    legend: { top: 0, left: 8, textStyle: { color: chartTheme.axisText() } },
    grid: { left: 48, right: 48, top: 40, bottom: 40 },
    xAxis: { type: 'category', data: dates, axisLabel: { fontSize: 11, color: chartTheme.axisText() } },
    yAxis: yAxisOpt,
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
    const params = startDate ? { start: startDate } : {}
    const fields = target.seriesFields || target.fields
    const series = await Promise.all(
      fields.map(async (f) => {
        const res = await getMacroIndicators({ indicator: f.indicator || target.indicator, field: f.field, ...params })
        const items = res?.items ?? []
        return {
          field: f.field,
          name: f.name,
          color: f.color,
          unit: items[0]?.unit ?? '',
          points: items.map((d) => ({ date: d.available_date, value: d.value })),
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
    syncMessage.value = '已提交：仅入库 PG，不写 qlib bin（无进度条）。约 5 秒后自动刷新最新值。'
    ElMessage.success('宏观同步已提交（仅入库）')
    // fetch-only 任务不写全局进度，5 秒后刷新一次最新值即可
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

onMounted(() => {
  loadStatus()
  window.addEventListener('resize', onPanelResize)
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', onPanelResize)
})
</script>

<style scoped lang="scss">
/* 展开面板绝对定位向下覆盖，可能超出本卡底部触及下方「同步状态」卡，提升本卡层级 */
.macro-card {
  position: relative;
  z-index: 2;
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

.chart-wrap {
  min-height: 200px;
}
.chart-macro {
  height: 340px;
}

.snapshot-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(210px, 1fr));
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
  line-height: 1.3;
  overflow-wrap: anywhere;
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

/* 展开走势面板：绝对定位悬浮于被点击卡所在行下方，覆盖后续行（不推挤右侧/下方卡片） */
.snapshot-chart {
  position: absolute;
  left: 0;
  right: 0;
  z-index: 3;
  padding: 14px 16px;
  background: var(--bg-secondary);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg, 12px);
  box-shadow: var(--shadow-md, 0 4px 16px rgba(0, 0, 0, 0.12));

  &__head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--space-md, 12px);
    flex-wrap: wrap;
    margin-bottom: 12px;
  }

  &__title {
    display: flex;
    align-items: baseline;
    gap: 10px;
    flex-wrap: wrap;
    min-width: 0;
  }

  &__name {
    font-size: 15px;
    font-weight: 600;
    color: var(--text-primary);
  }

  &__fields {
    font-size: 12px;
    color: var(--text-tertiary);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  &__desc {
    font-size: 12px;
    color: var(--text-secondary);
    background: var(--bg-hover, rgba(0, 0, 0, 0.03));
    border-left: 2px solid var(--primary);
    padding: 6px 10px;
    margin-bottom: 10px;
    line-height: 1.5;
    border-radius: 4px;
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
