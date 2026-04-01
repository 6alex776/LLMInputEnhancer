"""训练 Hybrid TextCNN 分类器。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import torch
from torch import nn
from torch.optim import AdamW
from torch.utils.data import DataLoader

if __package__ in {None, ""}:
    sys.path.append(str(Path(__file__).resolve().parent.parent))
    from classifier.dataset import TaskDataset, build_vocab, load_jsonl
    from classifier.labels import LABELS
    from classifier.model import HybridTextCNN
else:
    from .dataset import TaskDataset, build_vocab, load_jsonl
    from .labels import LABELS
    from .model import HybridTextCNN


PROJECT_ROOT = Path(__file__).resolve().parent.parent

#参数设置
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="训练文本任务分类器")
    parser.add_argument("--train", default=str(PROJECT_ROOT / "classifier" / "data" / "train.jsonl"))
    parser.add_argument("--val", default=str(PROJECT_ROOT / "classifier" / "data" / "val.jsonl"))
    parser.add_argument("--artifact-dir", default=str(PROJECT_ROOT / "classifier" / "artifacts"))
    parser.add_argument("--epochs", type=int, default=30)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--lr", type=float, default=1e-3)
    parser.add_argument("--max-length", type=int, default=128)
    parser.add_argument("--embedding-dim", type=int, default=96)
    parser.add_argument("--num-filters", type=int, default=64)
    parser.add_argument("--hidden-dim", type=int, default=64)
    parser.add_argument("--dropout", type=float, default=0.30)
    parser.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
    return parser.parse_args()


def compute_macro_f1(predictions: list[int], labels: list[int], num_classes: int) -> float:
    f1_scores: list[float] = []
    for class_id in range(num_classes):
        tp = sum(1 for pred, label in zip(predictions, labels) if pred == class_id and label == class_id)
        fp = sum(1 for pred, label in zip(predictions, labels) if pred == class_id and label != class_id)
        fn = sum(1 for pred, label in zip(predictions, labels) if pred != class_id and label == class_id)

        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        if precision + recall == 0:
            f1_scores.append(0.0)
        else:
            f1_scores.append(2 * precision * recall / (precision + recall))
    return sum(f1_scores) / max(len(f1_scores), 1)


def evaluate(model: HybridTextCNN, loader: DataLoader, criterion: nn.Module, device: str) -> tuple[float, float, float]:
    model.eval()
    total_loss = 0.0
    total_correct = 0
    total_count = 0
    predictions: list[int] = []
    labels: list[int] = []

    with torch.no_grad():
        for batch in loader:
            input_ids = batch["input_ids"].to(device)
            features = batch["features"].to(device)
            target = batch["label"].to(device)

            logits = model(input_ids, features)
            loss = criterion(logits, target)
            preds = torch.argmax(logits, dim=1)

            total_loss += float(loss.item()) * len(target)
            total_correct += int((preds == target).sum().item())
            total_count += len(target)
            predictions.extend(preds.cpu().tolist())
            labels.extend(target.cpu().tolist())

    avg_loss = total_loss / max(total_count, 1)
    accuracy = total_correct / max(total_count, 1)
    macro_f1 = compute_macro_f1(predictions, labels, len(LABELS))
    return avg_loss, accuracy, macro_f1


def main() -> int:
    args = parse_args()
    train_samples = load_jsonl(args.train)
    val_samples = load_jsonl(args.val)
    vocab = build_vocab(train_samples)

    train_dataset = TaskDataset(train_samples, vocab, args.max_length)
    val_dataset = TaskDataset(val_samples, vocab, args.max_length)
    train_loader = DataLoader(train_dataset, batch_size=args.batch_size, shuffle=True)
    val_loader = DataLoader(val_dataset, batch_size=args.batch_size)

    model = HybridTextCNN(
        vocab_size=len(vocab),
        embedding_dim=args.embedding_dim,
        num_filters=args.num_filters,
        hidden_dim=args.hidden_dim,
        dropout=args.dropout,
        num_classes=len(LABELS),
    ).to(args.device)

    criterion = nn.CrossEntropyLoss()
    optimizer = AdamW(model.parameters(), lr=args.lr)

    best_f1 = -1.0
    artifact_dir = Path(args.artifact_dir)
    artifact_dir.mkdir(parents=True, exist_ok=True)

    for epoch in range(1, args.epochs + 1):
        model.train()
        running_loss = 0.0
        sample_count = 0

        for batch in train_loader:
            input_ids = batch["input_ids"].to(args.device)
            features = batch["features"].to(args.device)
            target = batch["label"].to(args.device)

            optimizer.zero_grad()
            logits = model(input_ids, features)
            loss = criterion(logits, target)
            loss.backward()
            optimizer.step()

            running_loss += float(loss.item()) * len(target)
            sample_count += len(target)

        train_loss = running_loss / max(sample_count, 1)
        val_loss, val_acc, val_f1 = evaluate(model, val_loader, criterion, args.device)
        print(
            f"epoch={epoch} train_loss={train_loss:.4f} "
            f"val_loss={val_loss:.4f} val_acc={val_acc:.4f} val_macro_f1={val_f1:.4f}"
        )

        if val_f1 > best_f1:
            best_f1 = val_f1
            torch.save(model.state_dict(), artifact_dir / "model.pt")
            (artifact_dir / "vocab.json").write_text(
                json.dumps(vocab, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            (artifact_dir / "meta.json").write_text(
                json.dumps(
                    {
                        "model_name": "HybridTextCNN",
                        "max_length": args.max_length,
                        "embedding_dim": args.embedding_dim,
                        "num_filters": args.num_filters,
                        "kernel_sizes": [2, 3, 4, 5],
                        "extra_feature_dim": 8,
                        "hidden_dim": args.hidden_dim,
                        "dropout": args.dropout,
                        "num_classes": len(LABELS),
                        "labels": LABELS,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            print(f"saved best checkpoint to {artifact_dir}")

    print(f"best_macro_f1={best_f1:.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
