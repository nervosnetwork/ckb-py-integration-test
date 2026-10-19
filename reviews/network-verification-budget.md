# Network verification budget：Python 回归测试

25 个已确认的集成测试点；本次交付 17 个 pytest 方法，映射其中 19 点。VT2-03/04/05/30/55/57 尚未实现，保留为缺口，不计通过。

运行说明见 [suite README](../test_cases/network_verification/README.md)。用例预期不随被测版本自动改变；映射勾选不等于执行通过。

## 1. 变更与测试边界

网络来源验证增加机器本地 CPU 预算：relay、proposal response、网络 orphan 都应受限；本地提交、dry-run、本地恢复和区块共识验证不受这一网络 cap。
超限表示验证尚未完成，不等于交易无效，因此应可重试、不误 ban、不污染池状态。计量是线程 CPU 而非墙钟，本集成文档只验证外部结果，不测纳秒级预算边界。

本次移出测试点：直接调用 runner/Worker、注入 CPU 收据/时钟故障、Suspend/Resume/Stop 竞态、join/panic/资源析构、校准公式和采样、内存序、内部缓存计数、Rust API compile-fail、wasm/build/clippy 文档检查。这些需要单元/组件/构建测试，不改名包装成集成测试。

保留的规范验证回归也只使用真实交易与公开接口；不要求读取内部 cache、remaining、control channel、spend owner 或 shared HeaderIndex。

## 2. 共用环境与判定原则

1. **二进制与链配置**：记录固定提交、二进制 SHA-256、feature、平台和配置。production/test 分开产出；带 test 的节点可用 `max_tx_verify_time_ms=1` 制造稳定超限，另设 `12000` 对照。`max_tx_verify_cycles`、区块 cycles、交易大小、手续费、祖先数和成熟度分别满足对应路径。
2. **慢交易不是随便放大 cycles**：使用合法多脚本组/高 CPU 工作负载，记录完整交易字节、hash、真实 cycles 和 fee。先在独立节点或不入池的 dry-run 证明合法；低 cap 下确实拒绝、足够预算或本地路径确实成功。声明 cycles 必须真实，除非该点专测声明错误。
3. **冷缓存**：目标节点在网络测试前没有成功接收/本地提交/区块验证过目标交易；需要时重启节点并禁止重传。仅 `remove_transaction` 不作为清空验证缓存的证据。
4. **完成屏障**：协议已实际交付交易，之后再次宣告同 hash，收到新的 `GetRelayTransactions`，再查询池和状态。orphan 还需先观察数量增加，再补父并观察移除。`verify_queue_size=0`、RPC notify 返回 hash 或固定 sleep 都不单独证明预算路径已完成。
5. **结果观测**：以 `get_transaction`、`get_raw_tx_pool`、`tx_pool_info`、`get_peers`、`get_banned_addresses`、`sync_state` 和真实协议/订阅结果为主。当前版本带 hash 的原生日志仅补充拒绝归因；不沿用旧版本 的结构化日志、`get_overview` 或 `-1113` 预期。
6. **运行方式**：复用 Python `CkbTest`、现有节点/RPC/矿工工具；本套件不实现自定义 P2P 协议栈。
7. **超时与留证**：设置有界 watchdog，保存配置、交易/区块、协议事件、RPC 前后快照和退出结果。watchdog 超时是测试未得到结果，不等同已证实 CPU 超限。

## 3. Spec（可观察行为）

`explicit` 表示确认的行为要求；`observation` 是当前源码行为；`inference` 是由代码顺序推导的预期，均保留已确认的设计含义。

### SPEC-01：网络入口都执行预算
Condition: 真实 relay、proposal response 或补父后的网络 orphan 进入验证
Expected: 快速合法交易可入池；合法慢交易超限后不入池；proposal 与 orphan 两种处理分支均受限
Observable: 节点池成员、orphan 数量、真实协议请求及连接状态
Basis: 本测试项目的行为契约；由实际运行的节点版本检验。
Source: explicit
Testing: required

