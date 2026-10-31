# 08 · V2 → V3 迁移原则（2026-10-30）

**定位：V3 是重构，不是移植。**
V2 只提供两样东西——**不可再生的资产**与**用教训换来的设计结论**。
所有实现代码只作参考，不迁、不 import、不复制粘贴。

---

## 0 · 勘误（2026-10-31）

`doc_old/` 是 V2 文档的原样副本，只读、不改。其中被本轮核实推翻的结论集中列在这里，读到旧说法时以本节为准：

| 归档位置 | 旧说法 | 现状 | 依据 |
|---|---|---|---|
| `doc_old/06-v3.md` §5.7.1 | 「Windows ML 文档也把 DirectML 标为 legacy」 | **未获证实**。WinML overview（2026-09-08）写的是 DirectML 在「所有受支持的 Windows 版本」可用；ORT 官方 EP 页的措辞是 **sustained engineering**（"continues to be supported"），不是 deprecated、也不是官方称的 legacy | `learn.microsoft.com/en-us/windows/ai/new-windows-ml/overview`、`onnxruntime.ai/docs/execution-providers/DirectML-ExecutionProvider.html` |
| `doc_old/06-v3.md` §5.6 ④ / §5.8 | 「WinML 只在 WinRT/.NET/C++ 可用，Python 用不了」→ 因此「倾向 B（.NET）」 | **前提错误，该倾向作废**。微软已发官方 Python 包：`onnxruntime-windowsml` **1.30.0**（2026-09-17；另有 1.28.3 / 2026-10-26；cp311–cp314、win_amd64/arm64）与 `windowsml` **2.5.96**（Alpha）；模块名仍是 `onnxruntime` | PyPI 上述两包 |
| `doc_old/06-v3.md` §5.5 第 3 条 | 「首次下载合计约 400 MB」 | 过期。按现制品：仅人声档 ≈ 338 MB、四轨档 ≈ 831 MB（FCPE 已入库，不计入首下） | `docs/weights.md` §5 第 6 条 |
| `doc_old/06-v3.md` §5.7 | 「V3 只做两档，不做四轨」 | 改为「仅人声 + 四轨」；六轨暂缓，理由见 `weights` §3.4 | 本文件 §1.2 |
| `doc_old/06-v3.md` §5.1 / §5.6② | 「demucs 的分块循环要自己搬过来/自己驱动 Session」 | 实现改为复用 `demucs-onnx==0.3.4` + `pip install --no-deps`；「必须自写」的唯一理由（pip 硬依赖 CPU 版 ORT）已有标准解法 | `docs/weights.md` §3.3 |
| `doc_old/06-v3.md` §5.8 | 技术栈 A（Python）/ B（.NET）/ C（Rust）三选一，「倾向 B」 | 已定 **A（Python）**：B 的前提不成立（见上行），C 的调试成本与收益不匹配。该表整体作废，不再是待决项 | 本文件 §1.2「技术栈」行 |
| `doc_old/perf-audit.md` 第 211 行 | 「`onnxruntime-directml`……不建议」 | 已被实测修正：GAME 在 DML 上数值等价（边界 0/1201 帧、音高 p99 0.33 Hz）、segmenter 快 2.5×，只有 FCPE 不可用。DML 是**主选**（FCPE 例外） | `docs/07-spike.md` §3–§6 |

技术栈据此定案：**Python（FastAPI + 本地 Web UI）**，DirectML 优先、FCPE 固定 CPU；WinML 降为**可选 EP**——可用，但**未实测**，不得当成结论。

---

## 1. 迁移（只这两类）

### 1.1 资产：搬过去就不必重做

| 路径 | 体积 | 不可替代的原因 |
|---|---|---|
| `models/fcpe_net.onnx` | 41.3 MB（43 336 392 B） | FCPE 唯一可用形态：已验证与 PyTorch 数值等价（CPU RPA 100%，中位误差 0.0006 音分）；上游没有官方 ONNX，属于「自建产物」——**随仓库入库** |
| `models/fcpe_net.npz` | 772 KB（772 024 B） | 这张图的数值回归参考值，换机器/换运行时用来复验 |
| `temp-dev/models/fcpe_net_opset19.onnx` | 41.3 MB | 同一张图的 opset 19 版（`07-spike.md` §3 记「两个 opset 结果一致」）；只在 17 版报错时兜底，**不入库** |
| `temp-dev/models/fcpe_latent.onnx` | 41.3 MB | 只导主干（`mel → latent`）的定位图，复验「DML 偏差来自主干」时用它，**不入库** |
| `temp-dev/samples/vocadito` | 72.4 MB（75 934 380 B，409 个文件） | 40 首**人工标注**（音符 + 帧级 F0 + 双标注者）——全项目唯一的客观基准，也是「人类天花板」的唯一来源；**开发测试用，不入库** |
| `temp-dev/samples/openutau_official` | 34.4 MB（36 111 990 B，6 个文件） | 两首官方转写（含 188 s 长音频），用于长音频与 USTX 对照；**开发测试用，不入库** |
| `temp-dev/OpenUtau-master` | 60.1 MB（969 个文件） | OpenUtau 源码，写音符边界与 USTX 导出时**只读参考**；不复制、不 import、不打包 |

