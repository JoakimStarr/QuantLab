<template>
  <PageContainer>
    <!-- 页面头 -->
    <header class="page-header">
      <div class="page-header__lead">
        <h1 class="page-header__title">策略回测</h1>
        <p class="page-header__subtitle">多因子策略构建与回测分析</p>
      </div>
      <div class="page-header__actions">
        <el-button :icon="Refresh" @click="loadStrategies">刷新</el-button>
        <el-button :disabled="selectedResults.length < 2" :loading="comparing" @click="compareResults"
          >对比选中策略 ({{ selectedResults.length }})</el-button
        >
        <el-button type="warning" :loading="aiGenerating" :disabled="!factorCount" @click="onAiGenerate">
          {{ aiGenerating ? 'AI 生成中...' : '✨ AI 生成策略' }}
        </el-button>
        <el-button type="primary" :icon="Plus" @click="openCreate">新建策略</el-button>
      </div>
    </header>

    <!-- 策略列表表格 -->
    <SectionCard title="策略列表" class="table-card" v-loading="listLoading">
      <el-table
        v-if="strategies.length"
        :data="strategies"
        :row-class-name="rowClassName"
        @row-click="onRowClick"
        @selection-change="handleResultSelectionChange"
        size="default"
      >
        <el-table-column type="selection" width="48" />
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

    <!-- 回测指标 + 净值曲线（选中策略后显示） -->
    <template v-if="selectedStrategy">
      <!-- 指标卡组 -->
      <SectionCard
        ref="metricsRef"
        :title="`最新回测结果 — ${selectedStrategy.name}`"
        :subtitle="currentResult ? `${currentResult.start_date} ~ ${currentResult.end_date}` : ''"
      >
        <template #extra>
          <el-button v-if="currentResult" size="small" type="danger" plain @click="deleteCurrentResult"
            >删除该回测</el-button
          >
        </template>
        <div v-if="currentResult" v-loading="resultLoading">
          <!-- 核心指标突出：当前金额 + 3 个关键收益/风险指标 -->
          <div class="result-hero">
            <div class="result-hero__primary">
              <div class="result-hero__primary-label">当前金额</div>
              <div
                class="result-hero__primary-value"
                :class="currentValue >= initialCapital ? 'tone-success' : 'tone-danger'"
              >
                {{ fmtMoneyExact(currentValue) }}
              </div>
              <div class="result-hero__primary-sub">初始 {{ fmtMoneyExact(initialCapital) }}</div>
            </div>
            <div class="result-hero__item" v-for="h in heroMetrics" :key="h.key">
              <div class="result-hero__item-label">{{ h.label }}</div>
              <div class="result-hero__item-value" :class="h.tone">{{ h.value }}</div>
            </div>
          </div>

          <!-- 指标分区：收益 / 风险 / 风险调整后收益 -->
          <div class="metrics-section" v-for="sec in metricSections" :key="sec.key">
            <div class="metrics-section__title">{{ sec.title }}</div>
            <div class="metrics-grid" :class="'metrics-grid--' + sec.key">
              <div class="metric-card" v-for="m in sec.items" :key="m.label">
                <div class="metric-label">{{ m.label }}</div>
                <div class="metric-value" :class="m.tone">{{ m.value }}</div>
              </div>
            </div>
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
                  <span class="result-params__value">{{ currentResult.start_date }} ~ {{ currentResult.end_date }}</span>
                </div>
                <div class="result-params__item">
                  <span class="result-params__label">调仓频率</span>
                  <span class="result-params__value">{{ rebalanceLabel }}</span>
                </div>
                <div class="result-params__item">
                  <span class="result-params__label">topk/n_drop</span>
                  <span class="result-params__value"
                    >{{ currentResult.topk || '--' }}/{{ currentResult.n_drop || '--' }}</span
                  >
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
                  <span class="result-params__value">{{ fmtNum(currentResult.turnover, 3) }}</span>
                </div>
              </div>
            </el-collapse-item>
          </el-collapse>
        </div>
      </SectionCard>

      <!-- 净值曲线 -->
      <SectionCard title="净值曲线">
        <template #extra>
          <div class="chart-legend">
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
        <v-chart v-if="hasChart" :option="chartOption" class="chart-body" autoresize />
        <el-empty v-else description="暂无净值数据" :image-size="64" />
      </SectionCard>

      <!-- 收益日历：按日期查看当日收益与买卖情况 -->
      <SectionCard v-if="calendarMonths.length" title="收益日历">
        <template #extra>
          <span class="calendar-hint">点击日期查看当日收益与买卖情况</span>
        </template>
        <div class="calendar-legend">
          <span class="calendar-legend__item"><i class="square square--up"></i>当日收益为正</span>
          <span class="calendar-legend__item"><i class="square square--negative"></i>当日收益为负</span>
          <span class="calendar-legend__item"><i class="square square--flat"></i>无收益</span>
          <span class="calendar-legend__item"><i class="square square--trade"></i>当日有交易</span>
        </div>
        <div class="calendar-grid">
          <div class="calendar-month" v-for="m in calendarMonths" :key="m.ym">
            <div class="calendar-month__title">{{ m.ym }}</div>
            <div class="calendar-month__weekdays">
              <span v-for="w in ['一', '二', '三', '四', '五', '六', '日']" :key="w">{{ w }}</span>
            </div>
            <div class="calendar-month__cells">
              <div
                v-for="(cell, i) in m.cells"
                :key="i"
                class="calendar-day"
                :class="[
                  cell.blank ? 'calendar-day--blank' : 'calendar-day--fill',
                  !cell.blank && cell.ret > 0 ? 'calendar-day--up' : '',
                  !cell.blank && cell.ret < 0 ? 'calendar-day--negative' : '',
                  !cell.blank && cell.ret === 0 ? 'calendar-day--flat' : '',
                  !cell.blank && activeCalDate === cell.date ? 'calendar-day--active' : '',
                ]"
                :title="cell.date + (cell.ret != null ? ' ' + fmtPct(cell.ret) : '')"
                @click="!cell.blank && (calendarSelected = cell.date)"
              >
                <span class="calendar-day__num" v-if="!cell.blank">{{ cell.day }}</span>
                <span v-if="!cell.blank && (cell.ret > 0.0005 || cell.ret < -0.0005)" class="calendar-day__ret">
                  {{ fmtPct(cell.ret) }}
                </span>
                <span v-if="!cell.blank && cell.hasTrades" class="calendar-day__dot"></span>
              </div>
            </div>
          </div>
        </div>

        <!-- 选中日期的收益与买卖明细 -->
        <div v-if="activeCalDetail" class="calendar-detail">
          <div class="calendar-detail__head">
            <span class="calendar-detail__date">{{ activeCalDate }}</span>
            <span class="calendar-detail__ret" :class="activeCalDetail.ret > 0 ? 'tone-success' : activeCalDetail.ret < 0 ? 'tone-danger' : ''">
              当日收益 {{ fmtPct(activeCalDetail.ret) }}
            </span>
            <span class="calendar-detail__pnl" :class="activeCalDetail.pnl >= 0 ? 'tone-success' : 'tone-danger'">
              当日已实现盈亏 {{ fmtPnl(activeCalDetail.pnl) }}
            </span>
            <el-button size="small" @click="calendarSelected = ''">收起</el-button>
          </div>
          <div v-if="activeCalDetail.trades.length" class="calendar-detail__trades">
            <el-table :data="activeCalDetail.trades" size="small" stripe>
              <el-table-column prop="time" label="时间" width="110">
                <template #default="{ row }">
                  <span class="calendar-detail__time">{{ String(row.date).length > 10 ? String(row.date).slice(11) : '--' }}</span>
                </template>
              </el-table-column>
              <el-table-column prop="code" label="代码" min-width="110" />
              <el-table-column prop="action" label="方向" width="70">
                <template #default="{ row }">
                  <el-tag :type="row.action === 'BUY' ? 'danger' : 'success'" effect="light" size="small">
                    {{ row.action === 'BUY' ? '买入' : '卖出' }}
                  </el-tag>
                </template>
              </el-table-column>
              <el-table-column prop="behavior" label="行为" width="70" />
              <el-table-column prop="quantity" label="数量" width="90" align="right" />
              <el-table-column prop="price" label="成交价" width="100" align="right">
                <template #default="{ row }">{{ fmtPrice(row.price) }}</template>
              </el-table-column>
              <el-table-column prop="pnl" label="盈亏" width="110" align="right">
                <template #default="{ row }">
                  <span :class="row.pnl > 0 ? 'tone-success' : row.pnl < 0 ? 'tone-danger' : ''">
                    {{ row.pnl ? fmtPnl(row.pnl) : '--' }}
                  </span>
                </template>
              </el-table-column>
            </el-table>
          </div>
          <el-empty v-else description="当日无成交" :image-size="48" />
        </div>
      </SectionCard>

      <!-- 回测动作与行为：逐笔成交明细 -->
      <SectionCard v-if="hasTrades" title="交易明细（动作与行为）">
        <template #extra>
          <div class="chart-legend" style="gap: 16px">
            <span class="legend-item">
              <span
                style="
                  display: inline-block;
                  width: 10px;
                  height: 10px;
                  border-radius: 50%;
                  background: var(--danger);
                  margin-right: 6px;
                "
              ></span>
              买入 {{ tradeStats.buys }}
            </span>
            <span class="legend-item">
              <span
                style="
                  display: inline-block;
                  width: 10px;
                  height: 10px;
                  border-radius: 50%;
                  background: var(--success);
                  margin-right: 6px;
                "
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

        <!-- 按日分组视图：每个调仓日一行，展开看当日逐笔 -->
        <el-table v-if="tradeView === 'group'" :data="pagedGroups" size="small" stripe row-key="date">
          <el-table-column type="expand">
            <template #default="{ row }">
              <div class="trade-group-detail">
                <el-table :data="row.trades" size="small" stripe>
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
                    <template #default="{ row: sub }"
                      ><span class="cell-mono">{{ sub.code || '--' }}</span></template
                    >
                  </el-table-column>
                  <el-table-column label="成交价" width="90" align="right">
                    <template #default="{ row: sub }"
                      ><span class="cell-mono cell-tnum">{{ Number(sub.price).toFixed(3) }}</span></template
                    >
                  </el-table-column>
                  <el-table-column label="数量" min-width="100" align="right">
                    <template #default="{ row: sub }"
                      ><span class="cell-mono cell-tnum">{{ fmtNum(sub.quantity, 0) }}</span></template
                    >
                  </el-table-column>
                  <el-table-column label="成交金额" min-width="120" align="right">
                    <template #default="{ row: sub }"
                      ><span class="cell-mono cell-tnum">{{ fmtMoneyExact(sub.total) }}</span></template
                    >
                  </el-table-column>
                  <el-table-column label="费用" width="100" align="right">
                    <template #default="{ row: sub }"
                      ><span class="cell-mono cell-tnum">{{ fmtMoneyExact(sub.cost) }}</span></template
                    >
                  </el-table-column>
                  <el-table-column label="持仓" min-width="100" align="right">
                    <template #default="{ row: sub }"
                      ><span class="cell-mono cell-tnum">{{ fmtNum(sub.position, 0) }}</span></template
                    >
                  </el-table-column>
                </el-table>
              </div>
            </template>
          </el-table-column>
          <el-table-column prop="date" label="交易日期" min-width="120" />
          <el-table-column label="买入" width="96" align="center">
            <template #default="{ row }"
              ><span class="cell-tnum text-danger">{{ row.buys }}</span> 笔</template
            >
          </el-table-column>
          <el-table-column label="卖出" width="96" align="center">
            <template #default="{ row }"
              ><span class="cell-tnum text-success">{{ row.sells }}</span> 笔</template
            >
          </el-table-column>
          <el-table-column label="当日成交额" min-width="130" align="right">
            <template #default="{ row }"
              ><span class="cell-mono cell-tnum">{{ fmtMoneyExact(row.amount) }}</span></template
            >
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
        <el-table v-else :data="pagedTrades" size="small" stripe>
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
            <template #default="{ row }"
              ><span class="cell-mono">{{ row.code || '--' }}</span></template
            >
          </el-table-column>
          <el-table-column label="成交价" width="90" align="right">
            <template #default="{ row }"
              ><span class="cell-mono cell-tnum">{{ Number(row.price).toFixed(3) }}</span></template
            >
          </el-table-column>
          <el-table-column label="数量" min-width="100" align="right">
            <template #default="{ row }"
              ><span class="cell-mono cell-tnum">{{ fmtNum(row.quantity, 0) }}</span></template
            >
          </el-table-column>
          <el-table-column label="成交金额" min-width="120" align="right">
            <template #default="{ row }"
              ><span class="cell-mono cell-tnum">{{ fmtMoneyExact(row.total) }}</span></template
            >
          </el-table-column>
          <el-table-column label="费用" width="90" align="right">
            <template #default="{ row }"
              ><span class="cell-mono cell-tnum">{{ fmtMoneyExact(row.cost) }}</span></template
            >
          </el-table-column>
          <el-table-column label="成本价" min-width="90" align="right">
            <template #default="{ row }"
              ><span class="cell-mono cell-tnum">{{ fmtPrice(row.avgCost) }}</span></template
            >
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
            <template #default="{ row }"
              ><span class="cell-mono cell-tnum">{{ fmtNum(row.position, 0) }}</span></template
            >
          </el-table-column>
          <el-table-column label="剩余资金" min-width="110" align="right">
            <template #default="{ row }"
              ><span class="cell-mono cell-tnum">{{ fmtMoneyExact(row.cash) }}</span></template
            >
          </el-table-column>
        </el-table>
        <div class="trades-pagination">
          <el-pagination
            v-model:current-page="tradePage"
            v-model:page-size="tradePageSize"
            :total="tradeView === 'group' ? tradeGroupCount : filteredTrades.length"
            :page-sizes="[50, 100, 200]"
            layout="total, sizes, prev, pager, next"
            background
          />
        </div>
      </SectionCard>
    </template>

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
import { ref, reactive, computed, watch, nextTick, onMounted, onBeforeUnmount } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus/es/components/message/index'
import { Plus, Refresh } from '@element-plus/icons-vue'
import VChart from 'vue-echarts'
import '@/utils/echarts'
import PageContainer from '@/components/common/PageContainer.vue'
import ConfirmDialog from '@/components/common/ConfirmDialog.vue'
import { chartTheme, withAlpha } from '@/utils/chartTheme'
import { useThemeRev } from '@/composables/useChartTheme'

