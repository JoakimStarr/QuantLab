<template>
  <el-dialog
    :model-value="visible"
    @update:model-value="handleUpdateVisible"
    title="快速上手指南"
    width="560px"
    :close-on-click-modal="false"
    align-center
  >
    <div class="guide-content">
      <div
        v-for="(step, index) in steps"
        :key="index"
        class="guide-step"
      >
        <div class="guide-step__index">{{ index + 1 }}</div>
        <div class="guide-step__body">
          <h4 class="guide-step__title">{{ step.title }}</h4>
          <p class="guide-step__desc">{{ step.desc }}</p>
        </div>
      </div>
    </div>
    <template #footer>
      <div class="guide-footer">
        <el-checkbox v-model="dontShow">不再显示</el-checkbox>
        <el-button type="primary" @click="handleClose">开始使用</el-button>
      </div>
    </template>
  </el-dialog>
</template>

<script setup>
import { ref, watch } from 'vue'

const props = defineProps({
  visible: { type: Boolean, default: false }
})

const emit = defineEmits(['update:visible'])

const STORAGE_KEY = 'quantlab_guide_seen'
const dontShow = ref(false)

// 引导步骤
const steps = [
  {
    title: '同步数据',
    desc: '在「数据管理」页面同步股票行情数据到本地 Qlib 数据存储，支持全量和增量同步。'
  },
  {
    title: '查看因子',
    desc: '在「因子库」页面浏览、评价已有因子，或通过「AI因子挖掘」生成新因子。'
  },
  {
    title: '创建策略',
    desc: '在「策略回测」页面选择因子组合，配置 TopK 和调仓频率，运行回测。'
  },
  {
    title: '查看回测',
    desc: '查看回测的收益曲线、关键指标和交易明细，对比不同策略表现。'
  }
]

// 弹窗打开时重置勾选状态
watch(() => props.visible, (val) => {
  if (val) {
    dontShow.value = false
  }
})

function handleUpdateVisible(val) {
  if (!val) {
    // 关闭时如果勾选了不再显示，写入 localStorage
    if (dontShow.value) {
      localStorage.setItem(STORAGE_KEY, '1')
    }
  }
  emit('update:visible', val)
}

function handleClose() {
  if (dontShow.value) {
    localStorage.setItem(STORAGE_KEY, '1')
  }
  emit('update:visible', false)
}
</script>

<style scoped lang="scss">
.guide-content {
  display: flex;
  flex-direction: column;
  gap: var(--space-lg);
  padding: var(--space-sm) 0;
}

.guide-step {
  display: flex;
  gap: var(--space-md);
  align-items: flex-start;

  &__index {
    flex-shrink: 0;
    width: 32px;
    height: 32px;
    border-radius: var(--radius-full);
    background: var(--primary-gradient);
    color: var(--text-inverse);
    display: flex;
    align-items: center;
    justify-content: center;
    font-weight: 700;
    font-size: var(--font-size-base);
    line-height: 1;
  }

  &__body {
    flex: 1;
    min-width: 0;
  }

  &__title {
    font-size: var(--font-size-lg);
    font-weight: 600;
    color: var(--text-primary);
    margin: 0 0 var(--space-xs);
  }

  &__desc {
    font-size: var(--font-size-sm);
    color: var(--text-secondary);
    line-height: var(--line-height-relaxed);
    margin: 0;
  }
}

.guide-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  width: 100%;
}
</style>
