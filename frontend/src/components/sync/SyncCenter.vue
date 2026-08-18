<template>
  <el-drawer
    :model-value="syncStore.drawerOpen"
    direction="rtl"
    size="580px"
    :with-header="false"
    append-to-body
    class="sync-drawer"
    @update:model-value="(v) => !v && syncStore.close()"
  >
    <div class="sc">
      <!-- 头部 -->
      <header class="sc-header">
        <div class="sc-header__text">
          <div class="sc-header__title">数据同步中心</div>
          <div class="sc-header__sub">统一管理全站数据域，各处同步入口均汇聚于此</div>
        </div>
        <button class="sc-header__close" aria-label="关闭" @click="syncStore.close">
          <el-icon :size="18"><Close /></el-icon>
        </button>
      </header>

      <div class="sc-body">
        <!-- 一键全同步主卡 -->
        <div class="sc-hero">
          <div class="sc-hero__head">
            <div class="sc-hero__title">
              一键全同步
              <span class="sc-hero__badge">推荐日常使用</span>
            </div>
            <div class="sc-hero__desc">A股回填 → 指数 → ETF → 宏观 → 财报 → 外盘，后台顺序执行</div>
          </div>
          <div class="sc-hero__controls">
            <div class="sc-hero__field">
              <span class="sc-hero__label">回填</span>
              <el-input-number v-model="syncYears" :min="1" :max="30" size="small" style="width: 88px" />
              <span class="sc-hero__label">年</span>
            </div>
            <el-checkbox v-model="refreshMisc" size="small">刷新基础资料/行业</el-checkbox>
          </div>
          <el-button
            type="primary"
            class="sc-hero__btn"
            :loading="syncStore.fullSyncing"
            :disabled="syncStore.running || syncStore.fullSyncing"
            @click="onFullSync"
          >
            {{ syncStore.running ? '同步进行中...' : '开始全同步' }}
          </el-button>
        </div>

        <!-- 实时进度 -->
        <div v-if="syncStore.running || syncStore.progress?.status === 'failed'" class="sc-progress" :class="{ 'sc-progress--failed': syncStore.progress?.status === 'failed' }">
          <div class="sc-progress__head">
            <span class="sc-progress__msg">{{ syncStore.progress?.message || '正在同步...' }}</span>
            <span class="sc-progress__pct">{{ syncStore.progressPct }}%</span>
          </div>
          <el-progress
            :percentage="syncStore.progress?.progress_pct || 0"
            :status="syncStore.progress?.status === 'failed' ? 'exception' : undefined"
            :stroke-width="8"
            :show-text="false"
          />
        </div>

        <!-- 数据域网格 -->
        <div class="sc-grid-title">按数据域同步</div>
        <div class="sc-grid">
          <div v-for="d in domains" :key="d.key" class="sc-domain">
            <div class="sc-domain__icon">
              <el-icon :size="18"><component :is="resolveIcon(d.icon)" /></el-icon>
            </div>
            <div class="sc-domain__meta">
              <div class="sc-domain__name">{{ d.name }}</div>
              <div class="sc-domain__desc">{{ d.desc }}</div>
              <div class="sc-domain__last" :class="{ 'is-failed': syncStore.lastSync[d.key]?.status === 'failed' }">
                {{ lastSyncText(d.key) }}
              </div>
            </div>
            <el-button
              size="small"
              text
              type="primary"
              class="sc-domain__btn"
              :loading="syncStore.domainBusy[d.key]"
              :disabled="syncStore.running"
              @click="syncStore.syncDomain(d.key)"
            >
              同步
            </el-button>
          </div>
        </div>
      </div>

      <!-- 底部：高级管理入口 -->
      <footer class="sc-footer">
        <router-link to="/quant/data" class="sc-footer__link" @click="syncStore.close()">
          <el-icon :size="14"><SetUp /></el-icon>
          前往数据管理 · 智能下载 / 多源通道 / 校验修复 / 定时同步
        </router-link>
      </footer>
    </div>
  </el-drawer>
</template>

<script setup>
import { ref } from 'vue'
import { Close, SetUp } from '@element-plus/icons-vue'
import { resolveIcon } from '@/utils/icons'
import { useSyncStore, DATA_DOMAINS } from '@/stores/sync'

const syncStore = useSyncStore()
const domains = DATA_DOMAINS

const syncYears = ref(5)
const refreshMisc = ref(false)

function onFullSync() {
  syncStore.startFullSync(syncYears.value, refreshMisc.value)
}

// 相对时间：域卡片展示「最近同步」用
function lastSyncText(key) {
  const rec = syncStore.lastSync[key]
  if (!rec?.time) return '暂无同步记录'
  const t = new Date(rec.time)
  if (Number.isNaN(t.getTime())) return '暂无同步记录'
  const diff = (Date.now() - t.getTime()) / 1000
  if (rec.status === 'failed') return '上次同步失败'
  if (diff < 60) return '刚刚同步'
  if (diff < 3600) return `${Math.floor(diff / 60)} 分钟前同步`
  if (diff < 86400) return `${Math.floor(diff / 3600)} 小时前同步`
  return `${Math.floor(diff / 86400)} 天前同步`
}
</script>

