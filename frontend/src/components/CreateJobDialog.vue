<script setup lang="ts">
/**
 * 创建任务弹窗：把「做什么」和「用什么参数」一次性问完。
 *
 * 参数一旦创建就定稿，所以要调参就得再开一次这个窗、再建一个任务——
 * 这里不做任何「保存后重跑」的暗示。
 */
import { computed, ref } from 'vue'

import type { JobParams, ModelList, Scope } from '@/api/types'
import TierPicker from '@/components/TierPicker.vue'

const props = defineProps<{
  files: File[]
  models: ModelList | null
  /** 从工作台派生分析任务时为真：范围固定 analyze，且没有文件 */
  derive?: boolean
  initialScope?: Scope
}>()

const emit = defineEmits<{
  (event: 'submit', payload: { scope: Scope; params: JobParams }): void
  (event: 'cancel'): void
}>()

const SCOPES: { id: Scope; label: string; hint: string }[] = [
  { id: 'both', label: '分离 + 分析', hint: '先分轨，再对人声轨提音高、切音符' },
  { id: 'separate', label: '只分离', hint: '只出四条轨，不做音高' },
  { id: 'analyze', label: '只分析', hint: '输入已经是人声干声，跳过分轨' },
]

// 干声文件名特征，与后端 config.DRY_MARKERS 同口径：这里先提示，后端还会再拦一次
const DRY_PATTERN = /(vocal|人声|干声|dry|acap)/i
const NAMES = computed(() => props.files.map((file) => file.name))

const scope = ref<Scope>(props.derive ? 'analyze' : (props.initialScope ?? 'both'))
const tier = ref(props.models?.default ?? 'vocals')

const f0Min = ref<number | null>(null)
const f0Max = ref<number | null>(null)
const threshold = ref<number | null>(null)
const gapMs = ref<number | null>(null)
const splitOnsets = ref(true)
const nsteps = ref<number | null>(null)
const segThreshold = ref<number | null>(null)
const segRadius = ref<number | null>(null)
const estThreshold = ref<number | null>(null)
const language = ref('')
const smooth = ref(3)

// GAME 体积。实测（188 s 素材，与参考对照）：small 96.7% / medium 97.8% / large 95.8%。
// large 的边界更细，而那条参考本身是 medium 时代的产出，所以「更差」是相对参考而言；
// 留着可选是为了自己试听比较，不要在文案里承诺它更好。
const GAME_SIZES = [
  { id: 'small', label: '标准', hint: '48 MB，音高一致 96.7%' },
  { id: 'medium', label: '高精度', hint: '190 MB，音高一致 97.8%，与参考同代' },
  { id: 'large', label: '实验', hint: '378 MB，音高一致 95.8%（边界更细），耗时与显存最高' },
]
const gameSize = ref('small')

const needsSeparate = computed(() => scope.value !== 'analyze')
const needsAnalysis = computed(() => scope.value !== 'separate')
const namesLookDry = computed(() => NAMES.value.every((name) => DRY_PATTERN.test(name)))
const dryProblem = computed(
  () => !props.derive && scope.value === 'analyze' && !namesLookDry.value,
)

function buildParams(): JobParams {
  const params: JobParams = {
    separate: { tier: tier.value },
    exports: { smooth: smooth.value },
  }
  if (needsAnalysis.value) {
    const pitch: NonNullable<JobParams['pitch']> = {}
    if (f0Min.value) pitch.f0_min = f0Min.value
    if (f0Max.value) pitch.f0_max = f0Max.value
    if (threshold.value) pitch.threshold = threshold.value
    if (gapMs.value !== null) pitch.gap_ms = gapMs.value
    if (!splitOnsets.value) pitch.onsets = false
    if (Object.keys(pitch).length > 0) params.pitch = pitch

    const notes: NonNullable<JobParams['notes']> = { model: gameSize.value }
    if (nsteps.value) notes.nsteps = nsteps.value
    if (segThreshold.value) notes.seg_threshold = segThreshold.value
    if (segRadius.value) notes.seg_radius = segRadius.value
    if (estThreshold.value) notes.est_threshold = estThreshold.value
    if (language.value) notes.language = language.value
    params.notes = notes
  }
  return params
}

function submit(): void {
  if (dryProblem.value) return
  emit('submit', { scope: scope.value, params: buildParams() })
}
</script>

