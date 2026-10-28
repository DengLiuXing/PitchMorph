<script setup lang="ts">
const props = defineProps<{
  modelValue: number
  max: number
  /** False when the tier has one specialist or a single-file graph. */
  available: boolean
  /** Why the control is disabled; ignored when `available`. */
  reason: string
}>()

defineEmits<{ (event: 'update:modelValue', value: number): void }>()

const risky = (): boolean => props.available && props.modelValue > 1
</script>

<template>
  <div class="parallel-row">
    <span class="parallel-title">
      并发专家
      <em>实验性</em>
    </span>

    <div class="segmented parallel-seg" role="radiogroup" aria-label="并发专家数">
      <button
        v-for="n in max"
        :key="n"
        type="button"
        class="seg"
        role="radio"
        :aria-checked="n === modelValue"
        :disabled="!available"
        :title="available ? `${n} 个专家同时推理` : reason"
        @click="$emit('update:modelValue', n)"
      >
        {{ n }}
      </button>
    </div>

    <p class="parallel-note" :data-risk="risky() ? 'true' : undefined">
      <template v-if="!available">{{ reason }}</template>
      <template v-else-if="modelValue > 1">
        一个专家就能跑满 GPU 算力，多个专家不会更快，只会成倍占用显存；
        显存不足时任务会失败。建议改回 1。
      </template>
      <template v-else>一个专家就能跑满 GPU 算力。</template>
    </p>
  </div>
</template>