const themeRev = useThemeRev()
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

const router = useRouter()
const factorStore = useFactorStore()

// === 策略列表与选中 ===
const strategies = ref([])
const selectedStrategy = ref(null)
const listLoading = ref(false)

// === AI 策略 ===
const aiGenerating = ref(false)
const factorCount = computed(() => factorStore.factors?.length || 0)
// AI 生成偏好（风格/风险/调仓频率/资金/其他要求）
const aiPref = ref({ visible: false, style: '', risk: '', rebalance: '', capital: null, other: '' })

// === 回测参数（资金/区间） ===
const btParams = ref({
  visible: false,
  row: null,
  range: ['2020-01-01', new Date().toISOString().slice(0, 10)],
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
const metricsRef = ref(null)

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
let pollTimer = null
let statusPollTimer = null

// === 时间格式化 ===
function formatTime(ts) {
  if (!ts) return '--'
  return ts.replace('T', ' ').slice(0, 19)
}

// === 数值格式化 ===
function fmtPct(v, digits = 2) {
  if (v == null || v === '') return '--'
  return (v * 100).toFixed(digits) + '%'
}
function fmtNum(v, digits = 3) {
  if (v == null || v === '') return '--'
  return Number(v).toFixed(digits)
}

// === 交易明细（回测动作与行为） ===
const tradeType = ref('all')
const tradeCode = ref('')
const tradePage = ref(1)
const tradePageSize = ref(50)
const tradeView = ref('group') // 'group' 按日折叠 / 'flat' 逐笔
const tradeOrder = ref('desc') // 'desc' 最近优先 / 'asc' 最早优先
const paramsOpen = ref([]) // 回测参数折叠区，默认收起

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
  return { buys, sells, total, realized }
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
  // 逐代码跟踪加权平均成本（含买入费用），用于计算卖出时已实现盈亏
  const lots = {} // code -> { shares, costBasis }
  const startCapital =
    currentResult.value?.initial_capital ??
    currentResult.value?.metrics?.initial_capital ??
    (Number(btParams.value.capital) || 0)
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
      lot.costBasis += total + cost // 成本含买入费用
      avgCost = lot.shares > 0 ? lot.costBasis / lot.shares : 0
    } else {
      behavior = prevShares - qty > 0 ? '减仓' : '清仓'
      avgCost = prevShares > 0 ? lot.costBasis / prevShares : price
      // 已实现盈亏 = (卖出价 - 加权成本) × 数量 - 卖出费用
      pnl = (price - avgCost) * qty - cost
      lot.shares = Math.max(0, prevShares - qty)
      lot.costBasis = Math.max(0, lot.costBasis - avgCost * Math.min(qty, prevShares))
    }
    lots[key] = lot
    const nextPos = lot.shares
    // 剩余资金：初始资金 − 买入(成交额+费用) + 卖出(成交额−费用)
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
  // 展示层排序：默认倒序（最近 → 最远），盈亏/持仓仍按 enriched 正序计算
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
    // 买入标记压在净值线下方，卖出标在上方，方便看图定位调仓日
    if (buys) buy.push([i, base - 0.01, buys])
    if (sells) sell.push([i, base + 0.01, sells])
  }
  return { buy, sell }
})

