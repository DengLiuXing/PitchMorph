---
name: 记忆存放在项目内 .agent/memory
description: 本项目的会话记忆写入项目内 .agent/memory 目录，不使用全局 ~/.codebuddy 目录
type: feedback
---

规则：PitchMorph 的记忆文件放 `PitchMorph/.agent/memory/`，索引 `MEMORY.md` 同目录。

**Why:** 用户于 2026-10-27 明确要求「记忆留在项目文件夹啊, 留在 .agent 文件夹中」——希望记忆随项目走（能跟着仓库迁移、备份、共享），而不是散落在用户级全局目录里。

**How to apply:** 之后所有记忆的读写都使用 `c:/Users/Acer/daily/coding/Python/PitchMorph/.agent/memory/`，不要再用 `~/.codebuddy/projects/.../memory`。索引以该目录下的 `MEMORY.md` 为准。

另外两类内容分工清楚：**规格与结论**写 `AGENTS.md` / `docs/`（要跟着仓库走、要能给别人看），**跨会话的偏好与态度**才写 memory。`.codebuddy/` 已被 `.gitignore` 忽略，那里的计划草稿是本地临时件、不随仓库分发——所以计划里仍然成立的内容要在实施时落进 `docs/`，别把唯一副本留在那儿。
