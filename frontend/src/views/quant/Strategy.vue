<template>
  <PageContainer>
    <!-- 页面头 -->
    <PageHeader title="策略回测" subtitle="多因子策略构建与回测分析">
      <template #actions>
        <el-button :icon="Refresh" @click="loadStrategies">刷新</el-button>
        <el-button :disabled="selectedResults.length < 2" :loading="comparing" @click="compareResults"
          >对比选中策略 ({{ selectedResults.length }})</el-button
        >
        <el-button type="warning" :loading="aiGenerating" :disabled="!factorCount" @click="onAiGenerate">
          {{ aiGenerating ? 'AI 生成中...' : '✨ AI 生成策略' }}
        </el-button>
        <el-button type="primary" :icon="Plus" @click="openCreate">新建策略</el-button>
      </template>
    </PageHeader>

    <!-- 策略列表表格 -->
    <SectionCard title="策略列表" class="table-card" v-loading="listLoading">
      <el-table
        v-if="strategies.length"
        :data="strategies"
        :row-class-name="rowClassName"
        :row-key="(row) => row.id"
        :expand-row-keys="expandedKeys"
        @expand-change="onExpandChange"
        @row-click="onRowClick"
        @selection-change="handleResultSelectionChange"
        size="default"
      >
        <el-table-column type="selection" width="48" />
        <el-table-column type="expand" width="48">
          <template #default="{ row }">
            <BacktestResultDetail
              v-if="expandedId === row.id && currentResult"
              :result="currentResult"
              :strategy="row"
              :loading="resultLoading"
              @delete="deleteCurrentResult"
            />
          </template>
        </el-table-column>
        <el-table-column prop="id" label="ID" width="60" align="center">
          <template #default="{ row }"
            ><span class="cell-mono">{{ row.id }}</span></template
          >
        </el-table-column>
        <el-table-column prop="name" label="策略名称" min-width="160">
          <template #default="{ row }"
            ><span class="cell-name">{{ row.name }}</span></template
          >
        </el-table-column>
        <el-table-column label="因子" min-width="200">
          <template #default="{ row }">
            <span class="cell-factors" :title="row.factor_ids?.join(', ')">
              {{ row.factor_ids?.join(', ') || '--' }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="组合方式" width="110" align="center">
          <template #default="{ row }">
            <span v-if="row.combination_method === 'ic_weight'" class="pill pill--primary">IC加权</span>
            <span v-else-if="row.combination_method === 'ir_weight'" class="pill pill--primary">IR加权</span>
            <span v-else class="pill pill--muted">等权</span>
          </template>
        </el-table-column>
        <el-table-column label="topk/n_drop" width="110" align="center">
          <template #default="{ row }">
            <span class="cell-mono cell-tnum">{{ row.topk }}/{{ row.n_drop }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="benchmark" label="基准" width="120" align="center">
          <template #default="{ row }"
            ><span class="cell-mono cell-sm">{{ row.benchmark || '--' }}</span></template
          >
        </el-table-column>
        <el-table-column label="回测状态" width="120" align="center">
          <template #default="{ row }">
            <span class="status-badge" :class="getBacktestStatusClass(row.id)">{{
              getBacktestStatusText(row.id)
            }}</span>
          </template>
        </el-table-column>
        <el-table-column label="创建时间" width="180" align="center">
          <template #default="{ row }">
            <span class="time">{{ formatTime(row.created_at) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="180" align="center" fixed="right">
          <template #default="{ row }">
            <div class="row-actions">
              <a class="link link--primary" @click.stop="triggerBacktest(row)">回测</a>
              <a class="link link--success" @click.stop="viewResults(row)">结果</a>
              <a class="link link--warning" @click.stop="openWalkForward(row)">Walk-forward</a>
              <a class="link link--danger" @click.stop="archive(row)">归档</a>
            </div>
          </template>
        </el-table-column>
      </el-table>
      <el-empty v-else description="暂无策略" />
    </SectionCard>


    <!-- 新建策略对话框 -->
    <el-dialog v-model="showCreate" title="新建策略" width="560px">
      <el-form label-width="96px" :model="form">
        <el-form-item label="策略名称">
          <el-input v-model="form.name" placeholder="如 多因子动量策略" />
        </el-form-item>
        <el-form-item label="选择因子">
          <el-select v-model="form.factor_ids" multiple filterable placeholder="选择因子" style="width: 100%">
            <el-option v-for="f in factorOptions" :key="f.id" :label="`${f.name} (IC=${f.ic ?? '--'})`" :value="f.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="组合方式">
          <el-select v-model="form.combination_method" style="width: 180px">
            <el-option label="等权" value="equal_weight" />
            <el-option label="IC加权" value="ic_weight" />
            <el-option label="IR加权" value="ir_weight" />
          </el-select>
        </el-form-item>
        <el-form-item label="因子正交化">
          <el-switch v-model="form.orthogonalize" :active-value="1" :inactive-value="0" />
          <span style="margin-left: 12px; color: var(--text-tertiary); font-size: var(--font-size-sm)"
            >启用后按 IC 排序做 Gram-Schmidt 截面正交化，降低共线性</span
          >
        </el-form-item>
        <el-form-item label="topk">
          <el-input-number v-model="form.topk" :min="5" :max="300" />
        </el-form-item>
        <el-form-item label="n_drop">
          <el-input-number v-model="form.n_drop" :min="1" :max="50" />
        </el-form-item>
        <el-form-item label="调仓频率">
          <el-select v-model="form.rebalance_freq" style="width: 180px">
            <el-option label="每日" value="day" />
            <el-option label="每周" value="week" />
            <el-option label="每月" value="month" />
          </el-select>
        </el-form-item>
        <el-form-item label="基准">
          <el-select v-model="form.benchmark" filterable allow-create placeholder="选择基准" style="width: 240px">
            <el-option label="沪深300 (SH000300)" value="SH000300" />
            <el-option label="中证500 (SH000905)" value="SH000905" />
            <el-option label="中证1000 (SH000852)" value="SH000852" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showCreate = false">取消</el-button>
        <el-button type="primary" :loading="creating" @click="doCreate">创建</el-button>
      </template>
    </el-dialog>

    <!-- Walk-forward 滚动回测对话框（添加14） -->
    <el-dialog v-model="wfDialog.visible" title="Walk-forward 滚动回测" width="760px" :close-on-click-modal="false">
      <el-form label-position="top" v-if="wfDialog.result?.status !== 'done'">
        <div style="display: flex; gap: 12px; flex-wrap: wrap">
          <el-form-item label="训练窗口" style="flex: 1; min-width: 180px">
            <el-input v-model="wfDialog.form.trainWindow" placeholder="如 730D" />
          </el-form-item>
          <el-form-item label="测试窗口" style="flex: 1; min-width: 180px">
            <el-input v-model="wfDialog.form.testWindow" placeholder="如 180D" />
          </el-form-item>
          <el-form-item label="滚动步长" style="flex: 1; min-width: 180px">
            <el-input v-model="wfDialog.form.step" placeholder="如 180D" />
          </el-form-item>
        </div>
        <div style="display: flex; gap: 12px; flex-wrap: wrap">
          <el-form-item label="每期剔除数" style="flex: 1; min-width: 180px">
            <el-input-number v-model="wfDialog.form.nDrop" :min="0" :max="20" controls-position="right" />
          </el-form-item>
          <el-form-item label="调仓频率" style="flex: 1; min-width: 180px">
            <el-select v-model="wfDialog.form.rebalance" style="width: 100%">
              <el-option label="每日" value="day" />
              <el-option label="每周" value="week" />
              <el-option label="每月" value="month" />
            </el-select>
          </el-form-item>
        </div>
      </el-form>
      <div class="wf-result" v-if="wfDialog.result">
        <el-alert
          v-if="wfDialog.result.status === 'running'"
          type="info"
          :closable="false"
          title="回测进行中，请稍候..."
        />
        <el-alert
          v-else-if="wfDialog.result.status === 'failed'"
          type="error"
          :closable="false"
          :title="String(wfDialog.result.error || '回测失败')"
        />
        <template v-else-if="wfDialog.result.status === 'done' && wfDialog.result.result">
          <h4 class="wf-section-title">样本外整体指标</h4>
          <div class="wf-metrics" v-if="wfDialog.result.result.oos_metrics">
            <div class="wf-metric-card" v-for="(val, key) in wfDialog.result.result.oos_metrics" :key="key">
              <div class="wf-metric-label">{{ wfLabel(key) }}</div>
              <div class="wf-metric-value">{{ wfFmt(key, val) }}</div>
            </div>
          </div>
          <h4 class="wf-section-title" v-if="wfDialog.result.result.consistency">跨窗一致性</h4>
          <div class="wf-metrics" v-if="wfDialog.result.result.consistency">
            <div class="wf-metric-card" v-for="(val, key) in wfDialog.result.result.consistency" :key="key">
              <div class="wf-metric-label">{{ wfLabel(key) }}</div>
              <div class="wf-metric-value">{{ wfFmt(key, val) }}</div>
            </div>
          </div>
          <h4 class="wf-section-title">各窗口明细 ({{ wfDialog.result.result.n_windows || 0 }} 窗)</h4>
          <el-table :data="wfDialog.result.result.windows || []" size="small" max-height="320">
            <el-table-column prop="window_idx" label="#" width="50" />
            <el-table-column label="测试期" min-width="170">
              <template #default="{ row }">{{ row.test_start }} ~ {{ row.test_end }}</template>
            </el-table-column>
            <el-table-column prop="best_topk" label="topk" width="70" />
            <el-table-column label="训练夏普" width="90">
              <template #default="{ row }">{{ wfFmt('sharpe', row.train_sharpe) }}</template>
            </el-table-column>
            <el-table-column label="测试夏普" width="90">
              <template #default="{ row }">{{ wfFmt('sharpe', row.test_sharpe) }}</template>
            </el-table-column>
            <el-table-column label="年化" width="90">
              <template #default="{ row }">{{ wfFmt('annual_return', row.test_annual_return) }}</template>
            </el-table-column>
            <el-table-column label="最大回撤" width="100">
              <template #default="{ row }">{{ wfFmt('max_drawdown', row.test_max_dd) }}</template>
            </el-table-column>
          </el-table>
        </template>
      </div>
      <template #footer>
        <el-button @click="closeWalkForward">关闭</el-button>
        <el-button
          v-if="wfDialog.result?.status !== 'done'"
          type="primary"
          :loading="wfDialog.submitting"
          @click="submitWalkForward"
          >开始回测</el-button
        >
      </template>
    </el-dialog>

    <!-- AI 生成策略偏好弹窗 -->
    <el-dialog v-model="aiPref.visible" title="AI 生成策略偏好" width="480px" :close-on-click-modal="false">
      <el-form label-position="top">
        <el-form-item label="投资风格偏好">
          <el-select v-model="aiPref.style" clearable placeholder="不限定（AI 自动均衡）" style="width: 100%">
            <el-option label="动量" value="momentum" />
            <el-option label="反转" value="reversal" />
            <el-option label="低波动" value="lowvol" />
            <el-option label="价值" value="value" />
            <el-option label="成长" value="growth" />
            <el-option label="量价" value="volprice" />
          </el-select>
        </el-form-item>
        <el-form-item label="风险偏好">
          <el-select v-model="aiPref.risk" clearable placeholder="不限定" style="width: 100%">
            <el-option label="稳健（低换手/低回撤）" value="conservative" />
            <el-option label="平衡" value="balanced" />
            <el-option label="激进（更高收益弹性）" value="aggressive" />
          </el-select>
        </el-form-item>
        <el-form-item label="调仓频率偏好">
          <el-select v-model="aiPref.rebalance" clearable placeholder="不限定" style="width: 100%">
            <el-option label="日频" value="day" />
            <el-option label="周频" value="week" />
            <el-option label="月频" value="month" />
          </el-select>
        </el-form-item>
        <el-form-item label="初始资金（元）">
          <el-input-number
            v-model="aiPref.capital"
            :min="0"
            :step="1000000"
            :controls="false"
            placeholder="不限定（AI 自动判断）"
            style="width: 100%"
          />
          <el-space style="margin-top: 4px" :size="8">
            <el-button size="small" @click="aiPref.capital = 1000000">100万</el-button>
            <el-button size="small" @click="aiPref.capital = 10000000">1000万</el-button>
            <el-button size="small" @click="aiPref.capital = 100000000">1亿</el-button>
          </el-space>
          <div class="el-form-item__tip" style="color: var(--el-text-color-secondary); font-size: 12px">
            资金小 → AI 倾向小 topk、低换手以降低佣金占比；资金大 → 规避低流动性冲击
          </div>
        </el-form-item>
        <el-form-item label="其他要求（AI 自动权衡）">
          <el-input
            v-model="aiPref.other"
            type="textarea"
            :rows="2"
            :maxlength="300"
            show-word-limit
            placeholder="例如：希望降低换手、规避科创板/北交所、偏好小市值、目标年化 XX%、行业中性……AI 将尽力满足并说明取舍"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="aiPref.visible = false">取消</el-button>
        <el-button type="primary" :loading="aiGenerating" @click="confirmAiGenerate">生成策略</el-button>
      </template>
    </el-dialog>

    <!-- 回测参数弹窗（资金 / 区间） -->
    <el-dialog v-model="btParams.visible" title="回测参数" width="480px" :close-on-click-modal="false">
      <el-form label-position="top">
        <el-form-item label="初始资金（元）">
          <el-input-number v-model="btParams.capital" :step="1000000" :controls="false" style="width: 100%" />
        </el-form-item>
        <el-form-item label="回测区间">
          <el-date-picker
            v-model="btParams.range"
            type="daterange"
            range-separator="至"
            start-placeholder="开始"
            end-placeholder="结束"
            value-format="YYYY-MM-DD"
            style="width: 100%"
          />
          <div class="el-form-item__tip" style="color: var(--el-text-color-secondary); font-size: 12px">
            默认近 2 年至最新数据日（随数据自动更新，非固定 2020）
          </div>
        </el-form-item>
        <el-form-item label="标的池">
          <el-select
            v-model="btParams.universe"
            clearable
            placeholder="默认（config.quant.universe）"
            style="width: 100%"
          >
            <el-option v-for="o in universeOptions" :key="o.value" :label="o.label" :value="o.value" />
          </el-select>
        </el-form-item>
        <el-divider content-position="left">执行设置（影响回撤真实性）</el-divider>
        <el-form-item label="整手约束">
          <el-switch
            v-model="btParams.lotSize"
            active-text="按 100 股整手"
            inactive-text="允许任意整数股"
            inline-prompt
          />
          <div class="el-form-item__tip" style="color: var(--el-text-color-secondary); font-size: 12px">
            开启更贴近 A 股实盘（资金过小时会大量闲置现金，回撤更保守）
          </div>
        </el-form-item>
        <el-form-item label="成交价">
          <el-radio-group v-model="btParams.dealPrice">
            <el-radio-button value="close">T+1 收盘（默认）</el-radio-button>
            <el-radio-button value="open">T+1 开盘（更保守）</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="滑点（基点）">
          <el-input-number
            v-model="btParams.slippage"
            :min="0"
            :max="200"
            :step="5"
            :controls="false"
            style="width: 120px"
          />
          <span style="margin-left: 8px; color: var(--el-text-color-secondary)">0 = 无滑点（默认）</span>
        </el-form-item>
        <el-form-item label="费率">
          <div style="display: flex; gap: 12px; width: 100%">
            <div style="flex: 1">
              <div style="font-size: 12px; color: var(--el-text-color-secondary)">买入费率</div>
              <el-input-number
                v-model="btParams.costBuy"
                :min="0"
                :max="0.01"
                :step="0.0001"
                :precision="4"
                :controls="false"
                style="width: 100%"
              />
            </div>
            <div style="flex: 1">
              <div style="font-size: 12px; color: var(--el-text-color-secondary)">卖出费率</div>
              <el-input-number
                v-model="btParams.costSell"
                :min="0"
                :max="0.01"
                :step="0.0001"
                :precision="4"
                :controls="false"
                style="width: 100%"
              />
            </div>
          </div>
        </el-form-item>
        <el-form-item label="单笔最低佣金（元）">
          <el-input-number
            v-model="btParams.minCost"
            :min="0"
            :max="100"
            :step="1"
            :controls="false"
            style="width: 120px"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="btParams.visible = false">取消</el-button>
        <el-button type="primary" @click="confirmBacktest">开始回测</el-button>
      </template>
    </el-dialog>

    <!-- AI 生成策略结果弹窗 -->
    <ConfirmDialog
      v-model="aiResultDialog.visible"
      title="AI 策略已创建"
      :message="aiResultDialog.rationale"
      icon="success"
      type="primary"
      confirm-text="好的"
      :show-cancel="false"
      @confirm="aiResultDialog.visible = false"
    >
      <div v-if="aiResultDialog.detail" class="ai-result">
        <div class="ai-result__row">
          <span class="ai-result__label">因子</span>
          <span class="ai-result__value">{{ aiResultDialog.detail.factorNames }}</span>
        </div>
        <div class="ai-result__row">
          <span class="ai-result__label">参数</span>
          <span class="ai-result__value">{{ aiResultDialog.detail.params }}</span>
        </div>
      </div>
    </ConfirmDialog>

    <!-- 删除回测结果确认弹窗 -->
    <ConfirmDialog
      v-model="deleteDialog.visible"
      title="删除回测结果"
      message="删除后该回测结果不再显示（软删除），确定删除？"
      icon="warning"
      type="danger"
      confirm-text="确定删除"
      @confirm="doDeleteResult"
    />
  </PageContainer>
</template>

<script setup>
defineOptions({ name: 'QuantStrategy' })
import { ref, reactive, computed, nextTick, onMounted, onBeforeUnmount } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus/es/components/message/index'
import { Plus, Refresh } from '@element-plus/icons-vue'
import PageContainer from '@/components/common/PageContainer.vue'
import PageHeader from '@/components/common/PageHeader.vue'
import ConfirmDialog from '@/components/common/ConfirmDialog.vue'
import BacktestResultDetail from '@/components/quant/BacktestResultDetail.vue'
import { usePolling } from '@/composables/usePolling'
import { formatTime } from '@/utils/format'
import {
  listStrategies,
  createStrategy,
  runBacktest,
  listBacktestResults,
  getBacktestResult,
  deleteBacktestResult,
  getAllBacktestStatuses,
  runWalkForward,
  getWalkForwardResults,
  aiGenerateStrategy,
} from '@/api/strategy'
import { useFactorStore } from '@/stores/factor'
import { getQuantDataStatus } from '@/api/quant'

const router = useRouter()
const factorStore = useFactorStore()

// === 策略列表与选中 ===
const strategies = ref([])
const selectedStrategy = ref(null)
const listLoading = ref(false)
// 行内展开（单开折叠）：expandedKeys 仅保留一个 id，点其它行自动收起
const expandedKeys = ref([])
const expandedId = ref(null)

// === AI 策略 ===
const aiGenerating = ref(false)
const factorCount = computed(() => factorStore.factors?.length || 0)
// AI 生成偏好（风格/风险/调仓频率/资金/其他要求）
const aiPref = ref({ visible: false, style: '', risk: '', rebalance: '', capital: null, other: '' })

// === 回测参数（资金/区间） ===
// 默认区间 = 近 2 年 → 最新数据日期（取后端数据状态中最大的 latest_date，
// 而非硬编码 2020-01-01 或"今天"——今天数据未发布时回测尾端会全是 NaN）
const defaultBacktestRange = ref(['2020-01-01', new Date().toISOString().slice(0, 10)])

async function refreshDefaultBacktestRange() {
  try {
    const status = await getQuantDataStatus()
    const dates = (status?.items || [])
      .map((it) => it.latest_date)
      .filter(Boolean)
      .sort()
    const end = dates.length ? dates[dates.length - 1] : new Date().toISOString().slice(0, 10)
    const start = new Date(end)
    start.setFullYear(start.getFullYear() - 2)
    defaultBacktestRange.value = [start.toISOString().slice(0, 10), end]
  } catch {
    // 拉取失败保留默认（近 2 年 → 今天）
    const end = new Date()
    const start = new Date(end)
    start.setFullYear(start.getFullYear() - 2)
    defaultBacktestRange.value = [start.toISOString().slice(0, 10), end.toISOString().slice(0, 10)]
  }
}

const btParams = ref({
  visible: false,
  row: null,
  range: [...defaultBacktestRange.value],
  capital: 100000000,
  universe: '', // 标的池（空 = config 默认）
  lotSize: true, // 整手约束（100 股整手）
  dealPrice: 'close', // close=T+1收盘 / open=T+1开盘
  slippage: 0, // 滑点 bps
  costBuy: 0.0013, // 买入费率
  costSell: 0.0023, // 卖出费率
  minCost: 5, // 单笔最低佣金
})

// 标的池选项（从 GET /quant/universes 拉取实际存在的池，含 ETF 池）
const universeOptions = ref([])
async function loadUniverses() {
  try {
    const { listUniverses } = await import('@/api/quant')
    const items = await listUniverses()
    if (Array.isArray(items) && items.length) {
      universeOptions.value = items.map((u) => ({ value: u.name, label: `${u.name}（${u.count}）` }))
    }
  } catch (e) {
    // 拉取失败保留空选项（回测走 config 默认池）
  }
}

// === 回测状态 ===
const backtestStatuses = ref({})

// === 选中的策略（用于对比回测结果） ===
const selectedResults = ref([])
const comparing = ref(false)
function handleResultSelectionChange(val) {
  selectedResults.value = val
}
async function compareResults() {
  if (selectedResults.value.length < 2) {
    ElMessage.warning('请至少选择 2 个策略')
    return
  }
  comparing.value = true
  try {
    // 获取每个选中策略的最新回测结果 ID
    const promises = selectedResults.value.map((s) =>
      listBacktestResults(s.id, { limit: 1 })
        .then((data) => data?.items?.[0]?.id)
        .catch(() => null)
    )
    const ids = (await Promise.all(promises)).filter((id) => id != null)
    if (ids.length < 2) {
      ElMessage.warning('选中策略的有效回测结果不足 2 个，无法对比')
      return
    }
    router.push(`/quant/backtest-compare?ids=${ids.join(',')}`)
  } catch (e) {
    ElMessage.error('获取回测结果失败')
  } finally {
    comparing.value = false
  }
}

// === 回测结果 ===
const currentResult = ref(null)
const resultLoading = ref(false)

// === 新建策略对话框 ===
const showCreate = ref(false)
const creating = ref(false)
const factorOptions = ref([])
const form = reactive({
  name: '',
  factor_ids: [],
  combination_method: 'equal_weight',
  topk: 50,
  n_drop: 5,
  rebalance_freq: 'week',
  benchmark: 'SH000300',
  orthogonalize: 0,
})

// === 轮询控制 ===
// 回测结果轮询：每 3s 检查一次，最多 40 次
let pollRow = null
let pollPrevId = null
let resultAttempts = 0
const resultPolling = usePolling(async () => {
  resultAttempts++
  try {
    const data = await listBacktestResults(pollRow, { limit: 1 })
    const latest = data?.items?.[0]
    // 出现新的已完成结果（id 变化且指标已填充）
    if (latest && latest.id !== pollPrevId && latest.annual_return != null) {
      currentResult.value = await getBacktestResult(latest.id)
      ElMessage.success('回测完成')
      stopPolling()
    } else if (resultAttempts >= 40) {
      ElMessage.warning('回测仍在进行中，请稍后点击"结果"查看')
      stopPolling()
    }
  } catch (e) {
    if (resultAttempts >= 40) stopPolling()
  }
}, 3000, { immediate: false })

// 状态轮询：每 3s 刷新，无 running 时自动停止
const statusPolling = usePolling(async () => {
  await loadBacktestStatuses()
  if (!hasRunningStatus()) {
    stopStatusPolling()
  }
}, 3000, { immediate: false })

// === 选中行样式 ===
function rowClassName({ row }) {
  return selectedStrategy.value?.id === row.id ? 'is-selected' : ''
}

// === 回测状态显示 ===
function getBacktestStatusClass(strategyId) {
  const s = backtestStatuses.value[strategyId]?.status || 'idle'
  return 'status-badge--' + s
}

function getBacktestStatusText(strategyId) {
  const s = backtestStatuses.value[strategyId]?.status || 'idle'
  const map = { idle: '空闲', running: '运行中', completed: '已完成', failed: '失败' }
  return map[s] || s
}

// === 加载回测状态 ===
async function loadBacktestStatuses() {
  try {
    const data = await getAllBacktestStatuses()
    backtestStatuses.value = data?.items || {}
  } catch (e) {
    // 静默失败，不阻塞主流程
  }
}

function hasRunningStatus() {
  return Object.values(backtestStatuses.value).some((s) => s?.status === 'running')
}

// === 状态轮询（每 3s 刷新，无 running 时自动停止） ===
function startStatusPolling() {
  statusPolling.start()
}

function stopStatusPolling() {
  statusPolling.stop()
}

// === 加载策略列表 ===
async function loadStrategies() {
  listLoading.value = true
  try {
    const data = await listStrategies()
    strategies.value = data?.items || []
    await loadBacktestStatuses()
    // 如果有运行中的回测，启动状态轮询
    if (hasRunningStatus()) {
      startStatusPolling()
    }
  } catch (e) {
    if (e !== 'cancel') ElMessage.error('加载策略列表失败')
  } finally {
    listLoading.value = false
  }
}

// === 加载因子选项（供新建对话框选择） ===
async function loadFactors() {
  try {
    await factorStore.fetchList()
    factorOptions.value = factorStore.activeFactors
  } catch (e) {
    // 静默失败，不阻塞主流程
  }
}

// === 行内展开（单开折叠）：el-table 展开变化时同步状态并加载结果 ===
function onExpandChange(row, expandedRows) {
  const isExpanded = expandedRows.some((r) => r.id === row.id)
  if (isExpanded) {
    // 只保留当前行展开，其它自动收起
    expandedKeys.value = [row.id]
    expandedId.value = row.id
    selectStrategy(row)
  } else if (expandedId.value === row.id) {
    expandedKeys.value = []
    expandedId.value = null
    selectedStrategy.value = null
    currentResult.value = null
  }
}

// === 点击行：展开该行（若已展开则收起） ===
function onRowClick(row) {
  if (!row) return
  if (expandedId.value === row.id) {
    expandedKeys.value = []
    expandedId.value = null
    selectedStrategy.value = null
    currentResult.value = null
  } else {
    expandedKeys.value = [row.id]
    expandedId.value = row.id
    selectStrategy(row)
  }
}

async function selectStrategy(row, scroll = false) {
  if (resultLoading.value && selectedStrategy.value?.id === row.id) return
  selectedStrategy.value = row
  currentResult.value = null
  resultLoading.value = true
  try {
    const data = await listBacktestResults(row.id, { limit: 1 })
    const items = data?.items || []
    if (items.length && items[0].id != null) {
      currentResult.value = await getBacktestResult(items[0].id)
    }
  } catch (e) {
    if (e !== 'cancel') ElMessage.error('加载回测结果失败')
  } finally {
    resultLoading.value = false
    if (scroll) {
      nextTick(() => {
        const el = document.querySelector('.strategy-result')
        if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' })
      })
    }
  }
}

// === 删除当前回测结果（软删除，清理重复/过期记录） ===
const deleteDialog = ref({ visible: false })

function deleteCurrentResult() {
  if (!currentResult.value) return
  deleteDialog.value.visible = true
}

async function doDeleteResult() {
  deleteDialog.value.visible = false
  try {
    await deleteBacktestResult(currentResult.value.id)
    ElMessage.success('回测结果已删除')
    // 刷新为下一条最新结果
    if (selectedStrategy.value) await selectStrategy(selectedStrategy.value)
  } catch (e) {
    if (e !== 'cancel') ElMessage.error('删除失败: ' + (e?.message || e))
  }
}

// === 触发回测 + 轮询结果 ===
async function triggerBacktest(row) {
  // 弹参数窗：选择初始资金 / 回测区间 / 执行设置后再执行
  // AI 生成偏好存于 strategy.ai_prefs（单 JSON 字段），其 capital 预填为回测初始资金
  // 打开前刷新默认区间：近 2 年 → 最新数据日期（而非硬编码 2020 或"今天"）
  await refreshDefaultBacktestRange()
  btParams.value = {
    visible: true,
    row,
    range: [...defaultBacktestRange.value],
    capital: row?.ai_prefs?.capital || row?.capital || 100000000,
    universe: '',
    lotSize: true,
    dealPrice: 'close',
    slippage: 0,
    costBuy: 0.0013,
    costSell: 0.0023,
    minCost: 5,
  }
}

async function confirmBacktest() {
  const row = btParams.value.row
  if (!row) return
  selectedStrategy.value = row
  expandedKeys.value = [row.id]
  expandedId.value = row.id
  btParams.value.visible = false
  const [startDate, endDate] = btParams.value.range || []
  if (!startDate || !endDate) {
    ElMessage.warning('请选择回测区间')
    return
  }
  // 记录回测前的最新结果 id，用于判断新结果是否产生
  let prevId = null
  try {
    const data = await listBacktestResults(row.id, { limit: 1 })
    const items = data?.items || []
    prevId = items.length ? items[0].id : null
  } catch (e) {
    /* ignore */
  }

  try {
    const params = {
      start_date: startDate,
      end_date: endDate,
      initial_capital: btParams.value.capital,
      trade_unit: btParams.value.lotSize ? 100 : 1,
      deal_price: btParams.value.dealPrice,
      slippage_bps: btParams.value.slippage,
      cost_buy: btParams.value.costBuy,
      cost_sell: btParams.value.costSell,
      min_cost: btParams.value.minCost,
    }
    if (btParams.value.universe) {
      params.universe = btParams.value.universe
      // ETF 池自动按 ETF 标的类别执行（T+0 语义/无整手/涨跌停放宽）
      params.asset_class = String(btParams.value.universe).startsWith('etf') ? 'etf' : 'stock'
    }
    await runBacktest(row.id, params)
    ElMessage.success('回测已启动')
    // 立即刷新状态并启动轮询
    await loadBacktestStatuses()
    startStatusPolling()
    startPolling(row, prevId)
  } catch (e) {
    if (e !== 'cancel') ElMessage.error('回测启动失败')
  }
}

// === 轮询回测结果（每 3s 检查一次，最多 40 次） ===
function startPolling(row, prevId) {
  resultPolling.stop()
  pollRow = row
  pollPrevId = prevId
  resultAttempts = 0
  resultLoading.value = true
  resultPolling.start()
}

function stopPolling() {
  resultPolling.stop()
  resultLoading.value = false
}

// === "结果"链接：展开该行并加载回测结果，滚动定位 ===
function viewResults(row) {
  if (!row) return
  expandedKeys.value = [row.id]
  expandedId.value = row.id
  selectStrategy(row, true)
}

// === "归档"链接：仅提示 ===
function archive(row) {
  ElMessage.info(`归档策略「${row.name}」(id=${row.id})`)
}

// === AI 生成策略 ===
const aiResultDialog = ref({ visible: false, rationale: '', detail: null })

async function onAiGenerate() {
  if (!factorStore.factors?.length) {
    ElMessage.warning('因子库为空，请先在因子库导入 Alpha158 或新增因子')
    return
  }
  // 弹偏好窗：风格 / 风险偏好 / 调仓频率 / 资金 / 其他要求
  aiPref.value = { visible: true, style: '', risk: '', rebalance: '', capital: null, other: '' }
}

async function confirmAiGenerate() {
  aiGenerating.value = true
  try {
    const params = {}
    if (aiPref.value.style) params.style = aiPref.value.style
    if (aiPref.value.risk) params.risk_tolerance = aiPref.value.risk
    if (aiPref.value.rebalance) params.rebalance_pref = aiPref.value.rebalance
    if (aiPref.value.capital) params.capital = aiPref.value.capital
    if (aiPref.value.other?.trim()) params.other = aiPref.value.other.trim()
    const data = await aiGenerateStrategy(params)
    aiPref.value.visible = false
    ElMessage.success(`AI 已生成策略「${data.strategy?.name}」`)
    // 展示 AI 推荐理由（样式化弹窗，替代默认 ElMessageBox.alert）
    aiResultDialog.value = {
      visible: true,
      rationale: data.rationale || '',
      detail: {
        factorNames: (data.factors || []).map((f) => f.name).join(', '),
        params: `topk=${data.strategy?.topk}, n_drop=${data.strategy?.n_drop}, 调仓=${data.strategy?.rebalance_freq}`,
      },
    }
    loadStrategies()
  } catch (e) {
    const msg = e?.response?.data?.error?.message || e?.message || 'AI 生成失败'
    ElMessage.error('AI 生成策略失败: ' + msg)
  } finally {
    aiGenerating.value = false
  }
}

// === 新建策略对话框 ===
function openCreate() {
  Object.assign(form, {
    name: '',
    factor_ids: [],
    combination_method: 'equal_weight',
    topk: 50,
    n_drop: 5,
    rebalance_freq: 'week',
    benchmark: 'SH000300',
    orthogonalize: 0,
  })
  showCreate.value = true
}

// === Walk-forward 滚动回测（添加14） ===
const wfDialog = reactive({
  visible: false,
  submitting: false,
  strategyId: null,
  result: null,
  form: { trainWindow: '730D', testWindow: '180D', step: '180D', nDrop: 5, rebalance: 'day' },
})

// Walk-forward 轮询：每 3s，最多 120 次
let wfAttempts = 0
const wfPolling = usePolling(async () => {
  wfAttempts++
  if (wfAttempts > 120) {
    stopWfPolling()
    return
  }
  try {
    const data = await getWalkForwardResults(wfDialog.strategyId)
    if (data && data.status && data.status !== 'running') {
      wfDialog.result = data
      stopWfPolling()
    } else if (data) {
      wfDialog.result = data
    }
  } catch (e) {
    /* ignore */
  }
}, 3000, { immediate: false })

function openWalkForward(row) {
  wfDialog.strategyId = row.id
  wfDialog.visible = true
  wfDialog.submitting = false
  wfDialog.result = null
  Object.assign(wfDialog.form, { trainWindow: '730D', testWindow: '180D', step: '180D', nDrop: 5, rebalance: 'day' })
}

function closeWalkForward() {
  wfDialog.visible = false
  stopWfPolling()
}

async function submitWalkForward() {
  if (!wfDialog.strategyId) return
  wfDialog.submitting = true
  try {
    await runWalkForward(wfDialog.strategyId, {
      train_window: wfDialog.form.trainWindow,
      test_window: wfDialog.form.testWindow,
      step: wfDialog.form.step,
      n_drop: wfDialog.form.nDrop,
      rebalance: wfDialog.form.rebalance,
    })
    wfDialog.result = { status: 'running' }
    ElMessage.success('Walk-forward 回测已启动')
    startWfPolling()
  } catch (e) {
    ElMessage.error('Walk-forward 启动失败')
  } finally {
    wfDialog.submitting = false
  }
}

function startWfPolling() {
  wfAttempts = 0
  wfPolling.start()
}

function stopWfPolling() {
  wfPolling.stop()
}

const _WF_LABELS = {
  total_return: '总收益',
  annual_return: '年化收益',
  annual_volatility: '年化波动',
  sharpe: '夏普',
  max_drawdown: '最大回撤',
  n_days: '天数',
  sharpe_mean: '夏普均值',
  sharpe_std: '夏普标准差',
  sharpe_min: '夏普最小',
  sharpe_max: '夏普最大',
  positive_ratio: '正收益占比',
}
function wfLabel(k) {
  return _WF_LABELS[k] || k
}
function wfFmt(k, v) {
  if (v == null || v === '') return '--'
  const n = Number(v)
  if (Number.isNaN(n)) return '--'
  if (k === 'n_days') return String(Math.round(n))
  if (['total_return', 'annual_return', 'annual_volatility', 'max_drawdown', 'positive_ratio'].includes(k)) {
    return (n * 100).toFixed(2) + '%'
  }
  return n.toFixed(2)
}

async function doCreate() {
  if (!form.name || !form.factor_ids.length) {
    ElMessage.warning('请填写名称并选择因子')
    return
  }
  creating.value = true
  try {
    await createStrategy(form)
    ElMessage.success('策略已创建')
    showCreate.value = false
    loadStrategies()
  } catch (e) {
    if (e !== 'cancel') ElMessage.error('创建策略失败')
  } finally {
    creating.value = false
  }
}

onMounted(() => {
  loadStrategies()
  loadFactors()
  loadUniverses()
})

onBeforeUnmount(() => {
  stopPolling()
  stopStatusPolling()
  stopWfPolling()
})
</script>

<style scoped lang="scss">
// 页面头
.wf-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: var(--space-md);
  margin-bottom: var(--space-md);
}
// 策略列表表格卡片
.table-card {
  overflow: hidden;

  :deep(.el-table) {
    --el-table-border-color: var(--border-light);
    --el-table-header-bg-color: var(--bg-tertiary);
    --el-table-tr-bg-color: transparent;
    --el-table-row-hover-bg-color: var(--bg-hover);
    font-size: var(--font-size-base);

    th.el-table__cell {
      background: var(--bg-tertiary);
      font-size: var(--font-size-sm);
      color: var(--text-tertiary);
      font-weight: var(--font-weight-medium);
    }

    .el-table__row {
      cursor: pointer;
    }

    // 选中行高亮
    .el-table__row.is-selected td.el-table__cell {
      background: rgba(var(--primary-rgb), 0.05) !important;
    }
  }
}

// 单元格样式
.cell-mono {
  font-family: var(--font-mono);
  color: var(--text-secondary);
}
.cell-name {
  font-weight: var(--font-weight-medium);
  color: var(--text-primary);
}
.cell-factors {
  display: inline-block;
  max-width: 200px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-family: var(--font-mono);
  font-size: var(--font-size-sm);
  color: var(--text-secondary);
  vertical-align: middle;
}
.cell-tnum {
  font-variant-numeric: tabular-nums;
}
.cell-sm {
  font-size: var(--font-size-sm);
}

.text-success {
  color: var(--success, #1f9d6b);
}
.text-danger {
  color: var(--danger, #d24545);
}

// 组合方式 pill 徽标
.pill {
  display: inline-block;
  font-size: var(--font-size-sm);
  padding: 2px 10px;
  border-radius: var(--radius-full);
  line-height: 1.5;
}
.pill--muted {
  background: var(--bg-tertiary);
  color: var(--text-tertiary);
}
.pill--primary {
  background: rgba(var(--primary-rgb), 0.1);
  color: var(--primary);
}

// 回测状态徽标
.status-badge {
  display: inline-block;
  font-size: var(--font-size-sm);
  padding: 2px 10px;
  border-radius: var(--radius-full);
  line-height: 1.5;
}
.status-badge--idle {
  background: var(--bg-tertiary);
  color: var(--text-tertiary);
}
.status-badge--running {
  background: rgba(var(--primary-rgb), 0.1);
  color: var(--primary);
}
.status-badge--completed {
  background: rgba(0, 128, 0, 0.1);
  color: var(--success);
}
.status-badge--failed {
  background: rgba(255, 0, 0, 0.1);
  color: var(--danger);
}

// 操作链接
.row-actions {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: var(--space-md);
}
.link {
  cursor: pointer;
  font-size: var(--font-size-base);
  user-select: none;

  &:hover {
    opacity: 0.8;
  }
}
.link--primary {
  color: var(--primary);
}
.link--success {
  color: var(--success);
}
.link--danger {
  color: var(--danger);
}

/* Walk-forward 样式 */
.link--warning {
  color: var(--warning, #c8801c);
}
.wf-result {
  margin-top: 8px;
}
.wf-section-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-primary, #1f2329);
  margin: 16px 0 8px;
}
.wf-metrics {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(120px, 1fr));
  gap: 8px;
}
.wf-metric-card {
  background: var(--bg-tertiary, #f5f6f7);
  border-radius: 6px;
  padding: 8px 12px;
}
.wf-metric-label {
  font-size: 12px;
  color: var(--text-tertiary, #8a9099);
}
.wf-metric-value {
  font-size: 16px;
  font-weight: 600;
  margin-top: 2px;
  font-variant-numeric: tabular-nums;
}

// AI 生成策略结果弹窗内容
.ai-result {
  display: flex;
  flex-direction: column;
  gap: 6px;

  &__row {
    display: flex;
    gap: var(--space-sm);
    font-size: var(--font-size-sm);
    line-height: 1.5;
  }

  &__label {
    flex-shrink: 0;
    color: var(--text-tertiary);
  }

  &__value {
    color: var(--text-primary);
    word-break: break-all;
  }
}
</style>
