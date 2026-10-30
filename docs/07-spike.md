# 07 · V3 spike 记录（2026-10-30）

> 一次性验证，代码在 `temp-dev/spike/`（不入库）。目标：回答「ONNX + DirectML 这条路能不能走」。
> 状态：**FCPE 已测完，结论不利；GAME / demucs 因权重未就位，未测。**

## 1. 环境

| 项 | 值 |
|---|---|
| 隔离环境 | `temp-dev/spike/.venv`，Python 3.12 |
| ONNX Runtime | `onnxruntime-directml==1.24.4`（DML EP 的最新版，见 `docs/06-v3.md` 5.7.1） |
| 导出侧 | 主环境 torch 2.11.0+cu128（仅用于导出与参考值，不参与 V3 运行时） |
| 机器 | RTX 5060 Laptop，驱动 610.62 |

## 2. FCPE 导出

**结论：能导出，且 CPU 上与原 PyTorch 数值等价。**

- 切点：只导网络（`mel → f0`），mel 前处理放在图外。
  原因：`torchfcpe.mel_extractor` 里 `pad_right < y.size(-1)` 是数据相关分支，
  TorchScript 导出成 `onnx::If` 后直接失败；而 V3 的前处理本来就要用 C#/Rust 重写。
- 产物：`fcpe_net.onnx` 41.3 MB（opset 17）/ 同名 opset 19 版本各一份。
- 输入固定 `(1, 501, 128)`（5 s @16 kHz，hop 160 → 500 帧 + 1）。
- mel 等价性：自实现（reflect pad → hann → rfft → mel 投影 → log）与 `wav2mel` 的
  max abs diff **3.1e-04**，帧数 500 vs 501（少一帧，属 padding 边界，可接受）。
- f0 等价性（CPU EP）：

| 指标 | 值 |
|---|---|
| RPA（±50 音分） | **100%** |
| 音分误差 median / p99 | 0.0006 / 0.0018 |
| OS 侧耗时 | 24.2 ms / 5 s 音频 |

- **一个必须处理的差异**：ONNX 图在 31–32 个「无音高」帧输出 `NaN`，而 PyTorch 输出 `0.0`。
  宿主侧一律 `nan → 0`（本来就按 `f0 < f0_min` 判无声），不影响有声帧。

## 3. DirectML（核心问题）

| EP | 建 session | 单次耗时 | RPA | 音分误差 median | p99 |
|---|---|---|---|---|---|
| CPU | ✅ 0.07 s | 24.2 ms | **100%** | 0.0006 | 0.0018 |
| **DirectML** | ✅ 1.83 s | **2.7 ms** | **25.1%** | **165** | **34082** |

- DML 确实**跑起来了**（provider 列表含 `DmlExecutionProvider`，无报错、无算子回落警告），
  速度是 CPU 的 **9 倍**；
- 但**结果是错的**：中位偏差 165 音分（约 1.65 个半音），p99 是彻底乱掉。
- 换 opset 17 / 19 结果完全一致（RPA 都是 25.1%），所以不是导出器的问题。

**判断**：在 `onnxruntime-directml 1.24.4` 上，这个图**数值不可用**。
最可能的原因（待验证）是 DML EP 对解码段（`argmax/gather/onehot/reduce`）或注意力里的
softmax/div 走了 fp16 或近似实现；也可能只是该 EP 对这类通道布局（NCHW↔NHWC 转置）的实现缺陷。

**下一步定位（成本很低）**：把图再切一刀——只导「mel → logits」走 DML，解码（几十行、纯索引运算）回 CPU。
如果 logits 一致而解码不一致，这条架构仍然可行；如果 logits 就已经偏，DML 就出局。

## 3.1 定位：偏差来自主干，不是解码

把图再切一刀，只导主干（`mel → latent`，输出 `(1,501,360)`），两个 EP 各跑一遍：