<template>
  <div class="fixed inset-0 z-30 flex items-start justify-center overflow-y-auto bg-black/60 p-6 backdrop-blur-sm">
    <div class="panel mt-8 w-full max-w-2xl px-5 py-4">
      <div class="flex items-baseline gap-2">
        <h2 class="text-[15px] font-medium text-chalk">
          {{ derive ? '新建分析任务' : '创建任务' }}
        </h2>
        <span class="truncate text-[11.5px] text-chalk-mute" :title="NAMES.join('、')">
          {{ NAMES.join('、') }}
        </span>
        <button
          type="button"
          class="ml-auto cursor-pointer text-[11.5px] text-chalk-mute hover:text-chalk"
          @click="emit('cancel')"
        >
          取消
        </button>
      </div>

      <div v-if="!derive" class="mt-4 flex flex-wrap items-center gap-2">
        <button
          v-for="item in SCOPES"
          :key="item.id"
          type="button"
          class="btn !text-[11.5px]"
          :class="scope === item.id ? 'btn-primary' : ''"
          :title="item.hint"
          @click="scope = item.id"
        >
          {{ item.label }}
        </button>
        <span class="text-[11px] text-chalk-mute">
          {{ SCOPES.find((item) => item.id === scope)?.hint }}
        </span>
      </div>

      <div
        v-if="dryProblem"
        class="mt-3 border border-amber/40 bg-amber/[0.06] px-3 py-2 text-[11.5px] leading-relaxed text-amber-bright"
      >
        「只分析」要的是人声干声，这个文件名看不出是干声。混音上的伴奏会干扰基频，
        得到的多半是假音符——改选「分离 + 分析」，或先分离出人声再来分析。
      </div>

      <TierPicker v-if="needsSeparate" v-model="tier" :models="models" class="mt-4" />

      <div v-if="needsAnalysis" class="mt-4 flex flex-col gap-3">
        <span class="text-[11px] uppercase tracking-[0.16em] text-chalk-mute">音高与音符</span>
        <div class="grid grid-cols-2 gap-3 text-[11.5px] text-chalk-dim sm:grid-cols-3">
          <label class="flex flex-col gap-1">
            <span>最低音 (Hz)</span>
            <input v-model.number="f0Min" type="number" min="40" max="1000" placeholder="80" class="field" />
          </label>
          <label class="flex flex-col gap-1">
            <span>最高音 (Hz)</span>
            <input v-model.number="f0Max" type="number" min="100" max="2000" placeholder="1100" class="field" />
          </label>
          <label class="flex flex-col gap-1">
            <span>浊音阈值</span>
            <input v-model.number="threshold" type="number" step="0.001" min="0.0005" placeholder="0.006" class="field" />
          </label>
          <label class="flex flex-col gap-1">
            <span>补洞 (ms)</span>
            <input v-model.number="gapMs" type="number" step="10" min="0" max="400" placeholder="80" class="field" />
          </label>
          <label class="flex flex-col gap-1">
            <span>音符模型</span>
            <select v-model="gameSize" class="field">
              <option v-for="item in GAME_SIZES" :key="item.id" :value="item.id" :title="item.hint">
                {{ item.label }}（{{ item.id }}）
              </option>
            </select>
            <span class="text-[10px] text-chalk-mute">
              {{ GAME_SIZES.find((item) => item.id === gameSize)?.hint }}
            </span>
          </label>
          <label class="flex flex-col gap-1">
            <span>GAME 步数</span>
            <select v-model.number="nsteps" class="field">
              <option :value="null">默认 (8)</option>
              <option :value="4">4（约省 3 s）</option>
              <option :value="2">2（会把音符切碎）</option>
            </select>
          </label>
          <label class="flex flex-col gap-1">
            <span>边界阈值</span>
            <input
              v-model.number="segThreshold"
              type="number"
              step="0.05"
              min="0.01"
              max="0.99"
              placeholder="0.2"
              class="field"
            />
            <span class="text-[10px] text-chalk-mute">越小越容易切（更碎）</span>
          </label>
          <label class="flex flex-col gap-1">
            <span>边界半径 (s)</span>
            <input
              v-model.number="segRadius"
              type="number"
              step="0.01"
              min="0.01"
              max="0.2"
              placeholder="0.02"
              class="field"
            />
            <span class="text-[10px] text-chalk-mute">越大切点越少</span>
          </label>
          <label class="flex flex-col gap-1">
            <span>存在阈值</span>
            <input
              v-model.number="estThreshold"
              type="number"
              step="0.05"
              min="0.01"
              max="0.99"
              placeholder="0.2"
              class="field"
            />
            <span class="text-[10px] text-chalk-mute">
              低 = 保留弱音；0.5 起更容易把气口判成休止
            </span>
          </label>
          <label class="flex flex-col gap-1">
            <span>语言</span>
            <select v-model="language" class="field">
              <option value="">自动</option>
              <option value="zh">中文</option>
              <option value="ja">日语</option>
              <option value="en">英语</option>
              <option value="yue">粤语</option>
            </select>
          </label>
          <label class="flex items-center gap-2 pt-5">
            <input v-model="splitOnsets" type="checkbox" class="accent-amber" />
            <span>按起音切分音符</span>
          </label>
        </div>
      </div>

      <div class="mt-4 flex flex-col gap-2">
        <span class="text-[11px] uppercase tracking-[0.16em] text-chalk-mute">导出曲线平滑</span>
        <div class="flex items-center gap-2">
          <div class="flex overflow-hidden rounded border border-white/[0.08]">
            <button
              v-for="option in [1, 3, 5, 9]"
              :key="option"
              type="button"
              class="cursor-pointer px-2 py-0.5 text-[10.5px] transition-colors duration-150"
              :class="
                smooth === option
                  ? 'bg-amber/15 text-amber-bright'
                  : 'text-chalk-mute hover:text-chalk'
              "
              :title="option === 1 ? '不做中值滤波' : `中值滤波 ${option} 帧（${option * 10} ms）`"
              @click="smooth = option"
            >
              {{ option === 1 ? '关' : `${option} 帧` }}
            </button>
          </div>
          <span class="text-[11px] text-chalk-mute">
            影响弯音 MIDI 与 OpenUtau 的 pitd 曲线；音符本身的音高来自 GAME 模型
          </span>
        </div>
      </div>

      <div class="mt-5 flex items-center justify-end gap-2">
        <button type="button" class="btn" @click="emit('cancel')">取消</button>
        <button
          type="button"
          class="btn btn-primary"
          :disabled="dryProblem"
          :class="{ 'opacity-50': dryProblem }"
          @click="submit"
        >
          创建任务
        </button>
      </div>
    </div>
  </div>
</template>
