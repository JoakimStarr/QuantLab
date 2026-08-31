<template>
  <PageContainer narrow>
    <PageHeader title="系统设置" subtitle="LLM / 量化参数 / 日志与调度配置，保存后热重载，无需重启">
      <template #actions>
        <el-button :icon="Refresh" :loading="loading" @click="load">刷新</el-button>
        <el-button type="primary" :icon="Check" :loading="saving" @click="onSave">保存设置</el-button>
      </template>
    </PageHeader>

    <el-alert
      type="info"
      :closable="false"
      show-icon
      class="mb-16"
      title="配置说明"
      description="AI Provider 的增删改、切换、测试即时保存到后端；API Key 不回显。生成参数与数据源 Key 等通过「保存设置」写入：LLM 生成参数写入 JSON store，其余写入 config.yaml / .env（保留原有注释）。保存后立即热重载。"
    />

    <div class="settings-layout">
      <!-- 左侧分类导航 -->
      <aside class="settings-nav">
        <button
          v-for="s in sections"
          :key="s.key"
          type="button"
          class="settings-nav__item"
          :class="{ 'settings-nav__item--active': activeSection === s.key }"
          @click="activeSection = s.key"
        >
          <el-icon :size="16"><component :is="s.icon" /></el-icon>
          <span>{{ s.title }}</span>
        </button>
      </aside>

      <!-- 右侧内容区 -->
      <div class="settings-content">
        <!-- ============ LLM 配置 ============ -->
        <section v-if="activeSection === 'llm'" class="settings-panel">
          <div class="settings-panel__head">
            <div>
              <h2 class="settings-panel__title">LLM 配置</h2>
              <p class="settings-panel__subtitle">内置三家 + 自定义 Provider；当前使用（绿点）用于 AI 服务，失败按列表顺序自动回退。Provider 增删改/切换即时保存，API Key 不回显。</p>
            </div>
          </div>

          <!-- 当前使用 provider 状态条 -->
          <div class="ai-status-bar" :class="activeProvider?.configured ? 'ok' : 'warn'">
            <span class="ai-status-dot"></span>
            <div class="ai-status-info">
              <span class="ai-status-label">{{ activeProvider?.configured ? '已配置' : '未配置 API Key' }} · {{ activeProvider?.name || '' }}</span>
              <span class="ai-status-model">{{ activeProvider?.model || '（未设置模型）' }}</span>
              <span class="ai-status-url">{{ activeProvider?.base_url }}</span>
            </div>
            <el-button
              size="small"
              class="ai-status-test"
              :loading="testingId === activeProvider?.id"
              @click="activeProvider && testProvider(activeProvider)"
            >
              <el-icon v-if="testingId !== activeProvider?.id" style="margin-right: 4px"><Aim /></el-icon>
              测试连接
            </el-button>
          </div>

          <!-- Provider 列表 -->
          <div class="provider-list">
            <div v-for="p in aiProviders" :key="p.id" class="provider-card" :class="{ active: p.id === activeProviderId }">
              <div class="provider-card__head">
                <el-tag v-if="p.id === activeProviderId" size="small" type="success" effect="light">使用中</el-tag>
                <el-tag v-if="p.builtin" size="small" type="info" effect="light">内置</el-tag>
                <span class="provider-card__name">{{ p.name }}</span>
                <el-tag size="small" :type="p.configured ? 'success' : 'warning'" effect="light">
                  {{ p.configured ? '已配置' : '未配置 key' }}
                </el-tag>
                <span class="spacer"></span>
                <el-button size="small" :disabled="p.id === activeProviderId || actingId === p.id" @click="activateProvider(p.id)">
                  设为当前
                </el-button>
                <el-button size="small" :loading="testingId === p.id" @click="testProvider(p)">
                  <el-icon v-if="testingId !== p.id" style="margin-right: 4px"><Aim /></el-icon>
                  测试
                </el-button>
                <el-button size="small" @click="openEdit(p)">编辑</el-button>
                <el-button size="small" text type="danger" :disabled="actingId === p.id" @click="removeProvider(p)">
                  {{ p.builtin ? '重置' : '删除' }}
                </el-button>
              </div>
              <div class="provider-card__body">
              <div class="provider-card__meta">
                <span class="provider-meta-model">{{ p.model || '（未设置模型）' }}</span>
                <span v-if="p.max_tokens" class="provider-meta-tokens">max_tokens={{ p.max_tokens }}</span>
                <span class="provider-meta-url">{{ p.base_url }}</span>
              </div>
              </div>
            </div>
          </div>

          <!-- 添加 / 编辑表单 -->
          <div class="prov-editor">
            <el-button v-if="!editorOpen" block plain class="provider-add" style="width: 100%" @click="openCreate">
              <el-icon style="margin-right: 4px"><Plus /></el-icon>添加 Provider
            </el-button>

            <div v-else class="editor-box">
              <div class="editor-title">{{ editor.id ? '编辑 Provider' : '添加 Provider' }}</div>
              <el-form label-position="top">
                <el-form-item label="预设快捷填充">
                  <el-select v-model="editPreset" style="width: 100%" @change="applyEditPreset">
                    <el-option v-for="pr in EDIT_PRESETS" :key="pr.name" :value="pr.name" :label="pr.name" />
                  </el-select>
                </el-form-item>
                <el-form-item label="名称">
                  <el-input v-model="editor.name" placeholder="如：我的 DeepSeek" clearable />
                </el-form-item>
                <el-form-item label="Base URL">
                  <el-input v-model="editor.baseUrl" placeholder="https://open.bigmodel.cn/api/paas/v4" clearable />
                </el-form-item>
                <div class="provider-card__row">
                  <el-form-item label="模型名称">
                    <div class="model-picker">
                      <el-select
                        v-model="editor.model"
                        filterable
                        allow-create
                        default-first-option
                        placeholder="glm-4-flash"
                        style="width: 100%"
                      >
                        <el-option v-for="m in modelList" :key="m" :value="m" :label="m" />
                      </el-select>
                      <el-button class="model-fetch-btn" :loading="modelLoading" @click="fetchEditorModels">
                        获取模型
                      </el-button>
                    </div>
                  </el-form-item>
                  <el-form-item label="API Key">
                    <el-input
                      v-model="editor.apiKey"
                      type="password"
                      show-password
                      :placeholder="editorConfigured ? '已配置，留空保持不变' : '粘贴 API Key'"
                      clearable
                    />
                  </el-form-item>
                </div>
                <el-form-item label="单次输出上限 max_tokens（推理模型请调大，如 8192）">
                  <el-input-number
                    v-model="editor.maxTokens"
                    :min="256"
                    :max="32768"
                    :step="512"
                    controls-position="right"
                    placeholder="留空用全局默认"
                    style="width: 100%"
                  />
                </el-form-item>
                <div class="editor-actions">
                  <el-button :loading="editorTesting" @click="testEditor">测试连接</el-button>
                  <span class="spacer"></span>
                  <el-button @click="closeEditor">取消</el-button>
                  <el-button type="primary" :loading="editorSaving" @click="saveProvider">保存</el-button>
                </div>
              </el-form>
            </div>
          </div>

          <el-divider content-position="left">全局生成参数（JSON store，保存设置生效）</el-divider>
          <el-form label-position="top" class="global-form">
            <el-form-item label="温度 temperature">
              <el-slider v-model="aiGlobal.temperature" :min="0" :max="2" :step="0.1" show-input />
            </el-form-item>
            <el-form-item label="最大输出 token 数">
              <el-input-number v-model="aiGlobal.maxTokens" :min="64" :max="8192" :step="64" controls-position="right" />
            </el-form-item>
          </el-form>

          <el-divider content-position="left">AI 高级参数（config.yaml，保存设置生效）</el-divider>
          <el-form label-position="top" class="global-form">
            <el-form-item label="强制 JSON 输出">
              <el-switch v-model="form.ai_provider.force_json_output" active-text="是" inactive-text="否" />
            </el-form-item>
            <el-form-item label="失败重试次数">
              <el-input-number v-model="form.ai_provider.retry_times" :min="0" :max="10" controls-position="right" />
            </el-form-item>
            <el-form-item label="路由总预算(秒)">
              <el-input-number v-model="form.ai_provider.route_budget_seconds" :min="10" :max="3600" :step="10" controls-position="right" />
            </el-form-item>
            <el-form-item label="缓存有效期">
              <el-select v-model="form.ai_provider.cache_ttl" style="width: 100%">
                <el-option label="day（按天）" value="day" />
                <el-option label="hour（按小时）" value="hour" />
                <el-option label="none（不缓存）" value="none" />
              </el-select>
            </el-form-item>
            <el-form-item label="单请求总超时(秒)">
              <el-input-number v-model="form.ai_provider.total_timeout_seconds" :min="1" :max="300" controls-position="right" />
            </el-form-item>
          </el-form>
        </section>

        <!-- ============ 数据源 API Key ============ -->
        <section v-if="activeSection === 'api'" class="settings-panel">
          <div class="settings-panel__head">
            <div>
              <h2 class="settings-panel__title">数据源 API Key</h2>
              <p class="settings-panel__subtitle">写入 .env，留空表示保持不变</p>
            </div>
          </div>
          <el-form label-position="top" class="global-form">
            <el-form-item label="FRED API Key（全球宏观）">
              <el-input
                v-model="apiKeyInputs.fred"
                type="password"
                show-password
                :placeholder="apiKeyConfigured('fred') ? '已配置，留空保持不变' : '粘贴 FRED API Key'"
                clearable
              />
            </el-form-item>
            <el-form-item label="EIA API Key（全球宏观）">
              <el-input
                v-model="apiKeyInputs.eia"
                type="password"
                show-password
                :placeholder="apiKeyConfigured('eia') ? '已配置，留空保持不变' : '粘贴 EIA API Key'"
                clearable
              />
            </el-form-item>
          </el-form>
        </section>

        <!-- ============ 量化参数 ============ -->
        <section v-if="activeSection === 'quant'" class="settings-panel">
          <div class="settings-panel__head">
            <div>
              <h2 class="settings-panel__title">量化参数</h2>
              <p class="settings-panel__subtitle">回测口径与数据获取参数</p>
            </div>
          </div>
          <el-form label-position="top" class="form-grid">
            <el-form-item label="基准指数">
              <el-input v-model="form.quant.benchmark" placeholder="SH000300" clearable />
            </el-form-item>
            <el-form-item label="股票池">
              <el-select v-model="form.quant.universe" style="width: 100%">
                <el-option label="csi300（沪深300）" value="csi300" />
                <el-option label="csi500（中证500）" value="csi500" />
                <el-option label="csi800（中证800）" value="csi800" />
                <el-option label="csi1000（中证1000）" value="csi1000" />
              </el-select>
            </el-form-item>
            <el-form-item label="复权方式">
              <el-select v-model="form.quant.adjust" style="width: 100%">
                <el-option label="qfq（前复权）" value="qfq" />
                <el-option label="hfq（后复权）" value="hfq" />
                <el-option label="none（不复权）" value="none" />
              </el-select>
            </el-form-item>
            <el-form-item label="TopK（选股数量）">
              <el-input-number v-model="form.quant.topk" :min="1" :max="1000" controls-position="right" />
            </el-form-item>
            <el-form-item label="买入成本">
              <el-input-number v-model="form.quant.cost_buy" :min="0" :max="0.05" :step="0.0001" controls-position="right" />
            </el-form-item>
            <el-form-item label="卖出成本">
              <el-input-number v-model="form.quant.cost_sell" :min="0" :max="0.05" :step="0.0001" controls-position="right" />
            </el-form-item>
            <el-form-item label="滑点(bps)">
              <el-input-number v-model="form.quant.slippage_bps" :min="0" :max="100" controls-position="right" />
            </el-form-item>
            <el-form-item label="剔除前 N 名">
              <el-input-number v-model="form.quant.n_drop" :min="0" :max="20" controls-position="right" />
            </el-form-item>
            <el-form-item label="数据源">
              <el-select v-model="form.quant.data_source" style="width: 100%">
                <el-option label="baostock" value="baostock" />
              </el-select>
            </el-form-item>
            <el-form-item label="获取间隔(秒)">
              <el-input-number v-model="form.quant.fetch_interval_seconds" :min="0.1" :max="30" :step="0.1" controls-position="right" />
            </el-form-item>
            <el-form-item label="并发抓取数">
              <el-input-number v-model="form.quant.fetch_max_workers" :min="1" :max="16" controls-position="right" />
            </el-form-item>
            <el-form-item label="包含北交所">
              <el-switch v-model="form.quant.include_bj" active-text="是" inactive-text="否" />
            </el-form-item>
          </el-form>
        </section>

        <!-- ============ 蒙特卡罗模拟 ============ -->
        <section v-if="activeSection === 'monteCarlo'" class="settings-panel">
          <div class="settings-panel__head">
            <div>
              <h2 class="settings-panel__title">蒙特卡罗模拟</h2>
              <p class="settings-panel__subtitle">回测指标 bootstrap 置信区间与因子 IC 置换检验参数</p>
            </div>
          </div>
          <el-form label-position="top" class="form-grid">
            <el-form-item label="Bootstrap 抽样次数">
              <el-input-number v-model="form.monte_carlo.bootstrap_iterations" :min="100" :max="10000" :step="100" controls-position="right" />
            </el-form-item>
            <el-form-item label="平均块长(交易日)">
              <el-input-number v-model="form.monte_carlo.bootstrap_block" :min="1" :max="100" controls-position="right" />
            </el-form-item>
            <el-form-item label="置信水平">
              <el-input-number v-model="form.monte_carlo.bootstrap_ci" :min="0.5" :max="0.99" :step="0.01" :precision="2" controls-position="right" />
            </el-form-item>
            <el-form-item label="IC 置换检验次数">
              <el-input-number v-model="form.monte_carlo.permutation_n" :min="100" :max="10000" :step="100" controls-position="right" />
            </el-form-item>
            <el-form-item label="置换检验显著性水平">
              <el-input-number v-model="form.monte_carlo.permutation_alpha" :min="0.001" :max="0.5" :step="0.01" :precision="3" controls-position="right" />
            </el-form-item>
          </el-form>
        </section>

        <!-- ============ 日志与调度 ============ -->
        <section v-if="activeSection === 'logging'" class="settings-panel">
          <div class="settings-panel__head">
            <div>
              <h2 class="settings-panel__title">日志与调度</h2>
              <p class="settings-panel__subtitle">日志级别、保留策略与定时任务</p>
            </div>
          </div>
          <el-form label-position="top" class="form-grid">
            <el-form-item label="日志级别">
              <el-select v-model="form.logging.level" style="width: 100%">
                <el-option v-for="lvl in ['DEBUG', 'INFO', 'WARNING', 'ERROR']" :key="lvl" :label="lvl" :value="lvl" />
              </el-select>
            </el-form-item>
            <el-form-item label="普通日志保留(天)">
              <el-input-number v-model="form.logging.retention_days" :min="1" :max="90" controls-position="right" />
            </el-form-item>
            <el-form-item label="错误日志保留(天)">
              <el-input-number v-model="form.logging.error_retention_days" :min="1" :max="180" controls-position="right" />
            </el-form-item>
            <el-form-item label="日志清理">
              <el-switch v-model="form.logging.cleanup_enabled" active-text="启用" inactive-text="停用" />
            </el-form-item>
            <el-form-item label="日志输出到控制台">
              <el-switch v-model="form.logging.console" active-text="是" inactive-text="否" />
            </el-form-item>
            <el-form-item label="量化数据定时更新时间">
              <el-input v-model="form.scheduler.quant_data_update_time" placeholder="18:00" clearable />
            </el-form-item>
          </el-form>
        </section>

        <!-- ============ 任务参数 ============ -->
        <section v-if="activeSection === 'task'" class="settings-panel">
          <div class="settings-panel__head">
            <div>
              <h2 class="settings-panel__title">任务参数</h2>
              <p class="settings-panel__subtitle">并发与任务超时控制</p>
            </div>
          </div>
          <el-form label-position="top" class="form-grid">
            <el-form-item label="CPU 工作线程">
              <el-input-number v-model="form.task.cpu_workers" :min="1" :max="64" controls-position="right" />
            </el-form-item>
            <el-form-item label="IO 工作线程">
              <el-input-number v-model="form.task.io_workers" :min="1" :max="128" controls-position="right" />
            </el-form-item>
            <el-form-item label="最大并发任务">
              <el-input-number v-model="form.task.max_concurrent" :min="1" :max="32" controls-position="right" />
            </el-form-item>
            <el-form-item label="任务默认超时(秒)">
              <el-input-number v-model="form.task.task_timeout_seconds" :min="30" :max="7200" :step="30" controls-position="right" />
            </el-form-item>
          </el-form>
        </section>

        <!-- ============ 系统信息（只读） ============ -->
        <section v-if="activeSection === 'system'" class="settings-panel">
          <div class="settings-panel__head">
            <div>
              <h2 class="settings-panel__title">系统信息</h2>
              <p class="settings-panel__subtitle">当前运行时信息（只读）</p>
            </div>
          </div>
          <el-descriptions :column="2" border size="small">
            <el-descriptions-item label="应用名称">{{ appInfo.name || '-' }}</el-descriptions-item>
            <el-descriptions-item label="版本">{{ appInfo.version || '-' }}</el-descriptions-item>
            <el-descriptions-item label="环境">{{ securityInfo.app_env || '-' }}</el-descriptions-item>
            <el-descriptions-item label="鉴权">
              <el-tag size="small" :type="securityInfo.auth_enabled ? 'success' : 'warning'">
                {{ securityInfo.auth_enabled ? '已启用' : '未启用' }}
              </el-tag>
            </el-descriptions-item>
            <el-descriptions-item label="时区">{{ appInfo.timezone || '-' }}</el-descriptions-item>
            <el-descriptions-item label="数据目录">{{ form.data.processed_dir }} / {{ form.data.raw_dir }}</el-descriptions-item>
          </el-descriptions>
        </section>
      </div>
    </div>

    <!-- 保存确认弹窗（与其他页面统一使用 ConfirmDialog 组件） -->
    <ConfirmDialog
      v-model="saveDialog"
      title="保存设置"
      message="保存后立即热重载生效：生成参数、AI 路由、量化参数、日志级别等即时更新，无需重启后端。继续保存？"
      icon="question"
      confirm-text="保存"
      :loading="saving"
      @confirm="doSave"
    />
  </PageContainer>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  Check,
  Refresh,
  Aim,
  MagicStick,
  Key,
  TrendCharts,
  Histogram,
  Clock,
  SetUp,
  InfoFilled,
  Plus,
} from '@element-plus/icons-vue'
import PageContainer from '@/components/common/PageContainer.vue'
import PageHeader from '@/components/common/PageHeader.vue'
import ConfirmDialog from '@/components/common/ConfirmDialog.vue'
import {
  getSettings,
  saveSettings,
  fetchAISettings,
  saveAISettings,
  createAIProvider,
  updateAIProvider,
  deleteAIProvider,
  activateAIProvider,
  testAIProvider,
  testAISettings,
  fetchAIModelsByConfig,
} from '@/api/settings'

