# CKB PR #5316：性能、混沌与作恶节点测试分析

范围：真实节点的性能和恢复性分析及自动化，沿用已核对 head `c2596c9d2ea161afbeb9ba905b66a34454aaef27`；运行结果以对应native收据为准，设计与映射本身不代表通过。功能oracle复用[主文档](ckb-pr-5316-non-test.md)和[准入文档](ckb-pr-5316-transactional-admission.md)，场景标识与已有Case关联而不复制其实现。
分层：前16项以Python控制真实节点、RPC、进程与外部服务；末4项为单列的P2P专项分析，需要受控对端，尚未算作现有Python集成能力。普通节点会先验证再广播，向源节点RPC提交坏交易不证明接收节点经历了恶意网络消息。

## Spec

### SPEC-01：性能比较必须在相同正确性下进行
Condition: 两个由项目配置指定的真实节点二进制，以相同交易集、链、配置及负载运行
Expected: 先核对所有hash的准入/拒绝/未完成结局与链/池正确性，再按配对基线和预定阈值分析吞吐、延迟及资源趋势；不同语义结果不作直接速度排名
RBF验收补充（2026-11-05用户确认）：本地接受替换不承诺替换交易最终上链，允许旧交易先确认。TPF-03将模板清除旧交易的10秒窗口作为观测，未清除记录未完成和残留hash，不因此失败；使用实际模板正常提交区块，核对冲突双方在同一有效链上至多确认一方、失效交易及其后代退出Accepted，并确认独立新交易继续上链。默认dev链空载推进六块仍无一方确认时，记录确认未完成并报进展失败；旧交易确认后其仍有效的后代允许继续留池。按最终确认方分别解释样本，不把不同结局直接作速度排名。
Observable: 每请求开始/结束及返回值、队列/CPU/RSS/FD时间序列、目标hash集合、完整块与索引状态
Basis: [当前benchmark质量规则](https://github.com/nervosnetwork/ckb/blob/c2596c9d2ea161afbeb9ba905b66a34454aaef27/tx-pool/docs/BENCHMARK.md)与[维护说明](https://github.com/nervosnetwork/ckb/blob/c2596c9d2ea161afbeb9ba905b66a34454aaef27/tx-pool/docs/MAINTENANCE.md)；其内部Rust服务基准仅提供设计参考，不作为本次真实节点测试结果
Source: inference
Testing: required

### SPEC-02：故障恢复遵守已完成持久化与链状态边界
Condition: 进程退出/暂停、真实网络分区、节点目录I/O故障或外部请求取消
Expected: 恢复后可处理新请求，链保持共识有效；正常关闭与已成功保存快照的恢复分别核对；SIGKILL不保证保存尚在内存中的全部Accepted
Observable: PID/退出状态、文件字节及独立解码、peer拓扑、canonical tip、hash/body、重新提交结果
Basis: [持久化](https://github.com/nervosnetwork/ckb/blob/c2596c9d2ea161afbeb9ba905b66a34454aaef27/tx-pool/src/persisted.rs)、[链恢复](https://github.com/nervosnetwork/ckb/blob/c2596c9d2ea161afbeb9ba905b66a34454aaef27/tx-pool/src/authority/chain.rs)、[RPC取消契约](https://github.com/nervosnetwork/ckb/blob/c2596c9d2ea161afbeb9ba905b66a34454aaef27/rpc/src/module/pool.rs)
Source: observation
Testing: required

### SPEC-03：恶意负载与正常来源可分别观察
Condition: 真实peer发送昂贵合法、重复、冲突交易或反复断连，诚实来源持续存在
Expected: 每类流量按实际策略接收/忽略/拒绝；网络预算耗尽不单独触发ban；停止负载后恢复且诚实业务有进展，统计不得重复计算同hash
Observable: 每peer/目标hash请求与拒绝轨迹、交易池集合、诚实标记交易延迟、peer列表、ban地址和进程资源
Basis: [来源预算](https://github.com/nervosnetwork/ckb/blob/c2596c9d2ea161afbeb9ba905b66a34454aaef27/tx-pool/src/authority/budget.rs)、[入站](https://github.com/nervosnetwork/ckb/blob/c2596c9d2ea161afbeb9ba905b66a34454aaef27/tx-pool/src/authority/ingress.rs)、[网络结果](https://github.com/nervosnetwork/ckb/blob/c2596c9d2ea161afbeb9ba905b66a34454aaef27/sync/src/relayer/transactions_process.rs)
Source: inference
Testing: required

### SPEC-04：P2P不响应及非法响应遵守协议处置
Condition: 受控对端完成会话/协议握手且目标已实际请求交易，然后注入指定不响应或非法响应
Expected: 不响应可由超时/断线和backup推进；eligible重复raw hash整批忽略，但每项声明cycles先检查；指定签名错误为Script/malformed，合法交易高报且不超共识cycles为DeclaredWrongCycles，均处罚当前Remote来源；诚实来源继续工作
Observable: 实际线上请求/响应字节、请求归属、peer/ban变化、最终交易池和健康peer后续交易
Basis: [relay处理](https://github.com/nervosnetwork/ckb/blob/c2596c9d2ea161afbeb9ba905b66a34454aaef27/sync/src/relayer/transactions_process.rs)、[请求与known状态](https://github.com/nervosnetwork/ckb/blob/c2596c9d2ea161afbeb9ba905b66a34454aaef27/sync/src/types/mod.rs)、[入站处置](https://github.com/nervosnetwork/ckb/blob/c2596c9d2ea161afbeb9ba905b66a34454aaef27/tx-pool/src/authority/ingress.rs)
Source: observation
Testing: required

## 测试树

<!-- TEST-TREE-BEGIN -->
- 性能（真实节点）
  - TPF-01 [primary] -> SPEC-01：来源/大小/脚本成本下的准入吞吐及延迟
  - TPF-02 [primary] -> SPEC-01：网络验证压力下的诚实业务与链进展
  - TPF-03 [primary] -> SPEC-01：依赖图、RBF确认双方互斥、容量及模板刷新观测
  - TPF-04 [primary] -> SPEC-01：恢复、重组及索引追平耗时
  - TPF-05 [primary] -> SPEC-01：重复负载后的稳定资源趋势
  - TPF-06 [primary] -> SPEC-01：订阅与外部通知的端到端代价
- 混沌（外部故障）
  - TCH-01 [primary] -> SPEC-02：有负载时正常停止
  - TCH-02 [primary] -> SPEC-02：成功快照后的强制终止
  - TCH-03 [primary] -> SPEC-02：保存I/O失败及恢复
  - TCH-04 [primary] -> SPEC-02：分区、合法分叉与恢复
  - TCH-05 [primary] -> SPEC-02：进程暂停与恢复
  - TCH-06 [primary] -> SPEC-02：固定种子的组合操作序列
- 作恶节点（现有正常协议入口）
  - TMN-01 [primary] -> SPEC-03：昂贵但共识合法的交易压力
  - TMN-02 [primary] -> SPEC-03：多peer重复传播
  - TMN-03 [primary] -> SPEC-03：正确签名的冲突交易与RBF
  - TMN-04 [primary] -> SPEC-03：反复连接/断开
- P2P专项分析（受控对端入口待补）
  - TMN-05 [primary] -> SPEC-04：宣告后不响应、断线与后备来源
  - TMN-06 [primary] -> SPEC-04：坏脚本与错误声明cycles
  - NTD-99 [primary] -> SPEC-04：同响应重复raw hash
  - NTD-100 [primary] -> SPEC-04：重复项中的非法声明先被检查
  - [pending] 会话夹具 — 现有relay_v3_codec只有字节编解码，尚未发现完整Python握手/收发驱动；先建立有效请求/响应正对照，再实施这四项。
- [unanalysed] 包级丢包/重排、恶意区块头或compact block语义 — 需要额外协议夹具及对应功能规格，从sync/relayer和现有relay完整性测试继续；本批不列为已覆盖。
<!-- TEST-TREE-END -->

## 用例

所有新增项均未映射、未执行。NTD-99/100沿用原有编号，已从主文档的集成清单移至本文件P2P专项；其余编号不改写旧Case身份。

<!-- TEST-CASES-BEGIN -->
| 用例 | 场景 | 预期结果 | 防止的问题 | 优先级 |
| --- | --- | --- | --- | --- |
| `TPF-01` | - [x] 固定交易集和初始链，以本地RPC、正常relay及混合来源按开放式发送速率阶梯施压；远端完成样本worker≥1，覆盖快/慢脚本及大小交易 | 记录offered/实际发送/终态速率、准入吞吐及p50/p95/p99；校对全部唯一hash结局和失败率，在同主机配对基线及A/A噪声下判断退化；零worker另作来源控制 | 只统计成功样本或客户端限速掩盖服务退化 | P1 |
| `TPF-02` | - [x] worker≥1，多peer持续合法慢交易使验证繁忙；固定速率提交诚实本地快速交易、只读RPC和有效块，比较无压力对照 | 分别记录诚实交易与查询尾延迟、链同步滞后和网络拒绝原因；正确性保持，停止负载后在预定恢复期限内排空 | 远端吞吐正常却饿死本地、读RPC或链处理 | P0 |
| `TPF-03` | - [x] 固定不同规模的chain/fanout/diamond池，执行合法RBF；观测模板10秒后按实际模板正常提交区块，再提交独立新交易及测试容量置换；每轮重建相同状态 | RBF准入后池集合/费用/顺序精确；模板刷新未完成只记录时长与残留hash；允许旧交易或替换交易确认，但同一链无冲突花费，失效方及其后代退出Accepted，独立新交易可确认；保存实际赢家及图操作/容量样本，按规模和相同结局比较 | 把替换入池等同最终确认，或放宽刷新耗时后漏掉冲突、失效交易残留及停滞 | P1 |
| `TPF-04` | - [x] 固定有效池规模、文件字节量和两条分叉，分别测同目录恢复及真实reorg；同时观察普通/rich indexer追平 | 记录进程就绪、完整目标集合恢复、索引hash追平三个独立时间点；最终集合/文件/链正确后才比较耗时 | 只用RPC端口就绪冒充交易恢复或索引完成 | P1 |
| `TPF-05` | - [x] 按固定种子重复提交、RBF、移除、出块和重连周期；每轮回到相同逻辑状态并经过固定冷却窗口 | 记录进程累计CPU、RSS/footprint、线程/FD及公开队列在稳定窗口的趋势；允许缓存平台期，持续增长按预设预算及噪声区间判定 | 把缓存高水位当泄漏或漏掉多轮资源增长 | P1 |
| `TPF-06` | - [x] 相同事件负载下分别配置无订阅、正常订阅、慢订阅及超时模板通知端，保持节点与交易输入一致 | 比较真实端到端吞吐/尾延迟/CPU和drop指标；健康订阅可见标记事件，慢端不造成永久停顿；不从进程CPU推断编码次数 | 通知压力拖慢交易池或误把内部优化当性能实测 | P1 |
| `TCH-01` | - [x] 持久化启用且启动完成，混合提交与模板通知仍在进行时正常SIGTERM；记录停机前已返回成功的交易及未完成请求，再无peer重启 | 无drain/fault/save失败时，未被确认/合法淘汰/显式清理的已接受交易恢复且body一致；异常日志按失败记录；未完成请求逐hash查询，恢复后新交易可处理 | 正常停止丢失已完成准入或错误宣称未完成请求成功 | P0 |
| `TCH-02` | - [x] 固定链，先正常停机并独立验证快照S0；无peer重启并确认S0恢复，再接受新交易S1，在后续负载中SIGKILL并再次重启 | 数据库可打开，S0中仍有效交易恢复且可上链；S1允许未持久化而丢失，但不产生伪造body/重复花费；恢复后接受新交易；只验证进程终止故障 | 把内存准入误作崩溃持久保证或损坏已完成快照 | P0 |
| `TCH-03` | - [x] 保存过有效旧快照后重启；在节点临时文件路径施加占位/权限或独立磁盘限额，正常停机触发rename前写入失败，再撤销故障并重启 | 日志证实目标I/O失败且旧文件字节保持、可恢复；重新提交未保存交易后再次正常停机，确认新文件有效并可重启恢复；不要求停机保存期间RPC可用 | 磁盘错误破坏旧快照或使服务长期不可恢复 | P0 |
| `TCH-04` | - [x] 禁用自动重连干扰并按固定拓扑分区，双方分别出合法分叉与冲突交易；以peer集合证明分区后恢复连接 | 各节点最终选择累计工作量更高的合法链；索引及池按该链收敛，链上无重复花费，独立有效交易仍能提交 | 把短时分叉或高度比较错误当作最终一致性 | P0 |
| `TCH-05` | - [x] 对一个真实节点记录PID后SIGSTOP，其他节点继续出块/传播；经过固定窗口SIGCONT并恢复正常连接 | 暂停期间目标无进展属预期；恢复后链/池按规则追平、读RPC恢复且新交易可完成；记录恢复耗时，不由墙钟暂停证明线程CPU计费 | 进程恢复后遗留死锁、失效连接或误判CPU预算 | P0 |
| `TCH-06` | - [x] 固定种子交错RPC客户端断连、clear_tx_verify_queue、remove、正常停止保存并重启、分区/恢复与合法reorg；每步记录请求、完成响应和外部故障 | 未完成请求最终按hash对账；已确认/移除/替换结果符合当前链，故障全部撤销后新标记交易和模板可用；失败时保存最短可复现操作序列 | 组合操作产生永久卡住、过期状态或错误归因 | P0 |
| `TMN-01` | - [x] 所有节点维持默认8秒CPU上限，真实来源节点经本地提交后持续广播共识合法但CPU昂贵的不同交易，独立Accepted预先入池且容量足以排除正常容量淘汰；诚实peer和本地RPC持续提交快速标记交易 | 目标hash日志证实网络预算拒绝，不因预算耗尽而ban；诚实请求和链仍有进展，停止负载后排空且预先独立Accepted保持 | 昂贵合法交易拖垮正常用户或触发预算误封 | P0 |
| `TMN-02` | - [x] 多个真实peer反复广播相同合法hash，通过来源移除/重交、接收端clear_tx_pool重置relay代际及受控重连形成实际重传；记录接收端确实收到的请求/交易轨迹 | 同hash在Accepted只计一次、body一致；重复流量不重复累加公开字节/cycles，独立标记交易仍进展；不声称覆盖单响应内重复项 | 跨peer重复传播放大池资源或抑制正常交易 | P0 |
| `TMN-03` | - [x] 不同真实来源节点分别广播相同链上输入的有效双花与合格/不合格RBF，均正确签名；同时保留独立诚实交易 | 稳定观察点池与最终区块均无相互冲突输入；有序低费/高费样本符合-1111或替换规则；并发样本允许策略内赢家，诚实独立交易可上链 | 冲突来源污染池、绕过替换规则或影响无关交易 | P0 |
| `TMN-04` | - [x] 多真实peer按固定速率反复连接/断开并发合法交易；总连接数预留诚实peer空间，记录实际peer集合、FD和事件 | 连接/队列按公开配置处理，诚实链和RPC继续进展；停止扰动后稳定peer可传播且资源回到稳定区间；连接上限策略另行设计 | 连接抖动耗尽外部资源或留下失效peer状态 | P0 |
| `TMN-05` | - [ ] P2P专项，待受控对端：握手后宣告有效hash，以对端收到的含hash请求/线上字节确认目标已请求；保持会话不回body或断开，backup已宣告且持有同一交易 | backup来源收到该hash的后续请求，目标取得原body且独立交易继续；主响应已完成或请求未实际到达则夹具未达，不用目标的hash数量日志替代请求归属 | 恶意宣告占住请求或阻断后备来源 | P0 |
| `TMN-06` | - [ ] P2P专项，待受控对端：两个新hash独立运行；一项只破坏合法witness签名且cycles充分、排除ARGV特例；另一项保持合法并声明已知实际cycles+小增量且≤共识上限；均先确认属于该Remote peer的请求 | 前者到达Script校验失败并按malformed处罚来源，后者到达DeclaredWrongCycles并处罚；两者不入池，独立地址诚实peer交易可接受；known/未请求过滤或其他拒绝点均不算到达预期 | 无效交易通过网络准入或错误扩大peer处罚 | P0 |
| `NTD-99` | - [ ] P2P专项，待受控对端：对真实请求响应两个相同raw hash的eligible项，含不同witness/cycles变体及独立合法项，所有声明cycles合法 | 本响应整批忽略、其中交易均不入池且单纯重复不ban；后续干净响应能重新取得原交易；只验证公开入池/请求结果 | 单响应内重复被默选变体或放大入站 | P0 |
| `NTD-100` | - [ ] P2P专项，待受控对端：同一响应两个eligible项raw hash重复，仅后一项声明cycles超过共识上限，另以全部合法声明响应作对照 | 先对每个eligible项执行声明检查并按既有策略ban；整批不入池；不把这一结果归到普通重复忽略 | 重复去重掩盖后一个副本的非法cycles声明 | P0 |
<!-- TEST-CASES-END -->

## 负载与判定设计

本文件的TPF-01/02、TMN-01及其他CPU预算相关负载均使用普通二进制、默认8秒CPU上限，不增加预算配置维度或test feature构建要求。调整的是交易工作量、到达速率和并发；Remote自动校准维持默认行为，不把每笔交易的实际预算写成统一8秒。目标hash日志证明预算拒绝，性能deadline和故障恢复期限另行定义。

性能矩阵采用分阶段筛选而非全组合：来源Local/Remote/混合；快脚本/合法慢脚本；小交易/接近有效上限；独立/chain/fanout/diamond；完成型远端样本worker=1和项目常用正值；空池/中等/接近容量。先用一次不计入结果的pilot选择发送速率、持续时间和可承受规模，之后冻结。worker=0单列为来源差异控制：本地send_transaction可完成，Remote/notify预期排队而后台不验证，不要求远端吞吐或停止施压后自动排空，其样本不混入远端性能回退判断。故障与恶意流量先单因素再组合，便于定位原因。

同一主机交替运行基线/候选，记录二进制hash、编译配置、操作系统/CPU、链规格、输入集、节点配置、客户端配置和种子；两个二进制路径由运行配置提供，不写入长期用例版本号。两边分别做相同配置A/A噪声对照，再用平衡AB/BA顺序；清理数据状态或从同一快照开始，预热和冷启动单独统计。初始建议至少10个独立配对轮次，正式轮次/窗口由pilot冻结，不按看到的结果提前停止。

开放式负载记录计划发送时间与实际发送时间，客户端调度延迟单列；每个hash只计一次终态。分别报告HTTP成功、408/断连、策略拒绝、实际准入与截止时未完成数；408后最终Accepted仍与原请求关联。发送速率、成功吞吐、完成率和尾延迟同时报告，未完成/超时不得从样本中静默删除。p99样本不足时保留原始数据并标明不足，优先报告p50/p95及超时率，不以少量完成请求证明尾延迟稳定。

吞吐、延迟、恢复时间和资源趋势的数值门槛属于待确认的性能目标。运行前需固定允许吞吐回退比例、p95/p99增幅、最大错误率、诚实标记交易/链进展期限、排空及恢复期限、稳定资源增长预算。结论逐场景/指标给出，对配对轮次计算差值/比值及95%区间：完整越过已定回退线为退化信号，完整位于接受区间且质量检查有效才支持达标，跨线为不确定；没有阈值或A/A控制时只给诊断数据。功能错误、池/链不一致、进程崩溃则直接记录，不等待性能统计判断。

RSS不等于交易池预算账本，allocator/DB缓存也可能保持高水位；以相同逻辑状态的多轮稳定窗口分析增长趋势，并记录公开队列、FD和线程帮助归因。进程累计CPU可复用 `test_cases/network_verification/cpu_usage.py`，不当成某个交易的线程CPU收据。冻结采样/日志级别；高详细日志的诊断轮与常规性能轮分别保留。

## 入口、恢复与证据

- 现有入口：`test_cases/network_verification/workload.py` 提供普通RPC/节点relay、进程身份和目标hash观察；`test_cases/feature/test_relay_integrity.py`、`test_header_sync_and_peer_lifecycle.py` 提供真实多节点和连接场景；持久化、通知及reorg复用主文档对应用例。以上只是可复用基础，未据此给新增Case勾选。
- 故障只作用于本轮节点进程、临时目录及明确的节点间链路。每条时间线记录故障前、施加证据、撤销证据、稳定结果；SIGSTOP在清理时始终SIGCONT，分区恢复原拓扑，文件占位/限额撤销，最后确认原故障已消失。权限变化只有实际产生目标I/O错误才计入；ENOSPC使用独立限额文件系统，具体夹具尚待实现。
- 保存入口统一为正常SIGTERM/项目node.stop并等待旧PID退出，当前没有公开保存RPC。正常关闭核对已完成Accepted的恢复，前提是startup complete、drain完成且无fault/save失败；出现异常须保留为失败结果。SIGKILL仅核对此前正常停机已验证的快照及链完整性，不承诺全部内存池交易持久。TCH-02不宣称精确命中write/flush/rename的中间指令；各阶段崩溃切面仍是后续夹具工作。
- 恶意节点结果同时记录攻击流量和诚实控制流量。预算耗尽不因该原因ban；TMN-06事先固定一般Script失败为malformed及高报cycles为DeclaredWrongCycles，两者应处罚当前Remote来源。其他拒绝类别的预期须先按规格确定，避免事后跟随实际结果改写oracle。涉及ban隔离的P2P专项需独立网络地址；不同peer_id共用同一IP时，不用该拓扑证明地址封禁的隔离性。
- 现有 `test_cases/tx_pool_refactor/relay_v3_codec.py` 可复用payload编解码，但不提供完整网络会话。末四项实施前须完成真实握手、协议版本协商、请求归属、正常响应及日志/包捕获对照。缺少这些入口时标为“夹具未就绪”，不以notify_transaction或本地解析函数代替网络路径。
- 每轮保存manifest、带时间戳的请求/结果、hash集合和独立oracle、metrics/进程采样、日志偏移、故障事件、清理结果及native退出状态；报告超时、缺测和未达前置。Python仍使用项目pytest与CkbTest，按用户授权执行定向测试。


自动化入口：`test_pool_performance.py`（TPF-01/03/04/05/06）、`test_peer_resilience.py`（TPF-02、TMN-01～04、TCH-04/05）、`test_process_resilience.py`（TCH-01/02/06），均在 `test_cases/tx_pool_refactor/`；TCH-03复用并补全现有 `tests/test_36_pr5316_persistence_failures.py`。末四项 P2P 专项仍不纳入本轮。

性能入口目前提供诊断探针：TPF-01有固定短轮 A/A、B/B、AB/BA 顺序及 Local/Remote/混合、快慢脚本和大小交易矩阵；TPF-02为双真实来源的有限昂贵负载及无压力对照；TPF-03～06分别记录图操作、恢复里程碑、资源趋势和通知开销。保留所有请求、HTTP错误、未完成样本及不足100个完成样本的 p99 缺测标识。`CURRENT_MAIN`仅提供基线二进制，链规格/模板统一来自`CURRENT_TEST`；不是换用 mainnet 规格。当前未冻结数值阈值、持续施压规模或正式十轮配对设计，因此不作性能达标/退化统计结论；worker=0的既有功能对照也不混入完成吞吐。资源回稳区间与持续负载扩展仍按上述性能目标冻结后执行，复用这些入口。

运行时 JSON 收据保存在 `report/pool_performance/`，节点清理后仍保留；实际命令、退出状态及native结果保存在对应 `reports/` 执行收据。`[x]`表示对应自动化入口存在，不代表全部性能矩阵已执行或 P0 行为已验证。