> 上表是 **V3 仓库内的实际路径**（2026-10-31 核对）。
> 唯一的入库例外是 `models/fcpe_net.onnx` 与其 `.npz`：`.gitignore` 用 `models/*` + `!models/fcpe_net.*` 白名单放行，其余仍在 `temp-dev/` 下忽略。

> **GAME 不再迁移**：官方有 ONNX release，按 `docs/weights.md` 在运行时下载（带 SHA256 校验）。
> 原则是「官方能下载的不入库；官方没有 ONNX 的自建产物**入库**」（后者目前只有 FCPE；demucs 的权重来自 HF `StemSplitio`，不属于自建）。
>
> 两处此前含糊、现已明确：**demucs「待定」指的是权重分发档位**（首下 166 MB 单轨 / 660 MB bag，见 `weights` §3.1），不是要不要自建 ONNX；
> **FCPE 的交付方式已定**——直接随仓库分发，不走首启下载（`weights` §2）。
> **GAME 权重许可上游未声明**（代码 MIT），第三方一致转述为 CC BY-NC-SA 4.0，发布前必须确认——见 `weights` §1.1。

### 1.2 设计结论：只保留判断，不保留实现

| 结论 | 一句话 |
|---|---|
| 运行时矩阵 | GAME 走 **DirectML（已验）**、**FCPE 固定 CPU（已验）**（DML 主干失真，CPU 只花 0.9 s/首）、**demucs 固定 CPU（已验：DML 首跑即 `E_OUTOFMEMORY`，图融合阶段挂，见 `07-spike.md` §7）**。DML 的实际使用面就是 GAME 一张 |
| 技术栈 | **Python（FastAPI + 本地 Web UI）**。运行时**已定 WinML**：`onnxruntime-windowsml` 1.30.0 单包 + numpy，无需 Windows App SDK Runtime，`DmlExecutionProvider` 直连可用（实测见 `07-spike.md` §8）；适配层仍按 `winml → dml → cpu` 探测（`09-contract.md` §7），业务代码不出现 WinML 类型。撤销 `doc_old/06-v3.md` §5.8「倾向 .NET」 |
| 参数定稿 | 参数在创建任务时一次给全，调参=新建任务（D14）；**派生（展开音轨算音高 / 手动提音符 / 单独导出）同样是新建子 job**，不是原地覆盖——落地方式见 `docs/09-contract.md` §1。产物必须能被自己的 `meta.json` 解释（artifact 字段见该文 §2） |
| 导出 | **不做中值平滑**，曲线控制点 50 ms → 25 ms（D17）；平滑交给 OpenUtau |
| 验收 | 以人工盲听为主；V2 的调参结论全部作废（D18），指标必须先证明与听感相关 |
| 分离档位 | 只做「仅人声」与「四轨」，六轨暂缓。旧理由「不提供 `htdemucs_6s`」不成立——`StemSplitio/htdemucs-6s-onnx`（258 MB）存在；保留暂缓的理由只剩：guitar/piano 质量明显弱于前四轨，且无 FT 版（2026-10-31 定） |
| 分轨实现 | **复用 `demucs-onnx==0.3.4`（MIT）+ `pip install --no-deps`**，绕开它对 CPU 版 `onnxruntime>=1.17` 的硬依赖；包已提供 `separate_stem`、`providers="auto/dml"`、`SessionPool`/`prewarm`。仅当等价性（`max abs diff ≤ 1e-3`）不通过时才退回自实现（V1 的 7.8 s/0.25 分块对上游 diff = 0） |
| 档位与权重 | 「仅人声」= FT 单轨图 `htdemucs-ft-vocals-onnx`（fp16 166 MB，1 session）；「四轨」= **FT bag `htdemucs-ft-onnx`（fp16 660 MB，4 个 session 顺序跑、逐批释放）**。两档是**不同模型**，同一个上传在人声轨上不承诺一致——产物必须记 `model` 字段（`09-contract.md`） |
| 和声分离 | **在范围内，但属后续阶段**；V3 第一个可交付**不含**它。模型未选型（UVR/MVSEP 候选）、许可未知、增量耗时上限 `<30 s / 3 min`，三项都待办；先做可行性验证再纳入排期（`weights` §4） |
| 性能目标 | **原目标已被实测推翻，待重定**：demucs 只能跑 CPU，按 `07-spike.md` §7 的 RTF 外推 3 min 歌——仅人声 ≈**71 s**、四轨 ≈**286 s**。原「≤60 s / ≤150 s」作废（连同更早的 ≤2 GB）。两个选项待选：① 放宽到 ≤90 s / ≤300 s；② 四轨改用单文件 `htdemucs`（1 张图，约为 bag 的 1/4 耗时，SDR 略低）。峰值显存 ≤4 GB 仍成立——GPU 上只剩 GAME 一张图 |
| 切片 | 必须按静音切片且**单片设长度上限**（GAME 注意力 O(T²)，整段会 OOM）；阈值别太窄，会把长音切断 |
| 音符分析 | 只对单一声源可靠（UI 要提示）；模型音高优先于曲线中位数（人工标注上 82.8% vs 81.5%） |
| 前端 | MD3 重写；音高挂音轨预览（展开才算、与播放同步）；音符手动触发、半透明条叠加、导出单独触发 |
| 已知无效方向 | 谱通量起音融合、多种子集成、nsteps 16、large 体积、GAME 后处理参数微调——都试过且无收益 |

