import { onUnmounted, ref, shallowRef } from 'vue'

interface TrackNode {
  audio: HTMLAudioElement
  gain: GainNode
}

const DRIFT_LIMIT = 0.08 // 80 ms 以下听不出来，频繁改 currentTime 反而卡顿

/**
 * 多轨同步播放。
 *
 * 每轨一个 <audio>（天然支持 Range 请求与拖动定位），经 MediaElementSource
 * 接进同一个 AudioContext，再由各自的 GainNode 控制音量/静音/独奏。
 *
 * 主时钟是**第一条实际注册的轨**，不能写死 drums：「人声」档只有 vocals.wav，
 * 写死会让 play() 直接返回，按钮点了没反应。
 */
export function usePlayer() {
  const context = shallowRef<AudioContext | null>(null)
  const nodes = new Map<string, TrackNode>()
  const masterName = ref<string | null>(null)

  const playing = ref(false)
  const position = ref(0)
  const duration = ref(0)
  const volumes = ref<Record<string, number>>({})
  const muted = ref<string[]>([])
  // 多选 solo：DAW 里 solo 是每轨独立开关，点第二轨不该取消第一轨
  const solo = ref<string[]>([])

  let raf = 0

  function ensureContext(): AudioContext {
    if (!context.value) {
      context.value = new AudioContext()
    }
    return context.value
  }

  function master(): TrackNode | undefined {
    const name = masterName.value
    return name ? nodes.get(name) : undefined
  }

  function applyGains(): void {
    const soloing = solo.value.length > 0
    for (const [name, node] of nodes) {
      const silenced = soloing ? !solo.value.includes(name) : muted.value.includes(name)
      const level = volumes.value[name] ?? 0.9
      node.gain.gain.value = silenced ? 0 : level
    }
  }

  function attach(name: string, element: HTMLAudioElement): void {
    if (nodes.has(name)) return
    const ctx = ensureContext()
    const source = ctx.createMediaElementSource(element)
    const gain = ctx.createGain()
    source.connect(gain)
    gain.connect(ctx.destination)
    nodes.set(name, { audio: element, gain })
    if (volumes.value[name] === undefined) {
      volumes.value = { ...volumes.value, [name]: 0.9 }
    }
    if (!masterName.value) {
      masterName.value = name
      element.addEventListener('loadedmetadata', () => {
        duration.value = element.duration || 0
      })
      if (element.readyState >= 1) duration.value = element.duration || 0
    }
    applyGains()
  }

  function tick(): void {
    const lead = master()
    if (lead) {
      const now = lead.audio.currentTime
      for (const [name, node] of nodes) {
        if (name === masterName.value) continue
        if (Math.abs(node.audio.currentTime - now) > DRIFT_LIMIT) {
          node.audio.currentTime = now
        }
      }
      position.value = now
    }
    raf = requestAnimationFrame(tick)
  }

  async function play(): Promise<void> {
    const lead = master()
    if (!lead) return
    const ctx = ensureContext()
    // AudioContext 初始是 suspended，必须在用户手势里 resume，否则静默无声
    if (ctx.state === 'suspended') await ctx.resume()

    const at = lead.audio.currentTime
    const tracks = [...nodes.values()]
    for (const node of tracks) node.audio.currentTime = at
    await Promise.all(tracks.map((node) => node.audio.play()))
    playing.value = true
    cancelAnimationFrame(raf)
    raf = requestAnimationFrame(tick)
  }

  function pause(): void {
    for (const node of nodes.values()) node.audio.pause()
    playing.value = false
    cancelAnimationFrame(raf)
  }

  function toggle(): void {
    if (playing.value) pause()
    else void play()
  }

  function seek(seconds: number): void {
    const limit = duration.value || 0
    const clamped = limit > 0 ? Math.min(Math.max(0, seconds), limit) : Math.max(0, seconds)
    for (const node of nodes.values()) node.audio.currentTime = clamped
    position.value = clamped
  }

  function nudge(delta: number): void {
    seek(position.value + delta)
  }

  function toggleMute(name: string): void {
    muted.value = muted.value.includes(name)
      ? muted.value.filter((item) => item !== name)
      : [...muted.value, name]
    applyGains()
  }

  function toggleSolo(name: string): void {
    solo.value = solo.value.includes(name)
      ? solo.value.filter((item) => item !== name)
      : [...solo.value, name]
    applyGains()
  }

  function setVolume(name: string, value: number): void {
    volumes.value = { ...volumes.value, [name]: Math.min(1, Math.max(0, value)) }
    applyGains()
  }

  function reset(): void {
    pause()
    nodes.clear()
    masterName.value = null
    position.value = 0
    duration.value = 0
    volumes.value = {}
    muted.value = []
    solo.value = []
    void context.value?.close()
    context.value = null
  }

  onUnmounted(() => {
    cancelAnimationFrame(raf)
    void context.value?.close()
  })

  return {
    playing,
    position,
    duration,
    volumes,
    muted,
    solo,
    masterName,
    attach,
    play,
    pause,
    toggle,
    seek,
    nudge,
    toggleMute,
    toggleSolo,
    setVolume,
    reset,
  }
}
