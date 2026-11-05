# CKB PR #5316：事务化准入集成测试

范围：仅使用Python/CkbTest、真实节点RPC和公开查询验证RBF、容量置换及预演；来源固定于 `c2596c9d2ea161afbeb9ba905b66a34454aaef27`。共用环境与PR输入见[集成测试主文档](ckb-pr-5316-non-test.md)；历史编号保留。
本次只调整文档；TPA-15既有确认继续有效，其他条目没有新增人工批准或执行结论。

## Spec

独立oracle：由已知hash图计算冲突及后代去重集合；费用从输入输出差额计算，最低替换费独立计算为F=去重victim费用之和+floor(min_rbf_rate×B的serialized_size_in_block/1000)，覆盖F/F−1；配置min_rbf_rate>min_fee_rate启用RBF，夹具数值保持在无溢出的范围；字节由独立Molecule序列化计算，容量阈值写入节点真实配置。设置足够的其他资源并用合法正对照排除签名、祖先或单交易大小先行拒绝。Accepted为raw pool的pending与proposed并集；Gap只在detail中细分。

入口与观察：真实send_transaction/test_tx_pool_accept，辅以get_raw_tx_pool、tx_pool_info、get_transaction和get_pool_tx_detail_info；稳定tip、隔离其他负载后比较前后精确集合。日志和metrics只作公开诊断。多次查询最终相同不证明内部提交原子性；内部事件次数/原因、owner身份、跨分片重叠及Stale重试不作集成断言。

混合100/101夹具：交易尺寸与容量应使移除前k−1项仍超限，移除预期k项才够用，并排除其他合法闭包；RBF本身始终≤100。共享后代按hash去重，B本身不计。晚到父需要伪造已接受内部状态的子分支本次移出，不把两类并集回归声称为全部三类修改证明。

