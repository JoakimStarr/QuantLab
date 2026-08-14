<template>
  <SectionCard
    title="宏观指标"
    :compact="true"
    collapsible
    :collapsed="collapsed"
    @update:collapsed="onToggleCollapse"
  >
    <template #extra>
      <el-button size="small" link type="primary" @click="manageVisible = true">管理指标</el-button>
      <el-button size="small" link type="primary" @click="$router.push('/quant/macro')">详情</el-button>
    </template>
    <div v-if="loading" class="macro-grid">
      <div class="macro-cell" v-for="i in 8" :key="i">
        <el-skeleton :rows="2" animated />
      </div>
    </div>
    <div v-else-if="visibleItems.length" class="macro-grid">
      <div
        v-for="it in visibleItems"
        :key="it.indicator + '-' + it.field_name"
        class="macro-cell"
        :title="it.available_date + (it.prevDate ? '，较 ' + it.prevDate : '')"
      >
        <div class="macro-label">{{ it.label }}</div>
        <div class="macro-value" :class="trendClass(it.change)">
          {{ displayValue(it.value) }}
          <span v-if="it.unit" class="macro-unit">{{ it.unit }}</span>
        </div>
        <div v-if="hasChange(it.change)" class="macro-trend" :class="trendClass(it.change)">
          <el-icon v-if="it.change > 0" class="macro-trend__icon"><CaretTop /></el-icon>
          <el-icon v-else class="macro-trend__icon"><CaretBottom /></el-icon>
          <span>{{ fmtChange(it.change) }}</span>
        </div>
      </div>
    </div>
    <el-empty v-else :image-size="48" description="暂无宏观数据" />

    <el-dialog v-model="manageVisible" title="管理宏观指标" width="640px" append-to-body>
      <div class="manage-tip">默认展示核心指标，可按需勾选添加到首页。</div>
      <el-input v-model="manageKeyword" placeholder="搜索指标" clearable size="small" class="manage-search" />
      <div class="manage-groups">
        <div v-for="grp in groupedOptions" :key="grp.group" class="manage-group">
          <div class="manage-group__title">{{ grp.group }}</div>
          <el-checkbox-group v-model="draftKeys" class="manage-group__checks">
            <el-checkbox v-for="opt in grp.options" :key="opt.key" :value="opt.key" border class="manage-check">
              {{ opt.label }}
            </el-checkbox>
          </el-checkbox-group>
        </div>
      </div>
      <template #footer>
        <el-button size="small" @click="manageVisible = false">取消</el-button>
        <el-button size="small" type="primary" @click="saveSelection">保存</el-button>
      </template>
    </el-dialog>
  </SectionCard>
</template>

<script setup>
import { ref, computed, watch } from 'vue'
import { CaretTop, CaretBottom } from '@element-plus/icons-vue'
import SectionCard from '@/components/common/SectionCard.vue'

const props = defineProps({
  items: { type: Array, default: () => [] },
  loading: { type: Boolean, default: false },
})

const STORAGE_KEYS = {
  shown: 'macro_snapshot_shown',
  collapsed: 'macro_snapshot_collapsed',
}

// 默认展示的核心指标（indicator:field_name）
const DEFAULT_SHOWN = [
  'CPI:cpi',
  'PPI:ppi',
  'PMI:pmi',
  'PMI:pmi_nm',
  'LPR:lpr1y',
  'LPR:lpr5y',
  'GDP:gdp',
]

// 指标分组（用于管理弹窗），label 取自后端 macro_sync 配置
const GROUP_DEFS = [
  { group: '核心经济指标', fields: { CPI: ['cpi'], PPI: ['ppi'], PMI: ['pmi', 'pmi_nm'], GDP: ['gdp'], LPR: ['lpr1y', 'lpr5y'] } },
  { group: '货币供应与社融', fields: { MONEY_SUPPLY: ['m0_yoy', 'm1_yoy', 'm2_yoy'], SOCIAL_FINANCE: ['social_finance', 'sf_rmb_loan'], LOAN: ['new_loan', 'new_loan_yoy'] } },
  { group: '利率与资金面', fields: { TREASURY: ['trsy2y', 'trsy5y', 'trsy10y', 'trsy30y', 'trsy_spread_10y2y', 'us_trsy2y', 'us_trsy10y', 'us_trsy_spread'], SHIBOR: ['shibor_on', 'shibor_1w', 'shibor_3m', 'shibor_1y'], REPO_FR: ['fr001', 'fr007', 'fr014'], REPO_FDR: ['fdr001', 'fdr007', 'fdr014'] } },
  { group: '市场估值与情绪', fields: { MARKET_PE: ['pe_mid_ttm', 'pe_mid_lyr', 'pe_tt_quant_10y', 'pe_tt_quant_hist'], MARKET_PB: ['pb_sh', 'pb_sh_mid'], MARKET_PE_SH: ['pe_sh'], MARKET_DIV: ['div_yield_sh'], MARKET_CONG: ['congestion'], HS300_PE: ['hs300_pe_ttm', 'hs300_pe_std'], IVIX: ['ivix'] } },
  { group: '资金面（两融/北向）', fields: { MARGIN: ['margin_balance'], MARGIN_SZ: ['margin_balance_sz'], HSGT: ['hsgt_buy', 'hsgt_sell', 'hsgt_inflow', 'hsgt_net_buy', 'hsgt_cum_net', 'hsgt_hold_mv'] } },
  { group: '汇率与商品期货', fields: { FX: ['usdcny_mid'], COMMODITY: ['commodity_idx'], COPPER: ['copper_close'], CRUDE_OIL: ['crude_close'], GOLD: ['au_close'], SH_INDEX: ['sh_idx_close', 'sh_idx_vol'], FUTURES_IF: ['if_close', 'if_hold'], FUTURES_IC: ['ic_close', 'ic_hold'], FUTURES_TF: ['tf_close'] } },
]

