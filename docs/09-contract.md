# 09 · 接口契约（最小版，2026-10-31 草案）

**定位**：这不是实现文档，是多方约定。它只回答三件事——**一次计算是什么、产物归谁、失败了怎么表达**。
所有参数在创建时给全，重算或调参一律**新建 job**（D14，见 `doc_old/01-decisions.md`）。这条同时解决「懒派生」与 D14 的表面冲突：展开音轨、手动提音符、单独导出，都是**建子 job**，不是原地覆盖。

## 1. Job

| 字段 | 取值 | 说明 |
|---|---|---|
| `id` | ULID | 排序即创建顺序 |
| `kind` | `separate` / `pitch` / `notes` / `export` | 四种，各阶段一种 |
| `parent` | job id / null | 派生产物的来源；`separate` 为 null |
| `target` | stem 名 / null | `pitch` / `notes` / `export` 作用在哪条轨 |
| `params` | 见 §3 | 创建时定稿，运行中不可改 |
| `state` | `queued` / `running` / `succeeded` / `failed` / `cancelled` | 五态，无中间态 |
| `warnings` | string[] | 成功但有疑点（见 §5），不阻断 |
| `error` | `{code, message}` / null | 仅 `failed` 时非空 |
| `artifacts` | Artifact[] | 见 §2 |

## 2. Artifact

产物必须能回答「这个文件是谁算的」，否则 D14 的「产物能被自己的 meta 解释」落不了地：

| 字段 | 说明 |
|---|---|
| `path` | 相对 `data/jobs/{id}/` |
| `role` | `stem` / `pitch` / `notes` / `midi` / `ustx` |
| `model` | 权重标识：`htdemucs_ft_bag@fp16`、`htdemucs_ft_vocals@fp16`、`fcpe_net@opset17`、`GAME-1.0.3-medium` |
| `ep` | `dml` / `cpu` / `winml` |
| `params_hash` | 该次运行的参数指纹（用于判断能否复用） |
| `source_job` | 上游 job id，串起 stem → pitch → notes → export 的链 |

`model` 单独成字段的原因：仅人声档与四轨档用的是**不同权重**，同一首歌的人声轨不会完全相同，UI 与导出都要能分辨。

## 3. 参数（按 D18 划界）

| 类别 | 例子 | 怎么定 |
|---|---|---|
| 工程安全参数 | `slice_max_sec`（GAME 单片上限）、`pitch_window_sec`、`max_workers` | 用**内存与耗时实测**定，不受 D18 约束 |
| 听感参数 | `onset_threshold_db`（切片阈值）、`export_smooth` | 必须**人工盲听**定；`doc_old/06-v3.md` 的数值只作起点 |
| 显示参数 | `view_smooth`（预览平滑） | 纯前端，不入产物、不影响导出（与 `export_smooth` 分开命名，避免重演 D17） |

## 4. 端点

| 方法 | 路径 | 用途 |
|---|---|---|
| POST | `/api/jobs` | 创建（`kind` + `parent`/`target` + `params`） |
| GET | `/api/jobs/{id}` | 状态 + artifacts + warnings |
| GET | `/api/jobs/{id}/events` | SSE：进度、日志、状态变更（替代 V2 的 stdout JSON 协议） |
| POST | `/api/jobs/{id}/cancel` | 取消；已产出的 artifact 保留 |
| DELETE | `/api/jobs/{id}` | 删除 job 与其子 job 的产物 |
| GET | `/api/params/schema` | 前端表单的**唯一**来源（延续 V2 的思路） |

## 5. 失败态（UI 必须能表达）

| 情况 | 表达 | 后续 |
|---|---|---|
| 分离后 vocals 近乎静音 | `succeeded` + warning `no_vocal_signal` | 允许继续，但禁用音符与导出 |
| 纯器乐（无 vocals 轨） | `separate` 阶段 warning | 只保留四轨预览 |
| GAME 出 0 音符 | `succeeded` + warning `no_notes` | 导出按钮置灰 |
| FCPE 全 NaN | `succeeded` + warning `no_pitch` | 不画音高线（宿主侧 `nan → 0`） |
| 权重下载 / SHA256 失败 | `failed`，`code = weight_integrity` | 删残片，可重试 |
| 切片超长 | `succeeded` + warning `slice_capped` | 提示可能切断长音 |

## 6. 本页不解决

- 进度粒度：demucs 没有逐块回调，「整体跑到哪」只能按 session / 分块上报
- ORT 变体互斥（`onnxruntime-directml` / `-windowsml` 同模块名）的安装形态
- 缓存失效：只规定「重跑 = 新 job」，旧 job 按 TTL 清理，不做增量复用

