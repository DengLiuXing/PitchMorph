# 权重来源与分发策略

**原则：能从官方源获取的，一律运行时下载，不入库、不随仓库分发。**
只有「官方根本不提供 ONNX」的模型才自建，并且自建产物要记录导出参数与等价性验证。

## 1. 官方源可直接下载（不入库）

| 模型 | 官方源 | 体积 | SHA256 |
|---|---|---|---|
| GAME 1.0.3 medium（ONNX） | `https://github.com/openvpi/GAME/releases/download/v1.0.3/GAME-1.0.3-medium-onnx.zip` | 171.4 MB（179 775 226 字节） | `5b7a21e64c6310efac399f5d12838fffa70565be162436b5a4a65f290721e7d8` |

解包后结构（已核实）：

```
GAME-1.0.3-medium-onnx/
├── encoder.onnx      38.9 MB
├── segmenter.onnx    77.0 MB
├── estimator.onnx    71.0 MB
├── bd2dur.onnx        0.0 MB（极小的换算图，本工具也可用纯函数替代）
├── dur2bd.onnx        0.0 MB
└── config.json        （inference config + lang）
```

> 上面 URL 里的 owner 以 `openvpi/GAME` 为准，写错会 404；实际仓库名见 `docs/05-game.md`。

## 2. 官方没有 ONNX，自建（入库/随包分发）

| 模型 | 官方形态 | 自建产物 | 依据 |
|---|---|---|---|
| FCPE | 仅 PyTorch（`torchfcpe` 内置 `fcpe_c_v001.pt`），ONNX 推理类是空壳（`NotImplementedError`） | `models/fcpe_net.onnx` 41.3 MB | 与 PyTorch 数值等价：CPU RPA 100%、音高误差中位 0.0006 音分；mel 前处理放图外（上游 mel 里有数据相关分支，导不出） |

自建产物的导出条件（复现用）：

- 图范围：`mel (1,T,128) → f0`
- 输入固定 5 s（T=501），hop 160 @16 kHz
- opset 17（19 亦可，两者结果一致）
- 宿主侧必须 `nan → 0`（无声帧 ONNX 出 NaN，torch 出 0）

## 3. 需要先做决定的：demucs

demucs（htdemucs / htdemucs_ft / htdemucs_6s）**官方只发 PyTorch 权重**，
现有的 ONNX 全部来自社区导出，**不属于官方源**。

### 3.1 社区导出的实际流行度（2026-10-31 实测 HF API）

| 仓库 | 近 30 天下载 | likes | 形态 |
|---|---|---|---|
| `MansfieldPlumbing/Demucs_v4_TRT` | 225 | 4 | TensorRT，非 ONNX |
| `jasonvassallo/demucs-htdemucs-mlx` | 136 | 1 | MLX |
| `nsosu/demucs-onnx` | 46 | 1 | ONNX |
| `chinedudave06/demucs-onnx` | 22 | 1 | ONNX |
| `Allinsama/nsosu__demucs-onnx` | 16 | 0 | ONNX |
| `StemSplitio/htdemucs-onnx` | **0** | 1 | ONNX（4 轨） |
| `StemSplitio/htdemucs-6s-onnx` | **0** | 5 | ONNX（6 轨） |
| `StemSplitio/htdemucs-ft-onnx` | **0** | 9 | ONNX（FT） |

**结论：没有任何一份社区 ONNX 达到「下载量够多」的门槛**（ONNX 系最高 46，最热的 TensorRT 版也只有 225）。
所以「按流行度判定成立」这条对 demucs 不适用——不是不信任社区，是**样本根本不足**。

### 3.2 判据修正：看**分发渠道**的使用量，不是 HF 仓库

上面那张表的 0 是**误导性的**：pip 安装不计入 HuggingFace 的 `downloads` 字段。
真正被使用的渠道是 PyPI 包：

| 渠道 | 近 30 天 | 近 7 天 | 昨日 | 最新版 |
|---|---|---|---|---|
| **PyPI `demucs-onnx`** | **2242** | 996 | 147 | 0.3.4（2026-05-22） |

