<template>
  <div class="page-container" :class="{ 'page-container--narrow': narrow }">
    <slot />
  </div>
</template>

<script setup>
defineProps({
  narrow: Boolean
})
</script>

<style scoped lang="scss">
.page-container {
  max-width: 1400px;
  margin: 0 auto;
  width: 100%;

  &--narrow {
    max-width: 1000px;
  }
}
</style>