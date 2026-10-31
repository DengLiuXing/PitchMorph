# 权重来源与分发策略

**原则：能从官方源获取的，一律运行时下载，不入库、不随仓库分发。**
**例外是「官方根本不提供 ONNX」的自建产物——它必须入库**，并记录导出参数与等价性验证（目前只有 FCPE，41.3 MB）。
理由：网上拿不到，丢了就不可再生；首启下载反而给它加了一个新的失败点。

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

> 上面 URL 里的 owner 以 `openvpi/GAME` 为准，写错会 404；实际仓库名见 `doc_old/05-game.md`。

### 1.1 许可（2026-10-31 核实）

| 对象 | 结论 | 依据 |
|---|---|---|
| GAME 代码 | **MIT** | `openvpi/GAME` 的 `LICENSE`（Copyright (c) 2026 Team OpenVPI） |
| **GAME 权重** | **上游未声明** | README 只写「GAME is licensed under the MIT License」；v1.0.3 release notes 全文无许可字样；openvpi 在 Hugging Face 无模型仓库（API 返回空） |
| 第三方转述 | **CC BY-NC-SA 4.0**（三个独立来源一致） | `yasoukyoku/utai-runtimes` 镜像 README、`bdsqlsz/GAME-1.0-medium-ONNX` 模型卡、`aaatmy/music-detection-enhanced`（`mixed-mit-and-cc-by-nc-sa-4.0`） |

**按非商用处理，发布前必须向上游确认（阻断项）。** 若权重确为 NC，「开源且可商用」的定位不成立，只能做个人 / 非商业分发；这要在写 README 与选发布渠道之前解决。

## 2. 官方没有 ONNX，自建（分发方式见 §5）

| 模型 | 官方形态 | 自建产物 | 依据 |
|---|---|---|---|
| FCPE | 仅 PyTorch（`torchfcpe` 内置 `fcpe_c_v001.pt`），ONNX 推理类是空壳（`NotImplementedError`） | `models/fcpe_net.onnx` 41.3 MB | 与 PyTorch 数值等价：CPU RPA 100%、音高误差中位 0.0006 音分；mel 前处理放图外（上游 mel 里有数据相关分支，导不出） |

> **交付方式已定（2026-10-31）**：FCPE **随仓库入库**，不走首启下载。`.gitignore` 用 `models/*` 加白名单 `!models/fcpe_net.onnx` / `!models/fcpe_net.npz` 放行；
> 调试与兜底用的 `fcpe_latent.onnx`、`fcpe_net_opset19.onnx` 及其 npz 移到 `temp-dev/models/`，不占仓库体积。
> 这改写了原原则里的「不入库」一刀切——对官方可下载的模型仍然成立，对自建产物不成立。

> **复现脚本缺失（待补）**：FCPE 的导出脚本与 checkpoint 来源都没有固化——`temp-dev/spike/` 与 `tools/` 在 V3 仓库里都不存在。这是全项目唯一不可再生的产物，必须在写骨架前补 `tools/export_fcpe.py`（checkpoint 地址 + torch 版本 + 导出命令 + 期望的 SHA256）。补完之前，§5 第 1 条要求的自建产物 SHA256 也无从计算。

自建产物的导出条件（复现用）：

- 图范围：`mel (1,T,128) → f0`
- 输入固定 5 s（T=501），hop 160 @16 kHz
- opset 17（19 亦可，两者结果一致）
- 宿主侧必须 `nan → 0`（无声帧 ONNX 出 NaN，torch 出 0）

## 3. demucs（2026-10-31 核实后改写）

### 3.1 现成制品的实际形态

StemSplit 的 `demucs-onnx`（MIT，PyPI 0.3.4 / 2026-05-22，作者 StemSplit）在 HF `StemSplitio` 组织下发布 7 个 ONNX 仓库，全部 MIT，模型卡称与 PyTorch fp32 对齐误差 < 1e-3：

| 仓库 | 形态 | 体积（fp32 / fp16weights） |
|---|---|---|
| `htdemucs-ft-onnx` | 4 专家 bag（4 个 session） | 1.26 GB / 660 MB |
| `htdemucs-onnx` | 单文件 4 轨 | 316 MB / 166 MB |
| `htdemucs-6s-onnx` | 单文件 6 轨 | 258 MB / — |
| `htdemucs-ft-{drums,bass,other,vocals}-onnx` | 单轨专用（各一张图） | 316 MB / 166 MB |

来源：`pypi.org/project/demucs-onnx`、`stemsplit.github.io/demucs-onnx/models`、`huggingface.co/api/models/StemSplitio/htdemucs-ft-vocals-onnx`。

**HF 的下载量不能当判据**：PyPI 自 2026 起已不再提供 `downloads` 字段（JSON 里是 `-1`），HF API 返回的 `0` 也不反映 pip / 移动端安装量。上一版据此推「样本不足」，论据不成立；真正的门槛见 §3.2 判据 2。

### 3.2 判据

| # | 判据 | 怎么验 |
|---|---|---|
| 1 | 分发渠道有可核实的持续使用量 | pypistats.org 一类第三方统计，量级 ≥ 千/月，并记录查询日期（PyPI 官方已不提供该字段） |
| 2 | 数值等价 | 与官方 PyTorch 输出逐采样对比，`max abs diff ≤ 1e-3`（V1 的 `_chunked_infer` 对上游可达 0） |

判据 2 是**实现等价性**指标（这个制品算得对不对），与 D18 作废的**质量指标**（听感好不好）不是一回事：前者始终有效，后者必须先证明与听感相关。