### 1.3 WinML spike 的判据（Go / No-go）

**目的不是“多一条加速路径”，是“不被锁在 1.24.4”。** 四条同时满足才切为首选，否则停在可选：

| # | 判据 | 怎么验 |
|---|---|---|
| 1 | Python 侧能装起完整链路 | `onnxruntime-windowsml` + 两个 `wasdk-*` 包装完可 import；记录是否必须预装 Windows App SDK Runtime、Python 3.14 是否被接受（官方文档写 3.10–3.13，wheel 有 cp314） |
| 2 | 能拿到 GPU EP | 打印实际 provider 列表；WinML 走 **EP Catalog**，与 `providers=['DmlExecutionProvider']` 不是一回事——拿不到 GPU 就失去意义 |
| 3 | GAME 数值等价 | 对齐 `07-spike.md` §4 的判据：边界不一致帧 = 0、音高 p99 ≤ 0.33 Hz |
| 4 | 端到端不劣化 | 12 s 音频耗时 ≤ 现 DML 路径（首次图编译单独计，那是可接受的） |

No-go 时的动作：主线用 `onnxruntime-directml 1.24.4` + CPU 兜底（已验证可交付），WinML 只留文档记录。
**降级是设计的一部分**：适配层只认「一次探测 + 按 `winml → dml → cpu` 下移」，不散落在业务代码里。

> **结果（2026-10-31）：四条全过，Go。** 一个包 + numpy 即可（wasdk 不需要），DML EP 直连可用；
> 数值与 1.24.4 **逐位一致**（FCPE DML 28.4%、GAME encoder max 8e-4 完全相同），session 加载还快一倍。
> 详见 `07-spike.md` §8。推论：FCPE 仍然固定 CPU，此结论不因换包而改变。

## 2. 不迁移（只读参考）

代码一律不搬。需要某个细节时回来翻一下，**不要 import、不要复制粘贴**：

- `app/domain/**`、`app/exports.py`、`app/config.py` —— V2 的实现；V3 重新设计，只在需要核对算法细节时查阅
- `app/worker/**`、`app/protocol.py`、`app/orchestrator.py` —— torch 主链路与子进程协议，V3 运行时不装 PyTorch
- `app/worker/game/_vendor/**` —— PyTorch 版 GAME 推理，已被 ONNX 取代
- `frontend/**`、`app/static/**` —— 旧 Vue 前端，MD3 重写
- `tests/**` —— V2 的行为规格，仅作「这些边界条件当年踩过」的提醒
- `data/jobs/**` —— 旧任务与旧格式，不兼容
- `temp-dev/OpenUtau-master/` —— **外部**参考（不是 V2 的），写音符边界语义与 USTX 导出时只读对照；同样不复制、不 import、不打包