### SPEC-05：规范 cycles 与共识结果保持一致
Condition: 同一共识环境中的 VM 交易，覆盖各版本及适用的 spawn/exec，以及声明 cycles 低于实际需求的输入
Expected: 足够预算下 cycles 与固定基准一致，交易池与区块验证均接受；声明 cycles 不足时保留 ExceededMaximumCycles 与脚本定位
Observable: test_tx_pool_accept、get_raw_tx_pool(true)、get_transaction、目标区块 hash
Basis: 本测试项目的行为契约；由实际运行的节点版本检验。
Source: explicit
Testing: required

### SPEC-06：原有上下文和内建脚本检查仍有效
Condition: 真实交易经过节点 RPC 验证；DAO lock、Type ID、since、capacity 或 DAO fee 依赖错误
Expected: 保留对应规范错误；合法对照可接受；本范围不推断内部缓存命中或 VM 调用次数
Observable: RPC 错误类别及脚本/输入定位、池成员、打包结果；capacity/DAO fee 可能在池入口更早返回 Malformed，不套用内部 verifier 错误
Basis: 本测试项目的行为契约；由实际运行的节点版本检验。
Source: observation
Testing: required

### SPEC-07：超限可重试并保护池状态
Condition: 冷缓存合法网络交易因 CPU 预算未完成验证
Expected: 无新成功入池或公开拒绝记录，无新增 ban，可重新请求；RBF 候选超限时原池交易保持不变
Observable: get_transaction=unknown、原始池条目、get_peers/get_banned_addresses、同 hash 再次请求；RBF 不破坏旧池是验证先于提交的推断
Basis: 本测试项目的行为契约；由实际运行的节点版本检验。
Source: inference
Testing: required

### SPEC-08：公开接口不暴露内部预算结果
Condition: 网络超限后查询状态/订阅；随后本地正常提交及发生普通池淘汰
Expected: 没有预算专用 RPC 错误契约；预算结果不作为公开拒绝事件，已有普通拒绝事件继续发布
Observable: 真实 RPC 响应及同一 WebSocket/订阅连接事件；不直接调用错误适配或注入通知通道
Basis: 本测试项目的行为契约；由实际运行的节点版本检验。
Source: observation
Testing: required

### SPEC-09：网络预算与本地及共识路径隔离
Condition: 本地提交、dry-run、持久池恢复、重组恢复、RBF 冲突交易重新排队、有效区块接收
Expected: 不施加网络 CPU cap，仍执行各自 cycles 与上下文限制；dry-run 不入池、不形成可绕过后续网络验证的成功状态
Observable: 本地 RPC、池内容、正常重启、真实分叉切换、committed 状态
Basis: 本测试项目的行为契约；由实际运行的节点版本检验。
Source: explicit
Testing: required

### SPEC-10：实际无效交易仍被正常拒绝
Condition: 预算充足，声明 cycles 过高、低于实际执行需求或确定脚本非零退出
Expected: 仍保留对应公开拒绝原因、recent-reject 记录和远端惩罚；不当成 CPU 未完成验证
Observable: RPC 拒绝状态、脚本定位及 peer ban 结果
Basis: 本测试项目的行为契约；由实际运行的节点版本检验。
Source: observation
Testing: required

### SPEC-11：生产与测试配置边界
Condition: 用相互独立的 production/test 二进制启动节点
Expected: 生产拒绝三个内部字段；test 仅允许 max_tx_verify_time_ms 为正 u32；缺省可启动、低 cap 实际生效。源码当前缺省/生产上限为 8000ms，但集成不声称测出了精确 CPU 边界
Observable: 真实启动命令、退出码、错误输出、RPC readiness 及同一工作负载的网络处理结果
Basis: 本测试项目的行为契约；由实际运行的节点版本检验。
Source: explicit
Testing: required

### SPEC-14：同步进度不长期落后已验证链
Condition: 真实无效块处理结束，随后接受有效块且链已稳定
Expected: 稳定窗口内 unverified tip 高度不落后 verified tip；hash 与对应高度匹配。不要求重组中跨 RPC 调用具备原子快照
Observable: sync_state 的 unverified_tip_number/hash 与 tip_number/hash，以及 get_header/get_tip_header
Basis: 本测试项目的行为契约；由实际运行的节点版本检验。
Source: observation
Testing: required

