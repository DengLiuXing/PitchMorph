# 08 · V2 → V3 迁移原则（2026-10-30）

**定位：V3 是重构，不是移植。**
V2 只提供两样东西——**不可再生的资产**与**用教训换来的设计结论**。
所有实现代码只作参考，不迁、不 import、不复制粘贴。

---

## 1. 迁移（只这两类）

### 1.1 资产：搬过去就不必重做

| 路径 | 体积 | 不可替代的原因 |
|---|---|---|
| `temp-dev/spike/models/fcpe_net.onnx` | 41.3 MB | FCPE 唯一可用形态：已验证与 PyTorch 数值等价（CPU RPA 100%，中位误差 0.0006 音分）；上游没有官方 ONNX，属于「自建产物」 |
| `temp-dev/samples/vocadito` | 72.4 MB | 40 首**人工标注**（音符 + 帧级 F0 + 双标注者）——全项目唯一的客观基准，也是「人类天花板」的唯一来源 |
| `temp-dev/samples/openutau_official` | 34.4 MB | 两首官方转写（含 188 s 长音频），用于长音频与 USTX 对照 |
| `temp-dev/spike/models/*.npz` | 2.3 MB | 数值回归参考值（mel / latent / ref_f0），换机器/换运行时用来复验 |

> **GAME 不再迁移**：官方有 ONNX release，按 `docs/weights.md` 在运行时下载（带 SHA256 校验）。
> 原则是「官方能下载的不入库，只有官方没有 ONNX 的才自建」（目前只有 FCPE，demucs 待定）。
> 其余资产同样不入 git，V3 侧 `.gitignore` 已忽略 `models/` 与 `samples/`。

### 1.2 设计结论：只保留判断，不保留实现

| 结论 | 一句话 |
|---|---|
| 运行时矩阵 | GAME/demucs 走 DirectML（CPU 兜底），**FCPE 固定 CPU**（DML 主干失真，CPU 只花 0.9 s/首） |
| 参数定稿 | 参数在创建任务时一次给全，调参=新建任务（D14）；产物必须能被自己的 `meta.json` 解释 |
| 导出 | **不做中值平滑**，曲线控制点 50 ms → 25 ms（D17）；平滑交给 OpenUtau |
| 验收 | 以人工盲听为主；V2 的调参结论全部作废（D18），指标必须先证明与听感相关 |
| 分离档位 | 只做「仅人声」与「四轨」。六轨暂不做：`demucs-onnx` 不提供 `htdemucs_6s`，且该模型的 guitar/piano 质量弱、无 FT 版（2026-10-31 定） |
| 分轨实现 | 不引 `demucs-onnx` 包（它硬依赖 CPU 版 onnxruntime，与 DirectML 冲突），自实现「HF 拉权重 + 7.8 s/0.25 重叠分块」；等价性对上游 diff = 0 已验证 |
| 切片 | 必须按静音切片且**单片设长度上限**（GAME 注意力 O(T²)，整段会 OOM）；阈值别太窄，会把长音切断 |
| 音符分析 | 只对单一声源可靠（UI 要提示）；模型音高优先于曲线中位数（人工标注上 82.8% vs 81.5%） |
| 前端 | MD3 重写；音高挂音轨预览（展开才算、与播放同步）；音符手动触发、半透明条叠加、导出单独触发 |
| 已知无效方向 | 谱通量起音融合、多种子集成、nsteps 16、large 体积、GAME 后处理参数微调——都试过且无收益 |

## 2. 不迁移（只读参考）

代码一律不搬。需要某个细节时回来翻一下，**不要 import、不要复制粘贴**：

- `app/domain/**`、`app/exports.py`、`app/config.py` —— V2 的实现；V3 重新设计，只在需要核对算法细节时查阅
- `app/worker/**`、`app/protocol.py`、`app/orchestrator.py` —— torch 主链路与子进程协议，V3 运行时不装 PyTorch
- `app/worker/game/_vendor/**` —— PyTorch 版 GAME 推理，已被 ONNX 取代
- `frontend/**`、`app/static/**` —— 旧 Vue 前端，MD3 重写
- `tests/**` —— V2 的行为规格，仅作「这些边界条件当年踩过」的提醒
- `data/jobs/**` —— 旧任务与旧格式，不兼容

`temp-dev/spike/*.py` 与 `scripts/eval_*.py` 同样是参考：验证过一次就不再必须，V3 的评测工具重新写。

## 3. 只保留文档的「结论层」

V3 侧文档建议只留四页，其余归档在 V2：

| 保留 | 内容 |
|---|---|
| `V3 构想.md` | 方向与已确认边界（你的原文） |
| `docs/07-spike.md` | 运行时选型的实测数据（DML 对谁可用、对谁不可用） |
| `docs/05-game.md` 的实测表 | GAME 的体积/速度/质量、ONNX 可行性——避免重试已排除的参数 |
| `docs/01-decisions.md` 的 D14/D17/D18/D19 | 四条会反复被违反的约束 |

其余（`00-replication`、`01-decisions`、`02-frontend`、`03-pytorch-dual`、`04-ustx`、`05-game`、`06-v3`、`perf-audit`）
整份归档在本仓库的 **`doc_old/`**（V2 文档原样副本，只读），需要追溯时直接查，不必切仓库。
`docs/` 只保留 V3 自用的三份：`07-spike.md`（运行时实测）、`08-v3-migration.md`（本文）、`weights.md`（权重来源）。

## 4. 迁移前的两件事

- **许可**：vocadito 是 CC BY（需署名）；OpenUtau 官方转写来源待你确认；UVR 提纯模型未选型、许可未知
- **仓库**：`PitchMorph-V3` 目前是空目录、非 git 仓库。建仓顺序建议：`.gitignore` → 资产 → 文档 → 骨架代码
