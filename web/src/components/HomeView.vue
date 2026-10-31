<script setup lang="ts">
import { AudioLines, Inbox } from 'lucide-vue-next'
import { ref } from 'vue'

import type { JobMeta, Mode } from '@/api/types'
import { MODE_LABELS } from '@/api/types'
import JobCard from '@/components/JobCard.vue'
import MdSegmented from '@/components/MdSegmented.vue'
import UploadCard from '@/components/UploadCard.vue'

const props = defineProps<{ jobs: JobMeta[]; uploading: number }>()
const emit = defineEmits<{
  (event: 'upload', payload: { files: File[]; mode: Mode }): void
  (event: 'open', job: JobMeta): void
  (event: 'cancel', job: JobMeta): void
  (event: 'remove', job: JobMeta): void
}>()

const mode = ref<Mode>('vocals')
const MODES = [
  { value: 'vocals', label: MODE_LABELS.vocals },
  { value: 'fast', label: MODE_LABELS.fast },
  { value: 'quality', label: MODE_LABELS.quality },
]
const MODE_HINTS: Record<string, string> = {
  vocals: '只出 1 条人声轨，1 次前向，最快',
  fast: '出 4 条轨，1 次前向出，最快的四轨',
  quality: '出 4 条轨，4 次前向，分离最干净',
}

function pick(files: File[]): void {
  emit('upload', { files, mode: mode.value })
}
</script>

<template>
  <div class="mx-auto flex w-full max-w-[840px] flex-col gap-6 px-6 py-8">
    <header class="animate-rise-in">
      <h2 class="md-headline-small text-on-surface">本地分轨，然后逐轨看音高</h2>
      <p class="md-body-medium mt-2 max-w-[560px] text-on-surface-variant">
        一次只跑一个任务，按提交顺序排队。分轨完成后进入工作台：多轨共用一条时间轴，
        人声轨的音高在底部监视器里以 10 秒滑动窗口跟随播放头。
      </p>
    </header>

    <section class="flex flex-wrap items-center gap-3">
      <span class="md-label-large text-on-surface-variant">分离档位</span>
      <MdSegmented v-model="mode" :options="MODES" label="分离档位" />
      <span class="md-label-medium md-num text-on-surface-variant">
        {{ MODE_HINTS[mode] }}
      </span>
    </section>

    <UploadCard :busy="props.uploading > 0" @pick="pick" />

    <section class="flex flex-col gap-3">
      <div class="flex items-center gap-2">
        <AudioLines :size="16" class="text-on-surface-variant" />
        <h3 class="md-title-small text-on-surface">任务队列</h3>
        <span class="md-label-medium md-num text-on-surface-variant">{{ props.jobs.length }}</span>
        <span
          v-if="props.uploading > 0"
          class="md-label-small md-num ml-auto text-primary"
        >
          上传中 {{ props.uploading }}
        </span>
      </div>

      <div v-if="props.jobs.length === 0" class="flex flex-col items-center gap-2 rounded-md-xl border border-outline-variant px-6 py-12">
        <Inbox :size="28" class="text-on-surface-variant" />
        <p class="md-body-medium text-on-surface-variant">队列是空的</p>
        <p class="md-label-medium text-outline">把音频拖到上面的卡片里就开始处理</p>
      </div>

      <div v-else class="flex flex-col gap-2">
        <JobCard
          v-for="job in props.jobs"
          :key="job.id"
          :job="job"
          @open="emit('open', $event)"
          @cancel="emit('cancel', $event)"
          @remove="emit('remove', $event)"
        />
      </div>
    </section>

    <p class="md-label-small pt-2 text-center text-outline">
      音频与产物都保存在本机 data/jobs，不经过任何外部服务
    </p>
  </div>
</template>
