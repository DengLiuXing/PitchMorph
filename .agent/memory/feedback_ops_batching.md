---
name: 只有大文件下载才需要人工，普通构建与测试直接跑
description: 判断标准是会不会下载几百 MB 或需要代理；是就停下等用户，否则直接执行并汇报
type: feedback
---

需要停下让用户执行的操作只有一类：**大文件下载或受网络限制的操作**（torch wheel 约 2.5 GB、demucs / FCPE 权重、走 `dl.fbaipublicfiles.com` 或 HuggingFace 的下载、需要代理才能完成的请求）。

普通构建（`npm install`、`npm run build`、`uv sync` 装几 MB 的小依赖）与测试（`pytest`、`vue-tsc --noEmit`、smoke 脚本）**直接自己跑**，跑完汇报结果即可。

**Why:** 用户的网络不稳，大文件传输只能他本机解决（可能要走代理）；但把每一次 lint、测试、依赖安装都推给他反而拖慢节奏。

**How to apply:** 执行前先判断「这条命令会不会拉几百 MB 或需要翻墙」。会 → 集中列出命令并停下等待；不会 → 直接执行。删除文件仍需先说清路径与理由，但那属于一次性的确认，不是每次都要走人工。
