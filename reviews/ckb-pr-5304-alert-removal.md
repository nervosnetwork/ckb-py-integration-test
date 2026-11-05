# Alert 移除兼容性用例评审

评审范围：CKB #5304 的默认配置、旧配置加载、Alert RPC/协议移除、响应兼容，以及签名合法的过期告警首次到达和重复投递。

源码版本：`develop@d40e4722525d45d5ce7a8ae29a1dc6d18d2f747f`。旧行为分析基线：`17d7db5bb423a1b2177e14a132a41d5a91a515f3`。测试框架基线：`V210@112f47ba040467766cad16e4155f8254fdb09634`。执行前需固定实际新旧二进制；表内勾选只表示存在 TEST-MAP，不代表任意构建都已验证通过。

## 修改背景

旧 Alert 功能用于在节点间传播告警。它已经不再使用，但旧实现仍可能重新处理过期消息：清理过期告警时，也清除了“已经处理过”的记录；同一条签名合法的旧告警再次到达后，可能重新触发通知及用户配置的告警脚本。

#5304 移除了这套功能的接收、传播、RPC 和通知处理。新版仍允许合法旧配置加载，并保留 `get_blockchain_info.alerts` 这个空列表字段，方便节点与客户端升级。验证重点是旧功能无法被配置或消息重新启用，同时基本节点服务仍然可用。

这里的重放是“重复投递同一条旧消息”。签名合法不代表消息尚未过期；也不能只重复发送就假定触发了问题，旧实现的去重记录必须先经历过期清理。

## 接口说明

- 配置入口：待测二进制的 `ckb init/run`，网络和 RPC 的 `Alert` 配置值、`alert_signature`、`notify.network_alert_notify_script`、`notify.notify_alert_timeout`。
- RPC 入口：`send_alert`、`get_blockchain_info`、`local_node_info`、`get_peers`，以及用于确认节点正常工作的区块查询。
- 网络入口：普通节点连接与同步、旧节点的告警发送和传播。旧 Alert 协议名为 `/ckb/alt`，协议 ID 为 `110`，在 RPC 中表示为 `0x6e`。
- 可观察结果：生成的配置、启动结果、JSON-RPC 响应、支持和已协商的协议列表、受控告警脚本的调用记录、节点同步后的区块 hash。
- 成功结果：旧配置可加载但不恢复 Alert 能力；RPC 方法不存在、协议不注册/协商、`alerts` 保持空列表，旧告警无通知或转发副作用；普通 RPC 与同步正常。
- 范围边界：不验证任意 P2P 报文、签名算法安全性或资源压力极限。保留的新区块脚本和区块/交易订阅的完整回归尚未实现，需后续单独评审；本文件只检查基本 RPC 和同步健康。

## 共同前提与判定口径

- **新配置来自真实二进制：** 使用新版 `ckb init` 在独立数据目录生成配置，并检查解析后的字段；不能用本项目仍含旧 Alert 的 Jinja 模板代替“新版默认配置”。
- **旧配置是合法输入：** 在保留正常网络协议和 RPC 模块的配置上，分别加入网络 `Alert`、RPC `Alert`、合法签名配置、旧通知脚本与超时字段，并覆盖全部字段组合。通知超时满足旧规则（至少 100 毫秒）；“被忽略”不表示任意错误类型或非法值都应启动成功。脚本仅记录测试标记和调用，不执行实际运维动作。
- **控制配置组合：** 加载兼容性覆盖各类字段单独加入和全部组合；RPC、响应及网络消息行为以默认配置、完整旧配置两组验证，不对所有字段做无意义的全排列。默认配置没有旧脚本，脚本未执行的断言适用于配置了记录脚本的组。
- **有效签名对照：** 使用隔离 devnet 的测试密钥及旧节点认可的签名配置；告警的版本范围适用于旧节点，消息 ID 与其他用例隔离。先由旧接收节点的实际告警或脚本记录证明输入有效；只有发送 RPC 成功或签名字段存在不算投递成功。
- **新版的预期是入口消失：** 新版不再协商 Alert，发送者可能根本无法把告警交付给它。应表述为“向新版发起传播尝试，确认入口关闭且无副作用”，不能声称新版 Alert 处理器收到了并拒绝消息。
- **无副作用要有对照：** 正常区块同步证明连接可用，旧节点对照证明同类输入能触发旧路径；在固定、有界的观察窗口检查脚本记录、协议和响应。无通知但已断链、未发送成功或旧对照也未触发，均不能视为通过。
- **旧节点观察有陷阱：** 旧 `get_blockchain_info` 会清理过期告警，所以旧节点处理过期消息后，查询仍可能返回 `alerts=[]`。过期用例以告警脚本的新增记录为处理证据；这次查询可以用于两轮投递之间的清理动作。