const manageVisible = ref(false)
const manageKeyword = ref('')
const collapsed = ref(localStorage.getItem(STORAGE_KEYS.collapsed) === '1')
const draftKeys = ref([])

function loadShown() {
  try {
    const raw = localStorage.getItem(STORAGE_KEYS.shown)
    if (raw) {
      const arr = JSON.parse(raw)
      if (Array.isArray(arr) && arr.length) return arr
    }
  } catch {
    /* ignore */
  }
  return [...DEFAULT_SHOWN]
}

const shownKeys = ref(loadShown())

// 用数据源存在字段补齐可选列表，避免勾选了但数据为空
const availableOptions = computed(() => {
  const seen = new Set()
  const opts = []
  for (const it of props.items) {
    const key = `${it.indicator}:${it.field_name}`
    if (seen.has(key)) continue
    seen.add(key)
    opts.push({ key, indicator: it.indicator, field: it.field_name, label: it.label })
  }
  return opts
})

const groupedOptions = computed(() => {
  const kw = manageKeyword.value.trim().toLowerCase()
  const map = {}
  for (const gd of GROUP_DEFS) {
    for (const [ind, fields] of Object.entries(gd.fields)) {
      for (const f of fields) {
        const key = `${ind}:${f}`
        const it = availableOptions.value.find((o) => o.key === key)
        if (!it) continue
        if (kw && !it.label.toLowerCase().includes(kw) && !f.includes(kw)) continue
        if (!map[gd.group]) map[gd.group] = []
        map[gd.group].push(it)
      }
    }
  }
  return Object.entries(map).map(([group, options]) => ({ group, options }))
})

const visibleItems = computed(() =>
  props.items.filter((it) => shownKeys.value.includes(`${it.indicator}:${it.field_name}`))
)

watch(manageVisible, (v) => {
  if (v) draftKeys.value = [...shownKeys.value]
})

function saveSelection() {
  shownKeys.value = draftKeys.value.length ? [...draftKeys.value] : [...DEFAULT_SHOWN]
  localStorage.setItem(STORAGE_KEYS.shown, JSON.stringify(shownKeys.value))
  manageVisible.value = false
}

function onToggleCollapse(v) {
  collapsed.value = v
  localStorage.setItem(STORAGE_KEYS.collapsed, v ? '1' : '0')
}

function hasChange(v) {
  return v !== null && v !== undefined && Number(v) !== 0 && !Number.isNaN(Number(v))
}

function displayValue(v) {
  if (v === null || v === undefined || v === '') return '--'
  const n = Number(v)
  if (Number.isNaN(n)) return '--'
  return String(Number(n.toFixed(2)))
}

function trendClass(v) {
  const n = Number(v)
  if (n === null || n === undefined || Number.isNaN(n) || n === 0) return ''
  return n > 0 ? 'is-up' : 'is-down'
}

function fmtChange(v) {
  const n = Number(v)
  if (Number.isNaN(n)) return ''
  return `${n > 0 ? '+' : ''}${Number(n.toFixed(4))}`
}
</script>

<style scoped lang="scss">
.macro-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(120px, 1fr));
  gap: 10px;
}
.macro-cell {
  padding: 12px 14px;
  min-width: 0;
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: 10px;
}
.macro-label {
  font-size: 11px;
  color: var(--text-tertiary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.macro-value {
  font-size: 15px;
  font-weight: 700;
  color: var(--text-primary);
  margin-top: 2px;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.macro-unit {
  font-size: 11px;
  font-weight: 400;
  color: var(--text-tertiary);
  margin-left: 2px;
}
.macro-value.is-up {
  color: var(--chart-up);
}
.macro-value.is-down {
  color: var(--chart-down);
}
.macro-trend {
  display: flex;
  align-items: center;
  gap: 2px;
  margin-top: 4px;
  font-size: 12px;
  font-weight: 500;
  font-variant-numeric: tabular-nums;
}
.macro-trend__icon {
  font-size: 13px;
}
.macro-trend.is-up {
  color: var(--chart-up);
}
.macro-trend.is-down {
  color: var(--chart-down);
}
.manage-tip {
  font-size: 12px;
  color: var(--text-tertiary);
  margin-bottom: 8px;
}
.manage-search {
  margin-bottom: 12px;
  max-width: 240px;
}
.manage-groups {
  max-height: 46vh;
  overflow-y: auto;
}
.manage-group {
  margin-bottom: 12px;
}
.manage-group__title {
  font-size: 12px;
  font-weight: 600;
  color: var(--text-primary);
  margin-bottom: 6px;
  padding-bottom: 4px;
  border-bottom: 1px solid var(--border-light);
}
.manage-group__checks {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.manage-check {
  margin-right: 0;
}
</style>