`temp-dev/spike/*.py` 与 `scripts/eval_*.py` 同样是参考：验证过一次就不再必须，V3 的评测工具重新写。

> 注意「不迁移」不等于「不需要」：V2 的 `orchestrator.py` + `protocol.py` 是当时唯一的进度/取消机制，V3 由 `docs/09-contract.md` §4 重新规定（`/events` + `/cancel`），粒度限制写在该文 §6。这是「删掉实现、保留约束、补上新契约」的典型一例。

## 3. 只保留文档的「结论层」

`docs/` 只放 V3 自用的三份，外加**一个例外**（2026-10-31 定）：

| 文件 | 内容 |
|---|---|
| `docs/07-spike.md` | 运行时实测数据（DML 对谁可用、对谁不可用） |
| `docs/08-v3-migration.md` | 本文——迁移原则与设计结论 |
| `docs/weights.md` | 权重来源、体积、SHA256、许可、分发 |
| `docs/09-contract.md`（**例外**） | 最小接口契约：job 状态机、artifact 字段、端点、失败态。「不保留实现」这条不适用于它——接口是多方约定的边界，不是实现细节 |

例外只有这一条：**当且仅当不写就无法开工时，才允许新增一页契约**，且必须同样满足「结论 + 依据」。除契约外不新增其他文档。

方向与已确认边界在仓库根的 `V3 构想.md`（不算附录文档，单独计数）。

需要追溯的结论不在 `docs/` 里留副本，而是按「结论 → 归档位置」查 `doc_old/`（V2 文档原样副本，只读，不修改）：

| 结论 | 归档位置 |
|---|---|
| D14 / D17 / D18 / D19 四条会被反复违反的约束 | `doc_old/01-decisions.md` |
| GAME 的体积/速度/质量实测表（避免重试已排除的参数） | `doc_old/05-game.md` |
| USTX / 导出格式细节 | `doc_old/04-ustx.md`，以及 `temp-dev/OpenUtau-master`（源码对照，比文档可靠） |
| V2 前端与性能审计 | `doc_old/02-frontend.md`、`doc_old/perf-audit.md` |
| 其余（`00-replication`、`03-pytorch-dual`、`06-v3`） | `doc_old/` 同名文件 |

规则：`docs/` 里的断言一律写「结论 + 依据」，依据可以是 `doc_old/xx.md` 的行号，也可以是一个 URL；不复制原文。

## 4. 迁移前的两件事

- **许可（2026-10-31 核实）**：
  - GAME——代码 MIT（`LICENSE`，Team OpenVPI 2026）；**权重上游未声明**，三个第三方镜像一致转述 CC BY-NC-SA 4.0 → 按非商用处理，**发布阻断项**（`weights` §1.1）
  - demucs / StemSplitio 的 ONNX 权重——MIT（已核 HF 模型卡）
  - vocadito——CC BY 4.0（需署名）
  - OpenUtau 官方转写——来源待你确认
  - UVR 提纯模型——未选型，许可未知
- **仓库**：已是 git 仓库（HEAD `ad2a3ba`「清空 V2 代码与文档，改为 V3 结构」），`.gitignore` 已就位。原先「空目录、非 git 仓库」的说法过期；剩下要做的是补资产与骨架代码

## 5. 下一步（按顺序，2026-10-31 定）

> 开工第一件事不是写骨架，是把三件「只能靠实测知道」的事情测掉。每项都在小时级，产出追加到对应文档。

1. **WinML spike（当前待办）**——判据见 §1.3，四条全过才切首选，否则回落 `onnxruntime-directml 1.24.4`。
   代码放仓库外（不入库），结论追加为 `07-spike.md` §8「WinML 路线实测」。
2. ~~**demucs 在 DML 上的数值**~~ —— **已验（2026-10-31）**：DML 首跑即 `E_OUTOFMEMORY`，demucs 固定 CPU；
   CPU 实测 RTF 仅人声 0.394、四轨 1.590，原性能目标因此作废，见 `07-spike.md` §7 与 §1.2「性能目标」行。
3. **补 FCPE 导出脚本**——`tools/export_fcpe.py`（checkpoint 地址 + torch 版本 + 命令 + 期望 SHA256）；这是唯一不可再生的产物（`weights` §2）。
4. **GAME 权重许可**——向上游确认，NC 与否决定 README 措辞与发布渠道（`weights` §1.1，阻断项）。
