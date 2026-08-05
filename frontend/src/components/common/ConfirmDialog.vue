<template>
  <el-dialog
    :model-value="modelValue"
    width="440px"
    align-center
    :close-on-click-modal="false"
    :close-on-press-escape="!blockEscape"
    :show-close="false"
    @update:model-value="onUpdate"
  >
    <div class="confirm-dialog">
      <div class="confirm-dialog__icon" :class="`is-${icon}`">
        <el-icon>
          <WarningFilled v-if="icon === 'warning'" />
          <CircleCheckFilled v-else-if="icon === 'success'" />
          <CircleCloseFilled v-else-if="icon === 'error'" />
          <QuestionFilled v-else />
        </el-icon>
      </div>
      <div class="confirm-dialog__content">
        <h3 class="confirm-dialog__title">{{ title }}</h3>
        <div v-if="message" class="confirm-dialog__message">{{ message }}</div>
        <div v-if="$slots.default" class="confirm-dialog__extra"><slot /></div>
      </div>
    </div>
    <template #footer>
      <div class="confirm-dialog__footer">
        <el-button v-if="showCancel" @click="onUpdate(false)">{{ cancelText }}</el-button>
        <el-button :type="type" :loading="loading" :disabled="disabled" @click="onConfirm">{{ confirmText }}</el-button>
      </div>
    </template>
  </el-dialog>
</template>

<script setup>
import { WarningFilled, CircleCheckFilled, CircleCloseFilled, QuestionFilled } from '@element-plus/icons-vue'

defineProps({
  modelValue: { type: Boolean, default: false },
  title: { type: String, default: '提示' },
  message: { type: String, default: '' },
  icon: { type: String, default: 'question' }, // question / warning / success / error
  type: { type: String, default: 'primary' }, // el-button type
  confirmText: { type: String, default: '确定' },
  cancelText: { type: String, default: '取消' },
  showCancel: { type: Boolean, default: true },
  loading: { type: Boolean, default: false },
  disabled: { type: Boolean, default: false },
  blockEscape: { type: Boolean, default: false },
})

const emit = defineEmits(['update:modelValue', 'confirm'])

function onUpdate(val) {
  emit('update:modelValue', val)
}

function onConfirm() {
  emit('confirm')
}
</script>

<style scoped lang="scss">
.confirm-dialog {
  display: flex;
  gap: var(--space-md);
  align-items: flex-start;
  padding: var(--space-sm) 0 var(--space-xs);

  &__icon {
    flex-shrink: 0;
    width: 40px;
    height: 40px;
    border-radius: var(--radius-full);
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 20px;

    &.is-question {
      background: var(--info-soft);
      color: var(--info);
    }
    &.is-warning {
      background: var(--warning-soft);
      color: var(--warning);
    }
    &.is-success {
      background: var(--success-soft);
      color: var(--success);
    }
    &.is-error {
      background: var(--danger-soft);
      color: var(--danger);
    }
  }

  &__content {
    flex: 1;
    min-width: 0;
  }

  &__title {
    margin: 0 0 var(--space-xs);
    font-size: var(--font-size-lg);
    font-weight: var(--font-weight-semibold);
    color: var(--text-primary);
    line-height: 1.4;
  }

  &__message {
    font-size: var(--font-size-base);
    color: var(--text-secondary);
    line-height: var(--line-height-relaxed);
    white-space: pre-line;
    word-break: break-word;
  }

  &__extra {
    margin-top: var(--space-md);
    padding: var(--space-sm) var(--space-md);
    background: var(--bg-tertiary);
    border: 1px solid var(--border);
    border-radius: var(--radius-md);
    font-size: var(--font-size-sm);
    color: var(--text-secondary);
    word-break: break-all;
  }

  &__footer {
    display: flex;
    justify-content: flex-end;
    gap: var(--space-sm);
    width: 100%;
  }
}
</style>
