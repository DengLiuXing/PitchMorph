# Memory Index

- [记忆存放在项目内 .agent/memory](feedback_memory_location.md) — 记忆写入本项目 .agent/memory，不用全局 ~/.codebuddy
- [ffmpeg 内置而不是依赖系统安装](feedback_ffmpeg_dependency.md) — imageio-ffmpeg 内置优先，主链路仍走 soundfile + soxr
- [性能优化：并行已落地但默认关闭](project_perf_next_steps.md) — 实测 parallel=2 更慢（85→143 s/60 s 片段）且吃 10 GB，默认并行度 1