### SPEC-01：替换后的公开状态完整
Condition: 合法候选经真实RPC完成准入
Expected: 相关替换闭包退出、候选进入，失败时原Accepted保持
Observable: RPC返回、raw pool集合、body及公共总量
Basis: [tx-pool/src/authority/membership.rs](https://github.com/nervosnetwork/ckb/blob/c2596c9d2ea161afbeb9ba905b66a34454aaef27/tx-pool/src/authority/membership.rs)；[rpc/src/module/pool.rs](https://github.com/nervosnetwork/ckb/blob/c2596c9d2ea161afbeb9ba905b66a34454aaef27/rpc/src/module/pool.rs)
Source: observation
Testing: required

### SPEC-02：RBF独立费用与数量限制
Condition: RBF开启且存在输入冲突，其他资源充分
Expected: 不新增独立未确认输入；后代去重计费；费用≥最低费；100项可替换，101项为-1111
Observable: RPC错误种类、前后hash集合、独立费用及字节计算
Basis: [tx-pool/src/authority/membership.rs](https://github.com/nervosnetwork/ckb/blob/c2596c9d2ea161afbeb9ba905b66a34454aaef27/tx-pool/src/authority/membership.rs)；[tx-pool/src/constants.rs](https://github.com/nervosnetwork/ckb/blob/c2596c9d2ea161afbeb9ba905b66a34454aaef27/tx-pool/src/constants.rs)；[util/types/src/core/fee_rate.rs](https://github.com/nervosnetwork/ckb/blob/c2596c9d2ea161afbeb9ba905b66a34454aaef27/util/types/src/core/fee_rate.rs)
Source: observation
Testing: required

### SPEC-03：容量裁剪与共享100项额度
Condition: 正常RPC提交导致RBF及/或容量置换
Expected: 依更新后的包费率保留可容纳闭包，保护候选与其依赖；本次可构造的RBF和容量修改并集≤100，候选不计；无解则Full
Observable: 前后精确Accepted、独立尺寸及集合并集、-1106
Basis: [tx-pool/src/authority/membership.rs](https://github.com/nervosnetwork/ckb/blob/c2596c9d2ea161afbeb9ba905b66a34454aaef27/tx-pool/src/authority/membership.rs)；[tx-pool/src/authority/membership/graph.rs](https://github.com/nervosnetwork/ckb/blob/c2596c9d2ea161afbeb9ba905b66a34454aaef27/tx-pool/src/authority/membership/graph.rs)；[tx-pool/src/constants.rs](https://github.com/nervosnetwork/ckb/blob/c2596c9d2ea161afbeb9ba905b66a34454aaef27/tx-pool/src/constants.rs)
Source: observation
Testing: required

### SPEC-04：稳定条件下预演无副作用
Condition: tip、池、验证与记录通道稳定，调用两个公开入口
Expected: preview验证相同策略但不入池/写拒绝记录/改变替换历史；真实提交按结果准入或记录拒绝
Observable: 两个RPC响应、两节点交易查询、raw pool/conflicted及recent状态
Basis: [rpc/src/module/pool.rs](https://github.com/nervosnetwork/ckb/blob/c2596c9d2ea161afbeb9ba905b66a34454aaef27/rpc/src/module/pool.rs)；[tx-pool/src/authority/service/submission.rs](https://github.com/nervosnetwork/ckb/blob/c2596c9d2ea161afbeb9ba905b66a34454aaef27/tx-pool/src/authority/service/submission.rs)
Source: observation
Testing: required

### SPEC-08：conflicted表示保留的替换历史
Condition: recent启用、历史空间充足且存在有效恢复触发键
Expected: 失败候选只有拒绝状态；保留的被替换交易出现在conflicted并排除于Accepted总量
Observable: get_transaction、get_raw_tx_pool(verbose)及tx_pool_info
Basis: [tx-pool/src/authority/query.rs](https://github.com/nervosnetwork/ckb/blob/c2596c9d2ea161afbeb9ba905b66a34454aaef27/tx-pool/src/authority/query.rs)；[tx-pool/src/authority/membership.rs](https://github.com/nervosnetwork/ckb/blob/c2596c9d2ea161afbeb9ba905b66a34454aaef27/tx-pool/src/authority/membership.rs)
Source: observation
Testing: required

### SPEC-14：RBF和容量淘汰共同反映在最终集合
Condition: 一次提交需要两类淘汰才能准入
Expected: 两类受影响交易均退出Accepted，候选及不相关成员组成精确预期集合
Observable: RPC返回、raw pool及独立字节/cycles总量
Basis: [tx-pool/src/authority/membership.rs](https://github.com/nervosnetwork/ckb/blob/c2596c9d2ea161afbeb9ba905b66a34454aaef27/tx-pool/src/authority/membership.rs)
Source: observation
Testing: required

## 测试树

<!-- TEST-TREE-BEGIN -->
- 真实节点集成回归
  - 替换与最终池状态
    - TPA-01 [primary] -> SPEC-01：完整后代替换
  - RBF费用与数量规则
    - TPA-02 [primary] -> SPEC-02：独立未确认输入
    - TPA-03 [primary] -> SPEC-02：去重费用等号
    - TPA-04 [primary] -> SPEC-02：恰100项
    - TPA-23 [primary] -> SPEC-02：费用少1
    - TPA-24 [primary] -> SPEC-02：101项拒绝
  - 容量及共享修改上限
    - TPA-05 [primary] -> SPEC-03：候选自淘汰时原池保持
    - TPA-06 [primary] -> SPEC-03：恰好可容纳
    - TPA-07 [primary] -> SPEC-03：保留候选必需祖先
    - TPA-20 [primary] -> SPEC-03：多轮更新包费率
    - TPA-26 [primary] -> SPEC-03：混合并集100
    - TPA-27 [primary] -> SPEC-03：最小混合并集101
  - 预演与真实提交
    - TPA-08 [primary] -> SPEC-04：成功预演无副作用
    - TPA-09 [primary] -> SPEC-04：低费拒绝及recent区别
    - TPA-25 [primary] -> SPEC-04：Full拒绝及recent区别
  - 可查询替换历史
    - TPA-15 [primary] -> SPEC-08：conflicted与失败候选区别
  - 混合淘汰
    - TPA-17 [primary] -> SPEC-14：RBF和额外容量淘汰后的精确集合
  - 本次集成边界
    - [not_applicable] 内部锁、任务调度、精确时钟及资源预留 — 需要组件接缝；历史设计见文末，外部最终结果不证明这些切面。
    - [not_applicable] 手工异常 P2P 报文及构建组合 — 分属协议专项和构建检查；本清单采用节点正常通信与现成测试二进制。
    - [unanalysed] 其余存储优化与性能变化 — 本次只收敛已有设计的集成范围；扩展时从原始 PR 差异和相应公共入口继续，不计为已覆盖。
<!-- TEST-TREE-END -->

## 用例

当前保留17项集成场景；方框仅表示TEST-MAP存在，本次未编写或执行自动化。

<!-- TEST-CASES-BEGIN -->
| 用例 | 场景 | 预期结果 | 防止的问题 | 优先级 |
| --- | --- | --- | --- | --- |
| `TPA-01` | - [x] 真实提交A→C→D及独立对照；B冲突A的链上输入且满足完整后代费用和容量规则，再send_transaction(B) | B接受，A/C/D全部退出Accepted，独立项保留；公共总字节/cycles等于独立预期 | 替换遗漏后代或最终集合不完整 | P0 |
| `TPA-02` | - [x] A及后代、独立未确认父P已入池；构造高费B替换A但新增P的输出作为输入，经RPC提交 | 返回RBFRejected(-1111)，A/后代/P仍在池且总量保持，B未入池 | 高费绕过独立未确认输入限制或破坏原池 | P0 |
| `TPA-03` | - [x] A/D共享后代C，B同时冲突A/D；按去重集合计算最低替换费F，费用恰为F，其他限制充分 | B接受，A/D/C全部退出且共享C只计一次；最终集合与总量正确 | 共享后代重复收费或费用等号误拒绝 | P1 |
| `TPA-04` | - [x] 无额外容量淘汰，B的去重冲突加后代恰100项；每笔先经真实RPC确认入池，费用及祖先限制充分 | B接受且全部100项退出；独立对照保留，公共总量匹配新集合 | 把RBF上限100误成严格小于100 | P1 |
| `TPA-05` | - [x] 通过真实小容量配置及独立排名构造B需多轮裁剪但最终会自淘汰的集合，提交B | 返回Full(-1106)，所有原Accepted及公开总量保持，B未入池 | 候选最终被拒绝却先删掉旧成员 | P0 |
| `TPA-06` | - [x] 配置容量使无输入冲突的高排名B在移除指定低排名交易及后代后恰好可容纳；RPC提交B | B接受且指定闭包退出，其余成员保留；独立序列化总量恰等容量上限 | 容量边界或淘汰闭包错误 | P1 |
| `TPA-07` | - [x] B使用未确认P的输出作input或cell_dep；独立计算可知要容纳B就必须裁掉P且没有其他合法解 | 提交返回Full(-1106)，P及其他原成员和总量保持，B未入池 | 为候选腾空间时删掉候选自己的依赖 | P1 |
| `TPA-08` | - [x] 固定tip和池，B可合法替换A及后代；先test_tx_pool_accept(B)再真实send_transaction(B) | preview返回独立正确fee/cycles且原池/拒绝记录/历史保持，B在两节点均未入池；真实提交成功后才改变集合 | 预演产生真实准入或传播副作用 | P0 |
| `TPA-09` | - [x] recent-reject启用且健康，A→C稳定；B的替换费为独立最低费F−1，先preview后submit | 两入口均-1111；preview后B无拒绝记录，真实拒绝后可查询RBFRejected；A/C集合及总量保持 | 预演写入拒绝记录或真实拒绝破坏原池 | P1 |
| `TPA-15` | - [x] recent-reject启用且替换历史空间充足，A具有有效恢复触发键；低费B先失败，合格D再成功替换A | 失败B有拒绝记录但不在conflicted；保留为替换历史的A出现在conflicted，历史不计入Accepted字节/cycles | 把失败候选与替换历史混为一谈 | P1 |
| `TPA-17` | - [x] 合法B替换A后仍需额外淘汰无冲突低排名E才能容纳；以真实配置和交易尺寸构造，提交B | B入池，A/E均退出Accepted，其他成员和公开总量符合独立计算；只断言混合淘汰最终结果 | 只完成RBF或容量淘汰中的一部分 | P1 |
| `TPA-20` | - [x] 构造共享祖先A下低费闭包B→C及独立E，使首次裁剪B/C后A剩余包费率高于E而仍需腾出空间 | 最终额外淘汰E并保留A及其剩余后代；集合、祖先统计及总量符合独立重算 | 多轮容量裁剪使用过期包费率多删交易 | P1 |
| `TPA-23` | - [x] A/D共享后代C，B同时冲突A/D，费用为去重最低费F−1，其他条件充分，真实send_transaction | 返回RBFRejected(-1111)，A/D/C仍在Accepted且公共总量不变，B不入池 | 费用下界的真实拒绝清走原有闭包 | P1 |
| `TPA-24` | - [x] 无额外容量淘汰，真实构造B的冲突加后代去重101项，费用充分，RPC提交 | 返回RBFRejected(-1111)，101项原成员及总量保持；不要求错误文本给出完整遍历数 | RBF超限时部分移除旧池 | P1 |
| `TPA-25` | - [x] recent-reject启用且健康，容量使B最终自淘汰；tip和池固定，先preview再submit | 两入口均Full(-1106)；preview后无B拒绝记录，submit后有Full记录；原池及总量保持 | 混淆预演无副作用与真实拒绝记录 | P1 |
| `TPA-26` | - [x] 仅用正常RPC构造RBF闭包与额外容量淘汰并集恰100项，含共享后代；按尺寸保证唯一最小可容纳集合，不构造晚到父内部状态 | B接受，完整预期并集退出且其他成员保留；共享项只算一次，候选B不占既有项额度 | 分开计算RBF与容量修改上限或重复计数 | P0 |
| `TPA-27` | - [x] RBF闭包自身≤100，但真实配置及尺寸使至少还需容量淘汰而最小修改并集为101，其他限制充分且无其他合法裁剪方案 | 返回Full(-1106)，原Accepted集合和总量完全保持；与RBF自身101的-1111区分 | 总修改上限只检查RBF部分 | P0 |
<!-- TEST-CASES-END -->

## 移出本次集成清单的历史编号

`TPA-10`、`TPA-11`、`TPA-12`、`TPA-13`、`TPA-14`、`TPA-16`、`TPA-18`、`TPA-19`、`TPA-21`、`TPA-22`、`TPA-28`。这些场景依赖内部Plan/Apply、owner身份、资源预留、晚到父状态、提交屏障或唤醒次数，不列为待补Python集成用例。

本次保留TPA-26/27的真实RPC可达部分；TPA-17只检查混合淘汰结果，内部移除事件原因未纳入断言。已有Python替换、容量和预演用例应优先复用并补齐相应断言/映射；当前未据名称或上游Rust测试自动勾选。

自动化入口为 `test_cases/tx_pool_refactor/test_transactional_admission.py`。17 个集成 Case 映射到 11 个原生方法；同一方法内用确定循环覆盖 100/101、input/cell_dep 等同类边界，兼容 `CkbTest` 的 unittest 基类。交易大小由独立 Molecule 编码计算，fee 从已知输入/输出计算，Accepted cycles 使用固定 always_success 参考。容量经普通配置与真实 PID 重启生效；没有 CPU cap 配置或专用二进制要求。仅完成静态/收集及无需节点的辅助检查，产品断言待用户运行。