| EP | 单次耗时 | 与 PyTorch latent 的最大差 | 均值 |
|---|---|---|---|
| CPU | 24.4 ms | **0.0000** | 0.000000 |
| **DirectML** | **3.1 ms** | **0.9996** | 0.0951 |

结论：**解码是无辜的**，DML 是在主干上就偏了。解码那 31 个 `NaN` 只是 `-inf` 掩码
（`confident_mask = -inf`）在 ONNX 里的副作用，宿主侧 `nan → 0` 可解决，与 DML 无关。

推测原因（**不再追查**，见 D19）：DML EP 对 ConvNeXt 这类结构在内部走了 fp16 或近似实现，
而 GAME 的 EBF 主干没有触发同样的路径。用户判断「FCPE 走 CPU 只花 0.9 s/首歌」，
不值得为这个偏差改图或锁精度，因此根因到此为止。

## 4. GAME（medium, 1.0.3 ONNX）—— DML 完全正确

同一段 12 s 音频、同一套参数，两个 EP 各跑完整链路（encoder → segmenter×8 → bd2dur → estimator）：

| 指标 | CPU | DirectML |
|---|---|---|
| 实际 provider | CPU | Dml + CPU |
| encoder | 0.52 s | 0.81 s |
| segmenter × 8 | 1.69 s | **0.67 s** |
| tail | 0.38 s | 0.64 s |
| 有声音符 | 49 | 49 |
| **边界不一致帧** | — | **0 / 1201（0.00%）** |
| presence 不一致 | — | **0** |
| 音高差 | — | median 0.00 · p99 **0.33 Hz** · max 0.68 Hz |

即 GAME 在 DML 上**数值等价**（0.33 Hz ≈ 0.02 音分），segmenter 环节快 2.5×；
encoder 首轮更慢是因为包含 DML 图编译。热身后 12 s 音频总耗时约 0.83 s。

## 5. 未测部分

| 项 | 原因 |
|---|---|
| demucs 4 轨 / 6 轨（fp16 权重版 157.9 / 130.1 MB） | 按约定跳过；下次只需验「conv+STFT」这一类图在 DML 上是否同样精确 |

## 6. 结论：DML 可以用，但要按模型分开

1. **「统一 ONNX + 免 PyTorch」成立**：GAME 与 FCPE 都能出 ONNX，CPU 上数值与原件等价。
2. **DirectML 对 GAME 可用**（数值等价、segmenter 快 2.5×），**对 FCPE 不可用**（主干偏差 0.1 量级）。
3. FCPE 走 CPU 完全不痛：5 s 音频 **24 ms**（约 200× 实时），3 分钟歌约 **0.9 s**。
   所以 V3 的运行时矩阵可以是：

| 阶段 | 引擎 | 依据 |
|---|---|---|
| 分轨（demucs，待测） | DML 优先 → CPU 兜底 | 同类卷积图，GAME 已验证 DML 可用 |
| GAME（音符） | DML 优先 → CPU 兜底 | 实测数值等价 |
| FCPE（音高） | **CPU（或 CUDA 若可用）** | DML 上主干偏差不可接受；CPU 已足够快 |

4. Windows ML 仍然值得押（系统管 EP、可自动拉 CUDA），但它要求 WinRT 生态；
   而上面这张矩阵说明**即使只用 DML + CPU 也能交付**，WinML 从「必需」降级为「加分项」。
5. FCPE 的偏差根因**不再追查**（D19）：CPU 只花 0.9 s/首，收益不抵成本。

## 7. 剩余未验（唯一一项）

**demucs 的两张图在 DML 上是否同样精确**——这是矩阵里唯一还靠推测的格子
（「同类卷积图，GAME 已验证」）。验证需要下载 fp16 权重版共约 288 MB：
`htdemucs_fp16weights.onnx`（157.9 MB）与 `htdemucs_6s_fp16weights.onnx`（130.1 MB）。
也可以等 V3 代码成型、接入时顺手验，不必现在下。
