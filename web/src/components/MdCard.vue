<script setup lang="ts">
/* 完整字面量，别拼字符串：Tailwind 扫不到的类会被当未使用样式裁掉 */
const VARIANTS = {
  filled: 'md-card--filled',
  outlined: 'md-card--outlined',
} as const

withDefaults(
  defineProps<{
    variant?: 'filled' | 'outlined'
    interactive?: boolean
  }>(),
  { variant: 'filled', interactive: false },
)
</script>

<template>
  <div
    class="md-card"
    :class="[VARIANTS[variant], { 'md-card--interactive': interactive }]"
  >
    <slot />
  </div>
</template>