## 4. 集成测试树

<!-- TEST-TREE-BEGIN -->
- 网络准入
  - 正常 relay
    - VT2-01 [primary] -> SPEC-01, SPEC-05：冷缓存接收节点通过真实 relay 收到声明 cycles 正确的快速合法交易 → 交易进入 pending/proposed，交易体和 cycles 正确；发送 peer 保持连接
  - relay 超限与重试
    - VT2-02 [primary] -> SPEC-01, SPEC-07：低 cap 节点收到合法慢交易；处理结束后再次宣告同一 hash，并由同一 peer 发送快速合法交易 → 慢交易未入池、状态为 unknown、没有新增 ban；节点再次请求同一 hash，快速交易正常入池
  - proposal response
    - VT2-03 [primary] -> SPEC-01, SPEC-07：节点先发出 GetBlockProposal，再收到包含合法慢交易的真实 BlockProposal 响应；接收节点为低 cap、冷缓存 → 慢交易未入池且没有公开 rejected 记录；结束后再次宣告同 hash 可收到请求，连接仍可用
  - orphan
    - 直接验证
      - VT2-04 [primary] -> SPEC-01, SPEC-07：网络慢子交易先缺父；声明 cycles 正确且不超过 max_tx_verify_cycles；观察 orphan 增加后再补父 → 父交易被接受；子交易从 orphan 移除但未入池，状态 unknown、可重新请求且无新增 ban
    - 排队验证
      - VT2-05 [primary] -> SPEC-01, SPEC-07：网络慢子交易先缺父；声明 cycles 正确且大于 max_tx_verify_cycles、仍满足区块 cycles；观察 orphan 增加后补父 → 补父后子交易进入排队处理，结束后未入池；可重新请求、状态 unknown、无新增 ban
- 本地与共识隔离
  - 本地提交
    - VT2-06 [primary] -> SPEC-09, SPEC-08：同一合法慢交易已在当前节点走网络路径超限；保持低 cap，用 send_transaction 提交相同交易字节 → RPC 返回同一 hash，交易进入池；没有预算专用 RPC 错误
  - dry-run
    - VT2-07 [primary] -> SPEC-09, SPEC-07：父交易在池中，慢子交易尚未成功验证；先 test_tx_pool_accept，再经真实网络发送同一子交易 → dry-run 返回正确 cycles/fee 且不入池；后续网络低 cap 仍拒绝该交易，之后本地提交可成功
  - 接收有效区块
    - VT2-08 [primary] -> SPEC-09：接收节点低 cap 已拒绝慢交易，且没有本地成功验证它；另一节点独立将相同交易打包为有效区块并同步 → 接收节点接受完整区块，canonical tip 达到目标块，目标交易为 committed
  - 持久池恢复
    - VT2-09 [primary] -> SPEC-09：慢交易本地入池后正常停机；同一数据目录以低 cap 重启，期间没有网络重传或 RPC 重交 → 持久池交易重新出现在池中，交易体、cycles 与停机前一致；随后可正常打包
  - 重组恢复
    - VT2-10 [primary] -> SPEC-09：含慢交易的旧链被有效更优分叉替换；重组前重启接收节点清除内存验证缓存；交易在新链仍合法且 cycles 不超过恢复上限 → canonical tip 切换到新分叉；旧块脱离后的慢交易重新入池并可再次打包
  - 冲突交易排队恢复
    - VT2-45 [primary] -> SPEC-09：低 cap 下慢交易 A 网络超限；池中快速 B 花费 X/Y，本地提交仅花费 X 的 A 因替换费用不足被拒；再由仅花费 Y 的高费快速 C 替换 B → C 入池后，A 自动恢复到池，A/C 都可打包；恢复期间不手动重交 A，A 此前仅被拒绝而未成功入池