const loading = ref(false)
const saving = ref(false)
const testingId = ref('')
const actingId = ref('')
const saveDialog = ref(false)

// 左侧分类导航（key 与下方面板的 v-if 对应）
const sections = [
  { key: 'llm', title: 'LLM 配置', icon: MagicStick },
  { key: 'api', title: '数据源 API Key', icon: Key },
  { key: 'quant', title: '量化参数', icon: TrendCharts },
  { key: 'monteCarlo', title: '蒙特卡罗模拟', icon: Histogram },
  { key: 'logging', title: '日志与调度', icon: Clock },
  { key: 'task', title: '任务参数', icon: SetUp },
  { key: 'system', title: '系统信息', icon: InfoFilled },
]
const activeSection = ref('llm')

const appInfo = ref({})
const securityInfo = ref({})

// ---------- AI Provider（JSON store，从 Quantlerning 迁移） ----------
const aiProviders = ref([])
const activeProviderId = ref('')
const activeProvider = computed(
  () => aiProviders.value.find((p) => p.id === activeProviderId.value) || aiProviders.value[0] || null,
)
const aiGlobal = reactive({ maxTokens: 1024, temperature: 0.4 })

// 新增 provider 时的预设快捷填充
const EDIT_PRESETS = [
  { name: 'OpenCodeZen', base_url: 'https://opencode.ai/zen/v1', model: 'deepseek-v4-flash-free' },
  { name: '智谱 GLM', base_url: 'https://open.bigmodel.cn/api/paas/v4', model: 'glm-4.7-flash' },
  { name: '硅基流动 SiliconFlow', base_url: 'https://api.siliconflow.cn/v1', model: 'Qwen/Qwen2.5-7B-Instruct' },
  { name: '自定义', base_url: '', model: '' },
]