### 3.3 实现方式：复用 `demucs-onnx`（`--no-deps`），不自写分块

上一版决定「不引包、自实现 660 MB 下载 + 7.8 s/0.25 分块」，理由只有一条：包在 `requires_dist` 里硬依赖 CPU 版 `onnxruntime`（已核 `onnxruntime>=1.17`，基础依赖、无上界），会覆盖 `-directml` / `-windowsml` 变体。

依赖是真的，但**绕过成本是一个 pip flag**：`pip install demucs-onnx --no-deps`，再自装 `numpy / soundfile / soxr / huggingface-hub / tqdm`；模块名 `onnxruntime` 由你选的 ORT 变体提供。所以「必须自实现」不成立。

包本身已经覆盖 V1 当年手写的东西（来源：PyPI 项目页）：

| 能力 | 接口 | 说明 |
|---|---|---|
| 单轨分离 | `separate_stem(path, "vocals")` | 官方称比整 bag 快约 4×、省 75% 内存 |
| 单文件四轨 | `model="htdemucs"` | 1 个 session 出 4 轨，比 bag 快约 30% |
| EP 选择 | `providers="auto"` | 自动探测 CoreML / CUDA / **DML** / CPU |
| 会话复用 | `SessionPool` / `prewarm()` | 对冲 4 s/专家的重建成本 |
| 采样率 | 8 kHz–192 kHz | 自动重采样进出 |

**仍然成立的约束（与用不用包无关）**

- **7.8 s 是图输入硬约束**：htdemucs 训练段长就是 7.8 s，图输入固定 `(1, 2, 343980)`，分块不是实现口味
- **1/4 重叠 + 三角窗**是 demucs 默认（`overlap=0.25`、`transition_power=1`），用来消接缝
- **会话不常驻**：单专家跑完一块占 2.2–3.2 GB 且不回缩，四个专家常驻 ≈ 7.8 GB，8 GB 卡必 OOM；重建约 4 s/专家
- **单卡多专家并发是负优化**：吞吐不变、显存翻倍（`doc_old/perf-audit.md`、`doc_old/01-decisions.md`）

**仍要验**：包是 Beta，7 个版本集中在 2026-05-21/22 两天发布。用不用它由 §3.2 判据 2 的一次逐采样对比决定，不看文档；不通过再退回自实现（那时才需要搬 V1 的分块循环）。

**「仅人声」用哪个权重（更正上一版）**

- 旧写法「只跑 bag 里的 vocals 专家，工作量 = 四轨档的 1/4」**与 V1 实测矛盾**：bag 的图输出固定 `(1,4,2,N)`，上游明确 no per-stem-skip，V1 是四个专家各跑一遍整曲（69 块 × 4 = 276 次整图推理，`doc_old/perf-audit.md` 第 50 行）。
- 正确的解是**单轨专用仓库** `htdemucs-ft-vocals-onnx`（fp16 166 MB，一张图、每块只跑一次）：这才是 1/4 成本，首下体积也从 660 MB 降到 166 MB。

### 3.4 六轨：暂不做，但理由要换

旧理由「`demucs-onnx` 不提供 `htdemucs_6s`」**不成立**：HF 上有 `StemSplitio/htdemucs-6s-onnx`（258 MB，MIT），`separate_stem` 传 guitar/piano 时包还会自动路由到它。

决定不变（第一版只做「仅人声」与「四轨」），理由只剩一条：这两个音轨的质量明显弱于前四轨，且没有 FT 版；接进来只增加档位与 UI 复杂度，不解决当前瓶颈（音符分析）。

## 4. 尚未选型

- **主旋律/和声分离模型**：候选为 UVR/MVSEP 系（多数是 ONNX，但许可各异，部分禁止商用）。
  选型时必须同时确认「官方源可下载」与「许可允许分发」。
- UVR 系模型的输出命名、输入采样率各不相同，接入前要逐个核实。

**本期定位（2026-10-31 定）**：留在范围内，但属**后续阶段**——V3 第一个可交付**不含**它。
先做可行性验证（能否选到同时满足「ONNX 可跑 + 许可允许分发 + 3 分钟歌增量 < 30 s」的模型），再谈排期；
在此之前不承诺任何界面与导出行为。

## 5. 下载器的设计要求（V3 实现时）

1. 清单里每个条目带 **URL + 体积 + SHA256**，下载后校验，不匹配即报错并删除残片
2. 断点续传（大文件 171 MB，国内直连 GitHub 容易中断——本次调试就断过一次）
3. 离线可用：本地已存在且哈希匹配则跳过
4. 镜像可配置（`HF_ENDPOINT` 之类的环境变量），但**默认走官方源**
5. 自建产物（目前只有 FCPE）**随仓库入库**，不从网络拉；**不要**在安装时用 PyTorch 现场导出——那需要额外 2.5 GB 依赖（复现入口是 `tools/export_fcpe.py`）
6. **首下体积预算**（只算运行时下载的部分，GAME 按 zip 计；FCPE 随仓库、不计入）：
   - 仅人声档：GAME 171.4 MB + demucs 单轨 fp16 166 MB ≈ **338 MB**
   - 四轨档：GAME 171.4 MB + demucs bag fp16 660 MB ≈ **831 MB**
   - 主旋律/和声分离模型未选型，不在此列（§4）
   - `doc_old/06-v3.md` §5.5 的「首次下载约 400 MB」已过期，以本节为准
