# CKBuilder issue #38：CellFlow 测试分析与集成用例

## 需求与上下文

[CKBuilder issue #38](https://github.com/Nervos-Community-Catalyst/CKBuilder-projects/issues/38)
提出 CellFlow，用于在 CKB 应用中持久化交易意图、广播尝试和确认状态，并在广播响应丢失、交易竞争、RBF、节点切换或链重组后恢复。

评论讨论补充了几个关键边界：同输入交易通常会在提交时被拒绝；更高费率交易可能通过 RBF 替换先前交易；应用状态 Cell 与钱包资金 Cell 的过期恢复策略不同；一个业务 intent 在重新签名后可能对应多个 attempt hash。项目后续评论声称已在 Testnet 验证更高费率 RBF、丢失成功广播响应后的恢复，以及 expected Cell 的 live 检查。

本轮检查了公开仓库与部署状态。`/api/ready`、`/api/health` 正常，TypeScript typecheck 通过；HTTP client、状态机、断言、广播错误分类与 fake RPC 定向套件共 72 个用例通过。完整 `npm test` 仍有 6 个仓库自检失败，因为公开仓库缺少 `.env.example` 和 `.github/workflows/ci.yml`。仓库也没有提交 lockfile，`evidence/testnet` 只有说明与 manifest 示例，没有评论所述 Testnet 运行的完整证据包。

## 风险与场景矩阵

| 优先级 | 场景 | 核心预期 | 自动化位置 |
| --- | --- | --- | --- |
| P0 | 确定性 hash 与广播响应丢失 | 广播前计算 raw transaction hash；节点接受交易但客户端未收到响应时，可按该 hash 恢复 | 本 PR Python integration |
| P0 | 明确拒绝与模糊结果分类 | JSON-RPC rejection 不进入 timeout 恢复；连接中断或超时进入 reconciliation | CellFlow unit/fake RPC |
| P0 | 同输入、同费率竞争 | 只允许一个 winner；loser 归类为 input spent/mempool contention | 现有 `test_01_tx_replace_rule.py` |
| P0 | 更高费率 RBF | replacement 满足规则后替换原交易，两个 attempt 均保留可审计状态 | 现有 `test_01_tx_replace_rule.py` + CellFlow Testnet |
| P0 | 状态 Cell 与资金 Cell 过期 | 状态 Cell 冲突不能盲目重建；资金 Cell 可重新选币构建新 attempt | CellFlow application layer |
| P0 | 提交、确认深度与 canonicality | committed 后达到目标深度；创建区块仍在 canonical chain | 本 PR Python integration |
| P0 | expected Cell 生命周期 | 预期输出内容和 lock 匹配且 live；消费后不再 live | 本 PR Python integration |
| P1 | 多 RPC endpoint 差异 | endpoint disagreement 不过早终结 intent，最终状态可收敛 | CellFlow application layer |
| P1 | 重启、并发和幂等 | worker 重启或并发 reconcile 不产生重复 attempt/通知 | CellFlow DB/application layer |
| P1 | webhook/outbox | 通知可重试、去重，投递失败不改变链上最终状态 | CellFlow application layer |

## 集成框架实现

`test_cases/v210/test_38_cellflow_recovery_primitives.py` 覆盖适合 CKB Python 集成框架的链上原语：

1. 先构造并签名交易，不调用广播 RPC，独立计算 witness 之外的 raw transaction hash。
2. 通过本地故障代理向真实 CKB 节点转发 `send_transaction`；代理拿到成功上游响应后主动断开客户端连接，稳定制造 ambiguous broadcast outcome。
3. 不使用丢失的 RPC result，直接按预计算 hash 查询，确认交易已进入 pending/proposed。
4. 挖矿至 committed，再推进到 4 个 inclusive confirmations，并校验提交区块仍为该高度的 canonical block。
5. 校验预期 output 的 lock、data 和创建区块；随后消费该 output，确认旧 out point 不再 live。

该用例不引入 CellFlow 服务、PostgreSQL、Vercel 环境变量、API key 或 webhook，因此不会把应用层状态机结果误表述为 CKB 节点集成结果。应用语义、数据库幂等、多 endpoint 和通知投递仍应保留在 CellFlow 自身测试中。

## 验证结果

本地使用官方 `ckb 0.210.0 (2592ddf 2026-09-17)`：

```sh
python -m pytest -q test_cases/v210/test_38_cellflow_recovery_primitives.py
python -m pytest -q test_cases/v210
black --check test_cases/v210/test_38_cellflow_recovery_primitives.py
```

新增用例结果为 `1 passed`；完整 V210 目录结果为 `3 passed, 1 warning in 42.07s`；Black 检查通过。warning 是本机 Python 链接 LibreSSL 时 urllib3 的已知环境提示，与测试行为无关。测试退出后节点与故障代理均已停止。

此外使用 CI `prepare.sh` 固定安装的 `ckb-cli 0.205.0 (80efc21)` 驱动同一个 CKB 0.210.0 节点重跑新增用例，结果仍为 `1 passed`，未发现 CLI 构造交易路径的版本兼容问题。

本轮未发现 CKB 0.210.0 在上述链上恢复原语中的新增缺陷。CellFlow 仓库的可复现性问题与缺失的 Testnet evidence bundle 已作为项目方待补充项记录，不作为 CKB 节点 bug。

## 待项目方补充

- 发布评论中 Testnet 验证对应的完整 evidence bundle、公开交易哈希、原始 RPC 响应与文件校验和。
- 恢复仓库自检引用的 `.env.example`、`.github/workflows/ci.yml`，提交 lockfile，并确保干净 checkout 的 `npm test` 为绿色。
- 明确声明公开 Testnet 测试没有主动制造真实 reorg；若 reorg 只由模拟器覆盖，应将其与 Testnet 结果分开描述。
