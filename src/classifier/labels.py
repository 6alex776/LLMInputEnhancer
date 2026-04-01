"""分类标签定义。"""

from __future__ import annotations


LABELS = ["polish", "translate", "expand", "summarize"]
LABEL_TO_ID = {label: index for index, label in enumerate(LABELS)}
ID_TO_LABEL = {index: label for label, index in LABEL_TO_ID.items()}
TASK_DISPLAY_NAMES = {
    "polish": "文本润色",
    "translate": "中英互译",
    "expand": "文本扩写",
    "summarize": "文本缩写",
}
