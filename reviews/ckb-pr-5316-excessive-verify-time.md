# CKB PR #5316：ExcessiveVerifyTime 集成测试入口

当前集成清单保留 **EVT-01、EVT-04、EVT-05、EVT-06**，统一见[集成测试主文档](ckb-pr-5316-non-test.md)：真实relay/notify后的预算拒绝、本地重试、重启恢复、peer后续交易及包含相同交易的合法区块。

统一使用项目现有普通CKB二进制，保持默认8秒CPU上限；正常配置不写入 `max_tx_verify_time_ms` 等覆盖字段，无需另编译test feature。发送端以本地提交接受合法慢交易，接收端经真实relay/notify观察预算拒绝。

预算按执行线程CPU时间累计；网络耗尽不生成公开拒绝记录/通知或单独导致ban，旧`-1113`映射已删除。8秒是上限，带cycles声明的Remote仍会自动校准，实际预算可能更短；Proposal使用该上限，Local/Recovery不受此网络预算。通过目标hash的实际预算拒绝日志确认到达目标路径，再检查真实RPC、peer和链状态；单纯等待或交易尚未入池不证明预算耗尽。

EVT-02/03的精确CPU边界、off-CPU及暂停收据依赖内部观察，已移出集成清单，历史编号保留。本次只修订文档，现有TEST-MAP不表示新head已经运行通过。