## 用例

| 用例 | 场景 | 预期结果 | 防止的问题 | 优先级 |
| --- | --- | --- | --- | --- |
| `ALERT-REMOVE-01` | - [x] 使用新版二进制生成默认节点配置，并按该配置启动节点。 | 默认配置不启用网络/RPC 的 Alert，不生成旧签名和旧 Alert 通知默认字段；节点正常启动，普通 RPC 可用。 | 新生成的配置仍引导用户启用已经移除的功能，或默认节点无法启动。 | P1 |
| `ALERT-REMOVE-02` | - [x] 在正常配置上分别加入旧网络 Alert、旧 RPC Alert、合法旧签名配置、旧通知脚本与超时字段，再覆盖全部字段组合；启动并保留数据重启。 | 各组合法旧配置均可启动和重启，普通 RPC 与区块同步可用；不会仅因保留这些旧字段而报配置错误。 | 升级后旧配置无法使用，导致节点启动失败。 | P0 |
| `ALERT-REMOVE-03` | - [x] 在默认配置和显式保留旧 RPC Alert 模块的配置下调用 `send_alert`，分别提供正常告警参数、过期告警参数和缺少告警必填字段的参数。 | 对合法 JSON-RPC 请求均返回 `-32601 Method not found`，不会进入旧告警参数或签名处理；之后普通 RPC 仍可用。 | 旧 RPC 仍被注册，或加入旧模块配置后重新启用告警入口。 | P0 |
| `ALERT-REMOVE-04` | - [x] 新版节点分别使用默认配置和完整旧 Alert 配置，主动及被动连接新版节点、启用 Alert 的兼容旧节点，并同步新产生的区块。 | 本地支持协议和连接后的协商协议均无 Alert；普通连接正常，节点同步到相同区块 hash。 | Alert 协议被旧配置重新注册，或移除协议导致正常节点互通失败。 | P0 |
| `ALERT-REMOVE-05` | - [x] 新版节点在默认配置和完整旧 Alert 配置下，分别于启动就绪、同步新区块及保留数据重启后查询区块链信息。 | `alerts` 字段始终存在，类型为列表且值为 `[]`；响应可正常解析，其他链信息与节点实际状态一致。 | 移除 Alert 时删除字段、改变类型或破坏正常链信息查询，导致客户端不兼容。 | P1 |
| `ALERT-REMOVE-06` | - [x] 旧发送节点尝试传播签名合法且未过期的告警；旧节点中继对照可将告警送到下游旧观察节点，改由默认配置或完整旧配置的新版节点作为唯一中继。 | 新版不协商 Alert，`alerts=[]`，配置的旧告警脚本不执行；下游观察节点收不到经新版转发的告警，普通区块同步和 RPC 仍正常。 | 旧告警仍可被新版接收、传播或触发通知，或无效输入造成假通过。 | P1 |
| `ALERT-REMOVE-07` | - [x] 签名合法的告警在过期前由旧发送节点接受，经受控延迟后首次以过期状态到达旧接收对照并触发脚本；对默认配置和完整旧配置的新版发起同类传播尝试。 | 旧对照证明过期消息确实可进入旧处理路径；新版无 Alert 协议、`alerts=[]`、旧告警脚本不执行，普通 RPC 与同步仍正常。 | 只验证正常告警或 RPC 参数拒绝，漏掉网络侧接收过期告警的路径。 | P0 |
| `ALERT-REMOVE-08` | - [x] 同一条签名合法的过期告警在旧接收对照中完成三轮投递，每轮之间清理过期记录，观察到脚本再次触发；对默认配置和完整旧配置的新版进行有界的重复传播尝试。 | 旧对照对同一告警产生三轮新增处理记录；新版始终无 Alert 协议、`alerts=[]`、旧脚本调用次数为零；尝试期间及之后普通 RPC 和区块同步可用。 | 过期去重记录清理后，旧告警仍能重新触发新版处理；或未真正触发重放就宣称修复有效。 | P0 |

## 告警投递与重放的构造说明

### 为什么不能直接调用旧 RPC 发送过期消息

旧 `send_alert` 已经检查过期时间，直接提交过期消息会返回参数错误。这只能证明旧 RPC 做了校验，不能覆盖旧网络接收路径缺少过期拦截的问题。因此需要先在有效期内合法发送，再控制消息到达接收端的时间。

### 黑盒构造

自动化使用透明 TCP 延迟转发：由多个旧发送节点各自与旧接收节点预先建立连接、完成 Alert 协商；先暂停各连接发送到接收方向的转发，再在同一告警尚未过期时，让各发送节点提交一次该告警，暂扣产生的数据。过期后依次放行不同连接，让同一告警多次以过期状态到达。每轮观察到旧接收脚本的新记录后，再通过旧 `get_blockchain_info` 清理记录，随后放行下一条连接。

