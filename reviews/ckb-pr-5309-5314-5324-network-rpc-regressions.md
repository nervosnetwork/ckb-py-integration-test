# CKB PR #5309 / #5314 / #5324：测试分析与集成用例

## 当前范围

本轮覆盖分配给 `@Xcodes-chain` 的三个 CKB 变更：

- [PR #5309](https://github.com/nervosnetwork/ckb/pull/5309) 将 `h2` 升级到 0.4.17，处理 `RUSTSEC-2026-0258`。
- [PR #5314](https://github.com/nervosnetwork/ckb/pull/5314) 在复制远端 GetHeaders locator 前检查 `MAX_LOCATOR_SIZE`。
- [PR #5324](https://github.com/nervosnetwork/ckb/pull/5324) 使用饱和减法处理 hole punching 中的系统时钟回拨。

本地指定源码为 `/Users/xue/Downloads/ckb-ghsa-mqh2-6xxg-fpm3-dependabot-cargo-deps-bumps-rebase`。该目录不含 `.git`，因此无法从源码包证明 base/head 提交关系；已核对上述三项代码标记并从该目录完成定向 Rust 测试与 locked release build。构建产物报告 `ckb 0.209.0 ( )`，空提交信息来自源码包缺少 Git 元数据，不作为版本一致性判据。

Python 用例继承 `CkbTest`，复用 `CkbNodeConfigPath.CURRENT_TEST`、`Cluster`、Miner 和 RPC helper。框架没有 raw CKB P2P peer，也没有向运行中节点注入回拨系统时钟的 hook，所以两个负向边界由 CKB 源码中的精确回归测试负责；Python 只验证周边合法路径和节点存活，不把 smoke 结果表述为漏洞复现。

## 变更与风险分析

### #5309：h2 0.4.17

改动只更新 `Cargo.lock` 中的传递依赖，没有 CKB 业务代码变化。主要风险是依赖解析没有真正落到 0.4.17，或者 HTTP/RPC 相关构建及运行路径出现兼容性回归。使用 `--locked` 构建验证锁文件可复现，并通过连续 RPC 请求覆盖运行时 smoke；这不等价于复现 RustSec advisory。

### #5314：GetHeaders locator 预检查

修复后先读取 `block_locator_hashes().len()`，超过 `MAX_LOCATOR_SIZE` 时返回 `ProtocolMessageIsMalformed`，只有合法数量才执行 `to_entity()` 和 `Vec<Byte32>` 收集。核心风险是检查仍晚于远端可控分配、边界值误拒绝，或正常 header sync 被破坏。源码单测覆盖 `MAX_LOCATOR_SIZE + 1` 的拒绝；Python 用 24 个区块触发普通节点之间的合法 header sync。

### #5324：hole punching 时钟回拨

原始直接 `u64` 时间戳相减在系统时钟向后调整时可能下溢，影响请求间隔、pending/inflight 清理和 NAT traversal TTL。修复统一使用 `now.saturating_sub(timestamp)`，回拨时 elapsed 为 0。源码单测精确覆盖 `now < timestamp` 与正常递增；Python 仅验证相邻网络行为：已有 TCP peer 在提交不支持的 `quic-v1` 地址后仍存活且 RPC 可用。

## 测试用例

勾选表示用例已有自动化或可复现命令；结果见“验证”。

| 用例 | 场景 | 预期结果 | 自动化层 | 优先级 |
| --- | --- | --- | --- | --- |
| `DEP-5309-01` | - [x] 使用锁文件执行 release build | 构建成功并实际下载、编译 `h2 v0.4.17` | CKB source | P0 |
| `DEP-5309-02` | - [x] 两个节点同步后各连续执行 10 轮 `local_node_info` 与 `get_tip_block_number` | HTTP RPC 持续成功，连接数和高度稳定 | Python integration | P1 |
| `SYNC-5314-01` | - [x] 构造 `MAX_LOCATOR_SIZE + 1` 个 locator | 在实体复制前返回 `ProtocolMessageIsMalformed`，错误上下文包含实际值与上限 | CKB source | P0 |
| `SYNC-5314-02` | - [x] 源节点先生成 24 个区块，再由空 follower 建连同步 | follower 到达高度 24，两个同步状态标志为 true | Python integration | P0 |
| `NET-5324-01` | - [x] `now < timestamp` 与正常时间递增 | 回拨返回 0，正常路径返回真实 elapsed，不 panic/下溢 | CKB source | P0 |
| `NET-5324-02` | - [x] 已建立 TCP peer 后提交同端口 `quic-v1` 地址 | 请求被拒绝或忽略后 TCP peer、`ping_peers` 和 tip RPC 仍正常 | Python integration | P1 |

## 实现与限制

- Python 自动化位于 `test_cases/v210/test_5309_5314_5324_regressions.py`，一个测试类、两个测试方法，对应 4 个端到端映射项；源码层另外覆盖 2 个精确负向边界和 1 个 locked build 项。
- 两节点固定使用 RPC 端口 21014/21015、P2P 端口 21025/21026，类级 setup 启动，teardown 停止并清理；相同端口的测试类不应并行运行。
- `SYNC-5314-02` 证明合法 GetHeaders/同步路径未回归，不证明超限消息在真实远端会触发 ban，也不测量拒绝前后的内存差值。
- `NET-5324-02` 证明相邻网络路径保持健康，不向系统注入时钟回拨，不覆盖完整 NAT、端口映射服务或多节点 hole punching 成功率。
- `DEP-5309-02` 是 HTTP/RPC 兼容性 smoke；RustSec 修复有效性以 locked dependency 版本和上游修复为准，不声称通过黑盒 RPC 重现 advisory。

## 验证

源码定向验证：

```sh
cargo test -p ckb-network --lib elapsed_millis_saturates_when_clock_moves_backwards -- --test-threads=1
cargo test -p ckb-sync get_headers_rejects_oversized_locator_before_processing -- --nocapture --test-threads=1
cargo test -p ckb-sync tests::synchronizer::functions -- --nocapture --test-threads=1
cargo build --release --locked --bin ckb
```

结果分别为 1 passed、1 passed、14 passed，以及 locked release build 成功；构建日志确认 `h2 v0.4.17`。安装到 `download/current/ckb` 的二进制 SHA-256 为 `db29470eca513bed8d40863018093f5ed5788ff79f286b0a6526ac6b8c8ae2b1`。

Python 定向验证：

```sh
venv/bin/python -m pytest -q --html=report/v210_xcodes_new_cases.html test_cases/v210/test_5309_5314_5324_regressions.py
```

结果：`2 passed, 1 warning in 21.78s`。warning 是本机 Python 使用 LibreSSL 时 urllib3 的现有提示。扩展执行 V209、TCP/QUIC 与 RPC 集合得到 `37 passed, 5 skipped in 1055.49s`；skip 为 1 个 Tor opt-in 用例和 4 个等待 ckb-cli#683 合入的 TUI 用例。

Black 检查通过。本地验证结束后没有遗留 CKB 进程。远端定向 [develop CI run 35074014197](https://github.com/nervosnetwork/ckb-py-integration-test/actions/runs/35074014197) 从 `nervosnetwork/ckb@develop` 构建 CKB 并成功执行 `test_cases/v210`。截至本分析提交时未发现新增 issue。
