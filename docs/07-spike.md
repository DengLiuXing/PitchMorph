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

## 4. 未测部分

| 项 | 原因 |
|---|---|
| GAME（medium，ONNX 171.4 MB） | 下载中断，本地只有 39.1 MB 的残片（`BadZipFile`），需重新下载 |
| demucs 4 轨 / 6 轨（fp16 权重版 157.9 / 130.1 MB） | 按约定跳过；且核心风险在 GAME 的较新算子上 |

## 5. 目前的判断（不等 GAME 也能说）

1. **「统一 ONNX + 免 PyTorch」在导出侧是成立的**：FCPE 能导，CPU 上数值完全等价。
2. **DirectML 是最大不确定项**，而且第一次实测就不利。若只切解码无效，
   面众方案要么回到 CUDA EP（需带 CUDA 运行时），要么纯 CPU（速度待测）。
3. Windows ML 仍是更值得押的方向（系统管 EP），但它要求 WinRT 生态（.NET/C++/Rust），
   这条决策建议等 DML 的定位结论出来再定。
