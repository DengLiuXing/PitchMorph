<script setup lang="ts">
import { AlertTriangle, FolderOpen, Play, Trash2, X } from 'lucide-vue-next'
import { computed } from 'vue'

import type { JobMeta } from '@/api/types'
import { MODE_LABELS, STATE_LABELS, isActive } from '@/api/types'
import MdButton from '@/components/MdButton.vue'
import MdCard from '@/components/MdCard.vue'
import MdChip from '@/components/MdChip.vue'
import MdIconButton from '@/components/MdIconButton.vue'
import MdProgress from '@/components/MdProgress.vue'
import { formatClockStamp, formatSpan, formatTime } from '@/lib/format'

const props = defineProps<{ job: JobMeta }>()
const emit = defineEmits<{
  (event: 'open', job: JobMeta): void
  (event: 'cancel', job: JobMeta): void
  (event: 'remove', job: JobMeta): void
}>()

const running = computed(() => isActive(props.job.state))
const openable = computed(() => props.job.state === 'ready' || props.job.stems.length > 0)
const stems = computed(() => props.job.stems.map((stem) => stem.name))
const stateLabel = computed(() => STATE_LABELS[props.job.state] ?? props.job.state)
</script>

<template>
  <MdCard variant="filled" class="animate-rise-in px-4 py-3.5">
    <div class="flex items-start gap-4">
      <div class="min-w-0 flex-1">
        <div class="flex flex-wrap items-center gap-x-2 gap-y-1">
          <h3 class="md-title-small min-w-0 max-w-full truncate text-on-surface" :title="job.filename">
            {{ job.filename }}
          </h3>
          <MdChip variant="flat">{{ MODE_LABELS[job.mode] ?? job.mode }}</MdChip>
          <span
            class="md-label-medium"
            :class="job.state === 'failed' ? 'text-error' : job.state === 'ready' ? 'text-tertiary' : 'text-primary'"
          >
            {{ stateLabel }}
          </span>
        </div>

        <p class="md-label-medium md-num mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-on-surface-variant">
          <span>{{ formatClockStamp(job.created) }}</span>
          <span v-if="job.finished">耗时 {{ formatSpan(job.finished - job.created) }}</span>
          <span v-if="job.duration">{{ formatTime(job.duration, true) }}</span>
          <span v-if="stems.length">{{ stems.length }} 轨 · {{ stems.join(' / ') }}</span>
          <span v-if="job.ep">{{ job.ep }}</span>
        </p>

        <div v-if="running" class="mt-3 flex items-center gap-3">
          <MdProgress :value="job.fraction ?? 0" class="flex-1" />
          <span class="md-label-small md-num w-9 shrink-0 text-right text-on-surface-variant">
            {{ Math.round((job.fraction ?? 0) * 100) }}%
          </span>
        </div>
        <p v-if="running && job.message" class="md-body-small mt-1.5 truncate text-on-surface-variant">
          {{ job.message }}
        </p>

        <p
          v-if="job.state === 'failed'"
          class="md-body-small mt-2 flex items-start gap-1.5 text-error"
        >
          <AlertTriangle :size="14" class="mt-0.5 shrink-0" />
          <span>{{ job.failure }}</span>
        </p>
      </div>

      <div class="flex shrink-0 items-center gap-1">
        <MdButton v-if="openable" variant="tonal" compact @click="emit('open', job)">
          <template #icon>
            <FolderOpen :size="16" />
          </template>
          打开
        </MdButton>
        <MdButton v-if="running" variant="text" compact @click="emit('cancel', job)">
          <template #icon>
            <X :size="16" />
          </template>
          取消
        </MdButton>
        <MdIconButton v-else small label="删除任务与产物" @click="emit('remove', job)">
          <Trash2 :size="16" />
        </MdIconButton>
      </div>
    </div>

    <div v-if="openable && !running" class="mt-2 flex items-center gap-1.5 text-on-surface-variant">
      <Play :size="12" />
      <span class="md-label-small">打开后可多轨同步播放并查看音高</span>
    </div>
  </MdCard>
</template>
