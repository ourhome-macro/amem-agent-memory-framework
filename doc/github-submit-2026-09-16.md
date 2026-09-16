# GitHub 提交记录

日期：2026-09-16。

## 目标

- 仓库：`https://github.com/ourhome-macro/amem-agent-memory-framework.git`
- 分支：`develop/memory-runtime-simplification`
- 中文提交说明：`迁移RabbitMQ与Celery并完善Agent执行可靠性和评测`

## 提交范围

- RabbitMQ 消息队列、Celery 后台任务、事务 outbox、任务状态及人工核对命令。
- Agent 工具参数校验、同步工具超时处理、连续压缩及旧 checkpoint 兼容。
- PowerShell 一键启动、Compose 编排、数据库升级步骤、隔离测试镜像。
- 工作区内相互依赖的全链路 trace、Golden Set、评测基线、约束解析与相关文档。

## 验证依据

- 上一轮 Linux 回归 42 项通过，包含真实 RabbitMQ、独立 Celery prefork worker 与 AMEM gRPC 链路。
- 随后的主机名与端口校验 4 项针对性检查通过。
- 启动参数组合、生产 Compose 配置与变更格式检查通过。
- 本次提交前已 fetch 远程，当前分支相对远程为 ahead 0 / behind 0。

部署与恢复边界见 [可靠性修复说明](production-reliability-fixes-2026-09-15.md)。本次是代码提交与推送，不执行业务部署。