- 规范验证回归
  - VM 交易
    - VT2-24 [primary] -> SPEC-05：使用各 VM 版本及其支持的 spawn/exec 合法交易夹具；同一共识环境下，经 RPC 验证、网络入池，再由另一冷节点接收含交易的区块 → 每个夹具的 RPC/池 cycles 与固定基准相同；交易入池并最终 committed
  - DAO lock 尺寸
    - VT2-26 [primary] -> SPEC-06：合法 DAO deposit 转 withdrawing 的交易仅在 input0 对应输出改变 lock 尺寸；通过 RPC 校验/提交，另设合法尺寸对照 → 非法变体返回 DaoLockSizeMismatch 并且不入池；合法对照通过
  - Type ID
    - VT2-27 [primary] -> SPEC-06：以独立输入分别构造 Type ID 创建交易：合法 32 字节创建 hash，以及仅将创建 hash 改成另一 32 字节值的非法变体；通过 RPC 校验/提交 → 合法创建交易入池并可打包；错误创建 hash 返回 Type ID ValidationFailure（退出码 −3、输出 type 脚本定位）且不入池
  - since
    - VT2-52 [primary] -> SPEC-06：有效输入设置尚未达到的绝对区块高度 since，通过 RPC 校验/提交；随后按链规则推进到成熟高度再提交同一交易 → 未成熟时返回 Immature 并不入池；成熟后同一交易可接受
  - capacity
    - VT2-53 [primary] -> SPEC-06：非 DAO 交易输入合计 99 CKB、输出 100 CKB，单 cell 容量及其他规则有效；通过 RPC 校验/提交 → 在手续费预检查返回 RPC −1108 / Malformed，原因包含 Overflow，并且不入池；调整为合法输入输出金额后的对照可接受
  - DAO header 依赖
    - VT2-54 [primary] -> SPEC-06：DAO withdrawing 输入的交易其余条件有效，仅缺少对应 withdrawing header_dep；通过 RPC 校验/提交，再补齐依赖作对照 → 在 DAO 手续费预检查返回 RPC −1108 / Malformed，原因包含 InvalidOutPoint，且不入池；补齐且成熟后的合法对照通过
- 公开结果
  - 拒绝订阅
    - VT2-29 [primary] -> SPEC-08：先建立 rejected_transaction 订阅；处理一笔网络预算超限交易后，再用合法高费替换淘汰一笔已入池的普通交易 → 同一订阅收到被替换交易的公开 RBFRejected 事件；已完成处理的慢交易没有内部预算拒绝事件，订阅不中断
- 真实无效交易
  - 声明 cycles 错误
    - VT2-30 [primary] -> SPEC-10：充足预算下经真实 relay 发送快速合法脚本交易，但声明 cycles 高于其真实值；启用 recent-reject → 交易未入池，公开拒绝原因为 DeclaredWrongCycles；发送 peer 被 ban，后续查询保留拒绝记录
  - 脚本失败
    - VT2-55 [primary] -> SPEC-10：充足预算下通过真实 relay 发送确定非零退出的快速脚本交易，避开特殊可延后错误；启用 recent-reject → 交易未入池，公开拒绝保留 ValidationFailure 及脚本定位；发送 peer 被 ban，拒绝记录可查询
  - 声明 cycles 不足
    - VT2-57 [primary] -> SPEC-05, SPEC-10：独立节点取得单脚本组合法交易实际 cycles=C；冷接收节点在充分 CPU 预算下收到声明 C−1 的真实 relay，启用 recent-reject → 交易不入池；公开拒绝为 Verification，原因含 ExceededMaximumCycles 及正确脚本定位；peer 被 ban，拒绝记录可查询
- 节点配置
  - 生产构建
    - VT2-32 [primary] -> SPEC-11：独立生产二进制先使用普通配置启动，再分别加入 max_tx_verify_time_ms、min_tx_verify_time_ms、cycles_per_ms 启动 → 普通配置启动且 RPC 可用；三个字段分别触发 unknown field，进程以非零退出且节点未就绪
  - test 构建
    - VT2-33 [primary] -> SPEC-11：test 二进制分别使用缺省配置、cap=1/12000，以及 cap=0/−1/4294967296；另检查两个未开放预算字段 → 缺省和合法正值可启动；合格慢交易在 1ms 网络拒绝、在 12000ms 对照通过；非法值及未开放字段启动失败
