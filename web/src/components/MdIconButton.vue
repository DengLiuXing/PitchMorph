<script setup lang="ts">
withDefaults(
  defineProps<{
    label: string
    variant?: 'standard' | 'tonal'
    small?: boolean
    pressed?: boolean
    disabled?: boolean
    /** 给 M / S 这类按钮挂语义，配色由 md3.css 决定 */
    act?: string
  }>(),
  { variant: 'standard', small: false },
)
</script>

<template>
  <button
    type="button"
    class="md-icon-btn"
    :class="{ 'md-icon-btn--tonal': variant === 'tonal', 'md-icon-btn--sm': small }"
    :aria-label="label"
    :title="label"
    :aria-pressed="pressed"
    :disabled="disabled"
    :data-act="act"
  >
    <slot />
  </button>
</template>
