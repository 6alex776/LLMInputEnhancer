"""数据加载、字表构建与手工特征提取。"""

from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import re
import sys
from typing import Any

try:
    import torch
    from torch.utils.data import Dataset
except Exception:  # pragma: no cover - 运行时可能只用到推理辅助函数
    torch = None

    class Dataset:  # type: ignore[override]
        """缺少 torch 时的占位类型。"""

        pass

if __package__ in {None, ""}:
    sys.path.append(str(Path(__file__).resolve().parent.parent))
    from classifier.labels import LABEL_TO_ID
else:
    from .labels import LABEL_TO_ID


PAD_TOKEN = "<pad>"
UNK_TOKEN = "<unk>"
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_LATIN_RE = re.compile(r"[A-Za-z]")
_DIGIT_RE = re.compile(r"\d")
_PUNCT_RE = re.compile(r"[，。！？；：、,.!?;:()\[\]{}\"'“”‘’\-]")


def load_jsonl(path: str | Path) -> list[dict[str, Any]]:
    """读取 jsonl 数据集。"""
    file_path = Path(path)
    samples: list[dict[str, Any]] = []
    if not file_path.exists():
        raise FileNotFoundError(f"数据文件不存在：{file_path}")

    for line in file_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        item = json.loads(line)
        if not isinstance(item, dict):
            continue
        text = str(item.get("text", "")).strip()
        label = str(item.get("label", "")).strip()
        if text and label in LABEL_TO_ID:
            samples.append({"text": text, "label": label})
    return samples


def char_tokenize(text: str) -> list[str]:
    """使用字符级切分。"""
    return list((text or "").strip())


def build_vocab(samples: list[dict[str, Any]], min_freq: int = 1, max_size: int = 5000) -> dict[str, int]:
    """从训练数据构建字表。"""
    counter: Counter[str] = Counter()
    for item in samples:
        counter.update(char_tokenize(str(item.get("text", ""))))

    vocab = {PAD_TOKEN: 0, UNK_TOKEN: 1}
    for token, count in counter.most_common():
        if count < min_freq:
            continue
        if token in vocab:
            continue
        vocab[token] = len(vocab)
        if len(vocab) >= max_size:
            break
    return vocab


def encode_text(text: str, vocab: dict[str, int], max_length: int) -> list[int]:
    """将文本编码成定长 id 序列。"""
    token_ids = [vocab.get(token, vocab[UNK_TOKEN]) for token in char_tokenize(text)[:max_length]]
    if len(token_ids) < max_length:
        token_ids.extend([vocab[PAD_TOKEN]] * (max_length - len(token_ids)))
    return token_ids


def extract_manual_features(text: str) -> list[float]:
    """提取轻量手工特征。"""
    cleaned = (text or "").strip()
    length = max(len(cleaned), 1)
    newline_count = cleaned.count("\n")
    cjk_count = len(_CJK_RE.findall(cleaned))
    latin_count = len(_LATIN_RE.findall(cleaned))
    digit_count = len(_DIGIT_RE.findall(cleaned))
    punctuation_count = len(_PUNCT_RE.findall(cleaned))
    language_count = cjk_count + latin_count

    cjk_ratio = cjk_count / language_count if language_count else 0.0
    latin_ratio = latin_count / language_count if language_count else 0.0
    digit_ratio = digit_count / length
    punctuation_ratio = punctuation_count / length
    mixed_language = 1.0 if cjk_count > 0 and latin_count > 0 else 0.0
    short_text = 1.0 if length <= 32 else 0.0

    # 这些统计特征用来补充纯字符序列难以直接表达的信息，比如长短、语言混合程度和标点密度。
    return [
        min(length / 256.0, 1.0),
        min(newline_count / 8.0, 1.0),
        min(punctuation_ratio * 8.0, 1.0),
        cjk_ratio,
        latin_ratio,
        min(digit_ratio * 10.0, 1.0),
        mixed_language,
        short_text,
    ]


class TaskDataset(Dataset):
    """训练/验证数据集。"""

    def __init__(self, samples: list[dict[str, Any]], vocab: dict[str, int], max_length: int) -> None:
        self.samples = samples
        self.vocab = vocab
        self.max_length = max_length

    def __len__(self) -> int:
        return len(self.samples)

    def __getitem__(self, index: int) -> dict[str, Any]:
        if torch is None:
            raise RuntimeError("当前环境未安装 torch，无法构建训练数据集。")
        item = self.samples[index]
        text = str(item["text"])
        label = str(item["label"])
        return {
            "input_ids": torch.tensor(encode_text(text, self.vocab, self.max_length), dtype=torch.long),
            "features": torch.tensor(extract_manual_features(text), dtype=torch.float32),
            "label": torch.tensor(LABEL_TO_ID[label], dtype=torch.long),
            "text": text,
        }


if __name__ == "__main__":
    sample_text = "请在今天下班前提交最终版本。"
    print("sample_tokens:", char_tokenize(sample_text))
    print("sample_features:", extract_manual_features(sample_text))
