"""从上游 `training/data.py` 复制的最小片段。

`inference/data.py` 顶层写着 `from training.data import collate_nd`，而整份 `training/data.py`
会连带拖进 `training.augmentation`、`lib.indexed_dataset` 与训练期依赖。这里只保留那一个函数，
**函数体逐字照抄**（commit 69ba01d），不做任何改写。

升级上游时：重新确认 `collate_nd` 的实现没有变化，其余不要补。
"""

import torch


def collate_nd(values, pad_value=0, max_len=None):
    """
    Pad a list of Nd tensors on their first dimension and stack them into a (N+1)d tensor.
    """
    size = ((max(v.size(0) for v in values) if max_len is None else max_len), *values[0].shape[1:])
    res = torch.full((len(values), *size), fill_value=pad_value, dtype=values[0].dtype, device=values[0].device)

    for i, v in enumerate(values):
        res[i, :len(v), ...] = v
    return res