// === 收益日历（日/周/月/年 视图切换 + 导航） ===
// 每日收益率 = 净值相邻点变化率（用于日历着色）
const retByDate = computed(() => {
  const c = currentResult.value?.nav_curve || {}
  const dates = c.dates || []
  const nav = c.portfolio || []
  const map = new Map()
  for (let i = 0; i < dates.length; i++) {
    const d = String(dates[i]).slice(0, 10)
    const cur = Number(nav[i])
    const prev = i > 0 ? Number(nav[i - 1]) : 1
    if (isNaN(cur) || isNaN(prev) || !prev) continue
    map.set(d, cur / prev - 1)
  }
  return map
})

// 有交易发生的日期集合（用于日历标记）
const calTradeDates = computed(() => {
  const s = new Set()
  for (const x of enrichedTrades.value) s.add(String(x.date || '').slice(0, 10))
  return s
})

// 最新一个净值数据日（作为日历锚点）
const lastRetDate = computed(() => {
  const keys = [...retByDate.value.keys()].sort()
  return keys.length ? keys[keys.length - 1] : ''
})

const calUnit = ref('month') // 'day' | 'week' | 'month' | 'year'
const calOffset = ref(0) // 在当前视图单位下相对锚点的偏移（上一/下一）
const calAnchor = ref('') // 锚点基准日期 YYYY-MM-DD

