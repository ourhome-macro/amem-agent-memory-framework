# BGE-M3 + Qdrant 本地 Recall 复测

日期：2026-07-25

## 环境

- `sentence-transformers==5.2.0`
- `torch==2.10.0`
- `qdrant-client==1.18.0`
- 模型：`BAAI/bge-m3`
- 设备：CPU
- 向量维度：1024
- Qdrant 模式：`QdrantClient(':memory:')`

## 执行方式

本次不是 sqlite-vec 评测。流程是：

```text
SQLite
 -> 只负责事件 ingest、memory 派生和事实存储

BGE-M3
 -> 对 45 条 memory 生成 1024 维归一化向量

Qdrant
 -> 创建 collection
 -> 写入向量和 tenant/user/session/ACL payload
 -> 通过 QdrantVectorIndex 执行 semantic search
```

Qdrant 覆盖率：

```text
qdrant coverage = 1.0
events = 45
memories = 45
```

结果写入：

```text
doc/bge-m3-qdrant-benchmark-results.json
```

## 统一 Recall@5 结果

| 模式 | Pass@5 | R@5 all | R@5 non-empty | Precision@5 | No-result accuracy | P50 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| FTS5-only | 18/30 | 0.7000 | 0.5714 | 0.3200 | 0.7778 | 15.29ms |
| Qdrant vector t=0.0 | 14/30 | 0.8000 | 0.7143 | 0.1067 | 0.0000 | 178.82ms |
| Qdrant hybrid t=0.0 | 18/30 | 0.9333 | 0.9048 | 0.1333 | 0.0000 | 180.48ms |
| Qdrant vector t=0.3 | 14/30 | 0.8000 | 0.7143 | 0.1067 | 0.0000 | 177.75ms |
| Qdrant hybrid t=0.3 | 18/30 | 0.9333 | 0.9048 | 0.1333 | 0.0000 | 180.49ms |
| Qdrant vector t=0.5 | 25/30 | 0.9333 | 0.9048 | 0.3667 | 0.7778 | 149.23ms |
| Qdrant hybrid t=0.5 | 26/30 | 0.9667 | 0.9524 | 0.3733 | 0.7778 | 154.16ms |
| Qdrant vector t=0.6 | 25/30 | 0.8667 | 0.8095 | 0.4200 | 1.0000 | 82.09ms |
| Qdrant hybrid t=0.6 | 24/30 | 0.9000 | 0.8571 | 0.3600 | 0.7778 | 81.74ms |
| Qdrant vector t=0.7 | 12/30 | 0.4333 | 0.1905 | 0.3333 | 1.0000 | 78.82ms |
| Qdrant hybrid t=0.7 | 18/30 | 0.7000 | 0.5714 | 0.3200 | 0.7778 | 82.98ms |

## 结论

本地 BGE-M3 + Qdrant 下，最佳平衡点是：

```text
Qdrant hybrid, min_semantic_similarity = 0.5
Pass@5 = 26/30
R@5 all = 0.9667
R@5 non-empty = 0.9524
No-result accuracy = 0.7778
```

`t=0.0` 和 `t=0.3` 的召回高，但无结果 case 失控，`no-result accuracy=0`。这说明 BGE-M3/Qdrant 上线不能只看 Recall，必须做相似度阈值校准。

## 面试讲法

可以这样说：

```text
我做了 Qdrant 版复测：SQLite 保持事实源，只负责事件、memory、审计和 replay；BGE-M3 本地生成 1024 维向量后写入 Qdrant，并把 tenant/user/session/ACL payload 同步进去，保证向量 top-K 前能做权限预过滤。30 个 case 上，Qdrant hybrid 在阈值 0.5 时 R@5 non-empty 达到 0.9524，Pass@5 26/30，同时 no-result accuracy 仍有 0.7778。低阈值虽然 Recall 高，但无答案误召回严重，所以需要阈值校准和 no-result gate。
```
