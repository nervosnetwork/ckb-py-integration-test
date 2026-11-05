# CKB PR #5316：Python 集成测试评审

范围：仅考虑项目现有 Python/CkbTest 驱动的真实节点集成测试；沿用已核对 head `c2596c9d2ea161afbeb9ba905b66a34454aaef27`，本次按用户反馈收敛设计，未重新执行产品测试。
依据：[PR #5316](https://github.com/nervosnetwork/ckb/pull/5316)。准入专题见 [事务化准入集成测试](ckb-pr-5316-transactional-admission.md)；新增范围见 [性能、混沌与作恶节点分析](ckb-pr-5316-integration-resilience.md)。文档中的提交号固定来源，后续回归代码使用项目统一配置，不按版本跳过。

## Spec

共同验证边界：通过公开RPC、CLI、正常节点间通信、真实分叉/重启、文件系统及已公开日志/metrics观察。测试放在 `test_cases/`，复用既有夹具；Rust单测、内部屏障/锁/时钟/Plan注入、编译矩阵均从本次清单移出；异常P2P报文单列于新增分析的P2P专项，不混入本表。保留Case ID及既有TEST-MAP；`[x]`只表示有映射，未表示当前实现已覆盖全部断言或运行通过。

独立预期：Accepted集合取已知交易hash的集合运算；费用由输入输出差额计算，字节由独立Molecule序列化计算，proposal/since按链高度与共识配置计算。负例先验证相同路径的合法对照，并只改变目标条件。查询前停止无关负载并等待明确终态；等待超时是失败/夹具未达，不是通过。

观察层级：RPC返回及最终集合只证明对外行为；不宣称内部原子提交、缓存命中次数、锁顺序、线程数或精确CPU计费。网络预算必须有目标hash的实际预算拒绝证据；没有入池或墙钟等待超过8秒均不足以归因。正常节点relay与公开notify入口分别记为Remote与Proposal，不混用来源。订阅是best effort，索引查询以tip追平和集合收敛为界，不保证瞬时一致。

预算测试统一使用项目现有普通二进制，保持默认 **8秒CPU上限**，无需为本批用例单独编译CKB或开启test feature；正常场景不设置 `max_tx_verify_time_ms`、`min_tx_verify_time_ms`、`cycles_per_ms`，发送端和接收端均沿用默认策略。NTD-02/72中写入旧字段只是普通二进制拒绝未知配置的负例，不是预算测试的启动前提。

8秒是固定上限：Proposal（包括notify入口）及无cycles声明的Remote使用该上限；带cycles声明的Remote仍按节点自动校准决定实际预算，可能低于8秒。Local/Recovery没有这项网络预算。集成断言针对实际ExcessiveVerifyTime及后续状态，不要求“恰好第8秒返回”；HTTP/观察deadline独立设置，给排队和发布留余量。

慢交易先通过发送节点本地send_transaction证明共识有效，取得真实body/cycles，再由正常relay或notify触发接收端预算耗尽。优先复用已有合法慢VM夹具；需要时调整夹具工作量而非节点预算，始终保持共识cycles、输入和签名合法。若机器上未触发预算拒绝，明确记录夹具未达，不以sleep(8)、未入池、降低预算或skip算通过。

### SPEC-01：配置兼容与节点启动
Condition: 项目现有二进制加载真实配置
Expected: 兼容旧字段；废弃预算字段明确拒绝；可达错误给出启动诊断，合法配置就绪
Observable: 进程退出码/启动日志、RPC就绪及真实收交易结果
Basis: [util/app-config/src/configs/tx_pool.rs](https://github.com/nervosnetwork/ckb/blob/c2596c9d2ea161afbeb9ba905b66a34454aaef27/util/app-config/src/configs/tx_pool.rs)；[util/app-config/src/legacy/tx_pool.rs](https://github.com/nervosnetwork/ckb/blob/c2596c9d2ea161afbeb9ba905b66a34454aaef27/util/app-config/src/legacy/tx_pool.rs)；[src/main.rs](https://github.com/nervosnetwork/ckb/blob/c2596c9d2ea161afbeb9ba905b66a34454aaef27/src/main.rs)
Source: observation
Testing: required

### SPEC-02：网络CPU预算的外部影响
Condition: 普通二进制保持默认8秒CPU上限，经正常relay或notify触发已归因的预算耗尽
Expected: Proposal使用8秒上限，Remote有cycles声明时允许自动校准为更短预算；耗尽不改变既有Accepted、不形成公开拒绝记录或单独封禁；Local/恢复/合法块路径仍可处理相同交易
Observable: 目标hash日志、交易状态/body、peer列表、重启恢复及链顶
Basis: [util/app-config/src/configs/tx_pool.rs](https://github.com/nervosnetwork/ckb/blob/c2596c9d2ea161afbeb9ba905b66a34454aaef27/util/app-config/src/configs/tx_pool.rs)；[spec/src/consensus.rs](https://github.com/nervosnetwork/ckb/blob/c2596c9d2ea161afbeb9ba905b66a34454aaef27/spec/src/consensus.rs)；[tx-pool/src/authority/model.rs](https://github.com/nervosnetwork/ckb/blob/c2596c9d2ea161afbeb9ba905b66a34454aaef27/tx-pool/src/authority/model.rs)；[tx-pool/src/verification/calibration.rs](https://github.com/nervosnetwork/ckb/blob/c2596c9d2ea161afbeb9ba905b66a34454aaef27/tx-pool/src/verification/calibration.rs)；[tx-pool/src/verification.rs](https://github.com/nervosnetwork/ckb/blob/c2596c9d2ea161afbeb9ba905b66a34454aaef27/tx-pool/src/verification.rs)；[tx-pool/src/authority/notice.rs](https://github.com/nervosnetwork/ckb/blob/c2596c9d2ea161afbeb9ba905b66a34454aaef27/tx-pool/src/authority/notice.rs)；[rpc/src/error.rs](https://github.com/nervosnetwork/ckb/blob/c2596c9d2ea161afbeb9ba905b66a34454aaef27/rpc/src/error.rs)
Source: observation
Testing: required

### SPEC-04：cycles调度阈值不等于共识上限
Condition: 合法交易实际cycles跨过max_tx_verify_cycles配置阈值
Expected: 真实网络提交仍经过正常验证并可接受
Observable: 两节点Accepted、body和cycles
Basis: [tx-pool/src/authority/queue.rs](https://github.com/nervosnetwork/ckb/blob/c2596c9d2ea161afbeb9ba905b66a34454aaef27/tx-pool/src/authority/queue.rs)；[tx-pool/src/authority/ingress.rs](https://github.com/nervosnetwork/ckb/blob/c2596c9d2ea161afbeb9ba905b66a34454aaef27/tx-pool/src/authority/ingress.rs)
Source: observation
Testing: required

### SPEC-05：依赖解析和真实唤醒
Condition: RPC提交缺失或无效依赖，后续补齐合法producer
Expected: 区分终局resolve错误与可恢复Waiting；所有依赖齐备后重新验证
Observable: RPC错误、orphan数量、最终hash/body及池集合
Basis: [tx-pool/src/authority/jobs.rs](https://github.com/nervosnetwork/ckb/blob/c2596c9d2ea161afbeb9ba905b66a34454aaef27/tx-pool/src/authority/jobs.rs)；[tx-pool/src/authority/waiting.rs](https://github.com/nervosnetwork/ckb/blob/c2596c9d2ea161afbeb9ba905b66a34454aaef27/tx-pool/src/authority/waiting.rs)
Source: observation
Testing: required

### SPEC-06：链进展及重组后的交易有效性
Condition: 池验证同时发生链附加、真实reorg或truncate
Expected: 链可前进；池最终仅保留当前链有效交易，恢复集合按真实交易独立推导
Observable: canonical tip、Accepted集合、交易状态和可提交模板
Basis: [tx-pool/src/authority/chain.rs](https://github.com/nervosnetwork/ckb/blob/c2596c9d2ea161afbeb9ba905b66a34454aaef27/tx-pool/src/authority/chain.rs)；[verification/src/transaction_verifier.rs](https://github.com/nervosnetwork/ckb/blob/c2596c9d2ea161afbeb9ba905b66a34454aaef27/verification/src/transaction_verifier.rs)；[chain/src/verify.rs](https://github.com/nervosnetwork/ckb/blob/c2596c9d2ea161afbeb9ba905b66a34454aaef27/chain/src/verify.rs)
Source: observation
Testing: required

### SPEC-07：Proposal阶段按当前canonical链计算
Condition: 重复proposal、窗口推进、分叉及截断
Expected: Pending/Gap/Proposed与当前高度窗口一致
Observable: raw pool、detail状态及区块proposal集合
Basis: [util/proposal-table/src/lib.rs](https://github.com/nervosnetwork/ckb/blob/c2596c9d2ea161afbeb9ba905b66a34454aaef27/util/proposal-table/src/lib.rs)；[tx-pool/src/authority/query.rs](https://github.com/nervosnetwork/ckb/blob/c2596c9d2ea161afbeb9ba905b66a34454aaef27/tx-pool/src/authority/query.rs)
Source: observation
Testing: required

### SPEC-08：模板交易满足费用、依赖及预算规则
Condition: 真实有效交易形成候选图
Expected: 选择满足祖先闭包、读取顺序及byte/cycle上限，输出可被完整验证的块
Observable: 模板hash/顺序、独立字节/费用计算与submit_block结果
Basis: [tx-pool/src/authority/packing.rs](https://github.com/nervosnetwork/ckb/blob/c2596c9d2ea161afbeb9ba905b66a34454aaef27/tx-pool/src/authority/packing.rs)；[tx-pool/src/block_assembler/dao.rs](https://github.com/nervosnetwork/ckb/blob/c2596c9d2ea161afbeb9ba905b66a34454aaef27/tx-pool/src/block_assembler/dao.rs)；[tx-pool/src/block_assembler/cell_liveness.rs](https://github.com/nervosnetwork/ckb/blob/c2596c9d2ea161afbeb9ba905b66a34454aaef27/tx-pool/src/block_assembler/cell_liveness.rs)
Source: observation
Testing: required

### SPEC-09：模板更新和时间下界
Condition: 池成员/proposal/uncle变化或parent时间领先
Expected: 新模板反映有效当前内容；无关添加不使既有合法块无效；时间至少parent+1；参数分歧保留待确认
Observable: 模板交易/uncle集合、work_id、timestamp及提交结果
Basis: [tx-pool/src/authority/template.rs](https://github.com/nervosnetwork/ckb/blob/c2596c9d2ea161afbeb9ba905b66a34454aaef27/tx-pool/src/authority/template.rs)；[tx-pool/src/block_assembler/template.rs](https://github.com/nervosnetwork/ckb/blob/c2596c9d2ea161afbeb9ba905b66a34454aaef27/tx-pool/src/block_assembler/template.rs)；[rpc/src/module/miner.rs](https://github.com/nervosnetwork/ckb/blob/c2596c9d2ea161afbeb9ba905b66a34454aaef27/rpc/src/module/miner.rs)
Source: observation
Testing: required

### SPEC-10：曾成功验证的交易仍遵守完整校验
Condition: 改变witness/链上下文，或构造部分预热的区块
Expected: 脚本、上下文和总cycles限制仍有效；错误关联原交易位置
Observable: RPC/块验证结果、错误原因/index、费用及cycles
Basis: [verification/src/cache.rs](https://github.com/nervosnetwork/ckb/blob/c2596c9d2ea161afbeb9ba905b66a34454aaef27/verification/src/cache.rs)；[verification/src/transaction_verifier.rs](https://github.com/nervosnetwork/ckb/blob/c2596c9d2ea161afbeb9ba905b66a34454aaef27/verification/src/transaction_verifier.rs)；[verification/contextual/src/contextual_block_verifier.rs](https://github.com/nervosnetwork/ckb/blob/c2596c9d2ea161afbeb9ba905b66a34454aaef27/verification/contextual/src/contextual_block_verifier.rs)
Source: observation
Testing: required

### SPEC-11：RPC结果、查询及异步提交契约
Condition: 真实提交/预演/查询/清理及提交中的断连或408
Expected: 提交成功时已准入；公开状态和统计正确；已入队请求继续处理，其他读/链操作可前进
Observable: HTTP响应、hash/body、池集合、公开计数及canonical tip
Basis: [rpc/src/module/pool.rs](https://github.com/nervosnetwork/ckb/blob/c2596c9d2ea161afbeb9ba905b66a34454aaef27/rpc/src/module/pool.rs)；[rpc/src/module/test.rs](https://github.com/nervosnetwork/ckb/blob/c2596c9d2ea161afbeb9ba905b66a34454aaef27/rpc/src/module/test.rs)；[tx-pool/src/authority/query.rs](https://github.com/nervosnetwork/ckb/blob/c2596c9d2ea161afbeb9ba905b66a34454aaef27/tx-pool/src/authority/query.rs)；[tx-pool/src/service/controller.rs](https://github.com/nervosnetwork/ckb/blob/c2596c9d2ea161afbeb9ba905b66a34454aaef27/tx-pool/src/service/controller.rs)
Source: observation
Testing: required

### SPEC-12：正常peer传播和提交入口边界
Condition: 正常节点连接/断开，或公开入口提交大小边界交易
Expected: 后备peer仍可提供交易；无peer时不积压已完成结果；公开入口拒绝超大交易
Observable: peer列表、目标hash请求日志、队列指标、body及RPC大小错误
Basis: [sync/src/relayer/transactions_process.rs](https://github.com/nervosnetwork/ckb/blob/c2596c9d2ea161afbeb9ba905b66a34454aaef27/sync/src/relayer/transactions_process.rs)；[tx-pool/src/service/controller.rs](https://github.com/nervosnetwork/ckb/blob/c2596c9d2ea161afbeb9ba905b66a34454aaef27/tx-pool/src/service/controller.rs)；[tx-pool/src/authority/relay.rs](https://github.com/nervosnetwork/ckb/blob/c2596c9d2ea161afbeb9ba905b66a34454aaef27/tx-pool/src/authority/relay.rs)
Source: observation
Testing: required

### SPEC-13：通知投递和端点生命周期
Condition: 真实订阅端变慢/取消、HTTP或脚本超时
Expected: 通知按best effort处理；慢端不阻塞其他路径，超时/关闭释放外部资源，新订阅无历史补发
Observable: WS事件、drop指标、HTTP连接、子进程PID及退出状态
Basis: [rpc/src/module/subscription.rs](https://github.com/nervosnetwork/ckb/blob/c2596c9d2ea161afbeb9ba905b66a34454aaef27/rpc/src/module/subscription.rs)；[tx-pool/src/block_assembler/mod.rs](https://github.com/nervosnetwork/ckb/blob/c2596c9d2ea161afbeb9ba905b66a34454aaef27/tx-pool/src/block_assembler/mod.rs)；[notify/src/lib.rs](https://github.com/nervosnetwork/ckb/blob/c2596c9d2ea161afbeb9ba905b66a34454aaef27/notify/src/lib.rs)
Source: observation
Testing: required

### SPEC-14：v2文件格式、兼容读取及保存结果
Condition: 正常SIGTERM/项目node.stop触发保存，等待旧PID退出后重启，或施加真实文件系统故障
Expected: v2为8字节magic+8字节LE Accepted向量字节长度+两个Molecule TransactionVec；坏v2不回退v1；失败前后的可恢复文件符合提交边界
Observable: 文件字节/独立解码、错误日志、重启后的hash/body
Basis: [tx-pool/src/persisted.rs](https://github.com/nervosnetwork/ckb/blob/c2596c9d2ea161afbeb9ba905b66a34454aaef27/tx-pool/src/persisted.rs)；[tx-pool/src/service/controller.rs](https://github.com/nervosnetwork/ckb/blob/c2596c9d2ea161afbeb9ba905b66a34454aaef27/tx-pool/src/service/controller.rs)
Source: observation
Testing: required

### SPEC-15：节点停止及离线命令可完成
Condition: 真实stop、import或replay
Expected: 正常退出释放外部资源，已接受交易可恢复，导入链与输入一致
Observable: 进程退出、端口/数据库释放、重启池及chain tip
Basis: [tx-pool/src/service.rs](https://github.com/nervosnetwork/ckb/blob/c2596c9d2ea161afbeb9ba905b66a34454aaef27/tx-pool/src/service.rs)；[ckb-bin/src/subcommand/import.rs](https://github.com/nervosnetwork/ckb/blob/c2596c9d2ea161afbeb9ba905b66a34454aaef27/ckb-bin/src/subcommand/import.rs)；[ckb-bin/src/subcommand/replay.rs](https://github.com/nervosnetwork/ckb/blob/c2596c9d2ea161afbeb9ba905b66a34454aaef27/ckb-bin/src/subcommand/replay.rs)
Source: observation
Testing: required

### SPEC-16：公开诊断与工作完成
Condition: 隔离负载触发可归因的拒绝后结束
Expected: 诊断标明stage/reason；公开活动指标最终归零且label有界
Observable: 结构化日志及Prometheus端点
Basis: [tx-pool/src/authority/notice.rs](https://github.com/nervosnetwork/ckb/blob/c2596c9d2ea161afbeb9ba905b66a34454aaef27/tx-pool/src/authority/notice.rs)；[tx-pool/src/metrics.rs](https://github.com/nervosnetwork/ckb/blob/c2596c9d2ea161afbeb9ba905b66a34454aaef27/tx-pool/src/metrics.rs)
Source: observation
Testing: required

### SPEC-17：拒绝记录覆盖与保留
Condition: 无并发写入且TTL有效时重复拒绝同hash并重启
Expected: 同hash串行覆盖不反复增量计数，已有记录在正常重启后仍可查询；总量是近似估计
Observable: tx_pool_info与get_transaction拒绝原因
Basis: [tx-pool/src/component/recent_reject.rs](https://github.com/nervosnetwork/ckb/blob/c2596c9d2ea161afbeb9ba905b66a34454aaef27/tx-pool/src/component/recent_reject.rs)；[tx-pool/src/authority/query.rs](https://github.com/nervosnetwork/ckb/blob/c2596c9d2ea161afbeb9ba905b66a34454aaef27/tx-pool/src/authority/query.rs)
Source: observation
Testing: required

### SPEC-19：Indexer池视图及canonical同步
Condition: index_tx_pool开启，真实提交/移除/reorg/truncate
Expected: 追平后查询反映当前链及Accepted花费；Waiting/Replaced独立输入不按Accepted隐藏
Observable: get_indexer_tip、get_cells、get_cells_capacity与独立outpoint/capacity集合
Basis: [util/indexer-sync/src/lib.rs](https://github.com/nervosnetwork/ckb/blob/c2596c9d2ea161afbeb9ba905b66a34454aaef27/util/indexer-sync/src/lib.rs)；[util/indexer-sync/src/pool.rs](https://github.com/nervosnetwork/ckb/blob/c2596c9d2ea161afbeb9ba905b66a34454aaef27/util/indexer-sync/src/pool.rs)；[util/indexer/src/service.rs](https://github.com/nervosnetwork/ckb/blob/c2596c9d2ea161afbeb9ba905b66a34454aaef27/util/indexer/src/service.rs)；[util/rich-indexer/src/service.rs](https://github.com/nervosnetwork/ckb/blob/c2596c9d2ea161afbeb9ba905b66a34454aaef27/util/rich-indexer/src/service.rs)
Source: observation
Testing: required

## 测试树

<!-- TEST-TREE-BEGIN -->
- 真实节点集成回归
  - 配置与启动
    - NTD-01 [primary] -> SPEC-01：旧字段兼容与祖先限制
    - NTD-02 [primary] -> SPEC-01：默认二进制拒绝旧预算字段
    - NTD-03 [primary] -> SPEC-01：配置可达的资源边界
    - NTD-72 [primary] -> SPEC-01：默认运行入口
    - NTD-73 [primary] -> SPEC-01：诊断失败的公开行为
  - 网络预算后的真实结果
    - EVT-01 [primary] -> SPEC-02：来源、原池及重启恢复
    - EVT-04 [primary] -> SPEC-02：再次传播与本地提交
    - EVT-05 [primary] -> SPEC-02：同peer后续正常工作
    - EVT-06 [primary] -> SPEC-02：包含相同交易的有效块
  - 验证阈值
    - NTD-09 [primary] -> SPEC-04：阈值及阈值加一仍可接受
  - 缺失与错误依赖
    - NTD-13 [primary] -> SPEC-05：本地未知输入及有效父子对照
    - NTD-14 [primary] -> SPEC-05：两项依赖分步补齐
    - NTD-15 [primary] -> SPEC-05：终局依赖错误
    - NTD-16 [primary] -> SPEC-05：33笔等待交易全部唤醒
  - 链变动与池恢复
    - NTD-18 [primary] -> SPEC-06：慢提交期间链继续前进
    - NTD-19 [primary] -> SPEC-06：链花费清理冲突闭包
    - NTD-20 [primary] -> SPEC-06：detach后有效性
    - NTD-21 [primary] -> SPEC-06：Waiting的另一依赖被花费
    - NTD-22 [primary] -> SPEC-06：普通reorg精确恢复集合
    - NTD-106 [primary] -> SPEC-06：回退后的时间条件
  - Proposal窗口
    - NTD-24 [primary] -> SPEC-07：重复proposal与窗口边界
    - NTD-25 [primary] -> SPEC-07：分叉和截断后的阶段
  - 真实模板打包
    - NTD-27 [primary] -> SPEC-08：包费率与共享祖先
    - NTD-28 [primary] -> SPEC-08：读取者先于花费者
    - NTD-29 [primary] -> SPEC-08：约束环不阻碍有效模板
    - NTD-30 [primary] -> SPEC-08：字节及cycles等号边界
    - NTD-32 [primary] -> SPEC-08：同数量替换与阶段更新
  - 模板内容与时间
    - NTD-33 [primary] -> SPEC-09：同tip清池/替换后的新模板
    - NTD-34 [primary] -> SPEC-09：无关添加后旧块仍有效
    - NTD-36 [primary] -> SPEC-09：已装proposal与uncle选择
    - NTD-85 [primary] -> SPEC-09：模板参数规格待决定
    - NTD-107 [primary] -> SPEC-09：parent+1时间下界
  - 重复验证与区块检查
    - NTD-39 [primary] -> SPEC-10：同raw hash不同witness
    - NTD-40 [primary] -> SPEC-10：旧成功记录不跳过since
    - NTD-42 [primary] -> SPEC-10：块总cycles等号与超限
    - NTD-43 [primary] -> SPEC-10：脚本读取来源
    - NTD-105 [primary] -> SPEC-10：混合预热后的结果及错误index
  - 公共RPC
    - NTD-44 [primary] -> SPEC-11：提交成功后可查询及重复拒绝
    - NTD-45 [primary] -> SPEC-11：公开body/状态
    - NTD-46 [primary] -> SPEC-11：稳定集合的统计及排名
    - NTD-48 [primary] -> SPEC-11：清理未接受项
    - NTD-75 [primary] -> SPEC-11：live cell池视图
    - NTD-76 [primary] -> SPEC-11：估费边界
    - NTD-78 [primary] -> SPEC-11：删除父子闭包
    - NTD-95 [primary] -> SPEC-11：断连/408后的最终结果
    - NTD-96 [primary] -> SPEC-11：慢提交时读请求和链进展
  - 正常传播与公开入口限额
    - NTD-53 [primary] -> SPEC-12：请求peer断开后的后备传播
    - NTD-55 [primary] -> SPEC-12：无peer排空及重连传播
    - NTD-82 [primary] -> SPEC-12：单交易大小入口边界
  - 通知
    - NTD-59 [primary] -> SPEC-13：慢订阅与公开丢弃指标
    - NTD-60 [primary] -> SPEC-13：HTTP/脚本通知超时及退出
    - NTD-98 [primary] -> SPEC-13：订阅建立与取消后的投递
  - 持久文件与重启
    - NTD-61 [primary] -> SPEC-14：当前v2及旧v1恢复
    - NTD-62 [primary] -> SPEC-14：坏v2不回退v1
    - NTD-63 [primary] -> SPEC-14：成功迁移与独立解码
    - NTD-64 [primary] -> SPEC-14：真实保存失败保留旧文件
    - NTD-65 [primary] -> SPEC-14：清理失败仍使用已保存v2
  - 关闭与离线命令
    - NTD-67 [primary] -> SPEC-15：正常停止后无peer恢复
    - NTD-69 [primary] -> SPEC-15：import/replay结束及数据库释放
  - 对外诊断
    - NTD-74 [primary] -> SPEC-16：拒绝日志与Prometheus基线
  - 最近拒绝记录
    - NTD-81 [primary] -> SPEC-17：同hash串行覆盖及重启查询
  - Indexer公开结果
    - NTD-91 [primary] -> SPEC-19：池花费覆盖及移除刷新
    - NTD-93 [primary] -> SPEC-19：同高换链及截断后的索引
  - 本次集成边界
    - [not_applicable] 内部锁、任务调度、精确时钟及资源预留 — 需要组件接缝；历史设计见文末，外部最终结果不证明这些切面。
    - [not_applicable] 手工异常 P2P 报文及构建组合 — 分属协议专项和构建检查；本表采用节点正常通信与现成测试二进制，新增P2P专项及入口缺口另见补充分析。
    - [unanalysed] 其余存储优化与性能变化 — 本次只收敛已有设计的集成范围；扩展时从原始 PR 差异和相应公共入口继续，不计为已覆盖。
<!-- TEST-TREE-END -->

## 用例

以下63项为当前集成清单；每行仅保留真实入口与可观察结果。

<!-- TEST-CASES-BEGIN -->
| 用例 | 场景 | 预期结果 | 防止的问题 | 优先级 |
| --- | --- | --- | --- | --- |
| `NTD-01` | - [x] 分别用发布版字段、附带废弃 max_mem_size/max_cycles/cache 字段的配置启动节点；用旧祖先上限25与显式1001提交相应深度的合法交易链 | 节点就绪且可收交易；废弃字段不恢复旧限制；祖先下限按1000生效，1001按显式值生效 | 配置升级失败或有效祖先限制改变 | P1 |
| `NTD-02` | - [x] 使用项目提供的默认二进制，分别加入 max_tx_verify_time_ms、min_tx_verify_time_ms、cycles_per_ms 配置启动；缺省配置作正对照 | 缺省配置就绪；各旧预算字段启动报 unknown field，RPC不进入就绪状态 | 废弃测试配置在默认运行方式中生效 | P0 |
| `NTD-03` | - [x] 通过实际TOML配置分别设置可表达的超大worker数、超大池容量及显式worker=0后启动 | 前两项以明确配置/资源错误退出且无就绪RPC；零worker配置正常就绪，本地send_transaction可完成；不要求后台Remote/notify验证前进 | 异常配置导致panic、半启动或零worker不可用 | P1 |
| `EVT-01` | - [x] 所有节点用普通二进制且保持默认8秒CPU上限；同一合法慢交易分别经正常relay、notify、本地send_transaction和无peer重启恢复；预算拒绝以目标hash诊断确认 | Remote/Proposal耗尽后目标不入池且不成为公开rejected；Local可接受，重启可恢复；原有Accepted集合保持 | 把网络预算错误带入本地提交、恢复或已接受交易 | P0 |
| `EVT-04` | - [x] 真实来源节点广播慢交易，接收端确认预算拒绝；用新来源节点再次正常广播同一hash，再隔离网络并本地提交 | 两次均实际请求/处理该hash；本地提交相同字节后进入Accepted，body一致 | 一次预算拒绝永久阻止后续请求或本地重试 | P1 |
| `EVT-05` | - [x] 慢交易经真实peer触发目标hash的预算拒绝；同连接随后正常广播使用独立输入的快速合法交易 | peer未因此被ban，快交易在接收端入池；慢交易仍无公开拒绝记录；拒绝订阅以其他正常拒绝事件作健康对照 | 本地预算耗尽惩罚正常peer或中断后续传播 | P0 |
| `EVT-06` | - [x] 先确认接收节点对合法慢交易发生网络预算拒绝，再从真实出块节点同步包含相同交易的有效块；proposal、输入及块cycles均合法 | 接收节点链顶推进到该块，交易状态committed且body一致 | 交易池CPU预算污染区块共识验证 | P0 |
| `NTD-09` | - [x] 由真实发送节点广播实际cycles已知的合法交易；两接收节点将max_tx_verify_cycles分别设为该cycles与少1 | 两接收节点都接受该交易，body和cycles一致；此处只验证阈值不是执行拒绝上限 | 把调度阈值当成VM或共识限制 | P1 |
| `NTD-13` | - [x] 通过send_transaction提交引用不存在producer、已知producer越界输出的交易；再提交引用有效已接受父交易的正对照 | 前两者返回Unknown类resolve错误且无池成员/孤儿残留；有效父子均入池 | 本地非法引用无限滞留或正常父子被拒绝 | P0 |
| `NTD-14` | - [x] 通过notify_transaction提交同时缺input和dep-group成员的合法交易，分别以两种顺序补齐依赖 | 只补一项时仍无公开body且orphan保留；补齐全部后目标Accepted且orphan归零 | 漏查展开依赖或补齐后丢失唤醒 | P1 |
| `NTD-15` | - [x] 合法基线分别改为缺input且dep-group为空/格式坏，或header_dep不在有效链；经RPC提交 | 返回对应dep-group/header resolve错误，目标不留孤儿；有效依赖对照正常入池 | Unknown掩盖终局依赖错误 | P1 |
| `NTD-16` | - [x] 用notify_transaction放入33笔共享缺失dep、输入互不冲突的交易；确认orphan=33后提交共同producer | 全部33笔与producer最终Accepted、body可查、orphan归零；只验证跨页最终唤醒 | 超过一页的等待交易永久遗漏 | P1 |
| `NTD-18` | - [x] 隔离其他负载，发起仍在执行的合法慢交易请求并观察active指标上升；其间从第二节点同步有效连续区块 | 慢请求尚未结束时链顶已前进，随后交易按新链状态正常结算，节点继续响应 | 池验证负载阻止真实链同步进展 | P0 |
| `NTD-19` | - [x] 池含input消费者、cell_dep读取者及各自后代和独立对照；从第二节点接入花费其引用cell的有效区块 | 目标冲突交易及后代退出Accepted，attached交易为committed，独立对照保留；池总量匹配预期集合 | 链上花费后保留失效交易或误删独立交易 | P0 |
| `NTD-20` | - [x] 真实双节点分叉使已上链producer被detach，其池内子交易分别引用input、cell_dep或dep-group；切到新canonical链 | 最终池仅含在新链有效的父子，失效交易不出现在模板；恢复后的有效交易能正常上链 | 重组后继续使用旧来源接受失效交易 | P0 |
| `NTD-21` | - [x] notify_transaction形成缺A的Waiting交易，其另一输入/依赖B仍有效；接入花费B的块，再补齐A | 冲突waiter退出orphan且不会因A补齐而Accepted，独立等待交易仍可恢复 | 只跟踪缺失项而遗漏其他依赖被花费 | P1 |
| `NTD-22` | - [x] 用普通可容纳规模的双节点reorg，准备旧池与detach块中的有效父子、重复hash及新链attached交易 | 最终集合等于独立推导的有效旧池与detach非cellbase并集减attached且去重；有效父子可打包 | 普通重组无声丢掉应恢复的交易 | P0 |
| `NTD-24` | - [x] 真实出块使同一shortid在多个高度及block/uncle重复出现，按配置窗口推进closest和farthest边界 | RPC/detail的Pending/Gap/Proposed与独立高度计算一致，最后一个有效proposal过期后才退出对应阶段 | 重复proposal过早过期或阶段错误 | P0 |
| `NTD-25` | - [x] 构造真实同高度分叉并切换canonical链，另执行truncate；各分支proposal集合不同 | 追平后交易公开阶段与当前链高度/proposal独立模型一致，旧分叉proposal不继续生效 | 重组或截断后沿用旧proposal投影 | P1 |
| `NTD-27` | - [x] 以合法chain、fanout和diamond交易图提交高费子交易及独立竞争者，使用真实链规格的byte/cycle上限 | 模板满足独立包费率和祖先集合计算、父先子后、共享祖先只出现一次；组装块可提交 | CPFP选择、共享祖先费用或顺序错误 | P0 |
| `NTD-28` | - [x] 真实提交P→A及B，A读取X而B花费X；推进proposal窗口后取模板并提交组装块 | 若选择B且需要保留读取者，模板包含P→A→B的合法顺序；各交易只出现一次，块被接受 | 打包产生已花费依赖或遗漏读取者父交易 | P0 |
| `NTD-29` | - [ ] 用可合法入池的交易构造无因果环但read/spend约束成环的候选，另放独立可打包交易并推进窗口 | 模板在测试deadline内返回，不包含无效环组合且保留可打包独立项；组装区块通过完整校验 | 依赖排序无限等待或输出无效区块 | P0 |
| `NTD-30` | - [x] 用测试链规格及真实序列化调整基础开销，使候选的剩余byte/cycle预算恰好够用或少1；其他维度充分 | 恰好够用时纳入，少1时排除，后续可装交易仍被考虑；完整模板总量不超限 | 忽略基础开销或资源等号边界错误 | P1 |
| `NTD-32` | - [x] 保持池交易数不变做合法RBF；再推进proposal阶段，多次获取模板，并以相同链/交易集合的新节点作对照 | 模板使用当前hash、当前阶段及独立正确顺序；两节点最终内容满足同一集合/预算预期 | 按旧数量或旧阶段复用过期打包结果 | P1 |
| `NTD-33` | - [x] 取得含目标交易的模板，固定tip后clear_tx_pool或合法替换目标，再获取更新后的模板 | 新模板移除旧目标并反映当前可打包集合，work_id更新，parent_hash仍为固定tip | 池清理或替换后继续返回旧模板内容 | P0 |
| `NTD-34` | - [x] 取得含A的有效模板后加入独立、尚未进入提交窗口的B；使用旧模板与work_id提交区块 | 旧块仍可接受，A变为committed，B保留在池并可进入后续proposal；不要求所有池变化都作废旧块 | 无关池变化错误破坏已生成的有效块 | P1 |
| `NTD-36` | - [ ] 通过真实分叉提供合法uncle及proposal，测试链字节预算只容纳部分proposal；构造分别与已装入/未装入proposal冲突的uncle | 只实际装入的proposal影响uncle选择，最终uncle集合及总字节满足独立规则，组装块可提交 | 预算外proposal错误改变叔块选择 | P1 |
| `NTD-39` | - [x] 合法交易先入池再remove，保持raw交易不变仅破坏witness签名后重新send_transaction | 返回脚本校验错误且目标不入池；恢复合法witness可再次提交；不以此推断缓存命中次数 | 同raw hash的旧成功结果掩盖无效witness | P0 |
| `NTD-40` | - [x] 合法since交易先在成熟高度入池再remove；truncate到输入仍有效但since未成熟的高度，再preview/submit，随后恢复成熟高度 | 未成熟时两入口均返回Immature且池保持；成熟后相同交易重新接受，cycles正确 | 曾验证成功使新链上下文检查失效 | P0 |
| `NTD-42` | - [x] 节点先验证多笔合法交易，构造总cycles恰为块上限与超过上限的候选块；对照节点未预热 | 两节点均接受合法等号块、拒绝超限块，费用按输入输出独立计算；不把缓存当免计cycles | 预热交易绕过区块总cycles限制 | P0 |
| `NTD-43` | - [x] 复用现有syscall脚本，构造input和展开cell_dep的origin已声明、未声明及无链上来源三类交易，经RPC验证/出块 | 只有满足来源可见性及VM规则的脚本取得header/extension，其余返回预期ITEM_MISSING；dep-group容器不额外提供来源 | 脚本读取到未声明或错误来源数据 | P0 |
| `NTD-44` | - [x] 合法交易通过send_transaction正常返回后立即查询hash/body和raw pool，再重复提交相同字节 | 首次返回时已Accepted且body一致，重复提交为duplicate，原成员保留；仅断言响应后的公开状态 | 异步提交返回成功却查不到已接受交易 | P0 |
| `NTD-45` | - [x] 通过真实提交、出块窗口、缺依赖notify及合法替换形成Accepted、Waiting和conflicted样本，再查交易及detail | Accepted公开body，Gap对外pending且detail为gap；非Accepted无body，按recent记录为unknown/rejected；orphan仅为池统计 | 内部阶段被错误暴露为交易状态或body | P0 |
| `NTD-46` | - [x] 用真实待确认交易、缺依赖notify与替换历史构造稳定池；结束其他提交后查询info/raw/detail | 字节/cycles等于独立计算的Accepted总和，orphan等于已知waiter数；pending/gap排名从1起，proposed/unknown为0 | 等待/历史交易混入Accepted统计或排名错位 | P1 |
| `NTD-48` | - [x] 池含已Accepted交易和通过notify形成的Waiting交易，调用clear_tx_verify_queue后再补齐其缺失依赖 | Accepted及body保持；Waiting/verify_queue清零，清理的目标不因补齐依赖而自行入池 | 清验证队列误删已接受交易或留下待处理项 | P0 |
| `NTD-53` | - [x] 三节点共享目标交易；主来源日志确认收到含该hash的请求，backup已实际宣告该hash，目标尚未Accepted时断开主peer | backup来源日志出现目标发来的该hash请求，目标随后取得一致body；关联两来源日志、peer和故障时间线；主响应已完成或缺少前置证据则夹具未达 | 主peer断开后交易长期停滞 | P1 |
| `NTD-55` | - [x] 无peer节点提交合法交易，读取公开relay队列指标；之后连接共享链的第二节点 | 零peer时队列最终排空且本地交易保留；连接后交易传播，对端body一致 | 无人连接导致结果队列不消费或后续传播丢失 | P1 |
| `NTD-59` | - [ ] 建立快速与停止读取的慢订阅端，以真实交易事件施压直到公开drop指标变化，再发独立标记交易 | drop指标给出既定boundary/reason；快速订阅端与RPC继续前进；不要求慢订阅端零丢失或全量补发 | 慢订阅者阻塞节点或其他订阅者 | P1 |
| `NTD-60` | - [x] 配置本地超时HTTP通知端及受控不退出的通知脚本，持续获取更新模板后正常停止节点 | 端点观察到的并发受配置约束；超时/关机后子进程退出、连接释放，节点能再次启动 | 模板通知泄漏进程或阻碍节点退出 | P1 |
| `NTD-61` | - [x] 从有效v2文件派生含父子、read-before-spend、重复hash及已链上花费项的夹具，并准备有效v1；分别启动恢复 | v2为16字节头和两个TransactionVec；Accepted前缀及Recovery依赖顺序按格式保留/去重；最终只恢复有效交易且可打包 | 旧格式解码或失效交易被直接恢复 | P0 |
| `NTD-62` | - [x] 先验证当前有效v2能恢复，再分别截断16字节头、令accepted_len越界、破坏任一TransactionVec或超读取上限，同时保留可读v1 | 对应读取/解码错误可见，节点仍就绪但池为空且未回退v1；进程不panic | 损坏v2误回退旧池或崩溃 | P0 |
| `NTD-63` | - [x] 有效v1存在时启动并接收交易，正常SIGTERM停机且等待旧PID退出，独立解码v2后无peer重启 | 确认无保存失败日志，v2的magic、Accepted向量字节长度及两个TransactionVec有效，旧v1删除；同hash/body恢复，退出码本身不替代文件验证 | 迁移生成不可读文件或丢失待确认交易 | P1 |
| `NTD-64` | - [x] 保留已验证的有效v2文件，以目录占用v2临时文件路径制造创建失败；正常停机并等待旧PID退出 | 停机日志记录实际保存I/O错误，旧v2字节不变；解除占位后无peer重启可恢复旧快照；本行固定rename前失败 | 保存失败覆盖上一份可恢复快照 | P0 |
| `NTD-65` | - [x] 节点已载入有效旧v1后，以非空目录占用其清理路径；正常停机触发新v2写入和v1清理，再无peer重启 | 日志报告v1清理错误，新v2独立解码有效并包含预期新交易；重启优先恢复新v2，不要求回滚已完成的rename | 清理错误导致忽略已经提交的新快照 | P1 |
| `NTD-67` | - [x] 存在已Accepted交易及尚在等待的模板通知，正常停止节点；确认进程退出后同目录无peer重启 | 退出完成且端口释放，持久文件可读，原Accepted交易恢复且能提交上链；不声称内部join顺序或统一30秒上限 | 正常停止遗失已接受交易或留下进程 | P0 |
| `NTD-69` | - [x] 导出小有效链，分别实际运行import和replay sanity/profile命令，记录退出状态并重新打开目标数据库 | 命令正常结束，导入链tip/内容匹配输入，临时数据库可清理且不残留持库进程 | 离线命令退出挂起或数据库锁未释放 | P1 |
| `NTD-72` | - [x] 用CURRENT_TEST默认二进制和缺省配置启动，再分别添加旧预算字段启动；沿用已有默认二进制回归 | 缺省配置交易池就绪；旧预算字段报unknown field；本Case仅保留运行时检查，编译矩阵留在历史记录 | 默认运行方式意外接受废弃预算配置 | P1 |
| `NTD-73` | - [x] 使用已有对应诊断特性的测试二进制，分别设置已存在profile文件、零console buffer/pubinterval或无支持的RECORD_PATH | 出现相应初始化错误，既有文件不被覆盖，节点按既定降级策略继续就绪 | 诊断配置损坏已有输出或阻碍正常启动 | P1 |
| `NTD-74` | - [x] 隔离其他负载，启用公开Prometheus及拒绝日志，经notify触发有hash证据的预算/容量拒绝，等待处理结束 | 可见stage/reason及拒绝计数变化，active/驻留指标回到基线，动态交易hash不进入指标label | 对外诊断错口径、指标高基数或任务未结算 | P1 |
| `NTD-75` | - [x] 分别查询Accepted输出、已被池内交易花费的输出以及链Dead/Unknown，with_data开关各一次 | 池输出/花费覆盖链视图；已花费和链Dead/Unknown对外非live，with_data仅改变数据返回 | get_live_cell与实际可花费状态分歧 | P1 |
| `NTD-76` | - [x] 配置fallback估费模式，以空池、跨一个真实byte/cycle块边界的候选及target边界调用估费RPC | 越界target报错，空池返回min_fee；有效target按proposal窗口及独立跨界候选费率计算 | 估费错误采用实际打包选择规则 | P1 |
| `NTD-78` | - [x] 池含合法父子及独立交易，通过remove_transaction删除父交易，再删除同一hash | 首次true且父子都退出池，独立交易保留；再次false，池总量匹配剩余集合 | 公开删除遗漏后代或误删独立成员 | P1 |
| `NTD-81` | - [x] recent-reject启用且无其他写入，重复notify同一可稳定拒绝的hash，随后拒绝另一hash并重启 | 同hash串行覆盖不反复抬高公开近似计数；两个拒绝记录在TTL有效期内均可查询；不要求重启后的DB估计精确等于2 | 重复拒绝造成计数膨胀或记录丢失 | P1 |
| `NTD-82` | - [x] 用真实序列化得到恰为单交易大小上限与多1字节的有效输入，分别经send_transaction/notify_transaction提交 | 等号样本可入池；多1字节在入口明确报大小错误且无池成员/队列残留；其他验证条件保持有效 | 公共提交入口的大小边界错误 | P1 |
| `NTD-85` | - [x] 固定tip和池，分别请求默认模板及bytes_limit/proposals_limit/max_version不同参数 | 待确认：既有实现忽略三个参数但RPC文档称其为上限；先记录公开响应差异，产品预期确定后再写硬断言 | 把历史规格分歧直接当成本次回归结论 | P1 |
| `NTD-91` | - [x] 启用index_tx_pool并追平；提交消耗已索引cell的A，后移除A并等待刷新；另准备未被Accepted消费的独立Waiting/Replaced输入及禁用overlay对照 | get_cells和get_cells_capacity先排除A输入、移除后恢复；独立Waiting/Replaced输入仍可见；禁用overlay只反映链上cell | 池overlay阶段过滤错误或成员变化后不刷新 | P0 |
| `NTD-93` | - [x] 普通indexer与rich indexer分别追平旧链；通过真实同高度分叉切换或truncate使旧tip非canonical，并停在该tip | get_indexer_tip最终匹配新的hash/height；cells和capacity等于独立canonical集合，旧分叉独有cell消失 | 等高重组或截断后索引仍停留旧链 | P0 |
| `NTD-95` | - [x] 单一合法慢send_transaction先通过active指标及未完成HTTP确认在处理；分别关闭客户端连接、等待实际服务408；结束HTTP后目标仍未Accepted | 不重交目标，最终按hash查询到原交易Accepted/body一致；实际未形成断连/408或处理已提前完成时标为夹具未达，不能算通过 | HTTP结束取消了已入队交易 | P0 |
| `NTD-96` | - [x] 发起多个合法慢send_transaction并确认请求仍未完成，同期以独立HTTP连接查询池/链并由第二节点提交有效块 | 慢请求未结束时只读查询已有成功响应且链顶前进；慢请求随后按实际结局结束，不设跨机器吞吐门槛 | 异步等待占住RPC运行线程而阻碍读请求与链进展 | P1 |
| `NTD-98` | - [x] 无订阅者时产生旧事件，随后建立两个订阅并确认订阅成功，再产生带独立hash的新事件；取消后重新订阅作对照 | 现存订阅在健康低负载下收到新事件且hash/body匹配；新订阅不补发订阅前历史；只检查外部投递语义 | 订阅边界出现历史重放或新事件遗漏 | P1 |
| `NTD-105` | - [x] 块顺序为cellbase、A、B、C且费用/cycles不同；一节点仅预先验证A/C，另一节点未预热；另仅破坏B签名并重算块承诺 | 两节点对合法块结果一致且总费用/cycles符合独立预期；反例均报告B在原index=2的脚本失败，链不接受该块 | 混合预热交易导致验证结果或错误位置错配 | P0 |
| `NTD-106` | - [x] 准备含absolute/relative since、成熟cellbase input或展开cell_dep的合法交易及后代；切到累计工作量更高的较短合法分叉，使时间条件失效但共同前缀中的相关cell仍存在 | 失效交易及后代退出Accepted；无时间依赖的独立交易保留；条件重新满足后可再提交 | 回退后继续接受未成熟交易或过度清池 | P0 |
| `NTD-107` | - [x] 通过有效块形成timestamp略领先当前时钟的parent，预留共识future边界及+1余量，再请求模板并提交组装块 | 模板timestamp至少parent+1，组装块满足时间规则并被接受；不包含u64终端值内部注入 | 合法未来parent下生成不递增的模板时间 | P1 |
<!-- TEST-CASES-END -->

## 既有自动化适配

持久化实际入口是正常SIGTERM/项目node.stop；当前没有公开保存RPC。正常恢复预期以前一轮startup complete、drain完成、无fault及无保存失败为前提，测试必须检查相应日志与独立文件解码；异常则按失败结果记录，退出码或端口关闭本身不证明保存成功。rename前失败保留旧文件，rename成功后的v1清理失败保留新v2。仅使用外部文件系统条件，不注入write/flush/sync内部调用。

- 优先复用 `test_cases/tx_pool_refactor/tests/` 的现有用例。NTD-61～63对应的 `test_28_pr5316_persistence_corruption.py`、`test_29_pr5316_persistence_replay.py`、`test_35_pr5316_persistence_migration.py` 已适配当前16字节头与两个交易向量；正常停止后确认旧PID退出，先验证有效文件再派生负例。NTD-61已保留父子重排、重复hash、失效项、冲突顺序和打包断言；新增 read-before-spend 夹具核对真实 Accepted 写出顺序及原文件恢复后的同块 P→A→B 顺序；TEST-MAP 仍不表示运行通过。
- NTD-44只检查返回后的真实状态；NTD-33/34检查公开模板与块结果；NTD-39/40/42/105是预热后的语义回归，均不承诺内部屏障或命中次数。NTD-81已改为重启后两条记录可查询，不再固定近似 count。NTD-48新增 Waiting 清理、补父不复活及显式重交可成功的正对照。
- EVT沿用真实慢VM、正常relay、本地提交与恢复夹具，统一普通二进制及默认8秒CPU上限。`test_cases/network_verification/` 已移除正常启动的预算覆盖及重启cap参数，复用固定长循环ELF；发送端通过本地提交接受慢交易，所有节点默认8秒CPU上限，dev链仅放宽cycles到1e12。重试要求两次真实请求/拒绝后隔离本地成功，恢复使用原目录和相同配置；CPU停止以同节点本地完整执行为对照。旧45/46/47/51/52/53路径仅保留迁移说明，避免重复执行。EVT-01新增 notify 的 Proposal source 结构化拒绝、两次真实重试、指标结算、本地接收及冷恢复；本批只作静态检查，节点运行由用户执行。NTD-95/96需要真实慢请求及处理中的外部证据；未到达408/并发区间时不得计入覆盖。Indexer复用现有普通/rich indexer节点配置，等待hash和height匹配后断言精确集合。
- NTD-72保留用户已有默认二进制启动/旧字段拒绝更新，仅将编译组合从本次范围移出。NTD-72与NTD-02可复用同一参数化运行流程；不为相同断言再造用例。

本轮补齐入口：`test_cases/tx_pool_refactor/test_pool_rpc_progress.py`（NTD-18/48/85/95/96/98）、`test_indexer_pool_projection.py`（NTD-91/93）、`test_packing_context.py`（NTD-105/106/107）及 `test_peer_resilience.py`（NTD-53）。NTD-85 是响应表征，参数上限预期仍待确认；不把忽略参数冻结为预期。408、并发窗口和主来源响应阻断均有明确夹具到达断言，未到达按失败记录。NTD-105 的 A/C cycles 使用固定脚本参考，B 的签名 cycles 为独立构块节点与两验证节点的一致性对照，费用仍由输入输出独立计算。

仍未映射的三个前置问题：

- NTD-29：当前 `authority/membership.rs::validate_backing` 会拒绝读取已被池成员花费的 cell。尚未得到只用正常 RPC 可形成已 Accepted read/spend 环的合法夹具；恢复点是提供满足前置的交易图，不把 resolve 拒绝算作模板环处理覆盖。
- NTD-36：`fit_optional_content` 按 10 字节 proposal 前缀用尽可用字节后，余量不足 10 字节，装不下一个 uncle；因此在同一模板中观察“因字节截断而未装入的 proposal 不排除一个实际装入的 uncle”存在前置冲突。恢复点是确认可公开观察的拆分场景或补充不同预算条件，原预期未被改写。
- NTD-59：慢 TCP 订阅影响 RPC broadcast 接收端，公开 `ckb_notify_transaction_dropped{boundary,reason}` 计数发生在 notify service/subscriber 的 mpsc 边界；当前 TCP 入口没有控制该 mpsc 消费者的手段。恢复点是可达该边界的夹具或相应外部 drop 指标。TPF-06 记录实际指标及慢端效果，指标为零不计作 NTD-59 覆盖。

## 移出本次集成清单的历史编号

`EVT-02`、`EVT-03`、`NTD-04`、`NTD-05`、`NTD-06`、`NTD-07`、`NTD-08`、`NTD-10`、`NTD-11`、`NTD-12`、`NTD-17`、`NTD-23`、`NTD-26`、`NTD-31`、`NTD-35`、`NTD-37`、`NTD-38`、`NTD-41`、`NTD-47`、`NTD-49`、`NTD-50`、`NTD-51`、`NTD-52`、`NTD-54`、`NTD-56`、`NTD-57`、`NTD-58`、`NTD-66`、`NTD-68`、`NTD-70`、`NTD-71`、`NTD-77`、`NTD-79`、`NTD-80`、`NTD-83`、`NTD-84`、`NTD-86`、`NTD-87`、`NTD-88`、`NTD-89`、`NTD-90`、`NTD-92`、`NTD-94`、`NTD-97`、`NTD-101`、`NTD-102`、`NTD-103`、`NTD-104`、`NTD-108`、`NTD-109`、`NTD-110`、`NTD-111`。NTD-99/100另移至[补充分析的P2P专项](ckb-pr-5316-integration-resilience.md)，不计入本表；其余编号保留，不作为待补的Python集成用例。

移出原因包括：CPU收据/校准/暂停、许可及调度、缓存写锁与回填、DB快照锁序、Plan/owner身份、通知内部队列、估计值故障注入、协议异常报文及内部整数终端值。普通最终状态用例覆盖到的部分已写入上表，剩余内部性质留给相应组件或协议专项；不以相同RPC结果替代证明。