- 同步进度
  - 无效块后继续同步
    - VT2-39 [primary] -> SPEC-14：真实节点先处理头部合法但区块体验证失败的块，再从健康来源接收有效后继；停止继续出块并等待链处理稳定 → 无效块未进入 canonical chain；有效块成为 tip；稳定读取的 unverified_tip_number 不低于 tip_number，且其 hash 对应同高度已知 header
- 状态保护
  - RBF 候选超限
    - VT2-48 [primary] -> SPEC-07：池中有交易 A 及后代 C；满足替换条件的慢候选 B 经网络超限；随后本地提交相同 B 作替换成功对照 → 超限后 A/C 的成员、交易体、状态及 cycles 不变，B 不在池中；仅本地成功提交 B 后才替换 A/C
<!-- TEST-TREE-END -->

## 5. 集成测试点（完整当前集合）

复选框仅表示本测试项目是否有对应 `TEST-MAP`；未勾选不代表 PR 没有原生测试，也不代表本轮执行失败。

<!-- TEST-CASES-BEGIN -->
| 用例 | 场景 | 预期结果 | 防止的问题 | 优先级 |
| --- | --- | --- | --- | --- |
| `VT2-01` | - [x] 冷缓存接收节点通过真实 relay 收到声明 cycles 正确的快速合法交易 | 交易进入 pending/proposed，交易体和 cycles 正确；发送 peer 保持连接 | 网络预算误拒绝普通合法交易 | P0 |
| `VT2-02` | - [x] 低 cap 节点收到合法慢交易；处理结束后再次宣告同一 hash，并由同一 peer 发送快速合法交易 | 慢交易未入池、状态为 unknown、没有新增 ban；节点再次请求同一 hash，快速交易正常入池 | 预算拒绝变成永久拒绝、误 ban 或阻塞后续交易 | P0 |
| `VT2-03` | - [ ] 节点先发出 GetBlockProposal，再收到包含合法慢交易的真实 BlockProposal 响应；接收节点为低 cap、冷缓存 | 慢交易未入池且没有公开 rejected 记录；结束后再次宣告同 hash 可收到请求，连接仍可用 | proposal 没有 remote 信息而漏加预算 | P0 |
| `VT2-04` | - [ ] 网络慢子交易先缺父；声明 cycles 正确且不超过 max_tx_verify_cycles；观察 orphan 增加后再补父 | 父交易被接受；子交易从 orphan 移除但未入池，状态 unknown、可重新请求且无新增 ban | 补父后直接验证遗漏网络预算 | P0 |
| `VT2-05` | - [ ] 网络慢子交易先缺父；声明 cycles 正确且大于 max_tx_verify_cycles、仍满足区块 cycles；观察 orphan 增加后补父 | 补父后子交易进入排队处理，结束后未入池；可重新请求、状态 unknown、无新增 ban | orphan 转入验证队列时丢失网络来源 | P0 |
| `VT2-06` | - [x] 同一合法慢交易已在当前节点走网络路径超限；保持低 cap，用 send_transaction 提交相同交易字节 | RPC 返回同一 hash，交易进入池；没有预算专用 RPC 错误 | 把网络保护误用于用户本地提交 | P0 |
| `VT2-07` | - [x] 父交易在池中，慢子交易尚未成功验证；先 test_tx_pool_accept，再经真实网络发送同一子交易 | dry-run 返回正确 cycles/fee 且不入池；后续网络低 cap 仍拒绝该交易，之后本地提交可成功 | dry-run 意外入池或预热成功缓存掩盖网络预算 | P0 |
| `VT2-08` | - [x] 接收节点低 cap 已拒绝慢交易，且没有本地成功验证它；另一节点独立将相同交易打包为有效区块并同步 | 接收节点接受完整区块，canonical tip 达到目标块，目标交易为 committed | 网络拒绝污染共识验块或接收缓存掩盖问题 | P0 |
| `VT2-09` | - [x] 慢交易本地入池后正常停机；同一数据目录以低 cap 重启，期间没有网络重传或 RPC 重交 | 持久池交易重新出现在池中，交易体、cycles 与停机前一致；随后可正常打包 | 启动恢复错误施加网络预算或恢复静默丢交易 | P0 |
| `VT2-10` | - [x] 含慢交易的旧链被有效更优分叉替换；重组前重启接收节点清除内存验证缓存；交易在新链仍合法且 cycles 不超过恢复上限 | canonical tip 切换到新分叉；旧块脱离后的慢交易重新入池并可再次打包 | 重组回池错误受网络预算限制 | P0 |
| `VT2-24` | - [x] 使用各 VM 版本及其支持的 spawn/exec 合法交易夹具；同一共识环境下，经 RPC 验证、网络入池，再由另一冷节点接收含交易的区块 | 每个夹具的 RPC/池 cycles 与固定基准相同；交易入池并最终 committed | 执行器重构改变 cycles 或池与共识验块结果分叉 | P0 |
| `VT2-26` | - [x] 合法 DAO deposit 转 withdrawing 的交易仅在 input0 对应输出改变 lock 尺寸；通过 RPC 校验/提交，另设合法尺寸对照 | 非法变体返回 DaoLockSizeMismatch 并且不入池；合法对照通过 | 新验证路径漏做 DAO lock 尺寸检查 | P1 |
| `VT2-27` | - [x] 以独立输入分别构造 Type ID 创建交易：合法 32 字节创建 hash，以及仅将创建 hash 改成另一 32 字节值的非法变体；通过 RPC 校验/提交 | 合法创建交易入池并可打包；错误创建 hash 返回 Type ID ValidationFailure（退出码 −3、输出 type 脚本定位）且不入池 | 无需普通 VM 的脚本被误当成免验证 | P1 |
| `VT2-29` | - [x] 先建立 rejected_transaction 订阅；处理一笔网络预算超限交易后，再用合法高费替换淘汰一笔已入池的普通交易 | 同一订阅收到被替换交易的公开 RBFRejected 事件；已完成处理的慢交易没有内部预算拒绝事件，订阅不中断 | 内部预算结果泄露到公开订阅或影响后续事件 | P1 |
| `VT2-30` | - [ ] 充足预算下经真实 relay 发送快速合法脚本交易，但声明 cycles 高于其真实值；启用 recent-reject | 交易未入池，公开拒绝原因为 DeclaredWrongCycles；发送 peer 被 ban，后续查询保留拒绝记录 | 把声明不实误处理成可重试 CPU 超限 | P1 |
| `VT2-32` | - [x] 独立生产二进制先使用普通配置启动，再分别加入 max_tx_verify_time_ms、min_tx_verify_time_ms、cycles_per_ms 启动 | 普通配置启动且 RPC 可用；三个字段分别触发 unknown field，进程以非零退出且节点未就绪 | 测试预算配置混入生产或未知字段被静默忽略 | P0 |
| `VT2-33` | - [x] test 二进制分别使用缺省配置、cap=1/12000，以及 cap=0/−1/4294967296；另检查两个未开放预算字段 | 缺省和合法正值可启动；合格慢交易在 1ms 网络拒绝、在 12000ms 对照通过；非法值及未开放字段启动失败 | 测试开关失效、旧配置不兼容或无效值放宽预算 | P1 |
| `VT2-39` | - [x] 真实节点先处理头部合法但区块体验证失败的块，再从健康来源接收有效后继；停止继续出块并等待链处理稳定 | 无效块未进入 canonical chain；有效块成为 tip；稳定读取的 unverified_tip_number 不低于 tip_number，且其 hash 对应同高度已知 header | 先前失败留下过时同步进度 | P1 |
| `VT2-45` | - [x] 低 cap 下慢交易 A 网络超限；池中快速 B 花费 X/Y，本地提交仅花费 X 的 A 因替换费用不足被拒；再由仅花费 Y 的高费快速 C 替换 B | C 入池后，A 自动恢复到池，A/C 都可打包；恢复期间不手动重交 A，A 此前仅被拒绝而未成功入池 | 内部重新排队的本地恢复交易被误加网络预算 | P0 |
| `VT2-48` | - [x] 池中有交易 A 及后代 C；满足替换条件的慢候选 B 经网络超限；随后本地提交相同 B 作替换成功对照 | 超限后 A/C 的成员、交易体、状态及 cycles 不变，B 不在池中；仅本地成功提交 B 后才替换 A/C | 候选未完成验证就部分替换已接受交易 | P0 |
| `VT2-52` | - [x] 有效输入设置尚未达到的绝对区块高度 since，通过 RPC 校验/提交；随后按链规则推进到成熟高度再提交同一交易 | 未成熟时返回 Immature 并不入池；成熟后同一交易可接受 | 验证重构跳过上下文成熟度校验 | P1 |
| `VT2-53` | - [x] 非 DAO 交易输入合计 99 CKB、输出 100 CKB，单 cell 容量及其他规则有效；通过 RPC 校验/提交 | 在手续费预检查返回 RPC −1108 / Malformed，原因包含 Overflow，并且不入池；调整为合法输入输出金额后的对照可接受 | 验证路径漏做容量守恒检查 | P1 |
| `VT2-54` | - [x] DAO withdrawing 输入的交易其余条件有效，仅缺少对应 withdrawing header_dep；通过 RPC 校验/提交，再补齐依赖作对照 | 在 DAO 手续费预检查返回 RPC −1108 / Malformed，原因包含 InvalidOutPoint，且不入池；补齐且成熟后的合法对照通过 | 新验证入口跳过 DAO fee 上下文验证 | P1 |
| `VT2-55` | - [ ] 充足预算下通过真实 relay 发送确定非零退出的快速脚本交易，避开特殊可延后错误；启用 recent-reject | 交易未入池，公开拒绝保留 ValidationFailure 及脚本定位；发送 peer 被 ban，拒绝记录可查询 | 把真正脚本失败统一改成内部可重试结果 | P1 |
| `VT2-57` | - [ ] 独立节点取得单脚本组合法交易实际 cycles=C；冷接收节点在充分 CPU 预算下收到声明 C−1 的真实 relay，启用 recent-reject | 交易不入池；公开拒绝为 Verification，原因含 ExceededMaximumCycles 及正确脚本定位；peer 被 ban，拒绝记录可查询 | 把共识 cycles 耗尽误判成声明过高或 CPU 可重试超限 | P1 |
<!-- TEST-CASES-END -->