const editorOpen = ref(false)
const editor = reactive({ id: null, name: '', baseUrl: '', model: '', apiKey: '', maxTokens: null })
const editorConfigured = ref(false) // 编辑的 provider 是否已有 key（打码提示）
const editPreset = ref('自定义')
const editorSaving = ref(false)
const editorTesting = ref(false)
const modelList = ref([])
const modelLoading = ref(false)

// 表单模型：与 GET /settings 的 data 分区结构对应
// ai_provider 只保留 config.yaml 高级参数（providers 已由 JSON store 管理）
const form = reactive({
  ai_provider: {
    force_json_output: true,
    retry_times: 1,
    route_budget_seconds: 120,
    cache_ttl: 'day',
    total_timeout_seconds: 10,
  },
  quant: {},
  logging: {},
  scheduler: {},
  task: {},
  data: {},
  monte_carlo: {
    bootstrap_iterations: 1000,
    bootstrap_block: 20,
    bootstrap_ci: 0.9,
    permutation_n: 500,
    permutation_alpha: 0.05,
  },
})

// 仅收集用户主动输入的新 Key（留空表示保持不变）
const apiKeyInputs = reactive({
  fred: '',
  eia: '',
})
const apiKeyStatus = reactive({})
const apiKeyConfigured = (name) => apiKeyStatus[name]?.configured === true

