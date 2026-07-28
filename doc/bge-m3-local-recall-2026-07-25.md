# BGE-M3 本地 Recall 复测

日期：2026-07-25

## 环境

- `sentence-transformers==5.2.0`
- `torch==2.10.0`
- `transformers==4.57.3`
- 模型：`BAAI/bge-m3`
- 设备：CPU
- 向量维度：1024
- 模型缓存：`C:\Users\Administrator\.cache\huggingface\hub\models--BAAI--bge-m3`

验证命令：

```powershell
@'
from sentence_transformers import SentenceTransformer
m = SentenceTransformer('BAAI/bge-m3', device='cpu')
print(m.get_sentence_embedding_dimension())
print(len(m.encode(['测试'], normalize_embeddings=True)[0]))
'@ | py -3.12 -
```

输出确认 BGE-M3 为 1024 维。

## 执行命令

```powershell
py -3.12 benchmarks\bge_m3_recall_benchmark.py
```

该脚本执行完整流程：

1. 加载本地 BGE-M3。
2. 创建临时 SQLite store。
3. 导入 45 条事件。
4. 派生 45 条 memory。
5. 使用 BGE-M3 对 memory 内容编码。
6. 将 1024 维向量写入 sqlite-vec。
7. 在 30 个评测 case 上比较 FTS5-only、Vector-only、Hybrid-RRF。
8. 写出报告：`doc/bge-m3-benchmark-results.json`。

## 统一 Recall@5 结果

| 模式 | Pass@5 | R@5 all | R@5 non-empty | No-result accuracy |
| --- | ---: | ---: | ---: | ---: |
| FTS5-only | 18/30 | 0.7000 | 0.5714 | 0.7778 |
| Vector-only t=0.0 | 15/30 | 0.8333 | 0.7619 | 0.0000 |
| Hybrid-RRF t=0.0 | 19/30 | 0.9667 | 0.9524 | 0.0000 |
| Vector-only t=0.3 | 15/30 | 0.8333 | 0.7619 | 0.0000 |
| Hybrid-RRF t=0.3 | 19/30 | 0.9667 | 0.9524 | 0.0000 |
| Vector-only t=0.5 | 26/30 | 0.9667 | 0.9524 | 0.7778 |
| Hybrid-RRF t=0.5 | 26/30 | 0.9667 | 0.9524 | 0.7778 |
| Vector-only t=0.6 | 26/30 | 0.9000 | 0.8571 | 1.0000 |
| Hybrid-RRF t=0.6 | 24/30 | 0.9000 | 0.8571 | 0.7778 |
| Vector-only t=0.7 | 13/30 | 0.4667 | 0.2381 | 1.0000 |
| Hybrid-RRF t=0.7 | 18/30 | 0.7000 | 0.5714 | 0.7778 |

## 结论

当前本地 BGE-M3 复测下，最佳实用阈值是 `min_semantic_similarity=0.5`：

- `Vector-only t=0.5`：Pass@5 26/30，R@5 all 0.9667，R@5 non-empty 0.9524。
- `Hybrid-RRF t=0.5`：Pass@5 26/30，R@5 all 0.9667，R@5 non-empty 0.9524。

`t=0.0/0.3` 的 Recall 很高，但无结果场景完全失控，no-result accuracy 为 0。面试或汇报时不能只报 Recall，必须同时报 no-result accuracy、forbidden hit 和阈值校准依据。

## 面试讲法

可以这样讲：

```text
我用本地缓存的 BGE-M3，通过 sentence-transformers CPU 模式生成 1024 维归一化向量。
评测先导入事件并派生 memory，再对每条 memory 生成 embedding 写入 sqlite-vec。
查询时分别跑 FTS5-only、Vector-only 和 Hybrid-RRF。
在 30 个 case 上统一按 Recall@5 复算，BGE-M3 在阈值 0.5 时 R@5 non-empty 达到 0.9524，同时 no-result accuracy 为 0.7778。
这说明 BGE-M3 解决了 FTS5 在跨语言、改写、零词面重合上的召回问题，但必须用相似度阈值控制无答案误召回。
```
