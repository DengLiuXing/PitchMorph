---
name: PitchMorph 性能优化：并行已落地但默认关闭
description: 2026-10-28 批式并行落地、默认档切 vocals；实测 parallel=2 更慢且吃 10 GB 内存，默认并行度定为 1
type: project
---

2026-10-28 已完成三件事：批式并行实现（`separate_stems` 按 `parallel` 切批，批后 `session_pool().clear()`）、默认档从 `quality` 切到 `vocals`（仅人声）、前端提交任务时可选 1–4 的并行度。

**核心结论：parallel=2 更慢，默认并行度定为 1（`config.default_parallel()`）。**
实测：60 s 片段 quality 档 85.4 s → 143.4 s（慢 68%），WorkingSet 从约 3 GB 涨到 9.7 GB；398.9 s 全曲 parallel=1 = 520.0 s，parallel=2 在第二批中途被系统终止（10.2 GB）。数值无问题：parallel=1 与改造前串行实现、parallel=1 与 parallel=2 之间，四轨均逐采样 diff = 0。

**Why:** 单 session 已经吃满 16 个物理核（线程扫描默认 1.19 s 最优、24 线程 1.80 s），两个默认线程池的 session 等于 48 线程抢 24 个硬件线程，超订代价大于重叠收益。另外"批大小 × 0.6 GB"的内存估算被证伪，ORT 的 CPU arena 随线程数膨胀。

**How to apply:** 不要再把并行当成默认提速方向；用户若问"还能怎么快"，顺序是 ① 换档位（默认已是 vocals，只有要四轨才走 quality）② 电源方案「高性能」③ 给每个 session 限 `intra_op_num_threads` 后再试并行（需要绕开 `SessionPool` 的池 key，未验证）。CUDA 仍未实测，落地门槛是单块 < 0.57 s。全曲 parallel=2 与 parallel=4 的耗时还没补测，用户 2026-10-28 表示要找短素材再测。
