"""上游 `training` 包的最小替身：推理只用到 `collate_nd` 一个函数。

其余内容（数据集、增强、训练循环）会连带拖进 `training.augmentation`、`lib.indexed_dataset`
和大量训练期依赖，推理用不到。**这是 vendoring 中唯一被裁剪的文件**，改动见 `VENDOR.md`。
"""
