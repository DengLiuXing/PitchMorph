---
name: PitchMorph 性能优化：2026-10-28 的两步计划
description: 明天先加 CPU 多专家并行推理，再用本机 CUDA 实测 GPU 提升；基线数据与改动点都在 docs/perf-audit.md
type: project
---

2026-10-28 的既定顺序：**先做 CPU 多专家并行推理，再做本机 CUDA 实测**。

基线（2026-10-27 实测，399 秒 MP3 / quality 档四专家）：总耗时 512.1 秒，其中推理 488.6 秒占 95.4%，单块中位 1.72 秒，解码 0.6 秒、四次模型加载 19.8 秒、写盘 1.2 秒。机器是 i7-14650HX（16 核 24 线程）+ RTX 5060 Laptop（sm_120，驱动 617.42 / CUDA 13.4，电源方案「平衡」）。

两个关键约束，明天动手前必须先想起：

- **单 session 已经吃满物理核**：线程扫描显示默认最好（1.19 s），24 线程反而 1.80 s。所以「CPU 多线程」只能理解为**同时跑两个独立 session**，预期收益 0~34%（中性约 17%），不是线性翻倍。`app/jobs.py:90` 的单 worker 不要动，并行放在 `separate_stems` 内部。
- **并行必须同时处理两处隐患**：`app/separator.py:415` 的 `session_pool().clear()` 会清空整个池（并行时会清掉别人正在用的 session），`app/separator.py:402-403` 的进度回调在并行下会让百分比倒退。

**Why:** 用户 2026-10-27 下班前明确要求「明天先把 CPU 多线程调用推理模型加上，然后用本机 CUDA 测一下 GPU 实际提升效果」，并要求记录下来、次日提醒。诊断已完成但代码一行未改。

**How to apply:** 明天开工时先读 `docs/perf-audit.md` 的「明天计划」章节（含逐行改动点、验收标准与 GPU 判定门槛：单块 < 0.57 s 才值得落地）。CUDA 测试必须在独立 venv 里做，装完 `onnxruntime-gpu[cuda,cudnn]` 后要卸掉 CPU 版 `onnxruntime`，否则同模块名冲突。