async function load() {
  loading.value = true
  try {
    const [cfg, aiCfg] = await Promise.all([getSettings(), fetchAISettings()])
    // config.yaml 各分区（ai_provider 只取高级参数，忽略 providers/main_provider）
    for (const k of ['force_json_output', 'retry_times', 'route_budget_seconds', 'cache_ttl', 'total_timeout_seconds']) {
      if (cfg.ai_provider?.[k] !== undefined) form.ai_provider[k] = cfg.ai_provider[k]
    }
    for (const section of ['quant', 'logging', 'scheduler', 'task', 'data', 'monte_carlo']) {
      if (cfg[section]) Object.assign(form[section], cfg[section])
    }
    // API Key 状态（只读展示，数据源用于判断是否已配置）
    Object.keys(apiKeyStatus).forEach((k) => delete apiKeyStatus[k])
    Object.entries(cfg.api_keys || {}).forEach(([k, v]) => (apiKeyStatus[k] = v))
    // 系统信息
    appInfo.value = cfg.app || {}
    securityInfo.value = {
      app_env: cfg.security_env,
      auth_enabled: cfg.auth_enabled,
    }
    // 清空数据源 Key 输入（避免误带上次输入）
    Object.keys(apiKeyInputs).forEach((k) => (apiKeyInputs[k] = ''))
    // AI provider（JSON store）
    aiProviders.value = aiCfg.providers || []
    activeProviderId.value = aiCfg.active_provider_id || ''
    aiGlobal.maxTokens = aiCfg.max_tokens || 1024
    aiGlobal.temperature = typeof aiCfg.temperature === 'number' ? aiCfg.temperature : 0.4
  } catch (e) {
    if (e !== 'cancel') ElMessage.error('加载设置失败')
  } finally {
    loading.value = false
  }
}