三轮告警保持相同的 ID、内容、签名和过期时间。每条连接上的正常字节流只按原顺序转发一次，既不解析或改写 CKB 消息，也不复制加密流中的历史字节。延迟有上限，期间必须确认原连接没有超时重建；否则消息未到达不能作为对照或通过证据。

自动化必须用旧接收节点每轮新增的脚本记录验证该构造成立。新版不会协商 Alert，因而验证的是旧发送者发起同类尝试时入口仍关闭，而不是要求新版收到同一份告警。如果透明延迟方案无法建立可靠旧对照，应保留用例未完成，不能改用无效参数请求或无告警输入来代替，也不据此自动扩大为自定义原始 P2P peer。

### 如何观察“不转发”

未过期告警采用“旧发送节点 → 中继节点 → 旧观察节点”的受控拓扑。先以旧节点作中继，确认观察端能收到该告警；再用新版替换中继，使用独立测试消息进行同类尝试。排除发送者到观察端的直接连接、自动发现或其他传播旁路，核对实际 peer 列表；普通区块能经该拓扑同步，但告警不能经新版传递，才支持“不转发”的结论。

### 判定与运行边界

过期消息的处理记录以受控脚本输出为主，不能要求旧节点查询时还显示该过期告警。每次验证都检查消息标记和新增记录，避免复用上一次执行的结果。CPU、内存和日志可作为辅助观察，但本轮不设置未经论证的性能阈值，也不以“未压垮节点”替代功能断言。

新旧二进制、测试签名输入及透明延迟工具需在执行阶段准备。未满足旧对照、连接存活或实际投递条件时，应明确说明前置条件未成立，不记录测试通过。默认配置、合法旧配置、方法移除和响应兼容可先独立验证；跨版本互通仍需旧二进制。

## 覆盖依据