// 切换结果或切单位时复位导航
watch(
  [() => currentResult.value?.id, calUnit],
  () => {
    calAnchor.value = ''
    calOffset.value = 0
  },
  { immediate: true }
)

function shiftDate(dateStr, unit, delta) {
  const [y, m, d] = dateStr.split('-').map(Number)
  const dt = new Date(y, m - 1, d)
  if (unit === 'day') dt.setDate(dt.getDate() + delta)
  else if (unit === 'week') dt.setDate(dt.getDate() + delta * 7)
  else if (unit === 'month') dt.setMonth(dt.getMonth() + delta)
  else if (unit === 'year') dt.setFullYear(dt.getFullYear() + delta)
  const pad = (n) => String(n).padStart(2, '0')
  return `${dt.getFullYear()}-${pad(dt.getMonth() + 1)}-${pad(dt.getDate())}`
}

// 当前视图聚焦日期（锚点 + 偏移）
const calFocus = computed(() => {
  const base = calAnchor.value || lastRetDate.value
  if (!base) return null
  return shiftDate(base, calUnit.value, calOffset.value)
})

function calPrev() {
  calOffset.value -= 1
}
function calNext() {
  calOffset.value += 1
}
function calToday() {
  calOffset.value = 0
}

// 构造单个日单元格
function calDayCell(date) {
  const ret = retByDate.value.get(date)
  return {
    date,
    day: Number(date.slice(8, 10)),
    ret: ret == null ? null : ret,
    hasTrades: calTradeDates.value.has(date),
  }
}