// ---------- provider 操作（即时保存） ----------
async function activateProvider(id) {
  actingId.value = id
  try {
    const res = await activateAIProvider(id)
    activeProviderId.value = res.active_provider_id
    ElMessage.success('已设为当前 provider，AI 路由即时生效')
  } catch {
    // 拦截器已提示错误
  } finally {
    actingId.value = ''
  }
}

async function testProvider(p) {
  testingId.value = p.id
  try {
    const res = await testAIProvider(p.id)
    if (res.ok) ElMessage.success(`「${p.name}」连接成功${res.reply ? `：${res.reply}` : ''}`)
    else ElMessage.warning(`「${p.name}」${res.message}`)
  } catch {
    // 拦截器已提示错误
  } finally {
    testingId.value = ''
  }
}

async function removeProvider(p) {
  const label = p.builtin ? '重置为内置默认' : '删除该 provider'
  try {
    await ElMessageBox.confirm(`${label}「${p.name}」？`, '确认', {
      type: 'warning',
      confirmButtonText: '确认',
      cancelButtonText: '取消',
    })
  } catch {
    return
  }
  actingId.value = p.id
  try {
    const res = await deleteAIProvider(p.id)
    activeProviderId.value = res.active_provider_id
    await load()
    ElMessage.success(p.builtin ? `「${p.name}」已重置为内置默认` : `「${p.name}」已删除`)
  } catch {
    // 拦截器已提示错误
  } finally {
    actingId.value = ''
  }
}