结论：**`demucs-onnx` 有持续的真实使用量**，达到「下载量够多」的门槛，可用。

最终判据（两条同时满足）：

| # | 判据 | 怎么验 |
|---|---|---|
| 1 | 分发渠道有可核实的持续使用量 | PyPI / GitHub release 的下载或安装统计，量级 ≥ 千/月 |
| 2 | 数值等价 | 与官方 PyTorch 输出逐采样对比，`max abs diff ≤ 1e-3`（V1 的 `_chunked_infer` 对上游可达 0） |

判据 2 是保底：流行度证明「有人用」，等价性证明「算得对」，两者都要。

### 3.3 V3 沿用 V1 的做法（已定）

V1 用的是 `demucs-onnx==0.3.4`，V3 延续：

| 项 | V1 的做法 | V3 是否沿用 |
|---|---|---|
| 权重 | `htdemucs_ft` 四专家，`precision="fp16weights"`，单份 157.9 MB | ✅ |
| 获取方式 | 首次运行由包从 HF 下载 **约 660 MB**（四专家）；国内可设 `HF_ENDPOINT` 镜像 | ✅（但要加 SHA256 校验） |
| 分块 | **自写循环**：7.8 s 定长、`overlap=0.25`、三角窗 overlap-add；上游 `separate()` 无逐块回调，要进度和取消只能自己驱动 `Session.run` | ✅ |
| 仅人声 | 只跑 vocals 那个专家（工作量 = 四轨档的 1/4） | ✅ |
| 会话管理 | 不常驻：每批跑完 `sessions.clear()` + `runtime.release_sessions()`，重建约 4 s/专家（四个专家同时常驻 = 7.8 GB，8 GB 卡会 OOM） | ✅ |
| 打包 | `demucs-onnx` 硬依赖 CPU 版 `onnxruntime`，与 `onnxruntime-gpu` / `-directml` 同模块名**不能共存** | ⚠️ 需处理：V3 要 DirectML，得绕开这个硬依赖（见下） |

**唯一的实现障碍与决定**：`demucs-onnx` 在 `pyproject` 里硬依赖 `onnxruntime`（CPU 版），
而 V3 要装 `onnxruntime-directml`。两者同模块名，装在一起会互相覆盖（V1 踩过）。

**决定：不引入 `demucs-onnx` 包**，只实现它那两件事——从 HF 拉 660 MB 权重、按 7.8 s / 0.25 重叠做分块。
理由：这个包本身只是一个 HF 下载器 + 分块循环，而分块循环 V1 已经重写过一遍并验证过
（与上游 `separate()` 逐采样 diff = 0）。自己实现还能顺带把权重换成「URL + SHA256」清单。

### 3.4 六轨：暂不做，用四轨

`demucs-onnx` 只覆盖 `htdemucs` / `htdemucs_ft`（四轨），**不提供 `htdemucs_6s`**。

**决定：V3 第一版只做「仅人声」与「四轨」**，六轨留待后续——届时用官方 PyTorch 权重构建期导出
（判据 2 天然满足），或者等出现达标的分发渠道。

## 4. 尚未选型

- **主旋律/和声分离模型**：候选为 UVR/MVSEP 系（多数是 ONNX，但许可各异，部分禁止商用）。
  选型时必须同时确认「官方源可下载」与「许可允许分发」。
- UVR 系模型的输出命名、输入采样率各不相同，接入前要逐个核实。

## 5. 下载器的设计要求（V3 实现时）

1. 清单里每个条目带 **URL + 体积 + SHA256**，下载后校验，不匹配即报错并删除残片
2. 断点续传（大文件 171 MB，国内直连 GitHub 容易中断——本次调试就断过一次）
3. 离线可用：本地已存在且哈希匹配则跳过
4. 镜像可配置（`HF_ENDPOINT` 之类的环境变量），但**默认走官方源**
5. 自建产物（FCPE、demucs）要么随包分发，要么在安装时用 PyTorch 现场导出——后者需要额外 2.5 GB 依赖，不建议
