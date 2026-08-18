<template>
  <nav class="mobile-tabbar">
    <router-link
      v-for="item in tabItems"
      :key="item.path"
      :to="item.path"
      class="tab-item"
      :class="{ 'tab-item--active': isActive(item.path) }"
    >
      <el-icon :size="20"><component :is="resolveIcon(item.icon)" /></el-icon>
      <span class="tab-label">{{ item.label }}</span>
    </router-link>

    <!-- 更多：收纳其余导航项 -->
    <button
      type="button"
      class="tab-item"
      :class="{ 'tab-item--active': moreActive }"
      aria-haspopup="dialog"
      :aria-expanded="moreOpen"
      @click="openMore"
    >
      <el-icon :size="20"><MoreFilled /></el-icon>
      <span class="tab-label">更多</span>
    </button>

    <!-- 底部抽屉：其余导航入口 -->
    <Teleport to="body">
      <Transition name="sheet">
        <div v-if="moreOpen" class="more-mask" @click="moreOpen = false">
          <div class="more-sheet" role="dialog" aria-label="更多功能" @click.stop>
            <div class="more-sheet__handle" />
            <div class="more-sheet__title">更多功能</div>
            <div class="more-sheet__grid">
              <router-link
                v-for="item in moreItems"
                :key="item.path"
                :to="item.path"
                class="more-item"
                :class="{ 'more-item--active': isActive(item.path) }"
                @click="moreOpen = false"
              >
                <el-icon :size="22"><component :is="resolveIcon(item.icon)" /></el-icon>
                <span class="more-item__label">{{ item.title }}</span>
              </router-link>
            </div>
          </div>
        </div>
      </Transition>
    </Teleport>
  </nav>
</template>

<script setup>
import { ref, computed } from 'vue'
import { useRoute } from 'vue-router'
import { MoreFilled } from '@element-plus/icons-vue'
import { resolveIcon } from '@/utils/icons'
import { mobileTabs, mobileMoreItems } from '@/config/nav'

const route = useRoute()
const tabItems = mobileTabs
const moreItems = mobileMoreItems
const moreOpen = ref(false)

function openMore() {
  moreOpen.value = true
}

// /docs 用前缀匹配，与桌面侧栏规则保持一致
function isActive(path) {
  if (path === '/docs') return route.path.startsWith('/docs')
  return route.path === path
}

// 当前路由命中"更多"内的项时，按钮保持高亮
const moreActive = computed(() => moreItems.some((it) => isActive(it.path)))
</script>

<style scoped lang="scss">
.mobile-tabbar {
  position: fixed;
  bottom: 0;
  left: 0;
  right: 0;
  height: 56px;
  display: flex;
  align-items: center;
  justify-content: space-around;
  background: var(--bg-primary);
  border-top: 1px solid var(--border);
  z-index: 1000;
  padding-bottom: env(safe-area-inset-bottom);
}

.tab-item {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 2px;
  padding: 6px 12px;
  color: var(--text-tertiary);
  text-decoration: none;
  background: none;
  border: none;
  font: inherit;
  cursor: pointer;
  transition: color var(--duration-fast) var(--ease-in-out);
  -webkit-tap-highlight-color: transparent;

  &:active {
    color: var(--text-secondary);
  }

  &:focus-visible {
    outline: 2px solid var(--primary);
    outline-offset: -2px;
    border-radius: 8px;
  }

  &--active {
    color: var(--primary);
  }
}

.tab-label {
  font-size: var(--font-size-xs);
  line-height: 1;
}

// ---------- "更多"底部抽屉 ----------
.more-mask {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.45);
  z-index: 2000;
}

.more-sheet {
  position: absolute;
  bottom: 0;
  left: 0;
  right: 0;
  max-width: 480px;
  margin: 0 auto;
  background: var(--bg-primary);
  border-radius: 16px 16px 0 0;
  padding: 8px 16px calc(16px + env(safe-area-inset-bottom));
  box-shadow: 0 -8px 24px rgba(0, 0, 0, 0.12);
}

.more-sheet__handle {
  width: 36px;
  height: 4px;
  margin: 4px auto 10px;
  border-radius: var(--radius-full);
  background: var(--border);
}

.more-sheet__title {
  font-size: var(--font-size-sm);
  font-weight: var(--font-weight-medium);
  color: var(--text-tertiary);
  padding: 2px 4px 10px;
}

.more-sheet__grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 8px;
}

.more-item {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  padding: 14px 8px;
  border-radius: 12px;
  font-size: var(--font-size-sm);
  color: var(--text-secondary);
  text-decoration: none;
  transition: background var(--duration-fast) var(--ease-in-out);

  &:active {
    background: var(--bg-hover);
  }

  &:focus-visible {
    outline: 2px solid var(--primary);
    outline-offset: -2px;
  }

  &--active {
    color: var(--primary);
    background: rgba(var(--primary-rgb), 0.08);
  }
}

.more-item__label {
  line-height: 1.2;
}

// 抽屉进出场：遮罩淡入 + 面板上滑
.sheet-enter-active,
.sheet-leave-active {
  transition: opacity 200ms var(--ease-in-out);

  .more-sheet {
    transition: transform 250ms var(--ease-in-out);
  }
}

.sheet-enter-from,
.sheet-leave-to {
  opacity: 0;

  .more-sheet {
    transform: translateY(100%);
  }
}

// 桌面端隐藏移动底栏（>=768px）
@media (min-width: 768px) {
  .mobile-tabbar {
    display: none;
  }
}
</style>
