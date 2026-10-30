# vendored: GAME 推理路径

| 项 | 值 |
|---|---|
| 上游 | <https://github.com/openvpi/GAME> |
| commit | `69ba01ded0ddda5495e51ba373685f1be1a67ea2`（main，2026-10-30 取） |
| 许可 | MIT，全文见 `_vendor/LICENSE` |
| 位置 | `app/worker/game/_vendor/`，**只在 `app.worker` 子进程里被 import** |

## 搬了什么

`inference/` `lib/` `modules/` `deployment/` —— 整目录逐字复制，一个字都没改。
`training/` 只保留 `collate_nd`（见下）。

## 唯一被裁剪的文件

`_vendor/training/data.py`：上游那份 345 行，会连带拖进 `training.augmentation`、`lib.indexed_dataset`
与训练期依赖，而推理只用到一个 `collate_nd`。函数体逐字照抄，其余删掉。
`_vendor/training/__init__.py` 是我们写的空包声明。

## 为什么不用整包当依赖

上游没有 `pyproject.toml`，依赖声明只有 `requirements.txt`（含训练用的 `onnx` / `tensorboard` /
`colorednoise` / `onnxslim`）。当 git 依赖装会把这些一起拖进来，且无法钉住 commit 的 API。

## 升级流程

1. 重新浅克隆上游，确认 commit 变了
2. 整目录覆盖 `_vendor/{inference,lib,modules,deployment}`（不要手改其中任何文件）
3. 重新核对 `_vendor/training/data.py` 里的 `collate_nd` 是否与上游一致
4. 更新本文件顶部的 commit
5. `uv run pytest` + 一次 `compare_notes.py --midi` 回归（口径见 `docs/05-game.md`）

## 权重

**不入库**。放在 `app/worker/game/weights/`（`gitignore`），首次使用时按 release 下载：

```powershell
# GAME 1.0.0 是唯一发 PyTorch .pt 的版本（1.0.1–1.0.3 只有 ONNX / .oudep）
curl.exe -L -o GAME-1.0-small.zip https://github.com/openvpi/GAME/releases/download/v1.0.0/GAME-1.0-small.zip
```

体积与精度取舍见 `docs/05-game.md` 的 M0 实测：small 48.5 MB / 峰值 305 MiB / 音高一致 97.3%。