// 当前视图：月/周/日 → cells 网格；年 → months 卡片
const calView = computed(() => {
  const focus = calFocus.value
  if (!focus) return null
  const unit = calUnit.value
  const [fy, fm, fd] = focus.split('-').map(Number)
  const pad = (n) => String(n).padStart(2, '0')
  const toKey = (y, m, d) => `${y}-${pad(m)}-${pad(d)}`

  if (unit === 'month') {
    const total = new Date(fy, fm, 0).getDate()
    const firstDow = (new Date(fy, fm - 1, 1).getDay() + 6) % 7 // 周一=0
    const cells = []
    for (let i = 0; i < firstDow; i++) cells.push({ blank: true })
    for (let day = 1; day <= total; day++) cells.push(calDayCell(toKey(fy, fm, day)))
    return { unit, title: `${fy}年${fm}月`, cells, weekdays: true }
  }

  if (unit === 'week') {
    const dow = (new Date(fy, fm - 1, fd).getDay() + 6) % 7
    const monday = new Date(fy, fm - 1, fd - dow)
    const cells = []
    for (let i = 0; i < 7; i++) {
      const dt = new Date(monday.getFullYear(), monday.getMonth(), monday.getDate() + i)
      cells.push(calDayCell(toKey(dt.getFullYear(), dt.getMonth() + 1, dt.getDate())))
    }
    const first = cells[0].date
    const last = cells[6].date
    return { unit, title: `${first} ~ ${last}`, cells, weekdays: true }
  }

  if (unit === 'day') {
    return { unit, title: focus, cells: [calDayCell(focus)], weekdays: false }
  }

  // year：12 个月格，月收益 = (1+r1)(1+r2)…-1
  const months = []
  for (let m = 1; m <= 12; m++) {
    const ym = `${fy}-${pad(m)}`
    const keys = [...retByDate.value.keys()].filter((k) => k.startsWith(ym)).sort()
    let ret = null
    if (keys.length) {
      let prod = 1
      for (const k of keys) prod *= 1 + retByDate.value.get(k)
      ret = prod - 1
    }
    months.push({ ym, label: `${m}月`, ret, hasTrades: [...calTradeDates.value].some((k) => k.startsWith(ym)) })
  }
  return { unit, title: `${fy}年`, months, weekdays: false }
})

