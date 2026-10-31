<script setup lang="ts">
type Variant = 'filled' | 'tonal' | 'outlined' | 'text' | 'danger'

/* 类名必须是完整字面量：拼出来的 `md-btn--${variant}` 扫不到，会被当未使用样式裁掉 */
const VARIANTS: Record<Variant, string> = {
  filled: 'md-btn--filled',
  tonal: 'md-btn--tonal',
  outlined: 'md-btn--outlined',
  text: 'md-btn--text',
  danger: 'md-btn--danger',
}

withDefaults(
  defineProps<{
    variant?: Variant
    compact?: boolean
    disabled?: boolean
    type?: 'button' | 'submit'
    label?: string
  }>(),
  { variant: 'text', compact: false, disabled: false, type: 'button' },
)
</script>

<template>
  <button
    :type="type"
    class="md-btn"
    :class="[VARIANTS[variant], { 'md-btn--compact': compact }]"
    :disabled="disabled"
    :title="label"
  >
    <slot name="icon" />
    <slot />
  </button>
</template>
