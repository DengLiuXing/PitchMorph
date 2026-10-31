# 07 · V3 spike 记录（2026-10-30）

> 一次性验证，代码在 `temp-dev/spike/`（不入库）。该路径属于 V2 侧的临时目录；V3 仓库里的 `temp-dev/` 只放开发用资产（samples、OpenUtau 源码、调试模型，见 `08-v3-migration.md` §1.1）。目标：回答「ONNX + DirectML 这条路能不能走」。
> 状态：**FCPE 已测完，结论不利；GAME / demucs 因权重未就位，未测。**

## 1. 环境

| 项 | 值 |
|---|---|
| 隔离环境 | `temp-dev/spike/.venv`，Python 3.12 |
| ONNX Runtime | `onnxruntime-directml==1.24.4`（DML EP 的最新版，见 `doc_old/06-v3.md` 5.7.1） |
| 导出侧 | 主环境 torch 2.11.0+cu128（仅用于导出与参考值，不参与 V3 运行时） |
| 机器 | RTX 5060 Laptop，驱动 610.62 |

## 2. FCPE 导出

**结论：能导出，且 CPU 上与原 PyTorch 数值等价。**

- 切点：只导网络（`mel → f0`），mel 前处理放在图外。
  原因：`torchfcpe.mel_extractor` 里 `pad_right < y.size(-1)` 是数据相关分支，
  TorchScript 导出成 `onnx::If` 后直接失败；而 V3 的前处理本来就要在 Python 侧自实现（reflect pad → hann → rfft → mel 投影 → log）。
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
| demucs 4 轨 / 6 轨 | 按约定跳过；下次只需验「conv+STFT」这一类图在 DML 上是否同样精确。体积以 `docs/weights.md` §3.1 的实测表为准（单文件 4 轨 316 MB / fp16 166 MB；6s 258 MB） |

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

4. Windows ML 仍然值得押（系统管 EP、可自动拉 CUDA），而且**Python 里就能用**：
   微软已发官方包 `onnxruntime-windowsml`（1.30.0 / 2026-09-17，另有 1.28.3 / 2026-10-26；cp311–cp314、win_amd64/arm64）
   与 `windowsml`（2.5.96 / 2026-10-26，Development Status = Alpha），模块名仍是 `onnxruntime`。
   ~~原先「它要求 WinRT 生态、Python 里不能直接用」的说法有误，2026-10-31 更正。~~
   ~~但这不等于可用：三张图在 WinML 下的算子覆盖、数值与速度都还没测。~~
   **2026-10-31 已测（见 §8）：四条判据全过，定为首选运行时**；数值与 1.24.4 逐位一致，
   所以上面这张矩阵一句不改——WinML 只是换了 ORT 核心与加载方式，EP 分工照旧。
5. FCPE 的偏差根因**不再追查**（D19）：CPU 只花 0.9 s/首，收益不抵成本。

## 7. demucs 在 DML 上（2026-10-31 已验：不可用）

**结论：DML 在第一次前向就报错，demucs 只能走 CPU。** 不是数值偏差问题，是跑不起来。

- 制品：`StemSplitio/htdemucs-ft-vocals-onnx` 的 `fp16weights` 版，**165 612 636 B**（≈158 MiB；`weights.md` 写的 166 MB 是十进制）
- session 能建成（输入 `mix (1,2,343980)`，输出 `stems (1,4,2,343980)`），但 `run` 时在 `DmlGraphFusionHelper.cpp` 抛
  `Exception(2) tid(...) 8007000E`，即 **E_OUTOFMEMORY**：图融合阶段就分配不下
- 宿主侧看到的是 `UnicodeDecodeError: 'utf-8' codec can't decode byte 0xc4`——这是 WinML 版 ORT 的错误串编码缺陷（与 §8.1 那条 `Init provider bridge failed` 同源），**别被它误导成编码问题**
- 换 CPU 立即正常，输出无 NaN

| 档位 | EP | 12 s 音频耗时 | RTF |
|---|---|---|---|
| 仅人声（FT 单轨图） | CPU | 冷 9.07 s / 热 **4.73 s** | 0.394 |
| 四轨（FT bag，4 专家） | CPU | 冷 68.04 s / 热 **19.09 s** | 1.590 |
| 仅人声 | DML | ❌ E_OUTOFMEMORY | — |

**对性能目标的影响**（按 RTF 线性外推 3 min 歌）：

- 仅人声：180 × 0.394 ≈ **71 s**（原目标 ≤60 s，超）
- 四轨：180 × 1.590 ≈ **286 s**（原目标 ≤150 s，超近一倍）