// 年视图点击月份 → 跳到该月
function gotoMonth(ym) {
  calUnit.value = 'month'
  calAnchor.value = `${ym}-01`
  calOffset.value = 0
}

const calendarSelected = ref('')
const activeCalDate = computed(() => calendarSelected.value || calFocus.value || '')

// 选中日期的详情：当日收益 + 当日全部成交
const activeCalDetail = computed(() => {
  const d = activeCalDate.value
  if (!d) return null
  const ret = retByDate.value.get(d) ?? 0
  const trades = enrichedTrades.value.filter((x) => String(x.date || '').slice(0, 10) === d)
  const pnl = trades.reduce((s, x) => s + (Number(x.pnl) || 0), 0)
  return { ret, pnl, trades }
})

// 分页展示：一次最多渲染 50/100/200 行，避免 2000 笔全量进 DOM 造成卡顿。
// 过滤器/视图/排序变化时重置到第 1 页。
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
  return Number(v).toFixed(3)
}

async function exportTrades() {
  try {
    const id = currentResult.value?.id
    if (id == null) return
    const { exportTrades } = await import('@/api/quant')
    const res = await exportTrades(id)
    // blob 下载：res.data 为 Blob（blobRequest 无统一拦截器）
    const blob = res?.data || res
    const url = window.URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `backtest_${id}_trades.csv`
    a.click()
    window.URL.revokeObjectURL(url)
  } catch (e) {
    if (e !== 'cancel') ElMessage.error('导出交易明细失败')
  }
}

// === 指标卡（9张，含语义色） ===
// 核心指标突出展示：当前金额旁边放 3 个关键收益/风险指标（含夏普）
const heroMetrics = computed(() => {
  const m = currentResult.value || {}
  const ar = m.annual_return
  const tr = totalReturn.value
  return [
    { key: 'total', label: '总收益率', value: fmtPct(tr), tone: tr > 0 ? 'tone-success' : tr < 0 ? 'tone-danger' : '' },
    { key: 'annual', label: '年化收益', value: fmtPct(ar), tone: ar > 0 ? 'tone-success' : ar < 0 ? 'tone-danger' : '' },
    { key: 'sharpe', label: '夏普比率', value: fmtNum(m.sharpe), tone: '' },
  ]
})

// 指标分区：收益 / 风险 / 风险调整后收益
const metricSections = computed(() => {
  const m = currentResult.value || {}
  const er = m.excess_return
  return [
    {
      key: 'return',
      title: '收益类',
      items: [
        { label: '年化收益', value: fmtPct(m.annual_return), tone: m.annual_return > 0 ? 'tone-success' : m.annual_return < 0 ? 'tone-danger' : '' },
        { label: '超额收益', value: fmtPct(er), tone: er > 0 ? 'tone-success' : er < 0 ? 'tone-danger' : '' },
        { label: '基准收益', value: fmtPct(m.benchmark_return), tone: '' },
      ],
    },
    {
      key: 'risk',
      title: '风险类',
      items: [
        { label: '年化波动', value: fmtPct(m.annual_volatility), tone: '' },
        { label: '最大回撤', value: fmtPct(m.max_drawdown), tone: 'tone-danger' },
      ],
    },
    {
      key: 'adjusted',
      title: '风险调整后收益',
      items: [
        { label: '索提诺', value: fmtNum(m.sortino), tone: '' },
        { label: '卡玛比率', value: fmtNum(m.calmar), tone: '' },
        { label: '胜率', value: fmtPct(m.win_rate, 1), tone: '' },
      ],
    },
  ]
})

// 总收益率：区间累计收益 = 净值曲线最后一个点 - 1（曲线已归一化到 1.0）
const totalReturn = computed(() => {
  const p = currentResult.value?.nav_curve?.portfolio
  if (!Array.isArray(p) || !p.length) return null
  const last = Number(p[p.length - 1])
  return isNaN(last) ? null : last - 1
})

// === 回测重要参数（初始/当前金额等） ===
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

const rebalanceLabel = computed(() => {
  const map = { day: '每日', week: '每周', month: '每月' }
  return map[currentResult.value?.rebalance_freq] || currentResult.value?.rebalance_freq || '--'
})

