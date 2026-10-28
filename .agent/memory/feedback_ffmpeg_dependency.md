---
name: ffmpeg 内置而不是依赖系统安装
description: 需要 ffmpeg 的能力用 imageio-ffmpeg 内置、锁定版本；主解码链路仍走 soundfile + soxr
type: feedback
---

规则：需要 ffmpeg 时把它作为 pip 依赖内置（`imageio-ffmpeg`），不要依赖用户机器上装没装、装的是哪一版。

**Why:** 用户 2026-10-27 先要求「不要太依赖 ffmpeg」，同一天在比较系统探测与内置方案后改口：「直接内置 ffmpeg 吧，反正也不大」。动机是可复现性——本机系统 ffmpeg 是 8.1.1（gyan.dev full），同类分轨库 `unblend` 只支持 v4–v8、不支持 v9，跟随系统版本会引入不可控漂移。内置版为 7.1 essentials（83 MB），随 venv 落盘，不影响启动速度。

**How to apply:** `imageio-ffmpeg` 放进 `pyproject.toml` 必需依赖；探测顺序 `PITCHMORPH_FFMPEG` → 内置 → 系统 PATH（内置优先，保证跨机器行为一致）。主解码链路仍是 `soundfile`(libsndfile) + `soxr`，ffmpeg 只在容器（mp4/m4s/m4a/mkv…）上启用，别让它变成所有输入的中转站。启动不得因 ffmpeg 缺失而失败。
