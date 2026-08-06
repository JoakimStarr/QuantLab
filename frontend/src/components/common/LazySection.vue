<template>
  <div ref="el" class="lazy-section">
    <slot v-if="active" />
    <div v-else class="lazy-section__placeholder">
      <el-skeleton :rows="2" animated />
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, onBeforeUnmount } from 'vue'

const emit = defineEmits(['activate'])
const el = ref(null)
const active = ref(false)
let observer = null

onMounted(() => {
  if (typeof IntersectionObserver === 'undefined') {
    active.value = true
    emit('activate')
    return
  }
  observer = new IntersectionObserver(
    (entries) => {
      if (entries.some((e) => e.isIntersecting)) {
        active.value = true
        emit('activate')
        observer.disconnect()
        observer = null
      }
    },
    { rootMargin: '200px 0px' }
  )
  observer.observe(el.value)
})

onBeforeUnmount(() => {
  if (observer) observer.disconnect()
})
</script>

<style scoped lang="scss">
.lazy-section {
  width: 100%;
}
.lazy-section__placeholder {
  width: 100%;
}
</style>