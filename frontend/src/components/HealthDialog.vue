<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'

import type { Health } from '@/api/types'

const props = defineProps<{
  open: boolean
  health: Health | null
}>()

const emit = defineEmits<{ (event: 'close'): void }>()

const dialog = ref<HTMLDialogElement | null>(null)

watch(
  () => props.open,
  async (open) => {
    await nextTick()
    const element = dialog.value
    if (!element) return
    if (open && !element.open) element.showModal()
    if (!open && element.open) element.close()
  },
)

const ffmpegSource = computed(() => {
  const map: Record<string, string> = {
    env: '（PITCHMORPH_FFMPEG 指定）',
    path: '（系统 PATH）',
    bundled: '（内置）',
  }
  return map[props.health?.ffmpeg_source ?? ''] ?? ''
})

const cudaOptions = computed(() => {
  const options = props.health?.cuda_options
  if (!options || Object.keys(options).length === 0) return '默认（CPU 路径）'
  // Full names on purpose: these are the knobs that decide whether a session
  // fits in VRAM, and the health sheet is where you go to confirm they applied.
  return Object.entries(options)
    .map(([key, value]) => `${key}=${value}`)
    .join(' · ')
})

const rows = computed(() => {
  const data = props.health
  if (!data) return []
  const gpu = data.gpu_ready ? 'CUDA 可用' : (data.gpu_reason ?? '未启用')
  return [
    ['Python', data.python],
    ['ONNX Runtime', data.onnxruntime],
    ['GPU 状态', gpu],
    ['可用执行器', (data.available_providers || []).join(', ')],
    ['当前执行器', (data.active_providers || []).join(', ')],
    ['GPU 选项', cudaOptions.value],
    ['并行度', `${data.default_parallel ?? 1} / 上限 ${data.max_parallel ?? 4}`],
    ['ffmpeg', data.ffmpeg_version ? `${data.ffmpeg_version} ${ffmpegSource.value}` : '不可用'],
    ['权重目录', data.model_dir],
    ['支持格式', (data.supported_exts || []).join(' · ')],
  ] as [string, string][]
})

const note = computed(() => {
  if (!props.health) return ''
  if (props.health.gpu_ready) return ''
  return '当前在 CPU 上推理。运行 uv run python scripts/setup.py --apply 可安装 GPU 版执行器。'
})
</script>

<template>
  <dialog ref="dialog" class="sheet" @close="emit('close')">
    <form method="dialog" class="sheet-inner">
      <header class="sheet-head">
        <h3>环境自检</h3>
        <button class="icon-btn" value="close" aria-label="关闭">
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <path d="M6 6l12 12M18 6L6 18" />
          </svg>
        </button>
      </header>
      <dl class="sheet-list">
        <template v-for="[label, value] in rows" :key="label">
          <dt>{{ label }}</dt>
          <dd>{{ value }}</dd>
        </template>
      </dl>
      <p class="sheet-note">{{ note }}</p>
    </form>
  </dialog>
</template>