// 基准：v2.4.1 起回测结果持久化 benchmark 快照，优先取结果自带值，
// 兼容旧数据（结果无 benchmark 时回退到策略当前值）
const benchmarkLabel = computed(() => {
  return currentResult.value?.benchmark || selectedStrategy.value?.benchmark || '--'
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

const chartOption = computed(() => {
  void themeRev.value
  const c = currentResult.value?.nav_curve || {}
  return {
    grid: { top: 20, right: 24, bottom: 30, left: 50 },
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'line', snap: true },
      backgroundColor: chartTheme.bgCard(),
      borderColor: chartTheme.border(),
      textStyle: { color: chartTheme.textPrimary() },
      formatter: (params) => {
        const arr = Array.isArray(params) ? params : [params]
        const lines = arr.map((p) => {
          if (p.seriesType === 'scatter') {
            const count = p.value?.[2] ?? 0
            return `${p.marker} ${p.seriesName}: <b>${count}</b> 笔`
          }
          return `${p.marker} ${p.seriesName}: <b>${Number(p.value).toFixed(3)}</b>`
        })
        return `${params[0].axisValue}<br/>${lines.join('<br/>')}`
      },
    },
    xAxis: {
      type: 'category',
      data: c.dates || [],
      boundaryGap: false,
      axisLine: { lineStyle: { color: chartTheme.border() } },
      axisTick: { show: false },
      axisLabel: { color: chartTheme.axisText(), fontSize: 11, hideOverlap: true },
    },
    yAxis: {
      type: 'value',
      scale: true,
      axisLine: { show: false },
      axisTick: { show: false },
      axisLabel: {
        color: chartTheme.axisText(),
        fontSize: 11,
        formatter: (v) => Number(v).toFixed(1),
      },
      splitLine: { lineStyle: { color: chartTheme.border(), type: 'dashed' } },
    },
    series: [
      {
        name: '策略净值',
        data: c.portfolio || [],
        type: 'line',
        smooth: true,
        showSymbol: false,
        connectNulls: true,
        emphasis: { disabled: true },
        lineStyle: { color: chartTheme.primary(), width: 2 },
        areaStyle: { color: withAlpha(chartTheme.primary(), 0.08) },
        itemStyle: { color: chartTheme.primary() },
      },
      {
        name: '基准净值',
        data: c.benchmark || [],
        type: 'line',
        smooth: true,
        showSymbol: false,
        connectNulls: true,
        emphasis: { disabled: true },
        lineStyle: { color: chartTheme.axisText(), width: 1.5, type: 'dashed' },
        itemStyle: { color: chartTheme.axisText() },
      },
      // 交易时间线：买入标在净值线下方，卖出标在上方（只看调仓节奏，悬停显示笔数）
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
  }
})

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
  stopStatusPolling()
  statusPollTimer = setInterval(async () => {
    await loadBacktestStatuses()
    if (!hasRunningStatus()) {
      stopStatusPolling()
    }
  }, 3000)
}