## 6. 关键场景实施说明

### 网络、proposal 与 orphan

- **VT2-01/02**：先准备快速与慢速两种互不冲突交易；接收节点只通过网络获得目标交易。超限结果与同 peer 快速成功对照一起判断，避免把网络断连误判成预算生效。
- **VT2-03**：先发送包含 proposal ID 的 compact block，捕获节点 `GetBlockProposal`，再回真实 `BlockProposal`；RPC `notify_transaction` 不替代协议入口证明。proposal 窗口、区块有效性及链高度独立满足。
- **VT2-04/05**：先 withholding parent，实际观察 orphan 增加；再通过正常本地提交补父。分支由**真实声明 cycles 与配置阈值的大小关系**决定；排队点记录已有 enqueue 日志/协议与池完成序列，不强求瞬时队列长度一定被轮询捕获。较低阈值应仍允许准备交易、补父及大 cycles worker 工作；不通过谎报 cycles 选路。
- 所有超限点先用有效交易和低/高预算对照排除 cycles、手续费、依赖、容量等其他失败；普通真实无效交易另用 VT2-30/55/57 验证。

### 本地、重启、重组与验块

- **VT2-06/07**：保留目标交易完整字节，确保对照不是换了一笔轻交易。dry-run 前后池成员不变，并用随后的真实网络拒绝排除成功缓存预热。
- **VT2-08**：发送节点独立本地接受并按 proposal window 打包；接收节点只经历网络拒绝，没有成功本地验证。接收时启用完整验块，不用跳过验证的 RPC；验收以该区块成为 canonical 和交易 committed 为准。
- **VT2-09**：关停前确认目标在池，正常停机保留持久文件；重启期间隔离其他 peer。等待池实际恢复而非仅 RPC 可连通或 service started；再恢复出块验证可打包。
- **VT2-10**：分叉点前准备共同可用输入；旧分叉包含慢交易，新分叉不包含也不花费其输入。旧链节点重启后再接入更优有效分叉，避免旧内存成功缓存；同时满足恢复路径 `max_tx_verify_cycles`，不是仅满足区块上限。