怎么处理见 `docs/08-v3-migration.md` §1.2「性能目标」行——这是需要拍板的一处，不是实现细节。

## 8. WinML 路线（2026-10-31 已测，结论 Go）

**状态：已测完。** 判据见 `docs/08-v3-migration.md` §1.3。

### 8.1 安装与 EP

| 项 | 实测 |
|---|---|
| 环境 | 独立 venv，**Python 3.14.7**（官方文档写 3.10–3.13，cp314 wheel 实际存在且可用） |
| 装了哪些 | 只有 `onnxruntime-windowsml==1.30.0.202609102321` + `numpy`；两个 `wasdk-*` **没装也不需要** |
| Windows App SDK Runtime | **不需要**。不装 wasdk 也能创建 DML session |
| provider | `['DmlExecutionProvider', 'CPUExecutionProvider']`，`get_device()` = `CPU-DML` |
| 唯一告警 | `Init provider bridge failed.`——这是 WinML「EP 目录」桥未初始化（没装 wasdk），不影响直连 DML EP 的用法 |

> 比预期好：原以为要背 Windows App SDK Runtime，实际只多一个包，且无外部运行时依赖。

### 8.2 数值：与 1.24.4 逐位同源

同一台机器、同一批资产，两个 venv 各跑一遍：

| 测试 | `onnxruntime-directml 1.24.4` | `onnxruntime-windowsml 1.30.0` |
|---|---|---|
| FCPE DML（RPA / median / p99） | 28.4% / 89.058c / 2006.71c | **28.4% / 89.058c / 2006.71c** |
| FCPE CPU（RPA / median） | 100% / 0.001c | 100% / 0.001c |
| GAME encoder `x_seg` max\|diff\|（DML vs CPU） | 6.409e-04 | 6.410e-04（同量级，抖动） |
| GAME encoder `x_est` max\|diff\| | 8.088e-04 | 8.086e-04 |
| 12 s encoder（CPU / DML 热跑） | 0.129 s / 0.019 s | 0.137 s / 0.019 s |
| session 加载（GAME encoder） | 0.73 s | **0.38 s** |

**结论**：两个包里的 DirectML EP 是**同一份**（数值逐位一致），差异只在 ORT 核心版本（1.24.4 → 1.30.0）与加载速度。
所以 §3、§6 的所有 DML 结论**原样迁移**，包括「FCPE 固定 CPU」——28.4% 说明失真没被修好，不必重验第二遍。

### 8.3 判据结论

| # | 判据 | 结果 |
|---|---|---|
| 1 | Python 侧装得起 | ✅ 一个包 + numpy，无需 wasdk |
| 2 | 拿得到 GPU EP | ✅ `DmlExecutionProvider` 直连可用 |
| 3 | GAME 数值等价 | ✅ encoder 层 max 8e-4、均值 6e-5（与 1.24.4 同源）；8 步 segmenter 全链路留到 `stages/notes.py` 落地时补测 |
| 4 | 端到端不劣化 | ✅ 推理耗时相同，session 加载反而快一倍 |

**Go**：运行时用 `onnxruntime-windowsml`，EP 矩阵沿用 §6（GAME → DML、FCPE → CPU、demucs 待验）。

## 9. mel 前处理的数值偏差（2026-10-31，未解决）

运行时不能带 torch，所以 `mel` 必须在 numpy 里重写（`app/runtime/mel.py`）。对齐情况：

| 环节 | 结果 |
|---|---|
| 功率谱（vs `torch.stft`） | 一致：max\|diff\| 2.4e-3，相对 1e-4 |
| 滤波器矩阵（vs librosa） | 一致：max\|diff\| 2.9e-9 |
| mel 值（vs `wav2mel`） | **低 mel bin 系统性偏高**（bin 7 平均 +3.4），frame 0 偏差 ±2 |
| 端到端 f0（5 s 220 Hz 测试音） | RPA **100%**，但中位偏差 **8.04 音分**（p5–p95：1.4–12.3，方向单一） |

**结论：这个偏差不能就这么上。** D17 关注的相邻帧差中位数才 6.27 音分，8 音分的系统性偏高会把整条音高线整体推高。
怀疑来源：`Wav2MelModule` 里有 `resample_kernel`（即使同采样率也过一遍滤波器）+ 边界处理与我们的 reflect pad 不同。

**待办**：把 `wav2mel` 的 resample 与边界逻辑对齐后重测；在未对齐前，`stages/pitch.py` 的验收线是「与 torch 全链路中位差 ≤1 音分」。
