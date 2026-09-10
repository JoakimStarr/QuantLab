// v-stagger：容器内子元素按 index 依次入场（fade-up，默认间隔 30ms）。
// 用法：<div v-stagger>...</div> 或 <div v-stagger="{ step: 50, selector: ':scope > .item' }">
// 原理：mounted 时给每个子元素设置 inline animation（staggerFadeUp keyframes +
// index * step 的 delay，fill: both 保证延迟期内保持初始态不闪现）。
// prefers-reduced-motion 时不注入任何动画（直接静态展示）。
const DEFAULTS = { step: 30, selector: ':scope > *' }

function reducedMotion() {
  return typeof window.matchMedia === 'function' && window.matchMedia('(prefers-reduced-motion: reduce)').matches
}

export default {
  mounted(el, binding) {
    if (reducedMotion()) return
    const { step, selector } = { ...DEFAULTS, ...(binding.value || {}) }
    let children
    try {
      children = el.querySelectorAll(selector)
    } catch {
      children = el.querySelectorAll(':scope > *')
    }
    children.forEach((child, i) => {
      child.style.animation = `staggerFadeUp var(--duration-slow) var(--ease-out-expo) ${i * step}ms both`
    })
  },
  unmounted(el) {
    // 清理 inline 动画，避免 keep-alive 复用后重复触发
    el.querySelectorAll?.('[style*="staggerFadeUp"]').forEach((child) => {
      child.style.animation = ''
    })
  },
}
