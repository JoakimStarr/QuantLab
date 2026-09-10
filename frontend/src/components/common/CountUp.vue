<template>
  <span class="count-up">{{ prefix }}{{ display }}{{ suffix }}</span>
</template>

<script setup>
// 数字滚动组件：requestAnimationFrame + easeOutExpo 从旧值缓动到新值。
// prefers-reduced-motion 时跳过动画直接显示终值（全局 CSS 兜底无法关闭 JS 驱动的逐帧更新，
// 因此组件内部必须自行判断）。
import { ref, watch, onMounted, onBeforeUnmount } from 'vue'

const props = defineProps({
  value: { type: Number, default: 0 },
  duration: { type: Number, default: 800 }, // ms，接近 --duration-slower
  decimals: { type: Number, default: 0 },
  prefix: { type: String, default: '' },
  suffix: { type: String, default: '' },
})

const display = ref('0')
let rafId = null
let current = 0

const reducedMotion = () =>
  typeof window.matchMedia === 'function' && window.matchMedia('(prefers-reduced-motion: reduce)').matches

const easeOutExpo = (t) => (t >= 1 ? 1 : 1 - Math.pow(2, -10 * t))

function format(n) {
  return n.toLocaleString('zh-CN', {
    minimumFractionDigits: props.decimals,
    maximumFractionDigits: props.decimals,
  })
}

function animateTo(target) {
  cancelAnimationFrame(rafId)
  const from = current
  if (reducedMotion() || props.duration <= 0 || from === target) {
    current = target
    display.value = format(target)
    return
  }
  const start = performance.now()
  const step = (now) => {
    const t = Math.min((now - start) / props.duration, 1)
    current = from + (target - from) * easeOutExpo(t)
    display.value = format(current)
    if (t < 1) {
      rafId = requestAnimationFrame(step)
    } else {
      current = target
      display.value = format(target)
    }
  }
  rafId = requestAnimationFrame(step)
}

watch(
  () => props.value,
  (val) => {
    if (typeof val === 'number' && Number.isFinite(val)) animateTo(val)
  }
)

onMounted(() => {
  animateTo(typeof props.value === 'number' ? props.value : 0)
})

onBeforeUnmount(() => cancelAnimationFrame(rafId))
</script>