function stopStatusPolling() {
  if (statusPollTimer) {
    clearInterval(statusPollTimer)
    statusPollTimer = null
  }
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

// === 点击行选中策略并加载回测结果 ===
function onRowClick(row) {
  if (!row || selectedStrategy.value?.id === row.id) return
  selectStrategy(row)
}

async function selectStrategy(row, scroll = false) {
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
      nextTick(() => metricsRef.value?.$el?.scrollIntoView({ behavior: 'smooth', block: 'start' }))
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
  btParams.value = {
    visible: true,
    row,
    range: ['2020-01-01', new Date().toISOString().slice(0, 10)],
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
  stopPolling()
  let attempts = 0
  const maxAttempts = 40
  resultLoading.value = true
  pollTimer = setInterval(async () => {
    attempts++
    try {
      const data = await listBacktestResults(row.id, { limit: 1 })
      const latest = data?.items?.[0]
      // 出现新的已完成结果（id 变化且指标已填充）
      if (latest && latest.id !== prevId && latest.annual_return != null) {
        currentResult.value = await getBacktestResult(latest.id)
        ElMessage.success('回测完成')
        stopPolling()
      } else if (attempts >= maxAttempts) {
        ElMessage.warning('回测仍在进行中，请稍后点击"结果"查看')
        stopPolling()
      }
    } catch (e) {
      if (attempts >= maxAttempts) stopPolling()
    }
  }, 3000)
}

function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
  resultLoading.value = false
}

// === "结果"链接：选中策略并滚动到指标区 ===
function viewResults(row) {
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
let wfTimer = null

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
  if (wfTimer) clearInterval(wfTimer)
  let attempts = 0
  wfTimer = setInterval(async () => {
    attempts++
    if (attempts > 120) {
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
  }, 3000)
}

function stopWfPolling() {
  if (wfTimer) {
    clearInterval(wfTimer)
    wfTimer = null
  }
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
  return n.toFixed(4)
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
.page-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: var(--space-md);
  margin-bottom: var(--space-lg);
  animation: fadeInUp 0.5s var(--ease-out-expo) both;

  &__lead {
    flex: 1;
    min-width: 0;
  }

  &__title {
    font-size: var(--font-size-2xl);
    font-weight: 700;
    color: var(--text-primary);
    margin: 0 0 var(--space-xs);
    line-height: var(--line-height-tight);
  }

  &__subtitle {
    font-size: var(--font-size-sm);
    color: var(--text-secondary);
  }

  &__actions {
    display: flex;
    align-items: center;
    gap: var(--space-sm);
  }
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

// 回测指标区
.metrics-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: var(--space-md);
  min-height: 80px;
}
.result-params {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: var(--space-sm) var(--space-md);
  padding: var(--space-sm) var(--space-md);
  margin-bottom: var(--space-md);
  background: var(--bg-tertiary, #f5f6f7);
  border: 1px solid var(--border, #e5e6eb);
  border-radius: var(--radius-md, 8px);
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
.metric-card {
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  padding: var(--space-md);
}
.metric-label {
  font-size: var(--font-size-sm);
  color: var(--text-tertiary);
  margin-bottom: var(--space-xs);
}
.metric-value {
  font-size: var(--font-size-2xl);
  font-weight: var(--font-weight-semibold);
  font-variant-numeric: tabular-nums;
  color: var(--text-primary);
}
.tone-success {
  color: var(--success);
}
.tone-danger {
  color: var(--danger);
}

// 收益日历
.calendar-hint {
  font-size: 12px;
  color: var(--text-tertiary, #8a9099);
}
.calendar-legend {
  display: flex;
  flex-wrap: wrap;
  gap: 16px;
  margin-bottom: 14px;
  font-size: 12px;
  color: var(--text-secondary, #4e5969);

  &__item {
    display: inline-flex;
    align-items: center;
    gap: 6px;
  }
}
.square {
  display: inline-block;
  width: 12px;
  height: 12px;
  border-radius: 3px;

  &--up {
    background: var(--success, #1f9d6b);
  }
  &--negative {
    background: var(--danger, #d24545);
  }
  &--flat {
    background: var(--bg-tertiary, #f2f3f5);
    border: 1px solid var(--border, #e5e6eb);
  }
  &--trade {
    background: var(--primary);
    position: relative;

    &::after {
      content: '';
      position: absolute;
      left: 4px;
      top: 4px;
      width: 4px;
      height: 4px;
      border-radius: 50%;
      background: #fff;
    }
  }
}
.calendar-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
  gap: 16px;
}
.calendar-month {
  background: var(--bg-card);
  border: 1px solid var(--border, #e5e6eb);
  border-radius: var(--radius-md, 8px);
  padding: 12px;

  &__title {
    font-weight: 600;
    font-size: 14px;
    margin-bottom: 8px;
    color: var(--text-primary, #1f2329);
  }

  &__weekdays {
    display: grid;
    grid-template-columns: repeat(7, 1fr);
    text-align: center;
    font-size: 11px;
    color: var(--text-tertiary, #8a9099);
    margin-bottom: 4px;
  }

  &__cells {
    display: grid;
    grid-template-columns: repeat(7, 1fr);
    gap: 4px;
  }
}
.calendar-day {
  height: 40px;
  border-radius: 6px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  position: relative;
  font-size: 11px;
  line-height: 1.25;
  overflow: hidden;

  &--blank {
    cursor: default;
  }

  &--fill {
    background: var(--bg-tertiary, #f2f3f5);
  }

  &--up {
    background: rgba(31, 157, 107, 0.12);
    color: var(--success, #1f9d6b);
  }
  &--negative {
    background: rgba(210, 69, 69, 0.12);
    color: var(--danger, #d24545);
  }
  &--flat {
    background: var(--bg-tertiary, #f2f3f5);
    color: var(--text-secondary, #4e5969);
  }

  &--active {
    outline: 2px solid var(--primary);
    outline-offset: 1px;
  }

  &__num {
    font-weight: 600;
  }
  &__ret {
    font-size: 10px;
    opacity: 0.85;
  }
  &__dot {
    position: absolute;
    right: 4px;
    top: 4px;
    width: 6px;
    height: 6px;
    border-radius: 50%;
    background: var(--primary);
  }
}
.calendar-detail {
  margin-top: 16px;
  padding-top: 14px;
  border-top: 1px solid var(--border, #e5e6eb);

  &__head {
    display: flex;
    align-items: center;
    gap: 16px;
    margin-bottom: 12px;
    flex-wrap: wrap;
  }

  &__date {
    font-size: 16px;
    font-weight: 700;
    color: var(--text-primary, #1f2329);
  }

  &__time {
    font-size: 12px;
    color: var(--text-tertiary, #8a9099);
  }
}

// 净值曲线卡
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
.chart-body {
  width: 100%;
  height: 320px;
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
