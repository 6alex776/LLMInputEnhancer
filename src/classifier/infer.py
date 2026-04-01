"""运行时分类推理封装。"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import sys

try:
    import torch
except Exception as exc:  # pragma: no cover - 运行环境可能未安装 torch
    torch = None
    TORCH_IMPORT_ERROR = exc
else:
    TORCH_IMPORT_ERROR = None

if __package__ in {None, ""}:
    sys.path.append(str(Path(__file__).resolve().parent.parent))
    from app_logger import get_logger
    from classifier.dataset import encode_text, extract_manual_features
    from classifier.labels import ID_TO_LABEL, TASK_DISPLAY_NAMES
    if torch is not None:
        from classifier.model import HybridTextCNN
    else:  # pragma: no cover - 仅用于无 torch 环境下的类型占位
        HybridTextCNN = None  # type: ignore[assignment]
else:
    from app_logger import get_logger
    from .dataset import encode_text, extract_manual_features
    from .labels import ID_TO_LABEL, TASK_DISPLAY_NAMES
    if torch is not None:
        from .model import HybridTextCNN
    else:  # pragma: no cover - 仅用于无 torch 环境下的类型占位
        HybridTextCNN = None  # type: ignore[assignment]


logger = get_logger("classifier")


@dataclass(frozen=True)
class ClassificationResult:
    """分类结果。"""

    task_type: str
    label: str
    confidence: float
    engine: str


class TextClassifierRuntime:
    """加载并运行本地分类模型。"""

    def __init__(self, artifact_dir: str | Path | None = None) -> None:
        if artifact_dir is not None:
            self.artifact_dir = Path(artifact_dir)
        elif getattr(sys, "frozen", False):
            self.artifact_dir = Path(sys.executable).resolve().parent / "classifier" / "artifacts"
        else:
            self.artifact_dir = Path(__file__).resolve().parent / "artifacts"
        self.meta_path = self.artifact_dir / "meta.json"
        self.model_path = self.artifact_dir / "model.pt"
        self.vocab_path = self.artifact_dir / "vocab.json"
        self.device_name = "cuda" if torch is not None and torch.cuda.is_available() else "cpu"
        self.available = False
        self._status_text = "分类器尚未初始化。"
        self._model = None
        self._vocab: dict[str, int] = {}
        self._max_length = 128
        self._engine_name = "HybridTextCNN"
        self._load()

    def _load(self) -> None:
        if torch is None:
            self.available = False
            self._status_text = f"未检测到 PyTorch，分类器不可用：{TORCH_IMPORT_ERROR}"
            return
        if not self.model_path.exists() or not self.vocab_path.exists() or not self.meta_path.exists():
            self.available = False
            self._status_text = "分类模型文件未找到，请先在 classifier/artifacts 下生成 model.pt、vocab.json 和 meta.json。"
            return

        try:
            meta = json.loads(self.meta_path.read_text(encoding="utf-8"))
            self._vocab = json.loads(self.vocab_path.read_text(encoding="utf-8"))
            self._max_length = int(meta.get("max_length", 128))
            self._engine_name = str(meta.get("model_name", "HybridTextCNN"))

            # 推理时按训练阶段导出的配置还原模型，避免权重与结构不匹配。
            model = HybridTextCNN(
                vocab_size=len(self._vocab),
                embedding_dim=int(meta.get("embedding_dim", 96)),
                num_filters=int(meta.get("num_filters", 64)),
                kernel_sizes=tuple(meta.get("kernel_sizes", [2, 3, 4, 5])),
                extra_feature_dim=int(meta.get("extra_feature_dim", 8)),
                hidden_dim=int(meta.get("hidden_dim", 64)),
                num_classes=int(meta.get("num_classes", 4)),
                dropout=float(meta.get("dropout", 0.30)),
            )
            state_dict = torch.load(self.model_path, map_location=self.device_name)
            model.load_state_dict(state_dict)
            model.to(self.device_name)
            model.eval()

            self._model = model
            self.available = True
            self._status_text = f"分类器可用，当前推理设备：{self.device_name.upper()}。"
            logger.info("分类器加载完成：device=%s model=%s", self.device_name, self._engine_name)
        except Exception as exc:
            self.available = False
            self._status_text = f"分类器加载失败：{exc}"
            logger.exception("分类器加载失败。")

    def get_status_text(self) -> str:
        """返回当前运行状态。"""
        return self._status_text

    def predict(self, text: str) -> ClassificationResult | None:
        """预测文本对应的任务类型。"""
        cleaned = (text or "").strip()
        if not self.available or not cleaned:
            return None

        with torch.no_grad():
            # 推理阶段复用训练时相同的编码和手工特征，保持线上线下分类口径一致。
            input_ids = torch.tensor(
                [encode_text(cleaned, self._vocab, self._max_length)],
                dtype=torch.long,
                device=self.device_name,
            )
            features = torch.tensor(
                [extract_manual_features(cleaned)],
                dtype=torch.float32,
                device=self.device_name,
            )
            logits = self._model(input_ids, features)
            probabilities = torch.softmax(logits, dim=1)[0]
            label_id = int(torch.argmax(probabilities).item())
            confidence = float(probabilities[label_id].item())

        task_type = ID_TO_LABEL[label_id]
        return ClassificationResult(
            task_type=task_type,
            label=TASK_DISPLAY_NAMES[task_type],
            confidence=confidence,
            engine=self._engine_name,
        )
