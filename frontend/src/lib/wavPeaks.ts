/**
 * 直接解析 PCM-16 RIFF/WAV 字节算峰值。
 *
 * 不用 decodeAudioData：那会额外持有一份 float32 AudioBuffer
 * （3 分钟立体声约 63 MB/轨），只为画一张静态图。
 *
 * 与 v1 的差异（v1 实测到的三个毛病）：
 * 1. 桶数多于帧数时，v1 用整数 step 会留下读不到帧的尾部空桶 → 这里用浮点步进
 * 2. 只认 bits === 16，其他位深显式返回 null，不假装成功
 * 3. 环绕声道顺序无关，逐声道取绝对最大值
 */
export function wavPeaks(buffer: ArrayBuffer, buckets: number): Float32Array | null {
  if (buffer.byteLength < 44) return null

  const view = new DataView(buffer)
  if (view.getUint32(0, false) !== 0x52494646) return null // "RIFF"

  let offset = 12
  let channels = 0
  let bits = 0
  let dataStart = 0
  let dataLength = 0

  while (offset + 8 <= buffer.byteLength) {
    const id = view.getUint32(offset, false) // "fmt " / "data"
    const size = view.getUint32(offset + 4, true)
    const body = offset + 8

    if (id === 0x666d7420) {
      channels = view.getUint16(body + 2, true)
      bits = view.getUint16(body + 14, true)
    } else if (id === 0x64617461) {
      dataStart = body
      dataLength = Math.min(size, buffer.byteLength - body)
      break
    }
    // chunk 是偶数对齐的，奇数长度要补一个填充字节
    offset = body + size + (size % 2)
  }

  if (!channels || bits !== 16 || dataLength <= 0) return null

  const frames = Math.floor(dataLength / (2 * channels))
  if (frames <= 0) return null

  const peaks = new Float32Array(buckets)
  const perBucket = frames / buckets

  for (let bucket = 0; bucket < buckets; bucket += 1) {
    const from = Math.floor(bucket * perBucket)
    const to = Math.max(from + 1, Math.floor((bucket + 1) * perBucket))
    let peak = 0

    for (let frame = from; frame < to && frame < frames; frame += 1) {
      const base = dataStart + frame * channels * 2
      for (let channel = 0; channel < channels; channel += 1) {
        const sample = view.getInt16(base + channel * 2, true)
        const magnitude = sample < 0 ? -sample : sample
        if (magnitude > peak) peak = magnitude
      }
    }
    peaks[bucket] = peak / 32768
  }

  return peaks
}

/** 取回一个 wav 并算峰值；失败返回 null，调用方画一条占位直线即可。 */
export async function fetchPeaks(url: string, buckets: number): Promise<Float32Array | null> {
  const response = await fetch(url)
  if (!response.ok) return null
  return wavPeaks(await response.arrayBuffer(), buckets)
}