// ---------- 添加 / 编辑表单 ----------
function openCreate() {
  editor.id = null
  editor.name = ''
  editor.baseUrl = ''
  editor.model = ''
  editor.apiKey = ''
  editor.maxTokens = null
  editorConfigured.value = false
  editPreset.value = '自定义'
  editorOpen.value = true
  modelList.value = []
}

function openEdit(p) {
  editor.id = p.id
  editor.name = p.name
  editor.baseUrl = p.base_url
  editor.model = p.model
  editor.apiKey = ''
  editor.maxTokens = p.max_tokens ?? null
  editorConfigured.value = p.configured
  editPreset.value = '自定义'
  editorOpen.value = true
  modelList.value = []
}

function closeEditor() {
  editorOpen.value = false
}

function applyEditPreset(name) {
  const pr = EDIT_PRESETS.find((x) => x.name === name)
  if (!pr) return
  editor.name = pr.name
  editor.baseUrl = pr.base_url
  editor.model = pr.model
}

async function fetchEditorModels() {
  modelLoading.value = true
  try {
    const res = await fetchAIModelsByConfig({
      base_url: editor.baseUrl.trim(),
      api_key: editor.apiKey.trim() || undefined,
      model: editor.model.trim(),
    })
    modelList.value = res.models || []
    if (res.error) ElMessage.warning(res.error)
  } catch {
    modelList.value = []
  } finally {
    modelLoading.value = false
  }
}