<style scoped lang="scss">
.sc {
  display: flex;
  flex-direction: column;
  height: 100%;
  margin: -16px;
}

// ---------- 头部 ----------
.sc-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 20px 20px 16px;
  border-bottom: 1px solid var(--border);
}

.sc-header__title {
  font-size: 16px;
  font-weight: var(--font-weight-semibold);
  color: var(--text-primary);
}

.sc-header__sub {
  margin-top: 4px;
  font-size: var(--font-size-xs);
  color: var(--text-tertiary);
}

.sc-header__close {
  width: 32px;
  height: 32px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 8px;
  border: none;
  background: transparent;
  color: var(--text-tertiary);
  cursor: pointer;
  transition: all 150ms var(--ease-in-out);

  &:hover {
    background: var(--bg-hover);
    color: var(--text-primary);
  }

  &:focus-visible {
    outline: 2px solid var(--primary);
    outline-offset: -2px;
  }
}

// ---------- 主体 ----------
.sc-body {
  flex: 1;
  overflow-y: auto;
  padding: 16px 20px;
}

// 一键全同步主卡
.sc-hero {
  padding: 16px;
  border-radius: var(--radius-md);
  background: linear-gradient(135deg, rgba(var(--primary-rgb), 0.08), rgba(var(--primary-rgb), 0.02));
  border: 1px solid rgba(var(--primary-rgb), 0.18);
}

.sc-hero__title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 15px;
  font-weight: var(--font-weight-semibold);
  color: var(--text-primary);
}

.sc-hero__badge {
  padding: 2px 8px;
  border-radius: var(--radius-full);
  background: rgba(var(--primary-rgb), 0.12);
  color: var(--primary);
  font-size: var(--font-size-xs);
  font-weight: var(--font-weight-normal);
}

.sc-hero__desc {
  margin-top: 6px;
  font-size: var(--font-size-xs);
  color: var(--text-tertiary);
  line-height: 1.5;
}

.sc-hero__controls {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-top: 12px;
  flex-wrap: wrap;
}

.sc-hero__field {
  display: flex;
  align-items: center;
  gap: 6px;
}

.sc-hero__label {
  font-size: var(--font-size-sm);
  color: var(--text-secondary);
}

.sc-hero__btn {
  width: 100%;
  margin-top: 12px;
}

// 实时进度
.sc-progress {
  margin-top: 12px;
  padding: 12px 16px;
  border-radius: var(--radius-md);
  background: var(--bg-tertiary);

  &--failed {
    background: rgba(var(--danger-rgb, 239, 35, 42), 0.06);
  }
}

.sc-progress__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 8px;
}

.sc-progress__msg {
  font-size: var(--font-size-sm);
  color: var(--text-secondary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.sc-progress__pct {
  font-size: var(--font-size-sm);
  font-weight: var(--font-weight-medium);
  color: var(--primary);
  font-family: var(--font-mono);
  flex-shrink: 0;
}

// 域网格
.sc-grid-title {
  margin: 18px 0 10px;
  font-size: var(--font-size-xs);
  font-weight: var(--font-weight-medium);
  color: var(--text-tertiary);
  letter-spacing: 2px;
}

.sc-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 10px;
}

.sc-domain {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px;
  border-radius: var(--radius-md);
  background: var(--bg-card);
  border: 1px solid var(--border-light);
  transition: border-color 150ms var(--ease-in-out), box-shadow 150ms var(--ease-in-out);

  &:hover {
    border-color: rgba(var(--primary-rgb), 0.35);
  }
}

.sc-domain__icon {
  width: 36px;
  height: 36px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 10px;
  background: rgba(var(--primary-rgb), 0.08);
  color: var(--primary);
  flex-shrink: 0;
}

.sc-domain__meta {
  flex: 1;
  min-width: 0;
}

.sc-domain__name {
  font-size: var(--font-size-sm);
  font-weight: var(--font-weight-medium);
  color: var(--text-primary);
}

.sc-domain__desc {
  margin-top: 2px;
  font-size: var(--font-size-xs);
  color: var(--text-tertiary);
  line-height: 1.4;
  display: -webkit-box;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
  overflow: hidden;
}

.sc-domain__last {
  margin-top: 4px;
  font-size: var(--font-size-xs);
  color: var(--text-tertiary);
  line-height: 1.4;

  &.is-failed {
    color: var(--danger);
  }
}

.sc-domain__btn {
  flex-shrink: 0;
  white-space: nowrap;
  padding: 6px 10px;
}

// ---------- 底部 ----------
.sc-footer {
  padding: 12px 20px;
  border-top: 1px solid var(--border);
}

.sc-footer__link {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  padding: 8px;
  border-radius: 8px;
  font-size: var(--font-size-sm);
  color: var(--text-secondary);
  text-decoration: none;
  transition: all 150ms var(--ease-in-out);

  &:hover {
    background: var(--bg-hover);
    color: var(--primary);
  }
}

// 移动端：抽屉全宽，域网格单列
@media (max-width: 767px) {
  :global(.sync-drawer) {
    width: 100% !important;
  }

  .sc-grid {
    grid-template-columns: 1fr;
  }
}
</style>
