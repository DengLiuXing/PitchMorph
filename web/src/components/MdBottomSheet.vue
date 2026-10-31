<script setup lang="ts">
defineProps<{ label: string }>()
const emit = defineEmits<{ (event: 'close'): void }>()
</script>

<template>
  <section class="md-sheet animate-sheet-in shrink-0" role="dialog" :aria-label="label">
    <div class="md-sheet-handle" aria-hidden="true" />

    <header class="flex items-center gap-2 px-4 pb-2 pt-1">
      <slot name="header" />
      <button
        type="button"
        class="md-icon-btn md-icon-btn--sm ml-auto"
        aria-label="关闭"
        title="关闭"
        @click="emit('close')"
      >
        <slot name="close-icon" />
      </button>
    </header>

    <div class="px-4 pb-4">
      <slot />
    </div>
  </section>
</template>
