# CKB PR #5316：非测试 diff 补充测试分析（用例设计与自动化映射）

## 范围、变更与口径

- 固定 base/merge-base `97645fc3e1cd902b43b872a08907ce335226f369` → head `60b8e349470bdffe57e9e92c3e0961b91c326b3a`。沿用本地固定源码及上一轮原始 API/diff，不重新把移动分支当已审版本。
- 用户要求“除了测试代码可以不用考虑”：排除测试文件及混合文件里的 test-only 代码；保留产品、配置、构建、删除的旧实现。benchmark/测量工具单列影响处置，不拿性能脚本成功当产品正确性证明。
- rename-aware 总清单306项，其中143项测试路径（含1次test文件重命名），另 script/src/scheduler.rs 的全部改动仅 cfg(test)，合计144项排除；剩162项有非测试内容：107生产/配置、18构建、16文档、21测量工具。混合文件计一次，内部测试段不作行为证据。
- 96个补充点：保留并修订 EVT-01～06；新增NTD点；原TPA-01～25仍在原准入文档，不重复定义。当前提交包含关联 Python 回归用例；49/96 个点有 TEST-MAP。映射不等于完整覆盖或测试通过，运行前提与已知失败见[用例说明](../test_cases/tx_pool_refactor/tests/README.md)。
- 原始逐文件差异处置为本地设计辅助材料，本提交不包含过程日志；“有处置”只表示已纳入/说明原因，不表示每条分支证明完备。
- 重要修正：EVT旧稿的 Local send/dryrun 超时前提、-1106错误码、可配置min/max时间和初始load envelope描述已过时；当前源码是网络预算、-1113、一次机器校准、生产固定cap。本稿取代旧EVT草案。
- 新增补齐：等待/调度、reorg/proposal、packing/template、cache/syscall、RPC/relay、通知、持久化/关机、compact结构、配置/构建和DB维护。

## 共同 oracle 与层级

1. 默认固定tip、关闭无关提交/挖矿、合法签名与共识cycles，非被测资源充足；精确额度从当前配置/consensus推导，不硬编码机器耗时。每个分支单独fixture，出错路径不共享被污染的前置状态。
2. 交错/内部额度/VM收据用原生可控屏障、可控时钟或已有测试接缝；外部Python只证明RPC/P2P/链结果。不得用sleep和多次查询相同值替代中途原子性证明。
3. 观察Accepted须同时看精确hash集合、公开body与总量；内部检查owner身份、依赖足迹、账目和已提交事件。比较独立重算/规范验证oracle，不调用被测同一算法计算expected。
4. 内部事件义务原子/FIFO不承诺外围HTTP/WebSocket/indexer零丢失。notify掉消息后pending overlay可能陈旧且无自动resync，这是现有限制，不能写成已保证恢复。
5. Source=observation的预期均待G1确认其产品意图；“实现这样做”不是规格批准，也不是发现缺陷。本轮不设任意吞吐/RSS/性能阈值。

## Spec