async function saveProvider() {
  const name = editor.name.trim()
  const baseUrl = editor.baseUrl.trim()
  const model = editor.model.trim()
  if (!name || !baseUrl || !model) {
    ElMessage.warning('请填写 名称 / Base URL / 模型名称 后再保存')
    return
  }
  editorSaving.value = true
  try {
    const payload = { name, base_url: baseUrl, model }
    if (editor.apiKey.trim()) payload.api_key = editor.apiKey.trim()
    if (editor.maxTokens) payload.max_tokens = editor.maxTokens
    if (editor.id) {
      await updateAIProvider(editor.id, payload)
      ElMessage.success('已保存')
    } else {
      await createAIProvider(payload)
      ElMessage.success('已添加并保存，可在列表中设为当前使用')
    }
    editorOpen.value = false
    await load()
  } catch {
    // 拦截器已提示错误
  } finally {
    editorSaving.value = false
  }
}

async function testEditor() {
  editorTesting.value = true
  try {
    const res = await testAISettings({
      base_url: editor.baseUrl.trim(),
      api_key: editor.apiKey.trim() || undefined,
      model: editor.model.trim(),
      max_tokens: editor.maxTokens || aiGlobal.maxTokens,
      temperature: aiGlobal.temperature,
    })
    if (res.ok) ElMessage.success(`连接成功${res.reply ? `：${res.reply}` : ''}`)
    else ElMessage.warning(res.message)
  } catch {
    // 拦截器已提示错误
  } finally {
    editorTesting.value = false
  }
}

// ---------- 保存（生成参数 + config.yaml 分区 + 数据源 key） ----------
function onSave() {
  saveDialog.value = true
}

async function doSave() {
  saving.value = true
  try {
    // 1) JSON store 全局生成参数
    await saveAISettings({ max_tokens: aiGlobal.maxTokens, temperature: aiGlobal.temperature })
    // 2) config.yaml 各分区 + 数据源 key（api key 仅提交用户新输入的值）
    const payload = {}
    for (const section of ['ai_provider', 'quant', 'logging', 'scheduler', 'task', 'data', 'monte_carlo']) {
      payload[section] = form[section]
    }
    const apiKeys = {}
    const keyNames = { fred: 'FRED_API_KEY', eia: 'EIA_API_KEY' }
    for (const [field, envName] of Object.entries(keyNames)) {
      const value = (apiKeyInputs[field] || '').trim()
      if (value) apiKeys[envName] = value
    }
    if (Object.keys(apiKeys).length) payload.api_keys = apiKeys
    const res = await saveSettings(payload)
    ElMessage.success(res?.message || '设置已保存')
    saveDialog.value = false
    await load()
  } catch (e) {
    if (e !== 'cancel') ElMessage.error('保存失败，请检查输入或服务器日志')
  } finally {
    saving.value = false
  }
}

onMounted(load)
</script>

<style scoped lang="scss">
.mb-16 {
  margin-bottom: 16px;
}

// ---------- 左侧导航 + 右侧内容区 ----------
.settings-layout {
  display: flex;
  align-items: flex-start;
  gap: var(--space-lg);
}

.settings-nav {
  position: sticky;
  top: 16px;
  width: 180px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 8px;
  background: var(--bg-secondary);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
}

.settings-nav__item {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  padding: 9px 12px;
  border: none;
  background: transparent;
  border-radius: 8px;
  font-size: 14px;
  color: var(--text-secondary);
  cursor: pointer;
  text-align: left;
  transition: all 150ms var(--ease-in-out);

  &:hover {
    background: var(--bg-hover);
    color: var(--text-primary);
  }

  &:focus-visible {
    outline: 2px solid var(--primary);
    outline-offset: -2px;
  }

  &--active {
    background: rgba(var(--primary-rgb), 0.08);
    color: var(--primary);
    font-weight: var(--font-weight-medium);
  }
}

.settings-content {
  flex: 1;
  min-width: 0;
}

.settings-panel {
  animation: fadeInUp 0.3s var(--ease-out-expo) both;
}