### 规范验证回归

- **VT2-24**：使用已有真实合约夹具；同一交易/依赖/链环境的基准 cycles 来自固定基线二进制或已有确定夹具，应避免拿修改后节点自己的结果循环作证。各 VM 版本只搭配其支持的 syscall，不构造不支持组合。
- **VT2-26/27/52/53/54**：每个非法输入只改变目标规则所需字段，保留合法对照。RPC 校验和提交的错误匹配规范类别/索引，随后用原始池查询证实未准入；不把“RPC 出错”当成命中目标规则。
- 冷/热缓存逐项调用次数、缓存键覆盖、精确错误竞争顺序已移出本集成切片；上述点只证明公开验证契约。

- **VT2-26/54**：以真实 RPC 入口最先命中的错误为准；VT2-54 的 DAO fee 错误由 Malformed 包装，不声称触达内部 contextual verifier。VT2-53 的输入小于输出也先在 fee 预检查报 Malformed/Overflow，不应期待内部 OutputsSumOverflow。
- **VT2-27**：选 Type ID 创建作为本 PR 的代表性回归，所有 args 都是 32 字节，只改变派生 hash 正确性（−3），避免误测 args 长度错误（−1）。合法与非法交易使用不同的已确认输入，避免重复输入/已花费检查提前拦截；不扩展为完整 Type ID 更新规则矩阵。

