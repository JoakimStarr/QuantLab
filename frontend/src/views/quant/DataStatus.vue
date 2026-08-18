<template>
  <PageContainer narrow>
    <PageHeader title="数据管理" subtitle="管理 qlib 数据源同步与新鲜度" />

    <!-- KPI 概览 -->
    <div class="kpi-grid mb-6">
      <div class="kpi-card">
        <div class="kpi-label">股票总数</div>
        <div class="kpi-value">{{ currentStatus.stock_count || '--' }}</div>
        <div class="kpi-sub">universe: {{ currentStatus.universe || '--' }}</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-label">最新交易日</div>
        <div class="kpi-value">{{ currentStatus.latest_date || '--' }}</div>
        <div class="kpi-sub">
          {{ daysSinceUpdate }} 天前更新
          <el-tag v-if="dataNotToday" size="small" type="warning" class="ml-2">今日数据未发布</el-tag>
          <el-tag v-else-if="todayHalted" size="small" type="info" class="ml-2">今日休市（非交易日）</el-tag>
        </div>
      </div>
      <div class="kpi-card">
        <div class="kpi-label">数据时间范围</div>
        <div class="kpi-value" style="font-size: var(--font-size-lg); line-height: 1.6">
          {{ qlib.earliest_date || '--' }}<br />~ {{ currentStatus.latest_date || '--' }}
        </div>
        <div class="kpi-sub">{{ qlib.calendar_count ? qlib.calendar_count + ' 个交易日' : '--' }}</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-label">磁盘占用</div>
        <div class="kpi-value" style="font-size: var(--font-size-lg); line-height: 1.6">
          {{ qlib.disk_usage ? humanSize(qlib.disk_usage.dir_size_bytes) : '--' }}<br />剩余
          {{ qlib.disk_usage ? humanSize(qlib.disk_usage.free_bytes) : '--' }}
        </div>
        <div class="kpi-sub">qlib 数据目录</div>
      </div>
    </div>

    <!-- 数据损坏告警横幅：latest_date 被置空 + last_error 标记数据损坏时显示 -->
    <el-alert
      v-if="isDataCorrupt"
      class="mb-6 corrupt-alert"
      title="数据损坏 — 已标记下次同步全量重建"
      type="warning"
      :closable="false"
      show-icon
    >
      <template #default>
        检测到数据完整性问题，latest_date 已置空。建议点击下方「数据校验」查看具体差异，并通过「一键补齐」从数据库重建；
        若 bin 已严重损坏，可删除 data/qlib_bin/cn_data 后重新点击「开始同步」全量回填。
      </template>
    </el-alert>

    <!-- 数据源操作 -->
    <SectionCard class="mb-6">
      <div class="source-header">
        <div class="source-info">
          <div class="source-title">qlib 数据源</div>
          <div class="source-meta">
            <span class="meta-item">
              <span class="meta-label">数据目录:</span>
              <code>{{ qlib.provider_uri || '--' }}</code>
            </span>
            <span class="meta-item">
              <span class="meta-label">数据源:</span>
              <span class="badge badge-info">baostock + akshare</span>
            </span>
            <span class="meta-item">
              <span class="meta-label">最后更新:</span>
              <span>{{ formatTime(currentStatus.last_updated) }}</span>
            </span>
          </div>
          <div v-if="currentStatus.last_error && !isDataCorrupt" class="source-error">
            <span class="error-icon">!</span>
            <div class="error-content">
              <div class="error-head">
                <span class="error-category" :class="errorCategoryClass">{{ errorCategoryLabel }}</span>
                <span class="error-msg">{{ errorMessageBody }}</span>
              </div>
              <div v-if="errorSuggestion" class="error-suggestion">{{ errorSuggestion }}</div>
              <div class="error-actions">
                <el-button size="small" type="primary" @click="startFullSync" :loading="syncing" :disabled="!qlib.available"
                  >重试同步</el-button
                >
              </div>
            </div>
          </div>
        </div>
        <div class="source-actions">
          <el-button @click="loadPreview()" size="small">预览数据</el-button>
          <el-button @click="loadAll" :loading="loading" size="small">刷新</el-button>
        </div>
      </div>

      <!-- 下载通道分组：每个通道独立按钮 + 数据源徽标 -->
      <div class="download-panel">
        <!-- 一键全同步：主入口 -->
        <div class="download-row download-row--main">
          <div class="download-row__info">
            <div class="download-row__title">
              一键全同步
              <span class="badge badge-info">baostock + akshare + 东财</span>
            </div>
            <div class="download-row__desc">A股回填 → 指数 → ETF → 宏观 → 财报 → 外盘，后台顺序执行</div>
          </div>
          <div class="sync-years-group">
            <span class="sync-years-label">回填</span>
            <el-input-number v-model="syncYears" :min="1" :max="30" size="small" style="width: 88px" />
            <span class="sync-years-label">年</span>
            <el-checkbox v-model="refreshMisc" size="small">刷新基础资料/行业</el-checkbox>
            <el-button
              type="primary"
              @click="startFullSync"
              :loading="syncing"
              :disabled="!qlib.available || syncing"
            >
              {{ syncing ? '同步中...' : '开始同步' }}
            </el-button>
          </div>
        </div>

        <!-- A股行情通道 -->
        <div class="download-row">
          <div class="download-row__info">
            <div class="download-row__title">A股行情</div>
            <div class="download-row__desc">增量补齐最新交易日，baostock 为主源、akshare 兜底</div>
          </div>
          <div class="download-row__actions">
            <el-button size="small" @click="openEodDialog('baostock')" :disabled="!qlib.available || syncing">
              增量 EOD（baostock）
            </el-button>
            <el-button size="small" @click="openEodDialog('akshare')" :disabled="!qlib.available || syncing">
              EOD 兜底（akshare）
            </el-button>
            <el-button
              type="primary"
              plain
              size="small"
              @click="doSmartSync"
              :loading="smartChecking"
              :disabled="!qlib.available || syncing || smartChecking"
            >
              {{ smartChecking ? '检测中...' : '智能下载' }}
            </el-button>
          </div>
        </div>

        <!-- 指数 / ETF 通道 -->
        <div class="download-row">
          <div class="download-row__info">
            <div class="download-row__title">指数 / ETF</div>
            <div class="download-row__desc">指数主源 baostock（akshare 兜底）；ETF 支持 baostock 增量或腾讯 qfq 对齐</div>
          </div>
          <div class="download-row__actions">
            <el-button
              size="small"
              @click="doSyncIndices"
              :loading="indexSyncing"
              :disabled="!qlib.available || syncing"
            >
              指数同步
            </el-button>
            <el-button
              size="small"
              @click="doSyncEtf('baostock')"
              :loading="etfSyncing"
              :disabled="!qlib.available || syncing"
            >
              ETF 增量（baostock）
            </el-button>
            <el-button
              size="small"
              @click="doSyncEtf('tencent')"
              :loading="etfSyncing"
              :disabled="!qlib.available || syncing"
            >
              ETF 腾讯 qfq 对齐
            </el-button>
          </div>
        </div>

        <!-- 宏观 / 财报 通道 -->
        <div class="download-row">
          <div class="download-row__info">
            <div class="download-row__title">宏观 / 财报</div>
            <div class="download-row__desc">宏观走东财 datacenter + akshare 注册表；财报逐股增量</div>
          </div>
          <div class="download-row__actions">
            <el-button
              size="small"
              @click="doSyncMacro"
              :loading="macroSyncing"
              :disabled="!qlib.available || syncing"
            >
              宏观同步
            </el-button>
            <el-button
              size="small"
              @click="doSyncFundamental"
              :loading="fundamentalSyncing"
              :disabled="!qlib.available || syncing"
            >
              财报同步
            </el-button>
          </div>
        </div>

        <!-- 维护工具 -->
        <div class="download-row download-row--maintenance">
          <div class="download-row__info">
            <div class="download-row__title">维护工具</div>
            <div class="download-row__desc">校验 bin/DB 一致性，可一键补齐或配置定时同步</div>
          </div>
          <div class="download-row__actions">
            <el-button type="info" @click="doIntegrityCheck" :loading="integrityChecking" :disabled="!qlib.available">
              {{ integrityChecking ? '校验中...' : '数据校验' }}
            </el-button>
            <el-button size="small" @click="openScheduleDialog" :class="{ 'schedule-enabled': dataSchedule.enabled }">
              定时同步
            </el-button>
          </div>
        </div>
      </div>
      <div v-if="dataSchedule.enabled" class="schedule-banner">
        定时同步已开启：每日 {{ dataSchedule.run_time }} {{ dataSchedule.workdays_only ? '（仅工作日）' : '' }}
        <template v-if="dataSchedule.include_full"> · 一键全同步</template>
        <template v-if="dataSchedule.include_eod"> · EOD</template>
        <template v-if="dataSchedule.include_indices"> · 指数</template>
        <template v-if="dataSchedule.include_etf"> · ETF</template>
        <template v-if="dataSchedule.include_fundamental"> · 财报</template>
      </div>
    </SectionCard>

    <!-- 同步进度提示（轮询 /sync-progress 实时百分比） -->
    <div v-if="syncing" class="sync-progress mb-6">
      <div class="progress-header">
        <span class="progress-status">{{ syncProgress?.message || syncProgressText }}</span>
        <span class="progress-pct">{{ (syncProgress?.progress_pct || 0).toFixed(1) }}%</span>
      </div>
      <el-progress
        :percentage="syncProgress?.progress_pct || 0"
        :status="syncProgress?.status === 'failed' ? 'exception' : syncProgress?.status === 'done' ? 'success' : ''"
        :stroke-width="14"
        :show-text="false"
      />
      <div v-if="syncProgress?.data_source" class="progress-detail">
        <span>路径: {{ syncProgress.data_source }}</span>
        <span v-if="syncProgress.started_at">开始: {{ syncProgress.started_at.slice(11, 19) }}</span>
      </div>
    </div>

    <!-- 外盘隔夜情绪因子（akshare，手动触发，广播到 bin 供因子表达式引用） -->
    <SectionCard class="mb-6">
      <div class="card-header">
        <div class="card-title-group">
          <span class="card-header__title">外盘隔夜情绪因子</span>
          <span class="badge badge-info">akshare</span>
          <span v-if="externalMarket.synced_at" class="text-muted" style="font-size: var(--font-size-sm)">
            最近更新: {{ formatTime(externalMarket.synced_at) }}
          </span>
          <span v-else class="text-muted" style="font-size: var(--font-size-sm)">尚未同步</span>
        </div>
        <el-button type="primary" @click="doSyncExternalMarket" :loading="externalSyncing">
          {{ externalSyncing ? '拉取中...' : '拉取外盘数据' }}
        </el-button>
      </div>
      <p class="text-muted" style="font-size: var(--font-size-sm); margin: 8px 0 12px">
        拉取标普500/纳斯达克/道琼斯/恒生指数隔夜涨跌幅，对齐 A股日历后广播为因子字段 （<code>$us_sp500_ret</code>
        等）。每个交易日外盘收盘后手动触发一次。
      </p>
      <el-table
        v-if="Object.keys(externalMarket.items || {}).length"
        :data="Object.values(externalMarket.items)"
        size="small"
        stripe
      >
        <el-table-column label="指数" min-width="120">
          <template #default="{ row }">
            <span class="font-mono">{{ row.label }}</span>
          </template>
        </el-table-column>
        <el-table-column label="最新交易日" width="130" align="center">
          <template #default="{ row }">{{ row.last_date || '--' }}</template>
        </el-table-column>
        <el-table-column label="收盘" width="110" align="right">
          <template #default="{ row }">{{ row.close ?? '--' }}</template>
        </el-table-column>
        <el-table-column label="当日涨跌" width="110" align="right">
          <template #default="{ row }">
            <span :class="row.ret >= 0 ? 'text-success' : 'text-danger'">
              {{ row.ret != null ? (row.ret * 100).toFixed(2) + '%' : '--' }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="100" align="center">
          <template #default="{ row }">
            <el-tag size="small" :type="row.ok ? 'success' : 'danger'">{{ row.ok ? '正常' : '失败' }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="说明" min-width="180" show-overflow-tooltip>
          <template #default="{ row }">
            <span v-if="row.error" class="error-text">{{ row.error }}</span>
            <span v-else class="text-muted">已广播 {{ row.stocks_written }} 只股票</span>
          </template>
        </el-table-column>
      </el-table>
      <el-empty v-else description="暂无外盘数据，点击「拉取外盘数据」获取" :image-size="60" />
    </SectionCard>

    <!-- 数据状态详情 -->
    <SectionCard title="数据状态详情">
      <div class="quick-preview-bar">
        <span class="quick-preview-label">快速预览:</span>
        <el-button size="small" @click="loadPreview('csi300')">沪深300</el-button>
        <el-button size="small" @click="loadPreview('csi500')">中证500</el-button>
        <el-button size="small" @click="loadPreview('all')">全部A股</el-button>
        <el-button size="small" @click="loadPreview('sh600000')">浦发银行</el-button>
      </div>

      <el-table :data="statusList" size="small" stripe empty-text="暂无数据" max-height="400">
        <el-table-column prop="universe" label="股票池" width="120" align="center">
          <template #default="{ row }">
            <span class="font-mono">{{ row.universe }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="latest_date" label="最新日期" width="130" align="center" />
        <el-table-column prop="stock_count" label="股票数" width="100" align="center">
          <template #default="{ row }">
            <span class="num">{{ row.stock_count?.toLocaleString() || '--' }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="row_count" label="记录数" width="100" align="center">
          <template #default="{ row }">
            <span class="num">{{ row.row_count?.toLocaleString() || '--' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="100" align="center">
          <template #default="{ row }">
            <span class="status-badge sm" :class="getStatusClass(row.status)">{{ row.status }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="last_error" label="错误信息" min-width="200" show-overflow-tooltip>
          <template #default="{ row }">
            <span v-if="row.last_error" class="error-text">{{ row.last_error }}</span>
            <span v-else class="text-muted">--</span>
          </template>
        </el-table-column>
        <el-table-column label="更新时间" width="180" align="center">
          <template #default="{ row }">
            <span class="time">{{ formatTime(row.last_updated) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="100" align="center" fixed="right">
          <template #default="{ row }">
            <el-button size="small" link type="primary" @click="loadPreview(row.universe)">预览</el-button>
          </template>
        </el-table-column>
      </el-table>
    </SectionCard>

    <!-- 指数数据（stock_index 主表：指数/ETF 与股票区分，只含 OHLCV，不参与股票校验） -->
    <SectionCard title="指数 / ETF 数据" class="mt-6">
      <div class="card-header">
        <div class="card-title-group">
          <span class="text-muted" style="font-size: var(--font-size-sm)">
            stock_index 注册的指数与 ETF（共 {{ indicesList.length }} 个），只含 OHLCV 字段，校验时跳过股票专属要求
          </span>
        </div>
        <el-button size="small" @click="loadIndices" :loading="indicesLoading">刷新</el-button>
      </div>
      <el-table :data="indicesList" size="small" stripe empty-text="暂无指数/ETF 注册" max-height="300">
        <el-table-column label="类型" width="80" align="center">
          <template #default="{ row }">
            <el-tag size="small" :type="row.type === 'etf' ? 'success' : 'info'">
              {{ row.type === 'etf' ? 'ETF' : '指数' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="code" label="代码" width="120" align="center">
          <template #default="{ row }">
            <span class="font-mono">{{ row.code }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="name" label="名称" min-width="120" />
        <el-table-column prop="source" label="数据源" width="100" align="center" />
        <el-table-column label="bin 状态" width="100" align="center">
          <template #default="{ row }">
            <el-tag size="small" :type="row.has_bin ? 'success' : 'warning'">
              {{ row.has_bin ? '已同步' : '缺失' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="bin 字段" min-width="220">
          <template #default="{ row }">
            <span class="text-muted" style="font-size: var(--font-size-sm)">
              {{ (row.bin_fields || []).join(', ') || '--' }}
            </span>
          </template>
        </el-table-column>
      </el-table>
    </SectionCard>

    <!-- 同步统计（成功率/耗时/路径分布/失败原因） -->
    <SectionCard v-if="syncStats" title="同步统计" class="mt-6">
      <div class="sync-stats-grid">
        <div class="stat-cell">
          <div class="stat-label">最近 30 天成功率</div>
          <div class="stat-value">
            <span
              :class="
                syncStats.success_rate?.rate >= 0.8
                  ? 'text-success'
                  : syncStats.success_rate?.rate >= 0.5
                    ? 'text-warning'
                    : 'text-danger'
              "
            >
              {{ ((syncStats.success_rate?.rate || 0) * 100).toFixed(1) }}%
            </span>
            <span class="stat-sub"
              >成功 {{ syncStats.success_rate?.ok || 0 }} / 失败 {{ syncStats.success_rate?.failed || 0 }} / 共
              {{ syncStats.success_rate?.total || 0 }}</span
            >
          </div>
        </div>
        <div class="stat-cell">
          <div class="stat-label">平均耗时</div>
          <div class="stat-value">
            {{ formatDuration(syncStats.duration_stats?.avg) }}
            <span class="stat-sub"
              >p50 {{ formatDuration(syncStats.duration_stats?.p50) }} / p95
              {{ formatDuration(syncStats.duration_stats?.p95) }}</span
            >
          </div>
        </div>
        <div class="stat-cell">
          <div class="stat-label">路径分布</div>
          <div class="stat-value">
            <span v-if="Object.keys(syncStats.path_distribution || {}).length" class="path-chips">
              <el-tag v-for="(cnt, path) in syncStats.path_distribution" :key="path" size="small" class="mr-1">
                {{ path }} ×{{ cnt }}
              </el-tag>
            </span>
            <span v-else class="text-muted">--</span>
          </div>
        </div>
      </div>
      <div v-if="syncStats.failure_reasons?.length" class="failure-reasons mt-3">
        <div class="stat-label mb-1">失败原因</div>
        <div class="reason-chips">
          <el-tag v-for="r in syncStats.failure_reasons" :key="r.reason" size="small" type="danger" class="mr-1"
            >{{ r.reason }} ×{{ r.count }}</el-tag
          >
        </div>
      </div>
    </SectionCard>

    <!-- 同步历史 -->
    <SectionCard v-if="syncHistory.length" title="同步历史" class="mt-6">
      <el-table :data="syncHistory" size="small" stripe max-height="300">
        <el-table-column prop="version" label="版本" width="120" />
        <el-table-column prop="release_date" label="发布日期" width="120" />
        <el-table-column prop="file_size_mb" label="文件大小" width="100" align="right">
          <template #default="{ row }">{{ row.file_size_mb ? row.file_size_mb + ' MB' : '--' }}</template>
        </el-table-column>
        <el-table-column label="状态" width="100" align="center">
          <template #default="{ row }">
            <span class="status-badge sm" :class="getStatusClass(row.status)">{{ row.status }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="started_at" label="开始时间" width="180">
          <template #default="{ row }"
            ><span class="time">{{ formatTime(row.started_at) }}</span></template
          >
        </el-table-column>
        <el-table-column prop="finished_at" label="完成时间" width="180">
          <template #default="{ row }"
            ><span class="time">{{ formatTime(row.finished_at) }}</span></template
          >
        </el-table-column>
        <el-table-column label="耗时" width="90" align="center">
          <template #default="{ row }">
            <span class="time">{{ formatDuration(row.duration_seconds) }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="error" label="错误" min-width="200" show-overflow-tooltip>
          <template #default="{ row }">
            <span v-if="row.error" class="error-text">{{ row.error }}</span>
            <span v-else class="text-muted">--</span>
          </template>
        </el-table-column>
      </el-table>
    </SectionCard>

    <!-- 数据预览对话框（独立子组件，通过 ref.open(code) 调用） -->
    <DataPreviewDialog ref="previewDialogRef" />

    <!-- 增量EOD同步对话框 -->
    <el-dialog v-model="showEodDialog" title="增量EOD同步" width="460px" :close-on-click-modal="false">
      <div class="eod-sync-form">
        <p class="eod-hint">
          基于 <strong>baostock</strong>（主源, 一次拉全市场）或 <strong>akshare</strong>（兜底, 逐只爬）拉取最近 N
          天的日K数据，<br />
          增量追加到 qlib bin 目录。baostock 含 ST 标记和估值字段，推荐使用。
        </p>
        <el-form label-width="90px" label-position="left">
          <el-form-item label="数据源">
            <el-select v-model="eodForm.source" style="width: 100%">
              <el-option label="baostock（主源，一次拉全市场）" value="baostock" />
              <el-option label="akshare（兜底，逐只爬）" value="akshare" />
            </el-select>
          </el-form-item>
          <el-form-item label="股票池">
            <el-select v-model="eodForm.universe" style="width: 100%">
              <el-option label="沪深300" value="csi300" />
              <el-option label="中证500" value="csi500" />
              <el-option label="全部A股" value="all" />
            </el-select>
          </el-form-item>
          <el-form-item label="同步天数">
            <el-slider v-model="eodForm.days" :min="1" :max="30" show-input style="width: 100%" />
          </el-form-item>
          <el-form-item label="覆盖已有">
            <el-switch v-model="eodForm.overwrite" />
            <span class="eod-warn-hint"
              >开启后将用 baostock/akshare 数据覆盖已有日期（可能因复权差异导致价格断裂）</span
            >
          </el-form-item>
        </el-form>
        <div v-if="eodResult" class="eod-result">
          <el-alert
            :title="eodResultTitle"
            :type="eodResult.failed > 0 ? 'warning' : 'success'"
            :closable="false"
            show-icon
          />
          <div v-if="eodResult.new_dates?.length" class="eod-dates">
            <span class="eod-dates-label">新增日期:</span>
            <el-tag v-for="d in eodResult.new_dates" :key="d" size="small" class="eod-date-tag">{{ d }}</el-tag>
          </div>
        </div>
      </div>
      <template #footer>
        <el-button @click="showEodDialog = false">关闭</el-button>
        <el-button type="primary" @click="doEodSync" :loading="eodSyncing" :disabled="eodSyncing"> 开始同步 </el-button>
      </template>
    </el-dialog>

    <!-- 定时数据管理同步设置 -->
    <el-dialog v-model="scheduleDialogVisible" title="定时数据同步" width="480px" :close-on-click-modal="false">
      <div class="schedule-form">
        <el-form label-width="110px" label-position="left">
          <el-form-item label="启用定时同步">
            <el-switch v-model="scheduleForm.enabled" />
            <span class="schedule-hint">开启后每天到点自动同步，无需手动点击</span>
          </el-form-item>
          <el-form-item label="每日时间">
            <el-time-select
              v-model="scheduleForm.run_time"
              start="00:00"
              step="00:05"
              end="23:55"
              placeholder="选择时间"
              style="width: 140px"
            />
          </el-form-item>
          <el-form-item label="仅工作日">
            <el-switch v-model="scheduleForm.workdays_only" />
            <span class="schedule-hint">周一至周五执行，周末跳过</span>
          </el-form-item>
          <el-form-item label="同步环节">
            <div class="schedule-scopes">
              <el-checkbox v-model="scheduleForm.include_full">一键全同步</el-checkbox>
              <el-checkbox v-model="scheduleForm.include_eod">增量 EOD</el-checkbox>
              <el-checkbox v-model="scheduleForm.include_indices">指数</el-checkbox>
              <el-checkbox v-model="scheduleForm.include_etf">ETF</el-checkbox>
              <el-checkbox v-model="scheduleForm.include_fundamental">财报</el-checkbox>
            </div>
          </el-form-item>
          <el-form-item v-if="scheduleForm.include_full" label="回填年数">
            <el-input-number v-model="scheduleForm.years" :min="1" :max="30" style="width: 120px" />
            <span class="schedule-hint">一键全同步已含指数/ETF/宏观/财报/外盘</span>
          </el-form-item>
          <el-form-item v-if="scheduleForm.include_eod" label="EOD 参数">
            <div class="schedule-scopes">
              <el-select v-model="scheduleForm.universe" style="width: 110px">
                <el-option label="沪深300" value="csi300" />
                <el-option label="中证500" value="csi500" />
                <el-option label="全部A股" value="all" />
              </el-select>
              <el-select v-model="scheduleForm.eod_days" style="width: 90px">
                <el-option v-for="d in [1, 3, 5, 10, 20]" :key="d" :value="d" :label="`近${d}天`" />
              </el-select>
            </div>
          </el-form-item>
          <el-form-item v-if="scheduleForm.include_etf" label="ETF 回看">
            <el-select v-model="scheduleForm.etf_days" style="width: 120px">
              <el-option v-for="d in [7, 30, 90, 180, 365, 730]" :key="d" :value="d" :label="`近${Math.round(d / 30.4)}个月`" />
            </el-select>
          </el-form-item>
          <el-form-item v-if="scheduleForm.include_full && scheduleForm.include_eod" class="schedule-note">
            <span class="schedule-warn">提示：一键全同步已含增量 EOD 所需数据，勾选增量 EOD 将重复拉取</span>
          </el-form-item>
        </el-form>
      </div>
      <template #footer>
        <el-button @click="scheduleDialogVisible = false">关闭</el-button>
        <el-button type="primary" @click="doSaveSchedule" :loading="scheduleSaving">保存</el-button>
      </template>
    </el-dialog>

    <!-- 数据完整性校验弹窗 -->
    <el-dialog
      v-model="showIntegrityDialog"
      title="数据完整性校验"
      width="760px"
      :close-on-click-modal="false"
      append-to-body
    >
      <div v-if="integrityChecking" v-loading="true" style="min-height: 200px"></div>
      <div v-else-if="validationReport" class="integrity-result">
        <el-alert
          :title="validationReport.summary"
          :type="validationReport.ok ? 'success' : 'warning'"
          :closable="false"
          show-icon
          style="margin-bottom: 12px"
        />
        <div v-if="validationReport.sync_state?.syncing" class="calendar-sync-note">
          <el-tag size="small" type="warning">回填中</el-tag>
          <span class="calendar-sync-text">数据同步进行中，校验结果可能不完整，请等同步完成后再校验</span>
        </div>
        <div class="check-list">
          <div v-for="(chk, name) in validationReport.checks" :key="name" class="check-item">
            <el-tag :type="checkStatusTagType(chk.status)" size="small" class="check-status">{{
              checkStatusLabel(chk.status)
            }}</el-tag>
            <span class="check-name">{{ checkName(name) }}</span>
            <span class="check-msg">{{ chk.message }}</span>
          </div>
        </div>
        <div v-if="driftNeedsRepair" class="drift-box">
          <div class="drift-title">待修复差异</div>
          <el-tag v-if="validationReport.checks.fields.bad_size_stocks" size="small"
            >bin 长度异常 {{ validationReport.checks.fields.bad_size_stocks }} 只</el-tag
          >
          <el-tag v-if="validationReport.drift.stocks_with_gaps" size="small"
            >疑似损坏 {{ validationReport.drift.stocks_with_gaps }} 只</el-tag
          >
          <el-tag v-if="validationReport.drift.missing_calendar_days" size="small"
            >day.txt 缺 {{ validationReport.drift.missing_calendar_days }} 天</el-tag
          >
          <el-tag v-if="validationReport.drift.missing_field_files" size="small"
            >字段文件缺 {{ validationReport.drift.missing_field_files }} 个</el-tag
          >
          <el-tag v-if="validationReport.drift.db_without_bin" size="small"
            >DB 无 bin {{ validationReport.drift.db_without_bin }} 只</el-tag
          >
          <el-tag v-if="validationReport.drift.range_mismatch" size="small"
            >区间错位 {{ validationReport.drift.range_mismatch }} 只</el-tag
          >
          <el-tag v-if="validationReport.drift.bin_without_db" size="small" type="info"
            >bin 无 DB 记录 {{ validationReport.drift.bin_without_db }} 只</el-tag
          >
          <el-tag v-if="validationReport.drift.pg_missing_dates" size="small" type="warning"
            >缺 {{ validationReport.drift.pg_missing_dates }} 个交易日（需 baostock）</el-tag
          >
          <el-tag v-if="validationReport.drift.macro_bad_size" size="small"
            >宏观字段长度异常 {{ validationReport.drift.macro_bad_size }} 个</el-tag
          >
          <el-tag v-if="validationReport.drift.macro_missing" size="small"
            >宏观字段缺失 {{ validationReport.drift.macro_missing }} 个</el-tag
          >
        </div>
        <div v-if="integrityResult && integrityResult.calendar_sync" class="calendar-sync-note">
          <el-tag size="small" type="info">数据对齐</el-tag>
          <span class="calendar-sync-text">{{ integrityResult.calendar_sync }}</span>
        </div>
        <div class="integrity-stats" v-if="validationReport.rows !== undefined">
          <div class="stat-item">
            <span class="stat-label">qlib 抽样行数</span><span class="stat-value">{{ validationReport.rows }}</span>
          </div>
          <div class="stat-item">
            <span class="stat-label">抽样股票</span><span class="stat-value">{{ validationReport.total_stocks }}</span>
          </div>
          <div class="stat-item">
            <span class="stat-label">bin 股票数</span
            ><span class="stat-value">{{ validationReport.checks.coverage.stocks_in_bin }}</span>
          </div>
        </div>
      </div>
      <div v-else-if="integrityResult && !integrityResult.ok">
        <el-alert :title="integrityResult.error || '校验失败'" type="error" :closable="false" show-icon />
      </div>
      <template #footer>
        <el-button @click="showIntegrityDialog = false">关闭</el-button>
        <el-button
          v-if="driftNeedsRepair && !validationReport?.sync_state?.syncing"
          type="warning"
          @click="doRepair"
          :loading="repairing"
          :disabled="repairing"
        >
          {{ repairLabel }}
        </el-button>
        <el-button type="primary" @click="doIntegrityCheck" :loading="integrityChecking" :disabled="integrityChecking"
          >重新校验</el-button
        >
      </template>
    </el-dialog>

    <!-- 一键补齐确认弹窗 -->
    <el-dialog v-model="showRepairDialog" width="540px" :close-on-click-modal="false" :close-on-press-escape="false">
      <template #header>
        <div class="repair-dialog-header">
          <div class="repair-dialog-icon">
            <el-icon><WarnTriangleFilled /></el-icon>
          </div>
          <div class="repair-dialog-title-group">
            <div class="repair-dialog-title">确认执行一键补齐</div>
            <div class="repair-dialog-sub">检测到数据存在 {{ repairItems.length }} 类问题，补齐后将自动修复</div>
          </div>
        </div>
      </template>
      <div class="repair-dialog-body">
        <div class="repair-item-list">
          <div v-for="item in repairItems" :key="item.key" class="repair-item">
            <span class="repair-item-badge" :class="'is-' + item.level">{{ item.label }}</span>
            <span class="repair-item-desc">{{ item.desc }}</span>
          </div>
        </div>
        <el-alert v-if="repairNeedsBaostock" type="warning" :closable="false" show-icon class="repair-baostock-alert">
          <template #title>需从 baostock 补拉 {{ repairBaostockDays }} 个缺失交易日</template>
          将消耗网络请求与 baostock 每日配额（≤50k 次/天），耗时较长，请耐心等待。
        </el-alert>
        <p class="repair-tip">
          <el-icon><InfoFilled /></el-icon>
          任务提交后在独立进程后台运行，可随时关闭本窗口，进度会在页面上方实时显示。
        </p>
      </div>
      <template #footer>
        <el-button @click="showRepairDialog = false">取消</el-button>
        <el-button type="primary" :loading="repairing" :disabled="repairing" @click="confirmRepair">
          {{ repairing ? '提交中...' : '确认补齐' }}
        </el-button>
      </template>
    </el-dialog>

    <!-- 补齐进度弹窗 -->
    <el-dialog
      v-model="showRepairProgressDialog"
      width="480px"
      :close-on-click-modal="false"
      :close-on-press-escape="false"
    >
      <template #header>
        <div class="repair-progress-header">
          <div
            class="repair-progress-icon"
            :class="{ 'is-done': repairProgressDone, 'is-failed': repairProgressFailed }"
          >
            <el-icon v-if="!repairProgressDone && !repairProgressFailed" class="is-loading"><Loading /></el-icon>
            <el-icon v-else-if="repairProgressDone"><CircleCheckFilled /></el-icon>
            <el-icon v-else><CircleCloseFilled /></el-icon>
          </div>
          <div class="repair-progress-title-group">
            <div class="repair-progress-title">{{ repairProgressTitle }}</div>
            <div class="repair-progress-sub">{{ repairProgressMessage }}</div>
          </div>
        </div>
      </template>
      <div class="repair-progress-body">
        <el-progress
          :percentage="repairProgressPct"
          :status="repairProgressStatus"
          :stroke-width="12"
          :show-text="false"
        />
        <div class="repair-progress-meta">
          <span>任务: {{ repairProgressTaskLabel }}</span>
          <span v-if="syncProgress?.started_at">开始: {{ syncProgress.started_at.slice(11, 19) }}</span>
          <span>{{ repairProgressPct }}%</span>
        </div>
        <p class="repair-progress-tip">
          <el-icon><InfoFilled /></el-icon>
          任务在后台运行，关闭窗口不影响执行，可稍后回到本页查看。
        </p>
      </div>
      <template #footer>
        <el-button @click="showRepairProgressDialog = false">关闭</el-button>
      </template>
    </el-dialog>
  </PageContainer>
</template>

<script setup>
defineOptions({ name: 'QuantData' })
import { ref, reactive, computed, onMounted, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus/es/components/message/index'
import { WarnTriangleFilled, InfoFilled, Loading, CircleCheckFilled, CircleCloseFilled } from '@element-plus/icons-vue'
import PageContainer from '@/components/common/PageContainer.vue'
import PageHeader from '@/components/common/PageHeader.vue'
import SectionCard from '@/components/common/SectionCard.vue'
import { usePolling } from '@/composables/usePolling'
import DataPreviewDialog from '@/components/quant/DataPreviewDialog.vue'
import { formatDuration, formatTime, humanSize } from '@/utils/format'
import {
  getQuantDataStatus,
  syncFullData,
  getQlibStatus,
  getSyncHistory,
  getSyncStats,
  eodSync,
  getEodResult,
  syncIndices,
  getIndices,
  syncEtfData,
  getSyncProgress,
  triggerValidate,
  getValidateStatus,
  repairData,
  getExternalMarket,
  syncExternalMarket,
  syncFundamental,
  getDataSyncSchedule,
  saveDataSyncSchedule,
} from '@/api/quant'
import { syncMacro } from '@/api/macro'

const statusList = ref([])
const route = useRoute()
const loading = ref(false)
const syncing = ref(false)
const qlib = reactive({ available: false, provider_uri: '', earliest_date: null, calendar_count: 0, disk_usage: null })
const syncProgress = ref(null)
// 轮询连续拿不到进度（data=null）的次数，超过阈值停止轮询，避免空转泄漏
let nullPollCount = 0
const previewDialogRef = ref(null)
const syncHistory = ref([])
const syncStats = ref(null)
const indicesList = ref([])
const indicesLoading = ref(false)
const showEodDialog = ref(false)
const eodSyncing = ref(false)
const eodResult = ref(null)
const eodForm = reactive({ source: 'baostock', universe: 'csi300', days: 5, overwrite: false })
const syncYears = ref(5)
// 智能下载：缺失交易日 ≤ 该值时走增量 EOD，否则走一键补齐
const SMART_EOD_DAYS = 30
const refreshMisc = ref(false) // 一键全同步时是否顺带刷新基础资料/行业（默认关，日常不需要）
const indexSyncing = ref(false)
const etfSyncing = ref(false)
const fundamentalSyncing = ref(false)
const macroSyncing = ref(false)
const smartChecking = ref(false)
const externalMarket = ref({ synced_at: null, items: {} })
const externalSyncing = ref(false)
const integrityChecking = ref(false)
const showIntegrityDialog = ref(false)
const integrityResult = ref(null)
const validationReport = ref(null)
const repairing = ref(false)
const showRepairDialog = ref(false)
const repairItems = ref([])
const showRepairProgressDialog = ref(false)
const currentStatus = computed(() => statusList.value[0] || {})

// ==== 定时数据管理同步 ====
const dataSchedule = ref({ enabled: false, run_time: '18:00', workdays_only: true })
const scheduleDialogVisible = ref(false)
const scheduleSaving = ref(false)
const scheduleForm = ref({ ...dataSchedule.value })

async function loadDataSchedule() {
  const s = await getDataSyncSchedule()
  dataSchedule.value = { ...dataSchedule.value, ...(s || {}) }
}

function openScheduleDialog() {
  scheduleForm.value = {
    enabled: dataSchedule.value.enabled,
    run_time: dataSchedule.value.run_time,
    workdays_only: dataSchedule.value.workdays_only,
    include_full: dataSchedule.value.include_full,
    include_eod: dataSchedule.value.include_eod,
    include_indices: dataSchedule.value.include_indices,
    include_etf: dataSchedule.value.include_etf,
    include_fundamental: dataSchedule.value.include_fundamental,
    years: dataSchedule.value.years ?? 5,
    universe: dataSchedule.value.universe ?? 'all',
    eod_days: dataSchedule.value.eod_days ?? 5,
    etf_days: dataSchedule.value.etf_days ?? 30,
  }
  scheduleDialogVisible.value = true
}

async function doSaveSchedule() {
  scheduleSaving.value = true
  try {
    const s = await saveDataSyncSchedule({ ...scheduleForm.value })
    dataSchedule.value = { ...dataSchedule.value, ...(s || {}) }
    ElMessage.success('定时同步设置已保存')
    scheduleDialogVisible.value = false
  } catch (e) {
    ElMessage.error('保存失败')
  } finally {
    scheduleSaving.value = false
  }
}

// 数据损坏检测：latest_date 被置空且 last_error 标记数据损坏时为 true
// （兼容旧版 smart_sync 路径写入的状态；baostock 回填失败时 status=failed + last_error 由 sync_runner 标记）
const isDataCorrupt = computed(() => {
  return (
    currentStatus.value.latest_date === null &&
    !!currentStatus.value.last_error &&
    currentStatus.value.last_error.includes('数据损坏')
  )
})

const daysSinceUpdate = computed(() => {
  if (!currentStatus.value.latest_date) return '--'
  const diff = Math.floor((Date.now() - new Date(currentStatus.value.latest_date).getTime()) / 86400000)
  return diff
})

// 今日是否交易日（后端查 trade_calendar；null=未知，回退旧行为按日期判断）
const todayIsTradingDay = ref(null)

// 今日是交易日但 baostock 数据未发布/未拉到（latest_date 落后于今天）→ 提示而非异常；
// 非交易日（周末/节假日）属正常，不提示"未发布"
const dataNotToday = computed(() => {
  if (!currentStatus.value.latest_date) return false
  if (todayIsTradingDay.value === false) return false
  const latest = new Date(currentStatus.value.latest_date + 'T00:00:00')
  const now = new Date()
  return latest.toDateString() < now.toDateString()
})

// 今日非交易日：latest_date 停在最近交易日，属正常休市
const todayHalted = computed(() => {
  if (todayIsTradingDay.value !== false) return false
  if (!currentStatus.value.latest_date) return false
  const latest = new Date(currentStatus.value.latest_date + 'T00:00:00')
  const now = new Date()
  return latest.toDateString() < now.toDateString()
})

const syncProgressText = computed(() => {
  const kind = syncProgress.value?.kind || syncProgress.value?.data_source
  if (kind === 'repair') return '正在执行数据补齐（独立进程后台运行），请耐心等待...'
  if (kind === 'full') return '正在执行一键全同步（A股→指数→宏观→财报→外盘），请耐心等待...'
  return '正在通过 baostock 逐日回填全市场数据（从最新向旧），请耐心等待...'
})

const eodResultTitle = computed(() => {
  const r = eodResult.value
  if (!r) return ''
  if (r.ok === false || r.failed === undefined) return r.error || '同步失败'
  return `同步完成: 成功 ${r.success ?? 0}/${r.total_stocks ?? 0}，新增 ${r.new_dates?.length || 0} 个交易日`
})

function getStatusClass(status) {
  if (status === 'ok') return 'success'
  if (status === 'syncing') return 'warning'
  if (status === 'failed') return 'danger'
  return ''
}

// 数据预览：委托给独立子组件（DataPreviewDialog）打开并加载
function loadPreview(code) {
  previewDialogRef.value?.open(code || '')
}

async function loadSyncHistory() {
  try {
    const data = await getSyncHistory(10)
    syncHistory.value = data?.items || []
  } catch (e) {
    // 静默失败
  }
}

async function loadStatus() {
  try {
    const data = await getQuantDataStatus()
    statusList.value = data?.items || []
    todayIsTradingDay.value = data?.today_is_trading_day ?? null
    // syncing 状态由进度轮询统一管理；检测到外部（如定时任务）触发的 syncing 时启动
    const cur = statusList.value[0]
    if (cur && cur.status === 'syncing' && !progressPolling.isPolling.value && !syncing.value) {
      syncing.value = true
      startProgressPolling()
    }
  } catch (e) {
    if (e !== 'cancel') ElMessage.error('加载数据状态失败')
  }
}

async function loadQlib() {
  try {
    const data = await getQlibStatus()
    qlib.available = data?.available || false
    qlib.provider_uri = data?.provider_uri || ''
    qlib.earliest_date = data?.earliest_date || null
    qlib.calendar_count = data?.calendar_count || 0
    qlib.disk_usage = data?.disk_usage || null
  } catch (e) {
    if (e !== 'cancel') ElMessage.error('qlib 状态检查失败')
  }
}

async function loadSyncStats() {
  try {
    const data = await getSyncStats(30)
    syncStats.value = data
  } catch (e) {
    if (e !== 'cancel') syncStats.value = null
  }
}

async function loadIndices() {
  indicesLoading.value = true
  try {
    const data = await getIndices()
    indicesList.value = data?.items || []
  } catch (e) {
    if (e !== 'cancel') indicesList.value = []
  } finally {
    indicesLoading.value = false
  }
}

async function loadAll() {
  loading.value = true
  await Promise.all([loadStatus(), loadQlib(), loadSyncHistory(), loadSyncStats(), loadIndices(), loadDataSchedule()])
  loading.value = false
}

// 一键全同步（开始同步 / 错误横幅的重试同步共用）：
// A股回填 → 指数 → 宏观 → 财报 → 外盘，独立进程顺序执行
async function startFullSync() {
  syncing.value = true
  syncProgress.value = null
  try {
    await syncFullData(syncYears.value, 'all', refreshMisc.value)
    ElMessage.success(`一键全同步已提交（A股回填 ${syncYears.value} 年 → 指数 → 宏观 → 财报 → 外盘，后台执行）`)
    startProgressPolling()
  } catch (e) {
    if (e !== 'cancel') ElMessage.error('同步提交失败')
    syncing.value = false
  }
}

function startProgressPolling() {
  nullPollCount = 0
  progressPolling.start()
}

// 任务标签：优先用进度文件的 kind（任务归属），回退到 data_source（真实数据源）
const taskLabel = (progress) => {
  const key = progress?.kind || progress?.data_source
  return (
    {
      repair: '数据补齐',
      backfill: '数据回填',
      baostock: '数据同步',
      eod: '增量同步',
      etf: 'ETF 同步',
      macro: '宏观同步',
      eastmoney: '宏观同步',
      indices: '指数同步',
      fundamental: '财报同步',
      full: '一键全同步',
    }[key] || '后台任务'
  )
}

async function pollSyncProgress() {
  try {
    const data = await getSyncProgress()
    syncProgress.value = data
    if (data?.status === 'done' || data?.status === 'failed') {
      progressPolling.stop()
      nullPollCount = 0
      syncing.value = false
      const taskKey = data?.kind || data?.data_source
      const label = taskLabel(data)
      if (data?.status === 'done') {
        // 补齐/同步完成提示带上任务真实结果（如 "修复完成: bins(6ok/0failed/1skipped)..."）
        ElMessage.success(label + '完成' + (data?.message && data.message !== '正在同步...' ? `（${data.message}）` : ''))
        // 补齐完成后自动重新校验，刷新报告
        if (taskKey === 'repair' && showIntegrityDialog.value) {
          doIntegrityCheck()
        }
        // 补齐进度弹窗：成功后 2.5s 自动关闭；失败保持打开展示错误
        if (showRepairProgressDialog.value && taskKey === 'repair') {
          setTimeout(() => {
            showRepairProgressDialog.value = false
          }, 2500)
        }
      } else {
        ElMessage.error(label + '失败: ' + (data?.error || '未知错误'))
      }
      // EOD：复位按钮 loading，短读真实结果填对话框（结果文件在进度 done 后稍后写入）
      if (taskKey === 'eod') {
        eodSyncing.value = false
        readEodResultOnce()
      }
      loadAll()
      return
    }
    if (data === null) {
      // 连续一段时间无进度（worker 未写入/已退出且无残留文件），停止轮询
      nullPollCount += 1
      if (nullPollCount > 30) {
        progressPolling.stop()
        nullPollCount = 0
        syncing.value = false
        eodSyncing.value = false
        if (showRepairProgressDialog.value) showRepairProgressDialog.value = false
      }
    } else {
      nullPollCount = 0
    }
  } catch (e) {
    // 静默失败，继续轮询
  }
}

// 进度轮询：每 1s 拉取同步进度；done/failed 或连续无数据时自动停止
const progressPolling = usePolling(pollSyncProgress, 1000)

async function doEodSync() {
  eodSyncing.value = true
  eodResult.value = null
  try {
    // EOD 同步是后台任务，提交后立即返回；进度由全局进度轮询跟踪，
    // 完成时（pollSyncProgress 命中 kind=eod）再读真实结果填对话框
    await eodSync(eodForm.universe, eodForm.days, eodForm.overwrite, eodForm.source)
    ElMessage.success('增量同步已提交，后台执行中')
    syncing.value = true
    syncProgress.value = null
    startProgressPolling()
  } catch (e) {
    if (e !== 'cancel') ElMessage.error('增量EOD同步失败: ' + (e?.message || e))
    eodSyncing.value = false
  }
}

// 读最近一次 EOD 真实结果（worker 写完进度文件后稍后写 eod_last_result.json，短重试）
async function readEodResultOnce() {
  for (let i = 0; i < 10; i++) {
    try {
      const data = await getEodResult()
      if (data && data.ok !== false && data.success !== undefined) {
        eodResult.value = data
        return
      }
    } catch (e) {
      // 结果尚未写入，继续重试
    }
    await new Promise((r) => setTimeout(r, 500))
  }
}

// 「同步维护」通用提交：子任务 loading 态 + 提交后启动全局进度轮询
async function submitSyncTask(subFlag, fn, successMsg, errorMsg) {
  subFlag.value = true
  try {
    await fn()
    ElMessage.success(successMsg)
    syncing.value = true
    syncProgress.value = null
    startProgressPolling()
  } catch (e) {
    if (e !== 'cancel') ElMessage.error(errorMsg + ': ' + (e?.message || e))
  } finally {
    subFlag.value = false
  }
}

async function doSyncIndices() {
  await submitSyncTask(
    indexSyncing,
    syncIndices,
    '指数同步已提交，后台执行中',
    '指数同步失败'
  )
}

// 全市场 ETF 同步（source: baostock=按日全市场增量 / tencent=qfq 对齐现有时间范围）
async function doSyncEtf(source = 'tencent') {
  const years = syncYears.value || 2
  await submitSyncTask(
    etfSyncing,
    () => syncEtfData(years, source),
    source === 'tencent'
      ? 'ETF 同步已提交（腾讯 qfq 对齐现有时间范围，后台执行中）'
      : `ETF 同步已提交（baostock 约 ${years} 年历史，后台执行中）`,
    'ETF 同步失败'
  )
}

// 季频财报同步（默认只拉数据入库；进度显示在同步进度条）
async function doSyncFundamental() {
  await submitSyncTask(
    fundamentalSyncing,
    () => syncFundamental(false),
    '财报同步已提交（全市场逐股拉取，约 2-3 小时）',
    '财报同步提交失败'
  )
}

// 宏观指标同步（东财 datacenter + akshare 注册表，写 PG + 广播 bin）
async function doSyncMacro() {
  await submitSyncTask(
    macroSyncing,
    syncMacro,
    '宏观同步已提交（东财 + akshare，后台执行）',
    '宏观同步提交失败'
  )
}

// 打开增量 EOD 对话框并预设数据源（baostock 主源 / akshare 兜底）
function openEodDialog(source = 'baostock') {
  eodForm.source = source
  showEodDialog.value = true
}

// 智能下载：先触发数据完整性校验探测缺口，再自动选择最优通道
//   - 无缺口/最新 → 提示已最新，不重复拉取
//   - 小缺口（仅缺最近少量交易日）→ 增量 EOD（baostock，含自动回退 akshare）
//   - 大缺口或 bin 损坏 → 一键补齐（DB 权威重建 + baostock 补缺）
async function doSmartSync() {
  if (smartChecking.value) return
  smartChecking.value = true
  ElMessage.info('正在检测数据缺口，请稍候...')
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
  try {
    await triggerValidate()
    let status = null
    for (let i = 0; i < 120; i++) {
      await sleep(2000)
      status = await getValidateStatus()
      if (status?.status === 'done') break
      if (status?.status === 'failed') throw new Error(status?.error || '校验失败')
      if (status?.status === 'idle') {
        if (i > 10) throw new Error('校验未能启动，请重试')
        continue
      }
    }
    if (!status || status.status !== 'done') throw new Error('校验超时，请稍后重试')
    const drift = status.report?.drift
    const checks = status.report?.checks || {}
    const missing = drift?.pg_missing_dates || 0
    const corrupt = (drift?.stocks_with_gaps || 0) > 0 || (drift?.missing_field_files || 0) > 0

    if (drift && !drift.needs_repair && !checks.fields?.bad_size_stocks) {
      ElMessage.success('数据已是最新，无需同步')
      return
    }

    if (!corrupt && missing > 0 && missing <= SMART_EOD_DAYS) {
      ElMessage.info(`检测到缺 ${missing} 个交易日，执行增量 EOD 补齐`)
      await eodSync('all', missing, false, 'baostock')
      syncing.value = true
      syncProgress.value = null
      startProgressPolling()
      return
    }

    ElMessage.warning('检测到较大缺口或数据损坏，执行一键补齐（后台重建 + baostock 补缺）')
    const items = []
    if (drift?.missing_calendar_days)
      items.push({ key: 'calendar', level: 'warn', label: '日历缺失', desc: `day.txt 缺 ${drift.missing_calendar_days} 天` })
    if (checks.fields?.bad_size_stocks)
      items.push({ key: 'size', level: 'error', label: '长度异常', desc: `bin 长度异常 ${checks.fields.bad_size_stocks} 只` })
    if (drift?.stocks_with_gaps)
      items.push({ key: 'gaps', level: 'error', label: '疑似损坏', desc: `疑似损坏 ${drift.stocks_with_gaps} 只` })
    if (missing)
      items.push({ key: 'missing', level: 'warn', label: '缺交易日', desc: `缺 ${missing} 个交易日（需 baostock）` })
    repairItems.value = items.length ? items : [{ key: 'misc', level: 'warn', label: '数据不一致', desc: 'bin 数据存在不一致' }]
    showRepairDialog.value = true
  } catch (e) {
    if (e?.code !== 'SYNC_IN_PROGRESS' && e !== 'cancel') {
      ElMessage.error('智能下载检测失败: ' + (e?.message || e))
    }
  } finally {
    smartChecking.value = false
  }
}

async function loadExternalMarket() {
  try {
    const data = await getExternalMarket()
    externalMarket.value = data || { synced_at: null, items: {} }
  } catch {
    /* 拦截器已提示 */
  }
}

async function doSyncExternalMarket() {
  externalSyncing.value = true
  try {
    await syncExternalMarket()
    await loadExternalMarket()
    ElMessage.success('外盘因子已更新并广播到 bin')
  } catch (e) {
    if (e !== 'cancel') ElMessage.error('外盘拉取失败: ' + (e?.message || e))
  } finally {
    externalSyncing.value = false
  }
}

async function doIntegrityCheck() {
  integrityChecking.value = true
  showIntegrityDialog.value = true
  integrityResult.value = null
  validationReport.value = null
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
  try {
    // 校验在独立 worker 子进程执行：先触发，再轮询状态文件直到完成
    await triggerValidate()
    let status = null
    let idleCount = 0
    for (let i = 0; i < 120; i++) {
      // 最多等 4 分钟（每次 2s）
      await sleep(2000)
      status = await getValidateStatus()
      if (status?.status === 'done') break
      if (status?.status === 'failed') throw new Error(status?.error || '校验失败')
      if (status?.status === 'idle') {
        idleCount += 1
        if (idleCount > 5) throw new Error('校验未能启动，请重试')
        continue
      }
    }
    if (!status || status.status !== 'done') {
      throw new Error('校验仍在进行（超过 4 分钟），请稍后点击「重新校验」查看结果')
    }
    const data = status.report
    validationReport.value = data
    // 用后端返回的 checks_summary（数据对齐状态）替代硬编码文案
    integrityResult.value = { ...data, calendar_sync: data?.checks_summary || '校验只读，未改动任何数据' }
    if (data?.ok) ElMessage.success(data?.summary || '校验完成')
    else ElMessage.warning(data?.summary || '校验完成，发现差异，可点击「一键补齐」修复')
  } catch (e) {
    integrityResult.value = { ok: false, error: String(e?.message || e) }
  } finally {
    integrityChecking.value = false
  }
}

const driftNeedsRepair = computed(() => !!validationReport.value?.drift?.needs_repair)
const repairLabel = computed(() => {
  const d = validationReport.value?.drift
  if (d?.needs_baostock) return `一键补齐（含 baostock ${d.pg_missing_dates} 天）`
  return '一键补齐'
})
const checkStatusLabel = (s) => ({ ok: '正常', warn: '警告', error: '异常' })[s] || s
const checkStatusTagType = (s) => ({ ok: 'success', warn: 'warning', error: 'danger' })[s] || 'info'
const checkName = (n) =>
  ({
    fields: 'bin 字段完整性',
    fieldset: '字段集合',
    calendar: '日历同步',
    coverage: '覆盖一致性',
    qlib: 'qlib 可读性',
    macro: '宏观字段对齐',
  })[n] || n

const repairNeedsBaostock = computed(() => !!validationReport.value?.drift?.needs_baostock)
const repairBaostockDays = computed(() => validationReport.value?.drift?.pg_missing_dates || 0)

const repairProgressPct = computed(() => syncProgress.value?.progress_pct || 0)
const repairProgressStatus = computed(() => {
  const s = syncProgress.value?.status
  if (s === 'done') return 'success'
  if (s === 'failed') return 'exception'
  return ''
})
const repairProgressDone = computed(() => syncProgress.value?.status === 'done')
const repairProgressFailed = computed(() => syncProgress.value?.status === 'failed')
const repairProgressTitle = computed(() => {
  if (repairProgressDone.value) return '补齐完成'
  if (repairProgressFailed.value) return '补齐失败'
  return '数据补齐进行中'
})
const repairProgressMessage = computed(() => {
  const s = syncProgress.value
  if (s?.message) return s.message
  if (s?.status === 'done') return '数据已补齐，正在刷新状态...'
  if (s?.status === 'failed') return s?.error || '任务执行出错，请查看后台日志'
  return '正在提交补齐任务，请稍候...'
})
const repairProgressTaskLabel = computed(() => taskLabel(syncProgress.value) || '数据补齐')

function doRepair() {
  const d = validationReport.value?.drift
  if (!d) return
  const f = validationReport.value?.checks?.fields || {}
  const items = []
  if (d.missing_calendar_days)
    items.push({ key: 'calendar', level: 'warn', label: '日历缺失', desc: `day.txt 缺 ${d.missing_calendar_days} 天` })
  if (f.bad_size_stocks)
    items.push({ key: 'size', level: 'error', label: '长度异常', desc: `bin 长度异常 ${f.bad_size_stocks} 只` })
  if (d.stocks_with_gaps)
    items.push({ key: 'gaps', level: 'error', label: '疑似损坏', desc: `疑似损坏 ${d.stocks_with_gaps} 只` })
  if (d.missing_field_files)
    items.push({ key: 'fields', level: 'warn', label: '字段缺失', desc: `字段文件缺 ${d.missing_field_files} 个` })
  if (d.db_without_bin)
    items.push({
      key: 'db2bin',
      level: 'warn',
      label: 'DB 无 bin',
      desc: `DB 有记录但 bin 缺失 ${d.db_without_bin} 只`,
    })
  if (d.range_mismatch)
    items.push({ key: 'range', level: 'error', label: '区间错位', desc: `区间错位 ${d.range_mismatch} 只` })
  if (d.macro_bad_size)
    items.push({
      key: 'macro_size',
      level: 'error',
      label: '宏观字段异常',
      desc: `宏观字段长度异常 ${d.macro_bad_size} 个`,
    })
  if (d.macro_missing)
    items.push({
      key: 'macro_missing',
      level: 'warn',
      label: '宏观字段缺失',
      desc: `宏观字段缺失 ${d.macro_missing} 个`,
    })
  if (!items.length) items.push({ key: 'misc', level: 'warn', label: '数据不一致', desc: 'bin 数据存在不一致' })
  repairItems.value = items
  showRepairDialog.value = true
}

async function confirmRepair() {
  repairing.value = true
  syncing.value = true
  syncProgress.value = null
  showRepairDialog.value = false
  showRepairProgressDialog.value = true
  try {
    await repairData({ include_baostock: repairNeedsBaostock.value, universe: 'all' })
    ElMessage.success('补齐任务已提交（独立进程后台执行）')
    startProgressPolling()
  } catch (e) {
    showRepairProgressDialog.value = false
    if (e?.code !== 'SYNC_IN_PROGRESS') {
      // 非 409 冲突才重复提示（拦截器已弹过"正在同步/修复中"）
      if (e !== 'cancel') ElMessage.error('补齐提交失败: ' + (e?.message || e))
    }
    syncing.value = false
  } finally {
    repairing.value = false
  }
}

// 解析后端错误信息（格式: [分类] 详情\n建议: 建议内容）
const errorCategoryLabel = computed(() => {
  const err = currentStatus.value.last_error || ''
  const m = err.match(/^\[([^\]]+)\]/)
  return m ? m[1] : '错误'
})
const errorCategoryClass = computed(() => {
  const label = errorCategoryLabel.value
  if (label.includes('网络')) return 'network'
  if (label.includes('磁盘')) return 'disk_full'
  if (label.includes('损坏')) return 'data_corrupt'
  if (label.includes('中断') || label.includes('超时')) return 'interrupted'
  return ''
})
const errorMessageBody = computed(() => {
  const err = currentStatus.value.last_error || ''
  const body = err.replace(/^\[[^\]]+\]\s*/, '').split('\n')[0]
  return body || err
})
const errorSuggestion = computed(() => {
  const err = currentStatus.value.last_error || ''
  const m = err.match(/建议[:：]\s*([\s\S]+)$/)
  return m ? m[1].trim() : ''
})

// 页面打开时探测是否有独立 worker 正在跑（读 /sync-progress 实时文件）。
// 即使 stock_data_status 因 web 重启被 recover_stale_sync 误标 failed，
// 只要 worker 存活就恢复进度显示；不依赖 DB 状态。
async function checkRunningSync() {
  try {
    const data = await getSyncProgress()
    // 与后端 sync_is_active() 判定一致：非终态且存在进度即视为活跃
    const terminal = ['done', 'failed', 'idle', null]
    if (data && !terminal.includes(data.status) && !syncing.value) {
      syncing.value = true
      syncProgress.value = data
      startProgressPolling()
    }
  } catch (e) {
    // 静默失败，交给 loadStatus 的 DB 状态路径兜底
  }
}

onMounted(() => {
  loadAll()
  loadExternalMarket()
  checkRunningSync()
})

watch(
  () => route.query.preview,
  (code) => {
    if (code) loadPreview(String(code))
  },
  { immediate: true }
)
</script>

<style scoped lang="scss">
.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-md);
  flex-wrap: wrap;

  &__title {
    font-size: var(--font-size-lg);
    font-weight: 700;
    color: var(--text-primary);
  }
}
.card-title-group {
  display: flex;
  align-items: center;
  gap: var(--space-sm);
  flex-wrap: wrap;
}

.kpi-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 16px;
  @media (max-width: 767px) {
    grid-template-columns: repeat(2, 1fr);
  }
}

.kpi-card {
  background: var(--bg-card);
  border: 1px solid var(--border-light);
  border-radius: var(--radius-md);
  padding: 20px;
  .kpi-label {
    font-size: var(--font-size-base);
    color: var(--text-tertiary);
    margin-bottom: 8px;
  }
  .kpi-value {
    font-size: 28px;
    font-weight: 700;
    color: var(--text-primary);
    font-variant-numeric: tabular-nums;
  }
  .kpi-sub {
    font-size: var(--font-size-sm);
    color: var(--text-tertiary);
    margin-top: 6px;
  }
}

.source-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
}
.source-title {
  font-size: var(--font-size-lg);
  font-weight: 600;
  color: var(--text-primary);
}
.source-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 16px;
  margin-top: 8px;
  font-size: var(--font-size-base);
  color: var(--text-secondary);
}
.meta-label {
  color: var(--text-tertiary);
  margin-right: 4px;
}
.source-meta code {
  background: var(--bg-tertiary);
  padding: 2px 6px;
  border-radius: var(--radius-sm);
  font-size: var(--font-size-sm);
  color: var(--primary);
}
.source-actions {
  display: flex;
  gap: 8px;
  flex-shrink: 0;
}

.download-panel {
  margin-top: 16px;
  border: 1px solid var(--border-light);
  border-radius: var(--radius-md);
  overflow: hidden;
}
.download-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
  padding: 12px 16px;
  border-bottom: 1px solid var(--border-light);
  &:last-child {
    border-bottom: none;
  }
  &--main {
    background: var(--primary-soft-faint, rgba(64, 158, 255, 0.04));
  }
  &--maintenance {
    background: var(--bg-tertiary, #f5f7fa);
  }
}
.download-row__info {
  min-width: 0;
}
.download-row__title {
  font-size: var(--font-size-base);
  font-weight: 600;
  color: var(--text-primary);
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.download-row__desc {
  font-size: var(--font-size-sm);
  color: var(--text-tertiary);
  margin-top: 2px;
}
.download-row__actions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  flex-shrink: 0;
}

.source-error {
  margin-top: 10px;
  padding: 8px 12px;
  background: var(--danger-soft-faint);
  border: 1px solid var(--danger-soft-border);
  border-radius: 6px;
  font-size: var(--font-size-base);
  color: var(--danger);
  display: flex;
  align-items: center;
  gap: 8px;
  .error-icon {
    display: inline-flex;
    width: 18px;
    height: 18px;
    border-radius: 50%;
    background: var(--danger);
    color: var(--text-inverse);
    align-items: center;
    justify-content: center;
    font-size: var(--font-size-sm);
    font-weight: 700;
    flex-shrink: 0;
  }
}

.source-error {
  align-items: flex-start;
}
.error-content {
  flex: 1;
  min-width: 0;
}
.error-head {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.error-category {
  display: inline-block;
  padding: 2px 8px;
  border-radius: var(--radius-sm);
  font-size: var(--font-size-xs);
  font-weight: 600;
  flex-shrink: 0;
  background: var(--danger-soft-strong);
  color: var(--danger);
}
.error-category.network {
  background: var(--warning-soft-strong);
  color: var(--warning);
}
.error-category.disk_full {
  background: var(--danger-soft-strong);
  color: var(--danger);
}
.error-category.data_corrupt {
  background: var(--primary-soft-strong);
  color: var(--primary);
}
.error-category.interrupted {
  background: var(--warning-soft-strong);
  color: var(--warning);
}
.error-msg {
  font-size: var(--font-size-base);
  color: var(--danger);
  word-break: break-word;
}
.error-suggestion {
  margin-top: 6px;
  font-size: var(--font-size-sm);
  color: var(--text-secondary);
}
.error-actions {
  margin-top: 8px;
}

.sync-progress {
  background: var(--bg-card);
  border: 1px solid var(--border-light);
  border-radius: var(--radius-md);
  padding: 20px;
  .progress-bar {
    height: 4px;
    background: var(--bg-tertiary);
    border-radius: 2px;
    overflow: hidden;
    margin-bottom: 12px;
  }
  .progress-indicator {
    height: 100%;
    background: var(--primary);
    border-radius: 2px;
    animation: progress-pulse 2s ease-in-out infinite;
  }
  .progress-text {
    font-size: var(--font-size-base);
    color: var(--text-secondary);
  }
  .progress-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 8px;
  }
  .progress-status {
    font-size: var(--font-size-base);
    color: var(--text-primary);
  }
  .progress-pct {
    font-size: var(--font-size-base);
    font-weight: 600;
    color: var(--primary);
  }
  .progress-detail {
    display: flex;
    justify-content: space-between;
    margin-top: 6px;
    font-size: var(--font-size-sm);
    color: var(--text-secondary);
  }
}

@keyframes progress-pulse {
  0%,
  100% {
    width: 30%;
    opacity: 0.7;
  }
  50% {
    width: 70%;
    opacity: 1;
  }
}

.status-badge {
  display: inline-block;
  padding: 4px 10px;
  border-radius: var(--radius-full);
  font-size: var(--font-size-sm);
  font-weight: 500;
  &.sm {
    padding: 2px 8px;
    font-size: var(--font-size-xs);
  }
  &.success {
    background: var(--success-soft);
    color: var(--success);
  }
  &.warning {
    background: var(--warning-soft);
    color: var(--warning);
  }
  &.danger {
    background: var(--danger-soft);
    color: var(--danger);
  }
}

.badge {
  display: inline-block;
  padding: 2px 8px;
  border-radius: var(--radius-sm);
  font-size: var(--font-size-sm);
  font-weight: 500;
  &.badge-info {
    background: var(--primary-soft);
    color: var(--primary);
  }
}

.font-mono {
  font-family: var(--font-mono, monospace);
  font-size: var(--font-size-base);
}
.num {
  font-variant-numeric: tabular-nums;
  font-weight: 500;
}
.time {
  font-size: var(--font-size-sm);
  color: var(--text-tertiary);
  font-variant-numeric: tabular-nums;
}
.error-text {
  color: var(--danger);
  font-size: var(--font-size-sm);
}
.text-muted {
  color: var(--text-tertiary);
}

.mt-6 {
  margin-top: 24px;
}

.quick-preview-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 16px;
  flex-wrap: wrap;
}
.quick-preview-label {
  font-size: var(--font-size-base);
  color: var(--text-tertiary);
  margin-right: 4px;
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

.eod-sync-form {
  padding: 0 4px;
}
.eod-hint {
  font-size: var(--font-size-base);
  color: var(--text-secondary);
  line-height: 1.6;
  margin-bottom: 16px;
}
.eod-result {
  margin-top: 16px;
}
.eod-dates {
  margin-top: 12px;
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  align-items: center;
}
.eod-dates-label {
  font-size: var(--font-size-base);
  color: var(--text-secondary);
}
.eod-date-tag {
  font-family: var(--font-mono, monospace);
}
.eod-warn-hint {
  margin-left: 12px;
  font-size: var(--font-size-sm);
  color: var(--text-tertiary);
}
.sync-years-group {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
.sync-years-label {
  font-size: var(--font-size-base);
  color: var(--text-secondary);
}

.sync-stats-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 16px;
}
.sync-stats-grid .stat-cell {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.sync-stats-grid .stat-label {
  font-size: var(--font-size-sm);
  color: var(--text-tertiary);
}
.sync-stats-grid .stat-value {
  font-size: var(--font-size-xl);
  font-weight: 700;
  color: var(--text-primary);
  display: flex;
  align-items: baseline;
  gap: 8px;
  flex-wrap: wrap;
}
.sync-stats-grid .stat-sub {
  font-size: var(--font-size-sm);
  font-weight: 400;
  color: var(--text-secondary);
}
.path-chips,
.reason-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  align-items: center;
}
.text-success {
  color: var(--success, #1f9d6b);
}
.text-warning {
  color: var(--warning, #c8801c);
}
.text-danger {
  color: var(--danger, #d24545);
}
.mr-1 {
  margin-right: 4px;
}
.mt-3 {
  margin-top: 12px;
}
.mb-1 {
  margin-bottom: 4px;
}

.integrity-stats {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 12px;
}
.integrity-stats .stat-item {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 12px;
  background: var(--bg-tertiary, #f5f7fa);
  border-radius: 6px;
}
.integrity-stats .stat-label {
  font-size: var(--font-size-sm);
  color: var(--text-tertiary);
  margin-bottom: 4px;
}
.integrity-stats .stat-value {
  font-size: 20px;
  font-weight: 700;
  color: var(--text-primary);
  font-variant-numeric: tabular-nums;
}
.integrity-stats .stat-value.warn {
  color: var(--warning);
}
.calendar-sync-note {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
  padding: 8px 12px;
  background: var(--bg-tertiary, #f5f7fa);
  border-radius: 6px;
}
.calendar-sync-text {
  font-size: var(--font-size-base);
  color: var(--text-secondary);
}
.check-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin-bottom: 12px;
}
.check-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 10px;
  background: var(--bg-tertiary, #f5f7fa);
  border-radius: 6px;
  font-size: var(--font-size-base);
}
.check-item .check-status {
  flex-shrink: 0;
}
.check-item .check-name {
  flex-shrink: 0;
  color: var(--text-primary);
  font-weight: 600;
  min-width: 88px;
}
.check-item .check-msg {
  color: var(--text-secondary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.drift-box {
  margin-bottom: 12px;
  padding: 10px 12px;
  border: 1px dashed var(--warning);
  border-radius: 6px;
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  align-items: center;
}
.drift-box .drift-title {
  font-size: var(--font-size-base);
  font-weight: 600;
  color: var(--warning);
  margin-right: 4px;
}

.repair-dialog-header {
  display: flex;
  align-items: center;
  gap: 12px;
}
.repair-dialog-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 40px;
  height: 40px;
  border-radius: var(--radius-lg);
  background: var(--warning-soft-strong);
  color: var(--warning);
  font-size: 22px;
  flex-shrink: 0;
}
.repair-dialog-title {
  font-size: 16px;
  font-weight: 600;
  color: var(--text-primary);
}
.repair-dialog-sub {
  font-size: var(--font-size-sm);
  color: var(--text-tertiary);
  margin-top: 2px;
}
.repair-dialog-body {
  padding: 4px 4px 0;
}
.repair-item-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-bottom: 14px;
}
.repair-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 14px;
  background: var(--bg-tertiary, #f5f7fa);
  border: 1px solid var(--border-light);
  border-radius: var(--radius-md);
}
.repair-item-badge {
  flex-shrink: 0;
  padding: 2px 10px;
  border-radius: var(--radius-full);
  font-size: var(--font-size-sm);
  font-weight: 600;
  &.is-error {
    background: var(--danger-soft);
    color: var(--danger);
  }
  &.is-warn {
    background: var(--warning-soft);
    color: var(--warning);
  }
}
.repair-item-desc {
  font-size: var(--font-size-base);
  color: var(--text-primary);
  word-break: break-all;
}
.repair-baostock-alert {
  margin-bottom: 12px;
}
.repair-tip {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 0;
  padding: 0 2px;
  font-size: var(--font-size-sm);
  color: var(--text-tertiary);
  .el-icon {
    flex-shrink: 0;
  }
}

.repair-progress-header {
  display: flex;
  align-items: center;
  gap: 12px;
}
.repair-progress-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 40px;
  height: 40px;
  border-radius: var(--radius-lg);
  background: var(--primary-soft);
  color: var(--primary);
  font-size: 22px;
  flex-shrink: 0;
  &.is-done {
    background: var(--success-soft);
    color: var(--success);
  }
  &.is-failed {
    background: var(--danger-soft);
    color: var(--danger);
  }
}
.repair-progress-title {
  font-size: 16px;
  font-weight: 600;
  color: var(--text-primary);
}
.repair-progress-sub {
  font-size: var(--font-size-sm);
  color: var(--text-tertiary);
  margin-top: 2px;
  line-height: 1.5;
}
.repair-progress-body {
  padding: 8px 4px 0;
}
.repair-progress-meta {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
  margin-top: 10px;
  font-size: var(--font-size-sm);
  color: var(--text-secondary);
  font-variant-numeric: tabular-nums;
  flex-wrap: wrap;
}
.repair-progress-tip {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 14px 0 0;
  padding: 8px 10px;
  background: var(--bg-tertiary, #f5f7fa);
  border-radius: 6px;
  font-size: var(--font-size-sm);
  color: var(--text-tertiary);
  .el-icon {
    flex-shrink: 0;
  }
}

.schedule-enabled {
  border-color: var(--el-color-primary) !important;
  color: var(--el-color-primary) !important;
}

.schedule-banner {
  margin-top: 12px;
  padding: 8px 12px;
  border-radius: 6px;
  font-size: 13px;
  color: var(--el-color-primary);
  background: var(--el-color-primary-light-9);
}

.schedule-hint {
  margin-left: 8px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.schedule-warn {
  font-size: 12px;
  color: var(--el-color-warning);
}

.schedule-scopes {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  align-items: center;
}

.schedule-note .el-form-item__content {
  line-height: 1.5;
}
</style>