.settings-panel__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--space-md);
  flex-wrap: wrap;
  margin-bottom: var(--space-md);
  padding-bottom: var(--space-sm);
  border-bottom: 1px solid var(--border);
}

.settings-panel__title {
  margin: 0;
  font-size: var(--font-size-lg);
  font-weight: 600;
  color: var(--text-primary);
}

.settings-panel__subtitle {
  margin: 4px 0 0;
  font-size: var(--font-size-sm);
  color: var(--text-secondary);
}

.settings-panel__tags {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  align-items: center;
}

// 窄屏：导航改为顶部横向滚动标签
@media (max-width: 767px) {
  .settings-layout {
    flex-direction: column;
  }

  .settings-nav {
    position: static;
    width: 100%;
    flex-direction: row;
    overflow-x: auto;
    padding: 4px;
  }

  .settings-nav__item {
    flex-shrink: 0;
  }
}

// ---------- AI 状态条 ----------
.ai-status-bar {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 14px;
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  background: var(--bg-secondary);
  margin-bottom: var(--space-sm);
  flex-wrap: wrap;
}
.ai-status-bar.ok {
  border-color: rgba(var(--success-rgb, 52, 168, 83), 0.45);
  background: rgba(var(--success-rgb, 52, 168, 83), 0.06);
}
.ai-status-bar.warn {
  border-color: rgba(var(--warning-rgb, 240, 173, 78), 0.45);
  background: rgba(var(--warning-rgb, 240, 173, 78), 0.06);
}
.ai-status-dot {
  flex-shrink: 0;
  width: 10px;
  height: 10px;
  border-radius: 50%;
  background: var(--warning);
  box-shadow: 0 0 0 3px rgba(var(--warning-rgb, 240, 173, 78), 0.25);
}
.ai-status-bar.ok .ai-status-dot {
  background: var(--success);
  box-shadow: 0 0 0 3px rgba(var(--success-rgb, 52, 168, 83), 0.25);
}
.ai-status-info {
  display: flex;
  flex-direction: column;
  gap: 1px;
  min-width: 0;
  flex: 1;
}
.ai-status-label {
  font-size: 11px;
  color: var(--text-tertiary);
}
.ai-status-model {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-primary);
}
.ai-status-url {
  font-size: 11.5px;
  color: var(--text-tertiary);
  font-family: var(--font-mono);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ai-status-test {
  flex-shrink: 0;
}

// ---------- Provider 列表 ----------
.provider-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-sm);
  margin-top: var(--space-xs);
}

.provider-card {
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  padding: var(--space-sm) var(--space-md);
  background: var(--bg-secondary);
}
.provider-card.active {
  border-color: rgba(var(--primary-rgb), 0.55);
  background: rgba(var(--primary-rgb), 0.05);
}

.provider-card__head {
  display: flex;
  align-items: center;
  gap: var(--space-sm);
  flex-wrap: wrap;
  margin-bottom: 6px;
}

.provider-card__name {
  font-size: var(--font-size-base);
  font-weight: 600;
  color: var(--text-primary);
}

.provider-card__body {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.provider-card__meta {
  display: flex;
  flex-direction: column;
  gap: 1px;
}
.provider-meta-model {
  font-size: var(--font-size-sm);
  color: var(--text-primary);
  font-family: var(--font-mono);
}
.provider-meta-tokens {
  font-size: 11.5px;
  color: var(--el-color-warning);
  font-family: var(--font-mono);
}
.provider-meta-url {
  font-size: 11.5px;
  color: var(--text-tertiary);
  font-family: var(--font-mono);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 480px;
}

.provider-add {
  margin-top: 4px;
  color: var(--primary);
}

// ---------- 添加 / 编辑表单 ----------
.prov-editor {
  margin-top: var(--space-sm);
}
.editor-box {
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  padding: var(--space-md);
  background: var(--bg-secondary);
}
.editor-title {
  font-size: var(--font-size-base);
  font-weight: 600;
  color: var(--text-primary);
  margin-bottom: var(--space-sm);
}
.editor-actions {
  display: flex;
  gap: var(--space-sm);
  align-items: center;
  margin-top: 4px;
}
.model-picker {
  display: flex;
  gap: 8px;
  width: 100%;
}
.model-fetch-btn {
  flex-shrink: 0;
}

// Provider 卡片行（表单栅格）
.provider-card__row {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
  gap: var(--space-sm);
}

.spacer {
  flex: 1;
}

// 全局 / 通用表单栅格
.global-form,
.form-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
  gap: var(--space-sm) var(--space-lg);
  align-items: start;
}
</style>