### SPEC-01：配置升级与真实构建边界
Condition: 解析旧版配置或选择生产 feature
Expected: 旧字段兼容但不恢复废弃策略；默认 fee_rate，祖先数下限1000；时间覆盖字段仅 test feature 可用
Observable: 反序列化值、启动结果、feature 编译结果
Basis: TxPoolConfig/From、Limits::with_residency；Cargo feature 传递；[util/app-config/src/legacy/tx_pool.rs](https://github.com/nervosnetwork/ckb/blob/60b8e349470bdffe57e9e92c3e0961b91c326b3a/util/app-config/src/legacy/tx_pool.rs)；[util/app-config/src/configs/tx_pool.rs](https://github.com/nervosnetwork/ckb/blob/60b8e349470bdffe57e9e92c3e0961b91c326b3a/util/app-config/src/configs/tx_pool.rs)；[resource/ckb.toml](https://github.com/nervosnetwork/ckb/blob/60b8e349470bdffe57e9e92c3e0961b91c326b3a/resource/ckb.toml)；[tx-pool/src/authority/budget.rs](https://github.com/nervosnetwork/ckb/blob/60b8e349470bdffe57e9e92c3e0961b91c326b3a/tx-pool/src/authority/budget.rs)；[Cargo.toml](https://github.com/nervosnetwork/ckb/blob/60b8e349470bdffe57e9e92c3e0961b91c326b3a/Cargo.toml)
Source: observation
Testing: required

### SPEC-02：当前网络活跃 VM 时间策略
Condition: 冷证明缓存且其他容量/共识条件满足
Expected: 仅 Remote/Proposal 带累计活跃时间预算；Local/Recovery 无该预算；耗尽不是共识无效
Observable: DeadlineExceeded、Reject 分类、RPC 转换、peer 和区块结果
Basis: Source::verification_time_limit；verify_rtx；VmSliceState / VmVerificationBudget；RPCError；[tx-pool/src/authority/model.rs](https://github.com/nervosnetwork/ckb/blob/60b8e349470bdffe57e9e92c3e0961b91c326b3a/tx-pool/src/authority/model.rs)；[tx-pool/src/verification.rs](https://github.com/nervosnetwork/ckb/blob/60b8e349470bdffe57e9e92c3e0961b91c326b3a/tx-pool/src/verification.rs)；[script/src/verify.rs](https://github.com/nervosnetwork/ckb/blob/60b8e349470bdffe57e9e92c3e0961b91c326b3a/script/src/verify.rs)；[rpc/src/error.rs](https://github.com/nervosnetwork/ckb/blob/60b8e349470bdffe57e9e92c3e0961b91c326b3a/rpc/src/error.rs)；[util/types/src/core/tx_pool.rs](https://github.com/nervosnetwork/ckb/blob/60b8e349470bdffe57e9e92c3e0961b91c326b3a/util/types/src/core/tx_pool.rs)
Source: explicit
Testing: required

### SPEC-03：校准、加载与 VM 子任务寿命
Condition: 网络任务执行或控制命令改变
Expected: 机器校准一次复用；加载在活跃片段内；初始控制级别继承；取消不放任 VM 子任务
Observable: 预算计算值、加载屏障、子任务和资源计数
Basis: VmTiming::limit；VmChildTask::drop；chunk_run_with_signal；[tx-pool/src/verification/calibration.rs](https://github.com/nervosnetwork/ckb/blob/60b8e349470bdffe57e9e92c3e0961b91c326b3a/tx-pool/src/verification/calibration.rs)；[script/src/verify.rs](https://github.com/nervosnetwork/ckb/blob/60b8e349470bdffe57e9e92c3e0961b91c326b3a/script/src/verify.rs)
Source: explicit
Testing: required

### SPEC-04：选择顺序、计算许可与来源配额
Condition: 多个队列/来源竞争验证和资源
Expected: 排序是选择顺序非完成顺序；许可在出队前取得并持有到结算；peer/remote 不吞 trusted 余量
Observable: 实际选取轨迹、队列、CPU/active 计数、原始 peer 归属
Basis: queue::OrderKey；Budget active guards；service worker；[tx-pool/src/authority/queue.rs](https://github.com/nervosnetwork/ckb/blob/60b8e349470bdffe57e9e92c3e0961b91c326b3a/tx-pool/src/authority/queue.rs)；[tx-pool/src/authority/budget.rs](https://github.com/nervosnetwork/ckb/blob/60b8e349470bdffe57e9e92c3e0961b91c326b3a/tx-pool/src/authority/budget.rs)；[tx-pool/src/authority/service/execution.rs](https://github.com/nervosnetwork/ckb/blob/60b8e349470bdffe57e9e92c3e0961b91c326b3a/tx-pool/src/authority/service/execution.rs)；[tx-pool/src/authority/service.rs](https://github.com/nervosnetwork/ckb/blob/60b8e349470bdffe57e9e92c3e0961b91c326b3a/tx-pool/src/authority/service.rs)；[tx-pool/src/authority/jobs.rs](https://github.com/nervosnetwork/ckb/blob/60b8e349470bdffe57e9e92c3e0961b91c326b3a/tx-pool/src/authority/jobs.rs)
Source: observation
Testing: required

### SPEC-05：缺依赖分解、等待与唤醒
Condition: resolve遇到缺cell/dep或后续producer变化
Expected: 完整依赖足迹被观察；trusted仅等已知preaccepted且存在的输出；无效header/depgroup终局拒绝；唤醒重验
Observable: Waiting keys、Resolve队列、拒绝类型、按页唤醒及 Stale
Basis: jobs::resolve/missing；waiting::pending_producer/wake；[tx-pool/src/authority/jobs.rs](https://github.com/nervosnetwork/ckb/blob/60b8e349470bdffe57e9e92c3e0961b91c326b3a/tx-pool/src/authority/jobs.rs)；[tx-pool/src/authority/waiting.rs](https://github.com/nervosnetwork/ckb/blob/60b8e349470bdffe57e9e92c3e0961b91c326b3a/tx-pool/src/authority/waiting.rs)；[tx-pool/src/authority/store.rs](https://github.com/nervosnetwork/ckb/blob/60b8e349470bdffe57e9e92c3e0961b91c326b3a/tx-pool/src/authority/store.rs)；[tx-pool/src/dependency_sort.rs](https://github.com/nervosnetwork/ckb/blob/60b8e349470bdffe57e9e92c3e0961b91c326b3a/tx-pool/src/dependency_sort.rs)
Source: observation
Testing: required

### SPEC-06：可靠链更新与重组恢复
Condition: 链提交、截断、detach或generation替换
Expected: pause只控制计算；可靠链发布不依赖pool已启动；失效依赖及后代先撤下，恢复重新验证
Observable: snapshot tip、Accepted/Recovery集合、旧job提交结果、链进度
Basis: chain verify install_chain_tip_transition；authority chain::reconcile/recover；[chain/src/verify.rs](https://github.com/nervosnetwork/ckb/blob/60b8e349470bdffe57e9e92c3e0961b91c326b3a/chain/src/verify.rs)；[chain/src/init.rs](https://github.com/nervosnetwork/ckb/blob/60b8e349470bdffe57e9e92c3e0961b91c326b3a/chain/src/init.rs)；[chain/src/init_load_unverified.rs](https://github.com/nervosnetwork/ckb/blob/60b8e349470bdffe57e9e92c3e0961b91c326b3a/chain/src/init_load_unverified.rs)；[chain/src/utils/forkchanges.rs](https://github.com/nervosnetwork/ckb/blob/60b8e349470bdffe57e9e92c3e0961b91c326b3a/chain/src/utils/forkchanges.rs)；[tx-pool/src/authority/chain.rs](https://github.com/nervosnetwork/ckb/blob/60b8e349470bdffe57e9e92c3e0961b91c326b3a/tx-pool/src/authority/chain.rs)；[tx-pool/src/service/message.rs](https://github.com/nervosnetwork/ckb/blob/60b8e349470bdffe57e9e92c3e0961b91c326b3a/tx-pool/src/service/message.rs)
Source: explicit
Testing: required

### SPEC-07：proposal投影与不可变快照
Condition: proposal表前进或发生非连续变更
Expected: 按candidate=tip+1窗口构造Gap/Proposed，重复引用计数；非精确前驱回退重建
Observable: 对独立按height重算集合的逐项比较、旧快照不变
Basis: ProposalTable::height_position/finalize/successor_view；[util/proposal-table/src/lib.rs](https://github.com/nervosnetwork/ckb/blob/60b8e349470bdffe57e9e92c3e0961b91c326b3a/util/proposal-table/src/lib.rs)；[shared/src/shared_builder.rs](https://github.com/nervosnetwork/ckb/blob/60b8e349470bdffe57e9e92c3e0961b91c326b3a/shared/src/shared_builder.rs)
Source: observation
Testing: required

### SPEC-08：候选打包的因果与读取顺序
Condition: Accepted候选图参与pack
Expected: 费率选择保留祖先闭包，read-before-spend约束不是费率祖先；最终模板受字节/cycles约束
Observable: 独立拓扑/活性/预算oracle与全区块验证
Basis: packing.rs；packing/ordering.rs；block_assembler dao/cell_liveness；[tx-pool/src/authority/packing.rs](https://github.com/nervosnetwork/ckb/blob/60b8e349470bdffe57e9e92c3e0961b91c326b3a/tx-pool/src/authority/packing.rs)；[tx-pool/src/authority/packing/ordering.rs](https://github.com/nervosnetwork/ckb/blob/60b8e349470bdffe57e9e92c3e0961b91c326b3a/tx-pool/src/authority/packing/ordering.rs)；[tx-pool/src/authority/packing/graph.rs](https://github.com/nervosnetwork/ckb/blob/60b8e349470bdffe57e9e92c3e0961b91c326b3a/tx-pool/src/authority/packing/graph.rs)；[tx-pool/src/block_assembler/dao.rs](https://github.com/nervosnetwork/ckb/blob/60b8e349470bdffe57e9e92c3e0961b91c326b3a/tx-pool/src/block_assembler/dao.rs)；[tx-pool/src/block_assembler/cell_liveness.rs](https://github.com/nervosnetwork/ckb/blob/60b8e349470bdffe57e9e92c3e0961b91c326b3a/tx-pool/src/block_assembler/cell_liveness.rs)
Source: explicit
Testing: required

### SPEC-09：模板收据、叔块及调用期限
Condition: 并发模板请求与pool/uncle变更
Expected: controller入口起30秒不续期；只发布收据仍有效的准备结果；uncle/proposal选择符合精确预算；mandatory base限制取共识maximum，不取RPC请求字段；公开三参数既有忽略行为与RPC文档差异另待G1确认
Observable: 返回模板、work_id、selected owners、uncle版本、超时起点
Basis: authority/template；block_assembler candidate_uncles/mod；controller::get_block_template；[tx-pool/src/authority/template.rs](https://github.com/nervosnetwork/ckb/blob/60b8e349470bdffe57e9e92c3e0961b91c326b3a/tx-pool/src/authority/template.rs)；[tx-pool/src/block_assembler/mod.rs](https://github.com/nervosnetwork/ckb/blob/60b8e349470bdffe57e9e92c3e0961b91c326b3a/tx-pool/src/block_assembler/mod.rs)；[tx-pool/src/block_assembler/candidate_uncles.rs](https://github.com/nervosnetwork/ckb/blob/60b8e349470bdffe57e9e92c3e0961b91c326b3a/tx-pool/src/block_assembler/candidate_uncles.rs)；[tx-pool/src/block_assembler/template.rs](https://github.com/nervosnetwork/ckb/blob/60b8e349470bdffe57e9e92c3e0961b91c326b3a/tx-pool/src/block_assembler/template.rs)；[tx-pool/src/service/controller.rs](https://github.com/nervosnetwork/ckb/blob/60b8e349470bdffe57e9e92c3e0961b91c326b3a/tx-pool/src/service/controller.rs)；[rpc/src/module/miner.rs](https://github.com/nervosnetwork/ckb/blob/60b8e349470bdffe57e9e92c3e0961b91c326b3a/rpc/src/module/miner.rs)
Source: observation
Testing: required

### SPEC-10：缓存证明与真实验证上下文
Condition: pool或block尝试复用脚本验证缓存
Expected: key含witness/rules/可见cell来源；非脚本检查总执行；只有VM成功产生证明；assume-valid不造证明
Observable: VM执行计数、cache hit、contextual错误、区块cycles/fee
Basis: TxVerificationCacheKey::from_resolved；ContextualTransactionVerifier；BlockTxsVerifier；[verification/src/cache.rs](https://github.com/nervosnetwork/ckb/blob/60b8e349470bdffe57e9e92c3e0961b91c326b3a/verification/src/cache.rs)；[verification/src/transaction_verifier.rs](https://github.com/nervosnetwork/ckb/blob/60b8e349470bdffe57e9e92c3e0961b91c326b3a/verification/src/transaction_verifier.rs)；[verification/contextual/src/contextual_block_verifier.rs](https://github.com/nervosnetwork/ckb/blob/60b8e349470bdffe57e9e92c3e0961b91c326b3a/verification/contextual/src/contextual_block_verifier.rs)；[script/src/syscalls/load_header.rs](https://github.com/nervosnetwork/ckb/blob/60b8e349470bdffe57e9e92c3e0961b91c326b3a/script/src/syscalls/load_header.rs)；[script/src/syscalls/load_block_extension.rs](https://github.com/nervosnetwork/ckb/blob/60b8e349470bdffe57e9e92c3e0961b91c326b3a/script/src/syscalls/load_block_extension.rs)；[script/src/syscalls/mod.rs](https://github.com/nervosnetwork/ckb/blob/60b8e349470bdffe57e9e92c3e0961b91c326b3a/script/src/syscalls/mod.rs)
Source: explicit
Testing: required

### SPEC-11：公共查询与同步提交语义
Condition: RPC/controller查询混合内部阶段或管理池
Expected: 提交成功表示已验证准入；body仅Accepted；Gap对外pending；统计有明确定义；管理请求有序
Observable: RPC JSON、raw pool、内部阶段屏障、controller返回
Basis: query.rs；rpc pool/chain；jsonrpc pool/terminal；controller dispatch；[tx-pool/src/authority/query.rs](https://github.com/nervosnetwork/ckb/blob/60b8e349470bdffe57e9e92c3e0961b91c326b3a/tx-pool/src/authority/query.rs)；[rpc/src/module/pool.rs](https://github.com/nervosnetwork/ckb/blob/60b8e349470bdffe57e9e92c3e0961b91c326b3a/rpc/src/module/pool.rs)；[rpc/src/module/chain.rs](https://github.com/nervosnetwork/ckb/blob/60b8e349470bdffe57e9e92c3e0961b91c326b3a/rpc/src/module/chain.rs)；[util/jsonrpc-types/src/pool.rs](https://github.com/nervosnetwork/ckb/blob/60b8e349470bdffe57e9e92c3e0961b91c326b3a/util/jsonrpc-types/src/pool.rs)；[util/jsonrpc-types/src/terminal.rs](https://github.com/nervosnetwork/ckb/blob/60b8e349470bdffe57e9e92c3e0961b91c326b3a/util/jsonrpc-types/src/terminal.rs)；[util/jsonrpc-types/src/blockchain.rs](https://github.com/nervosnetwork/ckb/blob/60b8e349470bdffe57e9e92c3e0961b91c326b3a/util/jsonrpc-types/src/blockchain.rs)；[tx-pool/src/service/controller.rs](https://github.com/nervosnetwork/ckb/blob/60b8e349470bdffe57e9e92c3e0961b91c326b3a/tx-pool/src/service/controller.rs)；[tx-pool/src/service/dispatch.rs](https://github.com/nervosnetwork/ckb/blob/60b8e349470bdffe57e9e92c3e0961b91c326b3a/tx-pool/src/service/dispatch.rs)
Source: explicit
Testing: required

### SPEC-12：P2P批量入站与可恢复relay投影
Condition: 请求交易/提案到达或relay受压
Expected: 先验证请求/边界再维护跟踪；已完成前缀与未完成后缀可区别；derived mailbox溢出reset但不阻塞authority
Observable: known/requesting集合、completed前缀、reset顺序、peer ban、pool状态
Basis: transactions_process；block_proposal_process；types add_ask_for_txs；authority relay/ingress；[sync/src/relayer/transactions_process.rs](https://github.com/nervosnetwork/ckb/blob/60b8e349470bdffe57e9e92c3e0961b91c326b3a/sync/src/relayer/transactions_process.rs)；[sync/src/relayer/block_proposal_process.rs](https://github.com/nervosnetwork/ckb/blob/60b8e349470bdffe57e9e92c3e0961b91c326b3a/sync/src/relayer/block_proposal_process.rs)；[sync/src/relayer/mod.rs](https://github.com/nervosnetwork/ckb/blob/60b8e349470bdffe57e9e92c3e0961b91c326b3a/sync/src/relayer/mod.rs)；[sync/src/types/mod.rs](https://github.com/nervosnetwork/ckb/blob/60b8e349470bdffe57e9e92c3e0961b91c326b3a/sync/src/types/mod.rs)；[tx-pool/src/authority/relay.rs](https://github.com/nervosnetwork/ckb/blob/60b8e349470bdffe57e9e92c3e0961b91c326b3a/tx-pool/src/authority/relay.rs)；[tx-pool/src/authority/ingress.rs](https://github.com/nervosnetwork/ckb/blob/60b8e349470bdffe57e9e92c3e0961b91c326b3a/tx-pool/src/authority/ingress.rs)；[tx-pool/src/service/message.rs](https://github.com/nervosnetwork/ckb/blob/60b8e349470bdffe57e9e92c3e0961b91c326b3a/tx-pool/src/service/message.rs)
Source: explicit
Testing: required

### SPEC-13：事件出版与best-effort外围通知
Condition: 成功提交的事件等待发布或端点失败
Expected: 内部FIFO只发布ready prefix并在guard外执行；端点失败隔离；外围notify可丢弃但有诊断
Observable: 批次序列、callback重入、disabled状态、drop metrics与owner进度
Basis: notice Outbox/Endpoints/RecentWrites；callback；notify try_notify_transaction；[tx-pool/src/authority/notice.rs](https://github.com/nervosnetwork/ckb/blob/60b8e349470bdffe57e9e92c3e0961b91c326b3a/tx-pool/src/authority/notice.rs)；[tx-pool/src/callback.rs](https://github.com/nervosnetwork/ckb/blob/60b8e349470bdffe57e9e92c3e0961b91c326b3a/tx-pool/src/callback.rs)；[notify/src/lib.rs](https://github.com/nervosnetwork/ckb/blob/60b8e349470bdffe57e9e92c3e0961b91c326b3a/notify/src/lib.rs)；[rpc/src/module/subscription.rs](https://github.com/nervosnetwork/ckb/blob/60b8e349470bdffe57e9e92c3e0961b91c326b3a/rpc/src/module/subscription.rs)；[tx-pool/src/block_assembler/notify.rs](https://github.com/nervosnetwork/ckb/blob/60b8e349470bdffe57e9e92c3e0961b91c326b3a/tx-pool/src/block_assembler/notify.rs)；[util/app-config/src/configs/notify.rs](https://github.com/nervosnetwork/ckb/blob/60b8e349470bdffe57e9e92c3e0961b91c326b3a/util/app-config/src/configs/notify.rs)
Source: explicit
Testing: required

### SPEC-14：有界持久化与恢复
Condition: 启动读取v1/v2或正常退出保存
Expected: v2优先不回退坏文件；恢复重新验证和父先子后；写临时文件同步后rename；单writer lease
Observable: 文件格式/大小、Accepted集合、恢复顺序、错误日志、写入顺序
Basis: persisted load/prepare_replay/save；builder startup_replay；[tx-pool/src/persisted.rs](https://github.com/nervosnetwork/ckb/blob/60b8e349470bdffe57e9e92c3e0961b91c326b3a/tx-pool/src/persisted.rs)；[tx-pool/src/service/builder.rs](https://github.com/nervosnetwork/ckb/blob/60b8e349470bdffe57e9e92c3e0961b91c326b3a/tx-pool/src/service/builder.rs)；[tx-pool/src/dependency_sort.rs](https://github.com/nervosnetwork/ckb/blob/60b8e349470bdffe57e9e92c3e0961b91c326b3a/tx-pool/src/dependency_sort.rs)
Source: explicit
Testing: required

### SPEC-15：启动、停止与chain-only调用
Condition: builder组装、后台故障、服务关闭或离线import/replay
Expected: read lane在replay回调时可用；停止收束owned任务后才save；fault不保存；离线先drop未用pool再join链
Observable: query返回、started、任务完成与保存触发顺序、临时DB句柄
Basis: builder::start/drain；SharedPackage::into_chain_services_builder；ChainServiceScope；[tx-pool/src/service/builder.rs](https://github.com/nervosnetwork/ckb/blob/60b8e349470bdffe57e9e92c3e0961b91c326b3a/tx-pool/src/service/builder.rs)；[tx-pool/src/service/controller.rs](https://github.com/nervosnetwork/ckb/blob/60b8e349470bdffe57e9e92c3e0961b91c326b3a/tx-pool/src/service/controller.rs)；[tx-pool/src/service/dispatch.rs](https://github.com/nervosnetwork/ckb/blob/60b8e349470bdffe57e9e92c3e0961b91c326b3a/tx-pool/src/service/dispatch.rs)；[shared/src/shared_builder.rs](https://github.com/nervosnetwork/ckb/blob/60b8e349470bdffe57e9e92c3e0961b91c326b3a/shared/src/shared_builder.rs)；[ckb-bin/src/subcommand/import.rs](https://github.com/nervosnetwork/ckb/blob/60b8e349470bdffe57e9e92c3e0961b91c326b3a/ckb-bin/src/subcommand/import.rs)；[ckb-bin/src/subcommand/replay.rs](https://github.com/nervosnetwork/ckb/blob/60b8e349470bdffe57e9e92c3e0961b91c326b3a/ckb-bin/src/subcommand/replay.rs)；[chain/src/init.rs](https://github.com/nervosnetwork/ckb/blob/60b8e349470bdffe57e9e92c3e0961b91c326b3a/chain/src/init.rs)
Source: explicit
Testing: required

### SPEC-16：内存驻留、确定性比较及可诊断构建
Condition: retain packed字段、查询快照或开启诊断feature
Expected: 脱离大envelope的固定key/compact视图保持内容和hash；checked计算不wrap；诊断feature独立可组合
Observable: backing大小、hash/字段等价、比较次序、构建与诊断输出
Basis: try_into_compact；compact_packed；sort_key；metrics；main features；[util/types/src/core/views.rs](https://github.com/nervosnetwork/ckb/blob/60b8e349470bdffe57e9e92c3e0961b91c326b3a/util/types/src/core/views.rs)；[util/gen-types/src/extension/rust_core_traits.rs](https://github.com/nervosnetwork/ckb/blob/60b8e349470bdffe57e9e92c3e0961b91c326b3a/util/gen-types/src/extension/rust_core_traits.rs)；[tx-pool/src/util.rs](https://github.com/nervosnetwork/ckb/blob/60b8e349470bdffe57e9e92c3e0961b91c326b3a/tx-pool/src/util.rs)；[tx-pool/src/authority/residency.rs](https://github.com/nervosnetwork/ckb/blob/60b8e349470bdffe57e9e92c3e0961b91c326b3a/tx-pool/src/authority/residency.rs)；[tx-pool/src/component/entry.rs](https://github.com/nervosnetwork/ckb/blob/60b8e349470bdffe57e9e92c3e0961b91c326b3a/tx-pool/src/component/entry.rs)；[tx-pool/src/component/sort_key.rs](https://github.com/nervosnetwork/ckb/blob/60b8e349470bdffe57e9e92c3e0961b91c326b3a/tx-pool/src/component/sort_key.rs)；[tx-pool/src/metrics.rs](https://github.com/nervosnetwork/ckb/blob/60b8e349470bdffe57e9e92c3e0961b91c326b3a/tx-pool/src/metrics.rs)；[util/metrics/src/lib.rs](https://github.com/nervosnetwork/ckb/blob/60b8e349470bdffe57e9e92c3e0961b91c326b3a/util/metrics/src/lib.rs)；[src/main.rs](https://github.com/nervosnetwork/ckb/blob/60b8e349470bdffe57e9e92c3e0961b91c326b3a/src/main.rs)；[build.rs](https://github.com/nervosnetwork/ckb/blob/60b8e349470bdffe57e9e92c3e0961b91c326b3a/build.rs)；[Cargo.toml](https://github.com/nervosnetwork/ckb/blob/60b8e349470bdffe57e9e92c3e0961b91c326b3a/Cargo.toml)
Source: observation
Testing: required

### SPEC-17：过期与recent-reject并发维护
Condition: 到达过期边界或DB shard收缩
Expected: accepted timestamp严格早于cut才过期；remote deadline到点可过期；DB计数近似但不wrap，缺shard可恢复
Observable: member闭包、effect、维护进度、DB get/put/count与锁时序
Basis: store::expired；Pool::maintain；RecentReject::put_serialized/shrink；[tx-pool/src/authority/store.rs](https://github.com/nervosnetwork/ckb/blob/60b8e349470bdffe57e9e92c3e0961b91c326b3a/tx-pool/src/authority/store.rs)；[tx-pool/src/authority/service.rs](https://github.com/nervosnetwork/ckb/blob/60b8e349470bdffe57e9e92c3e0961b91c326b3a/tx-pool/src/authority/service.rs)；[tx-pool/src/component/recent_reject.rs](https://github.com/nervosnetwork/ckb/blob/60b8e349470bdffe57e9e92c3e0961b91c326b3a/tx-pool/src/component/recent_reject.rs)
Source: observation
Testing: required

### SPEC-18：无独立产品行为的差异处置
Condition: 纯文档、开发说明、benchmark/测量工具或已替换旧入口
Expected: 文档不作执行指令；删除实现由新行为回归承接；工具不构成性能验收结论
Observable: 逐文件映射、替代路径、工具证据限制
Basis: 完整diff及各文件角色；见diff-impact
Source: observation
Testing: not_tested — 纯说明不单独写运行用例；测量工具数值有效性/跨主机性能阈值另需明确性能目标，本轮仅影响处置

## 测试树

<!-- TEST-TREE-BEGIN -->
- 非测试产品行为补充
  - 配置升级与真实构建边界
    - 升级后的有效值
      - NTD-01 [primary] -> SPEC-01
    - 生产字段隔离
      - NTD-02 [primary] -> SPEC-01
    - 非法资源算术
      - NTD-03 [primary] -> SPEC-01
  - 当前网络活跃 VM 时间策略
    - 预算来源与错误出口
      - EVT-01 [primary] -> SPEC-02
    - 累计执行收据边界
      - EVT-02 [primary] -> SPEC-02
    - 非活跃间隔
      - EVT-03 [primary] -> SPEC-02
    - 超时后的再验证
      - EVT-04 [primary] -> SPEC-02
    - 对端仍可工作
      - EVT-05 [primary] -> SPEC-02
    - 共识隔离
      - EVT-06 [primary] -> SPEC-02
  - 校准、加载与 VM 子任务寿命
    - 校准计算
      - NTD-04 [primary] -> SPEC-03
    - 程序加载计费
      - NTD-05 [primary] -> SPEC-03
    - 组间暂停级别
      - NTD-06 [primary] -> SPEC-03
    - 取消 VM 所有权
      - NTD-07 [primary] -> SPEC-03
  - 选择顺序、计算许可与来源配额
    - 费率选择
      - NTD-08 [primary] -> SPEC-04
    - 阈值不是执行上限
      - NTD-09 [primary] -> SPEC-04
    - 许可与饱和
      - NTD-10 [primary] -> SPEC-04
    - peer 与 trusted 余量
      - NTD-11 [primary] -> SPEC-04
    - 推广不改占用来源
      - NTD-12 [primary] -> SPEC-04
  - 缺依赖分解、等待与唤醒
    - 可信来源未知producer
      - NTD-13 [primary] -> SPEC-05
    - 展开的缺依赖
      - NTD-14 [primary] -> SPEC-05
    - 无效依赖不是孤儿
      - NTD-15 [primary] -> SPEC-05
    - 页间与重验
      - NTD-16 [primary] -> SPEC-05
    - 可信producer消失
      - NTD-17 [primary] -> SPEC-05
  - 可靠链更新与重组恢复
    - pause期间链进度
      - NTD-18 [primary] -> SPEC-06
    - 块内消费与后代
      - NTD-19 [primary] -> SPEC-06
    - reorg producer身份
      - NTD-20 [primary] -> SPEC-06
    - 等待者的非trigger冲突
      - NTD-21 [primary] -> SPEC-06
    - Detailed有界恢复保留集合
      - NTD-22 [primary] -> SPEC-06
    - 启动历史扫描边界
      - NTD-23 [primary] -> SPEC-06
    - 超限ReplaceGeneration清空
      - NTD-90 [primary] -> SPEC-06
  - proposal投影与不可变快照
    - 窗口边界与重复
      - NTD-24 [primary] -> SPEC-07
    - 不连续与身份
      - NTD-25 [primary] -> SPEC-07
    - 起始与终端height
      - NTD-26 [primary] -> SPEC-07
  - 候选打包的因果与读取顺序
    - 共享祖先费率
      - NTD-27 [primary] -> SPEC-08
    - 依赖reader先于spender
      - NTD-28 [primary] -> SPEC-08
    - 条件约束形成环
      - NTD-29 [primary] -> SPEC-08
    - 精确预算
      - NTD-30 [primary] -> SPEC-08
    - liveness缓存与overlay
      - NTD-31 [primary] -> SPEC-08
    - 图缓存失效
      - NTD-32 [primary] -> SPEC-08
  - 模板收据、叔块及调用期限
    - 选中集合失效
      - NTD-33 [primary] -> SPEC-09
    - 无关添加
      - NTD-34 [primary] -> SPEC-09
    - 请求deadline
      - NTD-35 [primary] -> SPEC-09
    - proposal/uncle竞争
      - NTD-36 [primary] -> SPEC-09
    - uncle收据竞争
      - NTD-37 [primary] -> SPEC-09
    - 共识基础模板超限
      - NTD-38 [primary] -> SPEC-09
    - work_id耗尽
      - NTD-83 [primary] -> SPEC-09
    - uncle source version耗尽
      - NTD-84 [primary] -> SPEC-09
    - 公开模板参数兼容与文档差异
      - NTD-85 [primary] -> SPEC-09
    - 满池重复uncle拒绝
      - NTD-87 [primary] -> SPEC-09
    - 满池且目标height满
      - NTD-88 [primary] -> SPEC-09
    - 满池高height合法插入
      - NTD-89 [primary] -> SPEC-09
  - 缓存证明与真实验证上下文
    - 证明key隔离
      - NTD-39 [primary] -> SPEC-10
    - 命中仍做上下文检查
      - NTD-40 [primary] -> SPEC-10
    - assume-valid与失败不造证明
      - NTD-41 [primary] -> SPEC-10
    - 区块汇总限制
      - NTD-42 [primary] -> SPEC-10
    - syscall来源可见性
      - NTD-43 [primary] -> SPEC-10
  - 公共查询与同步提交语义
    - 提交的完成点
      - NTD-44 [primary] -> SPEC-11
    - 跨阶段公开投影
      - NTD-45 [primary] -> SPEC-11
    - 统计与排名
      - NTD-46 [primary] -> SPEC-11
    - 查询标识不混用
      - NTD-47 [primary] -> SPEC-11
    - clear verify queue
      - NTD-48 [primary] -> SPEC-11
    - live-cell外部查询
      - NTD-75 [primary] -> SPEC-11
    - 估费边界
      - NTD-76 [primary] -> SPEC-11
    - 管理槽与有序清池
      - NTD-77 [primary] -> SPEC-11
    - 本地移除与旧身份
      - NTD-78 [primary] -> SPEC-11
  - P2P批量入站与可恢复relay投影
    - 已请求Remote与取消
      - NTD-49 [primary] -> SPEC-12
    - Proposal响应边界
      - NTD-50 [primary] -> SPEC-12
    - 恶意来源撤销
      - NTD-51 [primary] -> SPEC-12
    - 未知交易索引配额
      - NTD-52 [primary] -> SPEC-12
    - 断线后的backup
      - NTD-53 [primary] -> SPEC-12
    - mailbox饱和
      - NTD-54 [primary] -> SPEC-12
    - IBD与无peer也drain
      - NTD-55 [primary] -> SPEC-12
    - bounded controller载荷
      - NTD-82 [primary] -> SPEC-12
  - 事件出版与best-effort外围通知
    - 未ready头不被越过
      - NTD-56 [primary] -> SPEC-13
    - callback panic隔离
      - NTD-57 [primary] -> SPEC-13
    - recent失败冷却
      - NTD-58 [primary] -> SPEC-13
    - 外围通知满/闭
      - NTD-59 [primary] -> SPEC-13
    - 矿工通知生命周期
      - NTD-60 [primary] -> SPEC-13
    - 同步callback写重入拒绝
      - NTD-86 [primary] -> SPEC-13
  - 有界持久化与恢复
    - v1/v2启动重验
      - NTD-61 [primary] -> SPEC-14
    - 坏v2不降级
      - NTD-62 [primary] -> SPEC-14
    - 成功原子保存
      - NTD-63 [primary] -> SPEC-14
    - rename前失败
      - NTD-64 [primary] -> SPEC-14
    - rename后清旧失败
      - NTD-65 [primary] -> SPEC-14
  - 启动、停止与chain-only调用
    - replay回调重入
      - NTD-66 [primary] -> SPEC-15
    - 正常stop顺序
      - NTD-67 [primary] -> SPEC-15
    - 故障stop
      - NTD-68 [primary] -> SPEC-15
    - 离线资源关闭
      - NTD-69 [primary] -> SPEC-15
  - 内存驻留、确定性比较及可诊断构建
    - 紧凑持有的内容
      - NTD-70 [primary] -> SPEC-16
    - 比较器边界
      - NTD-71 [primary] -> SPEC-16
    - production构建矩阵
      - NTD-72 [primary] -> SPEC-16
    - 诊断失败不阻止节点
      - NTD-73 [primary] -> SPEC-16
    - 指标语义
      - NTD-74 [primary] -> SPEC-16
  - 过期与recent-reject并发维护
    - 过期边界和闭包
      - NTD-79 [primary] -> SPEC-17
    - 计数与DB收缩交错
      - NTD-80 [primary] -> SPEC-17
    - 已存在key与表示边界
      - NTD-81 [primary] -> SPEC-17
  - 准入/RBF/容量 [not_applicable] — 当前新增表不重定义，复用已存在准入评审，语义及原有Case ID保留；完整diff映射链接到该文档。
  - 测试源码 [not_applicable] — 用户明确排除；混合文件内test-only段同样排除。
  - 文档与测量工具 [pending] — 已完成文件角色和影响处置，测量解析器/证据完整性与跨平台性能阈值未细化产品用例；续点为 tx-pool/scripts 及 docs/BENCHMARK.md，需单独性能目标。
<!-- TEST-TREE-END -->

## 完整变更用例表

<!-- TEST-CASES-BEGIN -->
| 用例 | 场景 | 预期结果 | 防止的问题 | 优先级 |
| --- | --- | --- | --- | --- |
| `NTD-01` | - [x] 分别解析发布版完整 tx_pool 配置、附带废弃 max_mem_size/max_cycles/cache 字段的配置 | 两者成功；废弃字段不改变有效策略；省略 verify_ordering 时为 fee_rate；祖先25规范化1000，1001保留 | 升级失败或悄悄恢复旧限额 | P1 |
| `NTD-02` | - [x] 对无 test feature 的独立构建分别添加 max_tx_verify_time_ms、min_tx_verify_time_ms、cycles_per_ms 字段 | 每个未知字段解析失败；生产序列化无这些字段；固定内部 cap 为 MIN_BLOCK_INTERVAL | 测试 feature 污染生产配置 | P0 |
| `NTD-03` | - [x] 构造使 W+2 或派生驻留字节算术溢出的配置后调用 fallible builder | 构建返回有原因的错误，不 panic、不启动半个池；显式 W=0 不被解析器改为默认值 | 整数溢出、半初始化服务 | P1 |
| `EVT-01` | - [x] 合法慢交易分别以 Remote、Proposal、Local、Recovery 验证；在内部给网络来源确定的小预算；网络候选原本会替换 A→C | 网络达到预算返回 ExcessiveVerifyTime 且未替换 A/C；错误适配为 -1113；Local/Recovery 不因这一本地预算拒绝；settlement 后本次 active 计数归还 | 错误来源、旧 -1106 断言或超时后部分替换 | P0 |
| `EVT-02` | - [ ] 在可控片段中分别累计 T−ε、T、T+ε，并令跨两个脚本组累计达到 T；让完成收据与计时器交错 | 小于T可成功，达到T耗尽；下一组只拿剩余预算；按子任务实际累计收据判定而不是父任务收到通知的先后 | 等号、组间重置、计时竞争误判 | P1 |
| `EVT-03` | - [ ] 冷缓存任务排队、Suspend 空闲及父任务迟读收据超过T，实际所有 Running 片段累计小于T后 Resume | 这些非活跃间隔不收费，恢复保留此前已用量，合法交易完成 | 墙钟等待误算或恢复清零 | P1 |
| `EVT-04` | - [x] 冷缓存网络交易在测试构建低 cap 下超时；确认 recent endpoint 健康、无该 hash 记录；重建更高 cap 服务后重提 | 首次不产生成功证明、不新增 recent-reject；无残留 active/job；后次实际验证并成功，不假定生产热更新预算 | 失败污染证明、拒绝记录或占用 | P1 |
| `EVT-05` | - [x] peer 声明 cycles 正确的合法交易仅触发时间预算；其后同 peer 提交快速合法交易 | 超时不算 malformed、不因此 ban，不写 recent；释放此次 relay 跟踪，后笔可 Accepted；不保证自动重传或网络送达 | 本地策略误罚 peer | P0 |
| `EVT-06` | - [x] 本节点池因低测试预算拒绝合法交易后，从独立合法出块路径收到包含它且 proposal/inputs/cycles 均有效的区块 | 完整共识验证接受该块；交易最终 committed，不复用池超时结论 | 节点本地计时污染共识 | P0 |
| `NTD-04` | - [ ] 给定固定 loading/execution measurement，包括 ceil 非整除和最小/最大 cap 输入，计算预算 | 按16倍裕量导出速度/最小量；ceil(cycles/rate) 与 minimum 取大后受 cap 截断；校准失败回退 cap；同进程换 generation 不重采样 | 声明 cycles 操控校准或舍入越界 | P1 |
| `NTD-05` | - [ ] 网络验证冷缓存交易，在 root 与动态程序 provider 上分别注入可结束的加载延迟 | 加载落在 Running 片段、计入最终收据；达到预算是 ExcessiveVerifyTime；provider 返回前不要求强制抢占 | 漏算加载或错误承诺硬墙钟中断 | P1 |
| `NTD-06` | - [ ] 在下一普通脚本组开始前已发布 Suspend，观察加载钩子，再 Resume | Suspend 时下一组尚未加载，Resume 后完成；不用等待新的 Suspend 边沿 | 控制信号仅按边沿处理 | P1 |
| `NTD-07` | - [ ] 任务运行中取消父 future；同步 provider 受屏障控制并最终返回 | 发出 pause/Stop 并终止 owned child；provider 返回且任务收束后无继续运行的子 VM；活动许可与 owner 结算一致 | 脱离父任务的 VM 或提前释放许可 | P0 |
| `NTD-08` | - [ ] 固定可选来源/阶段/大小lane，放入费率不同与费率相同但 arrival 不同的交易，再逐次释放一个计算许可 | fee_rate 模式按精确费率、平局 arrival 等比较器选取；arrival_time 模式按 arrival；不要求并发完成顺序相同 | 浮点费率或把完成顺序当调度 oracle | P1 |
| `NTD-09` | - [x] 远端声明 cycles 分别为 max_tx_verify_cycles 与其加1，真实 cycles 匹配且低于共识上限 | 分入相应调度lane，均可正常验证；阈值加1不直接成为 VM cycles 拒绝 | 把历史配置名误作共识限制 | P1 |
| `NTD-10` | - [ ] 冻结计算许可，再放入可验证 owner；恢复许可后将结算暂挂 | 无许可不抢走待执行 owner；计算结束到最终settlement仍持 active 槽；settlement后归还，owner账目不与active重复混算 | 出队饥饿、未结算任务无限增长 | P0 |
| `NTD-11` | - [ ] W=0与W>0分别用同 peer、多个 peer 填满 active remote 配额，同时发 Local 请求 | 总 active≤W+2、remote≤W+1、每peer≤ceil((W+1)/4)；remote饱和仍留trusted余量；本地可在计算许可可用时完成 | 恶意 peer 占尽本地通道 | P1 |
| `NTD-12` | - [ ] 已选中 Remote 任务持 peer active许可时推广为 Proposal；结算前再向原 peer 申请许可 | 原许可仍归原peer直到旧Job释放，不因推广提前给该peer增加并发 | 推广绕过 peer 上限 | P1 |
| `NTD-13` | - [x] Local/Recovery引用不存在producer或已知producer越界output；与已知preaccepted且index有效的输入作对照 | 前两者 Unknown 终局拒绝；仅最后一种进入Waiting，producer Accepted后再resolve | 无限保留本地孤儿 | P0 |
| `NTD-14` | - [x] Remote交易同时缺input和depgroup成员；分别只补部分及补齐全部 | 等待键包含展开后真实缺口；只补部分不误变ready，全部就绪再resolve/verify | 只观察第一个Unknown | P1 |
| `NTD-15` | - [x] 有一个缺input，同时另一个depgroup为空/格式坏或header_dep不在有效链中，分别提交 | 走对应 Resolve 拒绝而非长期Waiting；header只在有效链中时可作为上下文 | Unknown掩盖终局错误 | P1 |
| `NTD-16` | - [x] 超过32个waiter共享trigger，分多页唤醒；一页计划后让trigger被spender消费再apply | 所有页最终有机会处理；过期页不按旧ready接受，必须Stale/重规划；cursor不静默跳过存活waiter | 分页丢唤醒或基于死cell接受 | P1 |
| `NTD-17` | - [ ] trusted waiter有多个缺key，首key仍未ready，后一个key的已知preaccepted producer消失 | 仍检查后续key并终局 Unknown 退场，而不是首个未ready就无限等待 | 提前短路掩盖永久缺失 | P1 |
| `NTD-18` | - [ ] 冻结VM到暂停边界并保持未完成，提交连续有效链更新 | 新链snapshot/可靠控制继续前进；pause持有期间不启动新的池计算，不要求已运行provider瞬停；idle释放后恢复 | pool反压卡住链 | P0 |
| `NTD-19` | - [x] Accepted有输入消费者和cell_dep reader及其后代，链附加块消费其引用cell | 冲突owner及受影响因果后代撤下；已attached交易按committed处理，不误删不相关Accepted | 链与池活性足迹分歧 | P0 |
| `NTD-20` | - [x] pool producer进入链后又detach，子交易依赖其input/cell_dep/depgroup；旧解析曾无block元信息 | 通过producer hash与相关足迹失效旧证明；子孙先撤出Accepted再恢复解析，不直接保留旧pool-cell结果 | 重组复用失效cell来源 | P0 |
| `NTD-21` | - [x] Waiting交易缺A，同时已声明的另一个input或dep B被新块消费 | 即使缺失trigger A不变，也根据完整足迹撤销受冲突waiter | 仅missing索引漏掉链消费 | P1 |
| `NTD-22` | - [x] 构造Detailed重组输入（含payload恰等limit的边界），使增量路径回退recover_bounded；旧池只含Accepted，detach/旧池有效父子均可fit，attached集合已知 | 独立计算应保留集合=旧Accepted与detach非cellbase并集减attached且按hash去重；全部先成为Recovery/Resolve，父先子重验证后符合条件者Accepted；新snapshot正确安装，不允许用空池代替应保留结果 | 恢复分支无声丢掉可保留交易 | P0 |
| `NTD-23` | - [ ] 启动时冻结未验证历史scan cut，扫描中让链服务处理后来写入的新块 | 只对cut内缺BlockExt历史提交一次验证，新写入由正常服务处理，无双重验证请求 | 启动扫描与正常写入竞争 | P1 |
| `NTD-24` | - [x] 同shortid在多个高度及block/uncle重复，推进经过closest、farthest边界 | 单height去重；仍有Proposed出现即Proposed优先Gap；最后一个Proposed过期后按剩余Gap/Pending投影；与独立height oracle一致 | refcount下溢或过早失去proposal | P0 |
| `NTD-25` | - [x] 替换同高度、remove后reorg、跨height跳跃、传同内容但不同identity origin 分别finalize | 每一种与从primitive table重建结果一致；原持有ProposalView内容不变 | 误走增量快捷路径 | P1 |
| `NTD-26` | - [ ] 用含genesis proposals的表测试tip接近0和u64::MAX；另构造closest=0或closest>farthest | genesis不作为commit证据；终端height无加法溢出且无eligible结果；非法window构建报错 | 投影边界偏离TwoPhaseCommit | P0 |
| `NTD-27` | - [x] chain/fanout/diamond中高费子拉低费父，固定byte/cycle预算 | 选择包祖先去重计费、父在子前，独立重算每项祖先和总量；不以短id碰撞混同交易 | CPFP或共享祖先总量错误 | P0 |
| `NTD-28` | - [x] A读取cell X，B消费X；A自身还依赖父P，P/A未先选中 | 选择B前必须纳入且排序P→A→B所需闭包或放弃B；reader不是fee ancestry；最终交易顺序经标准cellchecker有效 | 打包出无效依赖顺序 | P0 |
| `NTD-29` | - [ ] 构造因果依赖本身无环但read/spend条件边形成环的候选集，并附无关合法候选 | 有界修复删除完整受影响因果后代包，或有界回退；不输出部分环包，无关候选可继续，结果区块有效 | 无限修复或残留无效后代 | P0 |
| `NTD-30` | - [x] 固定基础开销，交易remaining budget分别等于精确serialized bytes/cycles与少1 | 等号允许装入，少1对应维度排除，基础开销始终纳入；失败候选不令后续可装交易越界 | header/cellbase开销漏算、边界误差 | P1 |
| `NTD-31` | - [ ] 相同tip多次pack缓存live/dead/unknown，期间模板overlay已花掉cached-live，随后换tip | 当前overlay优先旧chain memo，换tip清缓存；缓存受推导条目上限约束；DAO只对实际筛选的有序交易计算 | memo掩盖本次花费或旧tip活性 | P0 |
| `NTD-32` | - [x] 保持候选数相同但换一个owner identity；另改max_ancestors；再只推进proposal相位 | 前两种重建图；proposal相位用当前快照重算，不沿用旧phase；结果与冷cache pack一致 | 仅按长度/旧tip复用错误图 | P1 |
| `NTD-33` | - [x] prepare模板后替换其选中owner或同tip clear pool，再允许publish | 旧prepared模板不发布，重新基于有效owner/lifecycle生成；不是只比较tip hash | ABA模板含已失效交易 | P0 |
| `NTD-34` | - [x] prepare后只添加未选中独立交易且所有receipt仍有效 | 允许原结果发布；不强求所有pool变更都使模板失效；后次请求可纳新交易 | 过度失效引发活锁 | P1 |
| `NTD-35` | - [ ] 在controller入队前起计，使请求排队消耗大部分30秒，剩余构建超过原deadline；另一个请求等同一driver | 第一请求按原deadline超时，不续30秒，不取消共享driver；后请求能拿到后续有效结果 | 超时按阶段重置、取消他人工作 | P1 |
| `NTD-36` | - [ ] 可选proposal只部分装入剩余字节，某uncle与装入proposal冲突、其子uncle依赖它；另有仅与未装proposal冲突uncle | 只实际装入proposal抑制冲突uncle及必要后代；未装proposal不额外抑制；链已知父按规则保留；精确预算成立 | 预算外proposal改变叔块选择 | P1 |
| `NTD-37` | - [ ] prepare uncle selection后插入/删除候选改变版本，再尝试旧选择提交 | 旧receipt失败不应用其prune；重做成功才prune；候选集合只按成功的新receipt改变 | 过期读裁掉有效候选 | P1 |
| `NTD-38` | - [ ] 在内部assembler fixture令mandatory基础模板（header/cellbase等）serialized size大于consensus.max_block_bytes；不使用RPC bytes_limit作为触发条件 | build_base_template返回Err(BlockAssemblerError::Overflow)，不生成超出共识大小的模板；此例不涉及work_id或uncle source version耗尽 | 把未使用的请求参数误作模板构建预算 | P1 |
| `NTD-39` | - [x] 保持raw交易但改witness，或保持witness而跨VM规则版本/可见input与展开dep来源block，分别查询旧proof | 每种改变都不命中旧证明，重新验证；无header可见origin差异对照不制造虚假键变化 | 跨上下文缓存错误接受 | P0 |
| `NTD-40` | - [x] 先得到成功proof，再在新的环境令since/maturity或capacity检查失败而script key相同 | 仍拒绝对应非脚本错误；cached cycles大于max_cycles同样拒绝；不因命中跳过检查 | 缓存绕过共识约束 | P0 |
| `NTD-41` | - [ ] 分别经过assume-valid、普通VM失败、成功VM路径，检查同key proof有无；随后完整验证 | 前两类不新增成功proof，普通成功才有；后续完整验证不被零cycles或失败结果短路 | 信任状态升级成伪验证证明 | P0 |
| `NTD-42` | - [x] 给多笔各自低于max_block_cycles的成功缓存proof，其总和高于块上限；另构造sum恰好等于上限 | 超过拒块，等于且其他共识条件有效时接受；fee重新计算；不把缓存命中当免计cycles | 缓存降低区块总资源检查 | P0 |
| `NTD-43` | - [x] 对input和展开cell_dep调用load_header/load_block_extension，分别有transaction_info且其origin在header_deps、origin未声明、无info | 仅第一种可通过可见来源解析；其余按ITEM_MISSING，保持VM版本syscall gates；depgroup容器不是额外可见来源 | 脚本观察与proof key的可见性分歧 | P0 |
| `NTD-44` | - [x] 用屏障挂起Local验证/Apply，随后放行直至send_transaction成功，再重复提交同笔 | 成功前不会把排队当成功；返回后hash已Accepted可查body；再次提交为duplicate不是要求rebroadcast | 异步入站误作同步RPC成功 | P0 |
| `NTD-45` | - [x] 以屏障固定Resolve、Verify、Waiting、Accepted Pending/Gap/Proposed、Replaced，分别调用get_tx_status/get_transaction_with_status；非Accepted分别准备recent为空/有该hash拒绝记录的fixture | 只有Accepted公开body及pending/proposed；Gap公开pending而detail为gap；包括Waiting在内的非Accepted无body，recent为空为unknown、有记录为rejected；orphan仅是tx_pool_info的Waiting计数，不是交易状态枚举 | 把内部Waiting/orphan统计当成公开状态 | P0 |
| `NTD-46` | - [x] 同一稳定cut含queued、active、Waiting、Accepted pending/gap/proposed和history；查询info/raw/detail | bytes/cycles仅Accepted，orphan仅Waiting，verify_queue不计active；pending/gap rank从1起，proposed/unknown为0；集合及数量一致 | 统计把驻留资源和可用交易混算 | P1 |
| `NTD-47` | - [ ] 设置可区分raw full hash和proposal shortid的交易查询，使用fetch_txs及fetch_txs_with_cycles各自接口 | compact block接口按shortid语义取值；fullhash接口只返回指定完整hash的Accepted交易及cycles，不因shortid同前缀误返 | 查询标识碰撞或错误cycles | P1 |
| `NTD-48` | - [x] 混合Accepted和Resolve/Verify/Waiting/Replaced，排入clear_verify_queue，挂起旧job到清理后才结算 | 保留Accepted，清掉全部unaccepted/history；旧job无权复活；不是仅清一个VecDeque | 管理接口残留僵尸工作 | P0 |
| `NTD-49` | - [ ] batch含非请求peer、已known及有效请求项；有效项处理中间取消future或发生入站错误 | 只接受当前requesting peer且未知项；RAII仅保留已settled前缀的跟踪，未完成后缀取消known可重试，入口拒绝也计完成 | known永久抑制尚未处理交易 | P0 |
| `NTD-50` | - [ ] 响应count/bytes分别在协议上限与加1；包含请求内和未请求shortid | 超限在任何inflight维护前失败；合法响应仅消费请求项一次；Proposal入站失败不预先known阻断后续Remote | 先消费请求后校验，交易丢失 | P1 |
| `NTD-51` | - [ ] current Remote产生malformed/declared-wrong-cycles，另有同peer未准入owner、其他peer和Accepted；再重做但先将culprit推广 | current时按策略ban并撤同peer cohort，不误撤其他peer/Accepted；已推广使旧culprit stale时不按旧身份ban | 旧验证结果撤销新来源或过罚 | P0 |
| `NTD-52` | - [ ] 填满global unique50000与peer32767注册边界；注册重复peer/hash及已有hash的新backup peer | 重复不重复计数；global满时已有hash仍可增加未满额backup，新hash不进入；peer超限报错且集合不越界 | 多peer索引无界增长 | P1 |
| `NTD-53` | - [ ] 同hash有主请求peer及backup，主peer断开 | 移除主peer关系/请求时刻，backup立刻可再次请求，不继续等已断开peer超时 | 断线后交易停滞 | P1 |
| `NTD-54` | - [ ] 暂停relay消费直至item或bytes达界，再发布current Ok/Reject/UnknownParents；恢复drain | 发布不等待；先观察GenerationReset再current可容纳结果，已丢UnknownParents由存活Waiting分页重建；authority owner不回滚，队列始终有界 | relay反压占住提交/关机 | P0 |
| `NTD-55` | - [x] 先积累已提交relay结果，再切IBD或移走所有peers并poll relayer | 仍消费结果并更新known/reset投影；只是网络广播被gated；origin peer不作回传目标 | 无人连接时mailbox永久不消费 | P1 |
| `NTD-56` | - [ ] 较早batch未activate而后batch已ready；随后activate前批；callback里同步只读query | 后批不越过；guard外按FIFO发布，callback查询可返回不死锁；取消一个等待receipt的客户端不取消已提交batch | 通知领先于提交或重入死锁 | P0 |
| `NTD-57` | - [ ] 首个pending callback panic，再触发proposed/reject及relay事件 | 本publisher所有callback后续禁用，不重复panic；relay/recent等独立端点继续按策略执行 | 一个插件panic停住所有池事件 | P1 |
| `NTD-58` | - [ ] 健康recent endpoint首写返回I/O error，失败完成起小于1秒内再写，再到期写成功 | 冷却期记录跳过不阻塞publisher；到期新写恢复，遗漏记录不回放；原owner提交不回滚 | 同步重试风暴或虚报持久性 | P1 |
| `NTD-59` | - [ ] 分别在notify service与subscriber边界填满或关闭channel，再触发pending/proposed/reject | try_send立即结束、不创建等待任务；相应boundary/reason drop计数增加；一个慢subscriber不阻碍另一个有空间者 | 无界后台通知任务 | P1 |
| `NTD-60` | - [x] 设置超时HTTP端点或不退出的notify子进程，同时持续更新模板后停止服务 | 每个端点并发受配置列表大小限制；发送当前模板；超时/关闭释放owned子进程，不留下脱离服务的notify任务 | 模板通知任务或子进程泄漏 | P1 |
| `NTD-61` | - [x] 分别读取有效v1与有效v2，交易中含父子、Accepted/Recovery重复hash及现已链花费项 | dedup后父先子重验证，合法项进入池，失效项不因文件标记成为Accepted；v2恢复元数据顺序生效 | 磁盘内容直接绕过准入 | P0 |
| `NTD-62` | - [x] 同时存在坏v2与可读v1，或v2长度/metadata count不匹配、超过推导read bound | load错误被记录，启动采用空pool而非v1旧数据；读操作有界且不panic | 旧交易无声复活、无界分配 | P0 |
| `NTD-63` | - [x] 存在v1，正常save v2，记录文件操作顺序并重开新v2 | 临时写→flush/sync→rename之后才删v1；新文件可重读；writer lease直到写操作结束才释放 | 未写完文件替换旧快照 | P1 |
| `NTD-64` | - [x] 在写、flush/sync或rename前注入错误，原持久文件有效 | 报告错误并清理临时文件；原文件保持字节完整，无并发第二writer踩覆盖 | 保存失败损坏旧快照 | P0 |
| `NTD-65` | - [x] v2已rename成功，再让删除v1失败 | 返回保存错误但新v2仍可读，下次load选v2；不错误断言所有失败都恢复旧文件 | 混淆已提交数据与清理失败 | P1 |
| `NTD-66` | - [ ] startup replay在pool started标志前触发callback，callback同步读取pool；ordinary消息队列同时忙 | reserved read handlers响应；replay完成后才started，无依赖自己启动结束的死锁 | 启动回调卡住节点 | P0 |
| `NTD-67` | - [x] 运行中有VM、等待notice及读请求，发stop，所有同步provider/endpoint可返回 | 停止新请求，收束handler/background，drain publisher后再持久化；最终无owned任务；不把两段30秒写成总30秒保证 | 持久化早于最终settlement | P0 |
| `NTD-68` | - [ ] 让当前未结算Job意外drop或后台worker/publisher非预期失败，触发终止 | generation fault，abort/join已归属任务，跳过持久化；有序取消路径不误当成功owner结算 | 故障状态落盘 | P0 |
| `NTD-69` | - [x] 对小有效链分别执行import、replay sanity/profile，期间无pool/relay consumer | SharedPackage先释放未用pool/relay，链正常处理；scope退出join链后删除临时DB，工具结束无后台持DB线程 | 离线工具关机阻塞/删库竞争 | P1 |
| `NTD-70` | - [ ] 从大block backing抽取tx/uncle/hash/outpoint/proposal-id，经新compact helpers留存后释放原envelope | 字节、cached tx/witness hash与字段保持；新retained key只有固定backing，compact tx/uncle只保留自身；不把ledger bound宣传为RSS上限 | 小key保活整个网络载荷 | P1 |
| `NTD-71` | - [ ] 相同hash outpoint index取255/256/u32::MAX，费用与size取易溢出的u64值，平局交易取不同shortid | outpoint按numeric index；fee交叉乘为u128精确排序；EvictKey平局确定；比较自反/反对称/传递一致 | 字节序或溢出导致不稳定排序 | P1 |
| `NTD-72` | - [x] 分别构建default、profiling、tokio-trace加tokio_unstable、二者组合；另单独tokio-trace不加unstable | 合法组合编译；缺unstable给明确编译错误；默认不携带test预算配置，CI integration用test而生产配置校验不feature合并 | feature串扰或平台编译破坏 | P1 |
| `NTD-73` | - [x] profiling已有输出文件，或console buffer/pubinterval为0、RECORD_PATH设置无支持值，分别启动 | diagnostic初始化给明确错误；不覆盖既有profile；节点按main策略继续启动，不误称这些配置有效 | 诊断导致数据覆盖或节点不启动 | P1 |
| `NTD-74` | - [x] 执行一次有明确边界/阶段的capacity拒绝与通知drop，再取消/完成活动任务到稳定baseline | 拒绝记录含bounded stage/reason，notify drop标明service/subscriber及full/closed；活动/驻留计数回归真实值，动态hash不作metric label | 指标错口径、隐含高基数或泄漏 | P1 |
| `NTD-75` | - [x] 已Accepted的输出与已被pool spender花费的输出分别get_live_cell，with_data开/关，再查链Dead/Unknown | pool overlay优先chain；已花费不返回live；with_data只控制data加载；chain Dead/Unknown对外Unknown | RPC可花费判断与pool成员分歧 | P1 |
| `NTD-76` | - [x] target在MIN/MAX边界与范围外，池分别为空及候选跨一个byte/cycle块界 | 越界明确错误；空池返回min_fee；有效target按proposal closest校正，以跨界候选费率估计而非套用真实pack排除逻辑 | 估费与packing混用错误oracle | P1 |
| `NTD-77` | - [ ] clone两个controller并发clear_pool/clear_verify_queue，先让首命令入reliable lane再挂起执行 | 共享唯一admin槽持有到有序执行完，第二调用不得积压第二份大snapshot；取消response等待不提前释放已入队槽；链更新按顺序保留 | 公共clear淹没可靠链通道 | P1 |
| `NTD-78` | - [x] 池含A及其因果后代，remove_local A；另分支挂起removal后替换A owner | 稳定cut移除正确闭包且返回true，缺hash返回false；身份变化返回competing-progress路径而不误删后继owner | 旧删除命令追杀新owner | P1 |
| `NTD-79` | - [ ] accepted timestamp设cut−1/cut/cut+1，remote deadline设now−ε/now/now+ε；有持续wake backlog | accepted仅cut−1过期并按membership撤因果闭包；remote≤now到期；每页wake后到期任务仍有机会处理，不被持续wake饥饿 | 时间等号误删及maintenance饥饿 | P1 |
| `NTD-80` | - [ ] 控制put持read guard，启动shrink；再故意让drop成功而create CF失败，随后get和put该shard | shrink不与活跃read guard下DB操作重叠；drop计数扣除不丢，缺CF get为None、后put重建；计数允许近似但不wrap为0 | CF句柄竞争、单调计数漂移 | P1 |
| `NTD-81` | - [x] 连续覆盖同hash后读取count；另注入总估计u64溢出与递增已到MAX | 顺序覆盖不重复加一；open时估计和溢出报错，增量到MAX饱和；不要求并发同key计数精确等于真实key数 | 错误精确性期待或饱和失效 | P1 |
| `NTD-82` | - [x] 分别让单tx serialized size、batch count/total_size、ID序列count等于上限和多1；调用Remote与notify入口 | 等号载荷可入队（其他资源充分），超限在入队前返回明确错误；实际保留compact数据，不携带caller过大capacity；满队列不先spawn等待任务 | 只验证wire消息漏掉内部API驻留边界 | P1 |
| `NTD-83` | - [ ] 内部将生成work_id的计数器置于可分配最后值与耗尽边界，再分别prepare模板 | 可分配值单调、允许空洞；耗尽返回BlockAssemblerError::CounterExhausted，不wrap、不发布复用work_id | work_id回绕或错误错误类型 | P1 |
| `NTD-84` | - [ ] 内部把叔块source_version置于终端值，尝试原本符合容量规则的插入/移除 | 返回CandidateUncleMutationError::SourceVersionExhausted，完整候选集合及source version不变；不是CounterExhausted | 版本耗尽后部分修改或收据ABA | P1 |
| `NTD-85` | - [ ] 固定tip和模板driver状态，对比默认参数与不同bytes_limit/proposals_limit/max_version；base和head分别记录响应约束字段 | 待确认：base handler和head controller实际均忽略三个参数，但RPC文档称它们是上限；先记录当前模板不随这三个参数变化的兼容证据，由G1决定沿用既有行为或另立约束，不把文档差异标为此PR新增回归 | 将现状直接当获批规格或误报新增缺陷 | P1 |
| `NTD-86` | - [ ] 在同步pending/proposed/reject callback内分别调用submit_local_tx/remove_local_tx/clear_pool/save_pool，再令callback返回 | 这些受保护写入口直接返回指明callback同步mutation的错误，未入队、无owner/磁盘副作用；callback返回后后续batch继续；同步只读成功另由原用例证明 | publisher自等自己的写重入死锁 | P0 |
| `NTD-87` | - [ ] 候选总数已128，在较高height重复插入已存在uncle；较低height仍有合法候选 | 返回false，完整候选集合与source receipt完全不变，最低height不先被驱逐 | 重复消息无成本删除叔块 | P1 |
| `NTD-88` | - [ ] 候选总数已128且某较高height已有10条，提交该height的新uncle | 返回false，全部候选及source receipt不变，不先删除最低height集合 | 局部quota拒绝引发全局驱逐 | P1 |
| `NTD-89` | - [ ] 候选总数已128，新uncle height高于最低height且目标bucket有空位、非重复 | 成功插入并精确移除原最低height整组，其他height完整保留、source version前进一次；不简单要求最终count仍128 | 成功驱逐策略误删或错误数量oracle | P1 |
| `NTD-90` | - [ ] 分别令重组payload limit+1、charge算术溢出或normalized分配失败；旧池含Accepted/preaccepted/history并有挂起旧Job | ChainReorgArgs为ReplaceGeneration且只保留新snapshot；执行后旧owners全部退役，新snapshot安装，旧Job不能提交；不保留detach payload或自动重放已丢弃交易，链更新继续 | 把空generation替换误当有交易恢复 | P0 |
<!-- TEST-CASES-END -->

## 评审与执行边界

原设计经过独立 B 静态审查与定向修订；该历史设计审查不等于当前全部 Python 用例的完整覆盖验收。公开模板参数的既有实现/文档差异（NTD-85）仍待产品决定。测量工具分支及未映射行仍是明确边界，不据此宣称全部非测试分支已覆盖或产品可合并。

本次提交按本表关联的 Python 回归快照打包，移除了用户指定的 console 端口占用日志用例；未映射行保留未勾选，已知失败断言继续保留。运行方式、执行证据适用范围以及与后续网络预算用例的关系见[用例说明](../test_cases/tx_pool_refactor/tests/README.md)。
