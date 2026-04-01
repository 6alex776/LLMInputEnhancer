"""Hybrid TextCNN 模型定义。"""

from __future__ import annotations

import torch
from torch import nn


class HybridTextCNN(nn.Module):
    """字符级 TextCNN + 手工特征分类器。"""

    def __init__(
        self,
        vocab_size: int,
        embedding_dim: int = 96,
        num_filters: int = 64,
        kernel_sizes: tuple[int, ...] = (2, 3, 4, 5),
        extra_feature_dim: int = 8,
        hidden_dim: int = 64,
        num_classes: int = 4,
        dropout: float = 0.30,
    ) -> None:
        super().__init__()
        self.embedding = nn.Embedding(vocab_size, embedding_dim, padding_idx=0)
        self.convs = nn.ModuleList(
            nn.Conv1d(embedding_dim, num_filters, kernel_size=kernel)
            for kernel in kernel_sizes
        )
        cnn_output_dim = num_filters * len(kernel_sizes)
        self.dropout = nn.Dropout(dropout)
        self.classifier = nn.Sequential(
            nn.Linear(cnn_output_dim + extra_feature_dim, hidden_dim),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(hidden_dim, num_classes),
        )

    def forward(self, input_ids: torch.Tensor, features: torch.Tensor) -> torch.Tensor:
        embedded = self.embedding(input_ids).transpose(1, 2)
        conv_outputs = []
        for conv in self.convs:
            activated = torch.relu(conv(embedded))
            pooled = torch.max(activated, dim=2).values
            conv_outputs.append(pooled)
        text_repr = torch.cat(conv_outputs, dim=1)
        fused = torch.cat([self.dropout(text_repr), features], dim=1)
        return self.classifier(fused)
