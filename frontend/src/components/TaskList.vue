<script setup lang="ts">
import { AlertCircle, CheckCircle2, Loader2, RotateCcw, Trash2, XCircle } from 'lucide-vue-next'
import { computed } from 'vue'

import type { Job } from '@/api/types'
import { isActive } from '@/api/types'
import { STAGE_TEXT, formatTime } from '@/lib/format'

const props = defineProps<{ jobs: Job[] }>()

const emit = defineEmits<{
  (event: 'open', job: Job): void
  (event: 'cancel', job: Job): void
  (event: 'retry', job: Job): void
  (event: 'rerun', job: Job): void
  (event: 'remove', job: Job): void
}>()

const groups = computed(() => [
  { key: 'active', title: '进行中', items: props.jobs.filter((job) => isActive(job.stage)) },
  { key: 'done', title: '已完成', items: props.jobs.filter((job) => job.stage === 'done') },
  {
    key: 'stopped',
    title: '失败与取消',
    items: props.jobs.filter((job) => job.stage === 'failed' || job.stage === 'canceled'),
  },
])
</script>

<template>
  <div class="flex flex-col gap-7">
    <div v-for="group in groups" :key="group.key">
      <div class="mb-2.5 flex items-center gap-2">
        <span class="text-[11px] uppercase tracking-[0.16em] text-chalk-mute">
          {{ group.title }}
        </span>
        <span class="num text-[11px] text-chalk-mute">{{ group.items.length }}</span>
        <span class="h-px flex-1 bg-white/[0.06]" />
      </div>

      <p v-if="group.items.length === 0" class="py-2 text-[11px] text-chalk-mute">
        暂无任务
      </p>

      <ul class="flex flex-col gap-2">
        <li
          v-for="job in group.items"
          :key="job.id"
          class="panel flex flex-col gap-2.5 px-4 py-3 animate-rise"
        >
          <div class="flex items-center gap-3">
            <component
              :is="
                job.stage === 'done'
                  ? CheckCircle2
                  : job.stage === 'failed'
                    ? AlertCircle
                    : job.stage === 'canceled'
                      ? XCircle
                      : Loader2
              "
              :size="14"
              :class="[
                job.stage === 'done' ? 'text-teal' : '',
                job.stage === 'failed' ? 'text-red-400' : '',
                isActive(job.stage) ? 'animate-spin text-amber' : 'text-chalk-mute',
              ]"
            />
            <span class="min-w-0 flex-1 truncate text-[12.5px] text-chalk" :title="job.filename">
              {{ job.filename }}
            </span>
            <span class="chip shrink-0">{{ job.tier === 'quality' ? '质量' : '快速' }}</span>
            <span v-if="job.duration_sec" class="num shrink-0 text-[11px] text-chalk-mute">
              {{ formatTime(job.duration_sec) }}
            </span>
          </div>

          <div v-if="isActive(job.stage)" class="flex items-center gap-3">
            <div class="h-[3px] flex-1 bg-white/[0.06]">
              <div
                class="h-full bg-amber transition-[width] duration-300"
                :style="{ width: `${job.progress}%` }"
              />
            </div>
            <span class="num w-9 text-right text-[11px] text-amber">{{ job.progress }}%</span>
            <span class="w-28 truncate text-[11px] text-chalk-mute">
              {{ job.detail || STAGE_TEXT[job.stage] }}
            </span>
          </div>

          <div v-else class="flex items-center justify-between gap-3">
            <span class="text-[11px] text-chalk-mute">
              <template v-if="job.stage === 'failed'">{{ job.error }}</template>
              <template v-else-if="job.stage === 'canceled'">已取消，产物已清理</template>
              <template v-else>
                用时 <span class="num">{{ job.elapsed_sec }}</span> s
                <span v-if="job.has_pitch" class="ml-2 text-teal">音高已提取</span>
              </template>
            </span>

            <div class="flex shrink-0 items-center gap-1.5">
              <button
                v-if="job.stage === 'done'"
                type="button"
                class="btn btn-primary cursor-pointer"
                @click="emit('open', job)"
              >
                打开工作台
              </button>
              <button
                v-if="job.has_stems && !isActive(job.stage)"
                type="button"
                class="btn cursor-pointer"
                title="用已有的 vocals.wav 重跑音高，不重新分离"
                @click="emit('rerun', job)"
              >
                重跑音高
              </button>
              <button
                v-if="isActive(job.stage)"
                type="button"
                class="btn cursor-pointer"
                @click="emit('cancel', job)"
              >
                取消
              </button>
              <button
                v-if="job.stage !== 'canceled' && !isActive(job.stage)"
                type="button"
                class="btn cursor-pointer"
                title="复用已解码的输入重跑，不需要重新上传"
                @click="emit('retry', job)"
              >
                <RotateCcw :size="12" />
                重试
              </button>
              <button
                v-if="!isActive(job.stage)"
                type="button"
                class="btn btn-ghost !px-1.5 cursor-pointer text-chalk-mute hover:text-red-400"
                title="删除任务与产物"
                @click="emit('remove', job)"
              >
                <Trash2 :size="13" />
              </button>
            </div>
          </div>
        </li>
      </ul>
    </div>
  </div>
</template>