- 新版保留 [RPC Alert 枚举作为忽略项](https://github.com/nervosnetwork/ckb/blob/d40e4722525d45d5ce7a8ae29a1dc6d18d2f747f/util/app-config/src/configs/rpc.rs#L15)、[旧网络 Alert 配置](https://github.com/nervosnetwork/ckb/blob/d40e4722525d45d5ce7a8ae29a1dc6d18d2f747f/util/app-config/src/configs/network.rs#L229)、[签名配置的加载入口](https://github.com/nervosnetwork/ckb/blob/d40e4722525d45d5ce7a8ae29a1dc6d18d2f747f/util/app-config/src/app_config.rs#L87)和[旧通知字段](https://github.com/nervosnetwork/ckb/blob/d40e4722525d45d5ce7a8ae29a1dc6d18d2f747f/util/app-config/src/configs/notify.rs#L10)，支持加载兼容预期。
- 新版[默认配置](https://github.com/nervosnetwork/ckb/blob/d40e4722525d45d5ce7a8ae29a1dc6d18d2f747f/resource/ckb.toml)、[协议列表](https://github.com/nervosnetwork/ckb/blob/d40e4722525d45d5ce7a8ae29a1dc6d18d2f747f/network/src/protocols/support_protocols.rs)、[RPC 服务注册](https://github.com/nervosnetwork/ckb/blob/d40e4722525d45d5ce7a8ae29a1dc6d18d2f747f/rpc/src/service_builder.rs)及[空 alerts 响应](https://github.com/nervosnetwork/ckb/blob/d40e4722525d45d5ce7a8ae29a1dc6d18d2f747f/rpc/src/module/stats.rs#L127)支持入口移除和响应兼容预期。
- 旧 [send_alert 过期检查](https://github.com/nervosnetwork/ckb/blob/17d7db5bb423a1b2177e14a132a41d5a91a515f3/rpc/src/module/alert.rs#L102)、[网络接收处理](https://github.com/nervosnetwork/ckb/blob/17d7db5bb423a1b2177e14a132a41d5a91a515f3/util/network-alert/src/alert_relayer.rs#L145)、[通知及去重清理](https://github.com/nervosnetwork/ckb/blob/17d7db5bb423a1b2177e14a132a41d5a91a515f3/util/network-alert/src/notifier.rs#L92)、[查询时清理](https://github.com/nervosnetwork/ckb/blob/17d7db5bb423a1b2177e14a132a41d5a91a515f3/rpc/src/module/stats.rs#L132)共同限定过期重放的构造和判据。

## 运行说明

对应实现见 [test_cases/feature/test_alert_removal.py](../test_cases/feature/test_alert_removal.py)，复用现有 `CkbTest`、`CkbNode` 及 `test_cases/conftest.py`。八条用例按旧配置类别、新旧 peer、连接方向及默认/旧配置组合展开为 **24 个测试**，不需要 PostgreSQL。

### 环境与二进制

从仓库根目录运行，使用 Python 3.10 至 3.12；现有框架依赖 `telnetlib`，不支持 Python 3.13。测试管理原生 CKB 进程、随机本地端口及独立 devnet，不支持框架的 Docker 运行模式；该模式下用例明确跳过，不算覆盖。运行前必须取消 `DOCKER` 环境变量；`DOCKER=0` 仍会被框架视为启用。

```bash
python3.10 -m venv venv
venv/bin/python -m pip install -r requirements.txt
unset DOCKER
```

- `CKB_TEST_BINARY`：包含 #5304 的待测二进制，默认 `download/current/ckb`。
- `CKB_TEST_OLD_BINARY`：仍支持 Alert 的旧节点对照，默认 `download/0.209.0/ckb`。
- `CKB_TEST_CLI`：本地签名 CLI，默认 `download/current/ckb-cli`。
- `CKB_TEST_BINARY_SHA256`、`CKB_TEST_OLD_BINARY_SHA256`：可选，分别校验对应 CKB 可执行文件的 SHA-256。

需提前准备可执行的新旧 CKB 及 CLI，固定构建提交、来源和摘要。测试打印节点二进制的实际路径、版本及 SHA-256；新旧构建可能显示相同版本号，不能仅凭版本号判断是否含修复。旧对照必须仍支持 Alert RPC、协议及通知行为。

[alert_message.py](../test_cases/feature/alert_message.py)使用旧 CKB 单测中的公开测试私钥，配置对应公钥和单签阈值。它按旧 `RawAlert` Molecule 格式计算 CKB 哈希，再调用本地 `ckb-cli util sign-message --recoverable`；CLI 使用 `--local-only`，临时私钥文件和 CLI 数据目录彼此隔离，签名命令限时 15 秒。该密钥仅用于 devnet 告警输入，不用于资金账户，也不需要额外密码学依赖。

### 执行

将示例路径替换为已确认的构建：

```bash
export CKB_TEST_BINARY=/absolute/path/to/ckb-with-5304
export CKB_TEST_OLD_BINARY=/absolute/path/to/ckb-before-5304
# 可选：export CKB_TEST_BINARY_SHA256=<待测二进制文件的 sha256>
# 可选：export CKB_TEST_OLD_BINARY_SHA256=<旧二进制文件的 sha256>
# 可选：export CKB_TEST_CLI=/absolute/path/to/ckb-cli
venv/bin/python -m pytest -vv test_cases/feature/test_alert_removal.py
# 仅过期首次到达及清理后的三轮重放
venv/bin/python -m pytest -vv test_cases/feature/test_alert_removal.py -k 'expired_alert_first_delivery or same_expired_alert'
```

### 有界投递与清理

- 每个节点均运行所选二进制的真实 `ckb init`。默认配置用例直接启动生成配置；其余功能用例启用测试出块 RPC、隔离网络发现，并按场景加入旧 Alert 字段。“默认”组表示未加入旧 Alert 字段，不表示所有生成配置均未改动。
- 节点通过公开 RPC 等待交易池就绪、导入共同初始区块；后续新区块经实际 P2P 同步。出块前等待模板追上 tip，避免启动时序影响同步判断。
- 完成会话协商后，为有效期 **30 秒**的告警签名，再暂停发送方向并在过期前提交同一告警；签名后不修改过期时间。所有发送端脚本及旧接收方向缓存共用到期前 **1 秒**的准备截止时间。
- [tcp_delay.py](../test_cases/feature/tcp_delay.py)只将各完整会话的原始字节按顺序转发一次，不解析 P2P，不重放加密字节。每次暂停最多 **60 秒**，每轮放行前检查剩余时间；缓存上限 **1 MiB**，原会话断开或重建即失败。普通轮询上限 **45 秒**，无告警观察窗口为 **2 秒**。
- 旧接收端必须为同一告警逐轮新增脚本记录，再调用旧 `get_blockchain_info` 清理过期及去重记录，才放行下一轮。三轮脚本记录是旧重放成立的证据；旧 `alerts=[]` 不能替代该证据，新版也不需要实际收到未协商协议的 payload。
- 每例只清理自己创建的节点、代理和目录。通过时删除目录；失败时将节点日志和告警脚本记录复制到 `report/alert_removal/<本例>/`，原始 `tmp/alert_removal/<本例>/` 中的日志、脚本记录和节点数据也保留，直到 runner 清理。旧对照未成立不能算作新版通过。

保留的新区块脚本和区块/交易订阅尚未添加对应回归，不计入本文件的覆盖结论。