### 公开订阅、RBF 与同步

- **VT2-29**：订阅先完成确认；网络慢交易完成可重试屏障后，再做一笔独立快速交易的合法 RBF，等待其明确 `RBFRejected` 事件。记录截至该事件及有界收尾窗口的完整序列，检查目标 hash 未出现内部拒绝。不以短暂“没消息”单独判定。这个黑盒点不声称触达内部 `TryFrom` 跳过分支：预算结果可能在上游已被过滤，内部异常转换另属组件测试。
- **VT2-30/55/57**：开启 recent-reject，使用独立 peer/节点避免 ban 相互污染。预算足够、脚本确定完成，RPC 拒绝内容与预期错误一致；不对每类错误泛化所有惩罚规则。
- **VT2-45**：在公共分叉前准备确认输入 X/Y；先网络低 cap 拒绝慢 A，随后快速 B 花费 X/Y。A 仅花费 X，满足普通最低手续费但不足以替换 B，本地提交需明确返回 RBFRejected。此入口先完成脚本、再检查替换，成功缓存只在成功准入后写入，所以失败 A 不预热成功缓存。快速高费 C 仅花费 Y、满足替换 B 的规则，提交 C 后 X 释放，节点应自行找回 A 并通过本地验证队列入池；期间隔离重传、不重启、不重交 A，以自动出现的同 hash 池条目为判定。
- **VT2-57**：使用实际 cycles 已知的单脚本组交易，声明 C−1 且 CPU 预算充足；预期是 Verification/ExceededMaximumCycles，不是 DeclaredWrongCycles。若改用多脚本组，错误内数值为失败组剩余额度，不将其固定为全交易 C−1。
- **VT2-48**：A/C 入池后固定链高度，保存两者的 hash、交易体、status、cycles；B 费用和替换条件提前满足，先网络超限再本地提交完全相同 B。本地对照成功后 A/C 被替换，才能排除最初只是 RBF 条件不满足。只断言公开池状态，不读取内部 spend owner。
- **VT2-39**：无效块必须进入真实区块体验证而非在头部预检查就失败；例如头部本身有效但 DAO 内容不匹配。坏来源与后续健康来源分开，待有效 tip 稳定且停止同步/出块后比较 RPC 字段。同高度才要求 hash 对应同一 header；存在更高候选时不要求它等于 canonical tip hash。该顺序黑盒检查不证明内部“两个候选先到达、失败 reset 后成功回调”的精确竞态被覆盖。
