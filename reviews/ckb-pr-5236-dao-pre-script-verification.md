# CKB PR #5236：集成测试用例

## 当前范围

按用户要求，只写 Python，测试类继承 `CkbTest`，复用 `CkbNode` 与 RPC；跨节点只使用普通 CKB 节点互联，不构造 P2P 消息，不编译 Rust 或新增合约。

使用 `download/current/ckb`：`ckb 0.209.0 (672745c 2026-09-16)`。节点准备直接复用 `CkbNodeConfigPath.CURRENT_TEST`，不额外制作二进制快照；原始二进制保持不变。

[PR #5236](https://github.com/nervosnetwork/ckb/pull/5236) 将 DAO 检查移到脚本验证之前。沿用已分析的 [变更函数](https://github.com/nervosnetwork/ckb/blob/120be833a9e62595ac3855dad34e00c9122b28e3/tx-pool/src/util.rs#L100-L141) 与 [DAO 检查规则](https://github.com/nervosnetwork/ckb/blob/120be833a9e62595ac3855dad34e00c9122b28e3/verification/src/transaction_verifier.rs#L865-L983)，不重新获取 PR。

当前黑盒判据：同一交易同时有 DAO 错误和缺失签名时，RPC 返回准确的 DAO 错误；仅修正 DAO 条件后返回预期脚本错误。它支持当前版本的错误优先级验证，不直接测量 VM 执行次数或 CPU 节省，也不声称完成 base/head 对照。

## 集成用例

保留原有 ID。勾选仅表示对应 `TEST-MAP` 已存在；运行结果见后文。

| 用例 | 场景 | 预期结果 | 防止的问题 | 优先级 |
| --- | --- | --- | --- | --- |
| `DAOQ-01` | - [x] 对同时存在 DAO 锁长度不匹配和缺失签名的交易，分别执行 send_transaction、test_tx_pool_accept；再提交仅修正锁长度的对照 | 两个入口先报 DAO 锁长度错误及准确索引，非法交易不在池和模板中；对照交易报缺失签名的脚本错误 | DAO 锁长度检查顺序回归或跳过脚本验证 | P0 |
| `DAOQ-02` | - [x] 普通输入对应非零 DAO 输出数据且缺失签名，分别执行两个 RPC；再将输出数据修正为零 | 两个入口先报 DAO 输出数据错误及准确索引，非法交易不在池和模板中；对照交易报缺失签名的脚本错误 | 漏掉 DAO 输出数据检查的前移 | P0 |
| `DAOQ-04` | - [x] 完成 DAO 存款、准备提现、最终提现及普通转账，分别使用本地提交和正常节点 relay | 各阶段正常入池和 committed；试接纳返回正 cycles 和预期 fee；相同交易在两节点相同链状态下的试接纳及 relay 入池 cycles/fee 一致 | 合法交易误拒绝或不同入口的验证结果不一致 | P0 |
| `DAOQ-05` | - [x] 存款锁与准备提现锁的长度相同、内容不同，随后使用新锁提现 | 准备提现和最终提现均 committed | 把锁长度一致误当成锁内容一致 | P1 |
| `DAOQ-06` | - [x] 混合普通和 DAO 输入输出，仅首、中、末 DAO 对之一的锁长度增加或减少 1 字节，经 RPC 试接纳 | 返回目标索引的 DAO 锁长度错误；非法交易不在池和模板中 | 长短方向、遍历位置或错误索引回归 | P1 |
| `DAOQ-10` | - [x] DAO 条件合法但缺少输入锁签名，分别执行两个 RPC | 两个入口均报预期脚本错误，交易不在池和模板中 | DAO 检查通过后放过脚本错误 | P1 |
| `DAOQ-11` | - [x] 对两类 DAO 错误交易与合法交易只执行试接纳；随后普通提交相同样本，并设置正常连接的观察节点 | 试接纳返回准确错误或合法 cycles/fee，本地及观察节点无已接纳池成员、模板成员或拒绝记录；后续合法提交确实 relay，非法提交留下对应拒绝记录 | RPC 错误映射或试接纳可观察副作用回归 | P0 |
| `DAOQ-14` | - [x] 合法 DAO 准备提现已上链，接入更高累计难度分叉，使该区块脱离主链；存款留在共同祖先 | 确认重组后交易重新入池，cycles/fee 与重组前试接纳一致；再次 committed 且区块不同于原区块 | 重组重新验证合法 DAO 交易时误拒绝或丢失 | P1 |

## 实现与限制

- 当前默认 CI 的 `download/current` 仍是官方 `ckb 0.209.0 (d166e28 2026-07-29)`，不包含 CKB PR #5236。测试类因此临时标记为 skip；用例、严格错误优先级断言和辅助代码均保留，待 CI 二进制包含 #5236 后移除标记恢复执行。
- 自动化：[测试类](https://github.com/nervosnetwork/ckb-py-integration-test/blob/0660e3ff72090f0658d430d5ef4b83d427220dc7/test_cases/tx_pool_refactor/test_21_dao_pre_script_verification.py)；[交易编码辅助函数](https://github.com/nervosnetwork/ckb-py-integration-test/blob/0660e3ff72090f0658d430d5ef4b83d427220dc7/test_cases/tx_pool_refactor/dao_precheck/support.py)。8 项用例对应 9 个测试方法，DAOQ-04 分本地与正常 relay 两个方法。
- 使用仓库已有 always_success 锁与节点内置 secp256k1、DAO 脚本。缺失签名是确定性脚本失败对照，所有数据由真实节点和链上交易生成。
- 主节点在 `setup_class` 启动、`teardown_class` 停止并清理，固定 RPC/P2P 端口为 8120/8225。各用例分配独立输入 Cell；跨节点用例额外使用 8121/8226，并在用例结束时停止、清理第二节点。不额外归档节点日志，不支持此类测试使用相同端口并行运行。超时只表示观察未完成，不当作错误优先级或性能证据。
- DAOQ-01/02 只覆盖当前版本 RPC；DAOQ-04 的 cycles/fee 是同版本入口一致性，并非历史基线回归。
- 按“不构造 P2P 消息”收窄 DAOQ-10 为无效 witness 检查；暂不实现伪造 declared cycles、异常远端交易、DAOQ-12 封禁策略。普通节点 relay 的合法交易覆盖不替代这些场景。
- 本轮也不覆盖缓存注入、精确 Suspend/Resume、规则激活边界和 VM 执行次数。DAOQ-11 的观察是公开接口的状态快照与正常 relay 对照，不是网络抓包证明。

## 验证

测试命令：

```sh
venv/bin/python -m pytest -q -o addopts='' test_cases/tx_pool_refactor/test_21_dao_pre_script_verification.py
```

本地运行结果：`9 passed, 1 warning in 77.91s (0:01:17)`，退出码 0。唯一 warning 来自现有框架对 `telnetlib` 的使用。该结果对应上述 `ckb 0.209.0 (672745c 2026-09-16)`，不代表其他 CKB 版本或 CI 已通过。

本次范围的映射检查：8/8 项用例有自动化，无未知映射、重复用例 ID 或勾选状态不一致；DAOQ-04 对应两个测试方法。由于映射检查器默认不识别 `test_cases` 目录名，检查时将本文件和测试原文复制到临时 `reviews` / `tests` 目录。映射存在不代替运行通过。

Black 格式检查和 Python 编译检查均通过，退出码 0。验证后主节点及第二节点数据目录已清理，原始 CKB 二进制未修改。

上述结果是本地验证记录；远端 CI 状态见 [测试 PR #141](https://github.com/nervosnetwork/ckb-py-integration-test/pull/141)。
