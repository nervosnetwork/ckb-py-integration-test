# ckb-cli API 服务认证回归用例

## 导读

- **这个改动要做什么**：避免本机浏览器中的网页绕过用户意愿，调用钱包 API 签名并发送转账。
- **行为变化**：旧服务不要求认证；修复后启动必须提供合法 token，普通请求必须携带匹配的 Bearer 认证头，否则返回 HTTP 401。无认证的预检请求仍可通过，但不能执行转账。
- **最怕出什么问题**：看到了 401，却没有发现转账仍被广播；批量请求或其他 HTTP 入口绕过认证；把无效转账误当认证拒绝；带私钥服务被误配置成对外监听；或加了认证以后合法客户端无法运行。
- **能测到什么**：以攻击者是否能接触 API 和能否导致真实资金状态改变为主线，继续采用已确认的 Python 黑盒集成层，用真实 CLI、公开 dev genesis 账户、CKB 节点和 indexer，覆盖不同 HTTP 请求形态、启动暴露面、认证拒绝、资金副作用及后续合法请求。项目没有根 `AGENTS.md` 的用例偏好，本次不创建或修改该文件。
- **测不到的**：HTTP 请求可以构造网页 Origin、`Origin: null` 和匹配的伪造 Host/Origin，但不等同于运行真实浏览器和 DNS 重绑定全链路；网络耗时不能稳定证明 token 比较的时序性质，本轮仅核查源码，不以黑盒时间断言作安全结论。

依据：[PR #690](https://github.com/nervosnetwork/ckb-cli/pull/690) 的描述、完整两文件 diff、固定父提交及修复提交；[API 文档](https://github.com/nervosnetwork/ckb-cli/blob/558a3648e44cc8829a5d1d9fab2135b1be8d2d94/docs/API-Server.md) 和实际 HTTP 依赖。

## 需要你决定

认证场景已获确认。本轮只完成 #690 用例和旧新版验证；先保留正常断言，不加 skip/xfail，也不在未经明确指示时发布 #690 的验收评论。

## 用例

共同前提与口径：

- 测试代码与 #691 同放在分支 `scz/ckb-cli-api-regressions`、套件 `suites/ckb_cli/tests/`，认证用例单独映射到本文件；保留 #691 的既有 Case ID 和已执行记录。仓库基线仍为 `origin/main` 的 `40a62da75597b107a249f80e3c5b445fc6f7b017`。
- 旧 CLI 为 #690 直接父提交 `d1e482b1318e42aadb6a89f7b2e33765e8d42763`；新 CLI 为 `558a3648e44cc8829a5d1d9fab2135b1be8d2d94`。均使用 Rust 1.95.0 和各自未修改的锁文件执行 `cargo build --locked --release`；旧版不使用 #691 的父提交代替。节点继续使用相同的 CKB 0.209.0 和 dev/indexer 配置。
- 每例使用隔离目录、CLI home、随机本地端口及本例 Popen 进程；只监听 127.0.0.1，不连接外部 P2P。私钥取现有公开 `ACCOUNT_PRIVATE_1`，来自 dev genesis 容量；发送地址由该测试私钥对应的 CLI 输出确定，收款地址沿用 #691 的固定测试地址。显式将矿工 lock 设置为不同于双方的测试脚本并检查不同，避免出块奖励改变发送或收款余额。所有资金操作仅发生在本地 dev 链。
- 服务使用固定的公开测试 token `!ckb-cli-test-token-690~`，同时覆盖 ASCII 图形字符边界 `!`、`~`。正确认证头为 `Authorization: Bearer !ckb-cli-test-token-690~`；不使用用户 token，不输出用户私钥。
- HTTP 旧新对照仅允许按 `server --help` 是否支持 `--auth-token` 选择启动形态：新版本传入该选项，旧版本保持原有无认证启动。请求体、认证头变体、预期断言和状态检查保持一致；不按测试失败结果回退，不跳过旧版，不以相同的 `2.0.0` 版本号区分构建。缺参数/非法参数启动场景直接构造相应 argv，不受此启动兼容逻辑影响。
- 转账体使用 `capacity=6100000000`（61 CKB）、`fee_rate=1000`、合法收款地址及 `force_small_change_as_fee="0.001"`。请求必须可在同一服务中仅补正确认证头后成功提交并 committed，且收款增加恰好 61 CKB；不得复用 #691 的非法容量字符串，以免无关 panic 掩盖认证问题。
- 成功余额查询要求 HTTP 200、JSON-RPC 2.0、匹配请求 id、无 error，result 恰含 `total/immature/dao` 三个非负整数字段；新链中固定收款地址的三个字段应全为 0，发送方 genesis 总额必须足以完成测试转账。无私钥查询场景也以这组已知初始收款余额为预期，不只检查响应存在。
- 每个未授权转账变体先建立健康查询和空交易池基线，发送完整合法转账体，再独立检查 HTTP 401、`WWW-Authenticate: Bearer`、响应正文与响应头均不泄露测试 token、节点 pending/proposed 和 verify queue 没有变化。随后在有限期限内出块，等待 indexer 跟上新 tip，再重新查询发送和收款余额，两者均须与基线一致。响应错误、交易池变化或余额变化分别记录，不能在第一个断言失败后放弃资金检查。
- 未授权阶段完成后，同一 API 进程不重启，仅将认证头改为正确值发送相同转账体，检查交易哈希、节点 committed 和收款增加 `6100000000` shannon；发送方必须减少至少该数额，但不硬编码实际手续费。再次检查 API pid 未变且仍存活。首次未授权失败仍使本例失败，后续合法请求不能将其覆盖。
- 拒绝矩阵包括：没有认证头、同长度错误 token、正确 token 多一个后缀、只有 token 而缺少 Bearer 前缀、无认证的 `Origin: https://example.com`、无认证的 `Origin: null`、无认证且 `Host=attacker.example:<port>` 与 `Origin=http://attacker.example:<port>` 匹配。同组还发送含有效 transfer 的 JSON-RPC 批量请求，确认认证发生在整个 HTTP 请求入口前，而不是仅检查单条请求形状；使用独立服务实例以免批量旧版交易污染后续变体。Host 变体仍通过 TCP 连接本地服务，仅证明这种 HTTP 输入形态受保护。未授权响应无需有 CORS 头，不要求网页脚本能读取 401。
- HTTP 方法和解析入口另以 GET 和含无效 JSON 正文的 POST 探测：先带正确 token 验证分别到达 HTTP 方法拒绝（405）和 JSON 解析错误（HTTP 200、JSON-RPC parse error），再只删认证头；新版两者都应先得到 401，避免把仅由下游拒绝造成的结果误判为认证。不带私钥的服务分别在本机和 `0.0.0.0` 监听，后者仍通过 127.0.0.1 连接；两种配置都要求认证查询，正确 token 查询成功，但 transfer 明确因缺少 `privkey-path` 返回 JSON-RPC `-32603` 且无 result、交易池和出块后余额不变。此处不声称已从远端机器发起请求。
- 带私钥服务还须用有效节点、私钥和正确 token 尝试 `--listen 0.0.0.0:<port>`；必须因非本地监听地址失败，不以网络/私钥错误代替。此项检查原有的本机监听安全边界，旧版和修复版都应通过。
- 启动失败应在 5 秒内正常退出并报告对应的 auth-token 参数问题，API 端口不对外服务；缺参数与校验错误分开识别，不将未知选项、节点断连或非法私钥当正确拒绝。非法 token 矩阵为 `""`、包含空格、包含换行、非 ASCII 和 DEL（`0x7f`）；空值可由 clap 的空参数校验直接拒绝，不强求进入自定义 validator 或匹配完整错误文案。NUL 无法作为进程 argv 传入，不伪造 CLI 覆盖。
- 预检使用 `OPTIONS`、Origin、`Access-Control-Request-Method: POST`、`Access-Control-Request-Headers: authorization, content-type`，分别发送浏览器实际使用的空 body 和故意附带的同一有效 transfer body。两者无认证时均须返回 HTTP 200，允许所给 Origin、POST 和上述两个请求头，无 RPC 转账结果，交易池与出块后的双方余额不变；之后用同一 Origin 和正确 token 发出 POST，其响应也必须有匹配该 Origin 的 `Access-Control-Allow-Origin`，并包含匹配请求 id 的 JSON-RPC 2.0 成功交易哈希且无 error，再真实确认交易。Origin 参数分为普通网页来源和 `null`。
- 所有 HTTP 请求显式设置超时 5 秒；进程就绪、出块及 indexer 同步各有总期限和次数上限，沿用 #691 的有限等待实现，不使用无限重试或任意长 sleep。缺少二进制、节点启动失败、健康基线失败均是前置失败，不是复现成功。

<!-- TEST-CASES-BEGIN -->
| 用例 | 场景 | 预期结果 | 防止的问题 | 优先级 |
| --- | --- | --- | --- | --- |
| `CLI-API-690-01` | - [x] 健康节点下，不传 auth-token 启动服务，分别带或不带 privkey-path。 | 5 秒内因缺必需参数退出，端口不提供 API。 | 仅保护带私钥模式，或允许旧部署继续无认证运行。 | P1 |
| `CLI-API-690-02` | - [x] 合法测试 token 可启动后，分别用空、空格、控制字符、非 ASCII token 启动。 | 因 token 校验失败退出，不能误认未知选项或节点故障为通过。 | 无效 token 被接受，或合法 ASCII 图形字符被误拒绝。 | P1 |
| `CLI-API-690-03` | - [x] 已注资钱包发送缺失、错误或变形认证头的合法转账，含网页/null Origin、伪造 Host 和批量请求；再仅补正确认证头。 | 未授权请求为 401、无 token 泄露，交易池和出块后余额不变；同进程认证转账确认并收款增加 61 CKB。 | 401 仍执行转账、批量入口绕过认证，或拒绝后正常钱包不可用。 | P1 |
| `CLI-API-690-04` | - [x] 带私钥及两种监听方式的无私钥服务查询余额；用 GET/无效 JSON 做认证前后对照，并尝试认证后无私钥转账。 | 未认证请求先返回 401；正确认证可查询；缺私钥转账明确因该原因被拒、无资金变化。 | 非转账接口、特殊 HTTP 入口或对外监听的无私钥部署绕过认证。 | P1 |
| `CLI-API-690-05` | - [x] 不带认证头发送空 body 及含合法转账 body 的 CORS 预检，再以同一 Origin 和正确 token 发 POST。 | 预检为 200 且允许所需 Origin/方法/头、无资金副作用；认证 POST 同样允许该 Origin 并真实转账成功。 | OPTIONS 被认证拦截、预检意外转账，或持 token 的网页客户端无法使用。 | P1 |
| `CLI-API-690-06` | - [x] 带有效私钥及 token 的服务尝试监听 `0.0.0.0`，再以本机地址正确启动作前置对照。 | 对外监听尝试明确因地址安全限制退出、端口不服务；本机启动可用。 | 认证改动意外放宽原有的带私钥服务监听边界。 | P1 |
<!-- TEST-CASES-END -->

## 测试树

<!-- TEST-TREE-BEGIN -->
- 启动前建立认证配置
  - 带或不带私钥均必须给 token：CLI-API-690-01 [primary] -> SPEC-01
  - 合法 ASCII 图形 token 可用，非法 token 被校验拒绝：CLI-API-690-02 [primary] -> SPEC-01
- 普通请求的认证边界
  - 无认证、错误认证及变形认证的有效转账被拒绝：CLI-API-690-03 [primary] -> SPEC-02
  - 批量 JSON-RPC 不能绕过整个 HTTP 请求的认证边界：CLI-API-690-03 [ref] -> SPEC-02
  - 网页 Origin、null Origin 与伪造 Host/Origin 不绕过认证：CLI-API-690-03 [ref] -> SPEC-02
  - 拒绝后交易池、发送及收款资金不变：CLI-API-690-03 [ref] -> SPEC-02
  - 原进程补正确认证头可确认同一合法转账：CLI-API-690-03 [ref] -> SPEC-03
  - 带或不带私钥的余额接口都要求认证，对外监听的无私钥服务亦然：CLI-API-690-04 [primary] -> SPEC-02 SPEC-03
  - GET、无效 JSON POST 在有效 token 时到达下游错误，去掉 token 时先受 HTTP 认证拦截：CLI-API-690-04 [ref] -> SPEC-02
  - 无私钥服务即使已认证也因缺少私钥不能转账：CLI-API-690-04 [ref] -> SPEC-05
- 浏览器预检例外
  - 空 body 及含转账 body 的无认证 OPTIONS 允许所需 Origin、POST、authorization/content-type，但不执行转账：CLI-API-690-05 [primary] -> SPEC-04
  - 随后的同 Origin 认证 POST 也允许该 Origin，并正常确认交易：CLI-API-690-05 [ref] -> SPEC-03 SPEC-04
- 原有服务配置的安全边界
  - 即使有正确 token，带私钥服务仍只能监听本机 IPv4：CLI-API-690-06 [primary] -> SPEC-06
<!-- TEST-TREE-END -->

## Spec

### SPEC-01：服务启动必须配置合法 token
- Condition: `ckb-cli server` 使用正常节点配置，分别带或不带私钥。
- Expected: 必须传入非空、不包含空格的 ASCII 图形 token；缺失或非法 token 在参数处理阶段被拒绝，合法 token 可以启动。
- Observable: 进程退出状态、auth-token 对应的参数诊断、端口状态及合法 token 的健康认证查询。
- Basis: [PR 描述](https://github.com/nervosnetwork/ckb-cli/pull/690) 明确必需性、字符范围以及不带私钥部署也受影响；[参数与校验](https://github.com/nervosnetwork/ckb-cli/blob/558a3648e44cc8829a5d1d9fab2135b1be8d2d94/src/subcommands/api_server.rs#L63-L72)。
- Source: explicit
- Testing: required

### SPEC-02：未认证普通请求被拒绝且没有转账副作用
- Condition: 健康的钱包 API 收到普通请求，缺少或未精确匹配 `Authorization: Bearer <token>`；请求可以带任意 Origin 或 Host 输入，正文可以是批量 JSON-RPC 或无效 JSON；无私钥服务可以监听在 `0.0.0.0`。
- Expected: HTTP 401、Bearer 认证挑战，响应不泄露 token，不执行转账；该规则同样覆盖余额接口、未配置私钥的服务及特殊 HTTP 入口。
- Observable: 新 HTTP 响应、节点交易池及验证队列、出块且 indexer 同步后的发送和收款余额。
- Basis: [PR 描述](https://github.com/nervosnetwork/ckb-cli/pull/690) 明确 401、no method is run、Origin null、DNS rebinding 和所有部署；[中间件响应](https://github.com/nervosnetwork/ckb-cli/blob/558a3648e44cc8829a5d1d9fab2135b1be8d2d94/src/subcommands/api_server.rs#L183-L195) 定义 Bearer challenge。
- Source: explicit
- Testing: required

### SPEC-03：正确认证不破坏正常钱包操作
- Condition: 同一 API 服务收到带正确 token 的余额查询或有效转账，包括先前曾拒绝未认证请求的服务。
- Expected: 查询返回真实容量；合法转账可提交并 committed，收款增量与请求的 61 CKB 一致。
- Observable: JSON-RPC 成功响应、真实节点交易确认及 indexer 同步后的余额增量，进程 pid 不变。
- Basis: [PR 描述和合法请求测试](https://github.com/nervosnetwork/ckb-cli/pull/690) 明确正确 token 请求被处理；[转账 API 文档](https://github.com/nervosnetwork/ckb-cli/blob/558a3648e44cc8829a5d1d9fab2135b1be8d2d94/docs/API-Server.md#transfer) 定义真实转账行为和 shannon 单位。
- Source: explicit
- Testing: required

### SPEC-04：预检无需 token，但不能执行 RPC 转账
- Condition: 浏览器形态的 OPTIONS 预检请求，不带认证，要求 POST 与 authorization/content-type，Origin 为网页来源或 null。
- Expected: 空 body 与含转账 body 的预检均成功且允许所需跨域请求，不执行 RPC 方法；合法持 token 客户端可继续 POST，实际 POST 响应同样允许该 Origin。
- Observable: HTTP 200、预检及 POST 的 CORS 允许头、OPTIONS 无 RPC 转账结果、无交易池及出块后资金变化，以及后续实际认证转账成功。
- Basis: [PR 描述](https://github.com/nervosnetwork/ckb-cli/pull/690) 明确 OPTIONS 认证豁免及持 token 浏览器客户端可用；[OPTIONS 豁免](https://github.com/nervosnetwork/ckb-cli/blob/558a3648e44cc8829a5d1d9fab2135b1be8d2d94/src/subcommands/api_server.rs#L185-L188)，锁定依赖 `jsonrpc-http-server 18.0.0` 的 OPTIONS 与 CORS 处理用于核对实际可观察入口。
- Source: explicit
- Testing: required

### SPEC-05：没有私钥的 API 不具备转账能力
- Condition: 服务未配置 `--privkey-path`，本机或 `0.0.0.0` 监听，请求携带正确 token、完整有效转账参数。
- Expected: 转账因缺少私钥被明确拒绝，不产生交易及资金变化。
- Observable: 匹配 id 且无 result 的 JSON-RPC `-32603` 错误，信息指向 `privkey-path`；真实节点交易池与出块后收款余额。
- Basis: [旧版](https://github.com/nervosnetwork/ckb-cli/blob/d1e482b1318e42aadb6a89f7b2e33765e8d42763/src/subcommands/api_server.rs#L218-L230) 与 [新版](https://github.com/nervosnetwork/ckb-cli/blob/558a3648e44cc8829a5d1d9fab2135b1be8d2d94/src/subcommands/api_server.rs#L283-L296) 均明确只在提供私钥时允许 transfer；这是独立于认证的既有资金安全边界。
- Source: explicit
- Testing: required

### SPEC-06：带私钥服务不能对外监听
- Condition: 使用有效私钥、正常节点及新版本的合法 token，指定非本机监听地址。
- Expected: 服务在开放端口前拒绝该配置；正确本机地址可启动。
- Observable: CLI 退出、明确的 listen-ip 安全错误、端口不可连接与同参数本机对照。
- Basis: [旧版](https://github.com/nervosnetwork/ckb-cli/blob/d1e482b1318e42aadb6a89f7b2e33765e8d42763/src/subcommands/api_server.rs#L71-L76) 与 [新版](https://github.com/nervosnetwork/ckb-cli/blob/558a3648e44cc8829a5d1d9fab2135b1be8d2d94/src/subcommands/api_server.rs#L81-L87) 均有此显式限制；它阻止把可签名钱包直接暴露到网络。
- Source: explicit
- Testing: required

## 验证设计

- **独立预期**：启动、认证与预检规则来自 PR 的明确要求；资金预期由测试输入的固定数额独立给出：拒绝和 OPTIONS 阶段双方余额增量均为 0，随后合法转账收款增量为 `6100000000` shannon。余额字段来自新鲜查询，交易池和确认状态直接查询真实节点，不通过被测中间件推导预期。
- **有效基线**：实际使用已注资测试私钥，先证明节点和 API 正常；负请求保留所有合法业务字段，之后同一进程只改认证头的请求必须真正 committed。仅收到 401、或仅看到一个瞬时余额不变，均不足以支持无转账副作用。
- **实际入口与观察**：直接启动真实 `ckb-cli server`，通过新 HTTP 请求发送单条及批量 transfer、查询、GET、无效 JSON 和 OPTIONS，并检查私钥模式的监听地址限制；资金检查观察节点交易池和出块、indexer 同步后的新状态。没有 mock RPC、模拟钱包、产品代码修改或只计数的替代服务；黑盒资金观察证明未广播/确认转账，不声称观察了内部所有函数调用。
- **旧失败／新通过**：认证拒绝与缺 token 启动是旧版复现主线；旧版应出现无认证服务启动或无认证合法转账实际进入交易池/确认，新版应拒绝并保持资金不变。合法请求和 OPTIONS 在两版都可能通过，这是兼容性验证。旧版不能识别新增选项属于能力差异，不能将 unknown argument 当作非法 token 校验或认证修复证据；旧版原生失败保留，不改为 xfail/skip。
- **新增安全边界的判据**：批量请求与单条请求共用“完整合法转账、401、交易池与资金不变”的 oracle；随后仅增加正确认证头，批量中的转账也须真实确认。没有 id 的 `transfer` 不加入资金 oracle：本仓锁定的 `jsonrpc-core 18.0.0` 只执行注册为 notification 的方法，普通 `transfer` 即使在旧版也不会因去掉 id 而执行。`CLI-API-690-06` 检查旧有对外监听限制，旧、新版都应通过，不算 #690 修复证明。
- **与 #691 同一测试 PR**：两项是独立产品 PR，本轮只按 #690 的父提交和 PR head 验收认证场景。#691 的既有用例和验收记录不重跑、不修改，也不把任一单独产品版本当作两项修复的组合结果。
- **复用与自动化风格**：复用 #691 的真实 dev 节点、容量、确认和进程管理；仅提取共用启动/HTTP 支持，测试中的拒绝、状态和资金断言保持直接可读。首个新增用例按已确认的 TEST-MAP、编号中文步骤和对应块注释编写，既有根 `AGENTS.md` 不作变更。
- **执行证据**：构建及运行使用 `ai-test-agent/scripts/run_with_evidence.py`，按版本分 scope，保存原生 JUnit、命令退出状态、构建提交/二进制身份、请求与服务日志。`reports/` 是本地忽略目录，不随代码提交发布；实测结果见下文。
- **检查及 CI 边界**：运行本文件的设计和映射检查；本仓既有 28 项其他 Case 的映射差异单独保留，不改无关文档或工具来声称全绿。现有默认 CI 不运行新增 suite；认证用例也不以永久 skip 掩盖尚未接入 CI 的事实，不在本次设计中扩展全局 CI 改造。

## 尚未由 #690 消除的风险

- `--auth-token` 目前只校验非空和 ASCII 图形字符，单字符 token 也合法，服务鉴权层没有应用级猜测频率限制。[源码](https://github.com/nervosnetwork/ckb-cli/blob/558a3648e44cc8829a5d1d9fab2135b1be8d2d94/src/subcommands/api_server.rs#L152-L204) 与 [文档](https://github.com/nervosnetwork/ckb-cli/blob/558a3648e44cc8829a5d1d9fab2135b1be8d2d94/docs/API-Server.md#authentication) 一起说明：安全性依赖部署者按建议使用长随机 token。现行 PR 明确允许任何非空可打印 ASCII，因此不能把“短 token 必须被拒绝”伪装成已批准的验收断言。
- PR 文档明确提示命令行 token 对本机其他用户可能可见。若目标系统允许其他用户读取该进程参数，读取者就可带 token 调用钱包转账；需要在目标 OS/权限模型下验证，当前只有源码与 PR 声明，尚无跨用户实验。HTTP 401 黑盒用例无法证明 token 保密。
- 虽然 PR 描述使用“恒时比较”，[实际比较函数](https://github.com/nervosnetwork/ckb-cli/blob/558a3648e44cc8829a5d1d9fab2135b1be8d2d94/src/subcommands/api_server.rs#L197-L204) 对长度不等立即返回；只有长度相同的字节内容不按匹配前缀提前退出。本轮不据此声称可从网络计时稳定推算 token，也不声称比较对不同长度仍完全恒时。
- 不带私钥的服务可以监听 `0.0.0.0`，并仍用明文 HTTP 传输 Bearer token。认证可保护其查询接口，但若部署在可被旁路观察的网络上，token 仍可能暴露；本用例只验证本机建立的该类监听配置与 HTTP 入口，不证明远端链路保密。[监听规则](https://github.com/nervosnetwork/ckb-cli/blob/558a3648e44cc8829a5d1d9fab2135b1be8d2d94/src/subcommands/api_server.rs#L81-L87)、[HTTP 启动](https://github.com/nervosnetwork/ckb-cli/blob/558a3648e44cc8829a5d1d9fab2135b1be8d2d94/src/subcommands/api_server.rs#L132-L143)。
- #690 单独修复版仍包含 #691 所修的非法费用容量 panic；持正确 token 的调用方若提交非法容量，仍可能使同一 API 的后续请求失败。此项属于另一产品 PR，本轮不重跑 #691，也不把单独 #690 head 的该失败误判为认证回归。
- 另一个既存、与 #690 分开的产品风险候选：插件管理器会扫描 `plugins/inactive` 并执行插件的 `get_config`，发生在 `server` 启动前；使用第三方插件密钥库且开启调试日志时，插件请求/响应可能包含密码或导出的密钥。需有已安装的不可信插件或可读日志等额外前提，宜单列后续安全复核，不能把它们记为 #690 验收失败。[插件加载](https://github.com/nervosnetwork/ckb-cli/blob/558a3648e44cc8829a5d1d9fab2135b1be8d2d94/src/plugin/manager.rs#L61-L97)、[插件执行](https://github.com/nervosnetwork/ckb-cli/blob/558a3648e44cc8829a5d1d9fab2135b1be8d2d94/src/plugin/manager.rs#L1085-L1117)。

上述安全问题来自本地源码审查与独立复核，尚未通过动态测试验证，不作为已证实的漏洞结论。

## 独立设计复核

独立 B 会话 `/root/cli690_auth_design_review` 以无父会话历史的方式读取原始 PR、完整 diff、固定版本源码及 HTTP 依赖，随后完成整个文档及有界修订复核，结论为无剩余阻塞项、可提交 G1 审阅。本地原生 subagent 的来源边界不等于宿主级隔离证明。

扩展安全面后，新的独立 B 会话 `/root/cli690_broadened_design_b` 同样从原始 PR 和源码审查六项设计，指出 GET/无效 JSON 需要正确 token 的下游错误对照、无私钥转账须核对专属错误，并要求将既有监听边界与 #690 修复证据分开。上述项均已纳入用例与 Spec；有界复核结论为无剩余设计阻塞。另一只读威胁建模会话指出弱 token、命令行参数及插件边界等残余问题，均只按实际前提记录，不将静态候选冒称已动态验证。

- 必要修订：浏览器可用性不能只靠预检 200 与 requests POST 成功证明；已补普通空 body 预检、带转账 body 的对抗预检，以及实际认证 POST 响应的匹配 CORS 允许头。
- 资金观察：已明确矿工 lock 与发送/收款隔离，检查交易池、出块及 indexer 同步后双方余额，再用同一请求仅改正确认证头证明有效转账。
- 实现注意：空 token 可由 clap 拒绝，不限定自定义全文；成功查询校验完整字段和已知初始收款余额，已经纳入共同约定。

两版构建均已完成：[旧版构建记录](../reports/ckb-cli-690-build-base/execution.json)、[新版构建记录](../reports/ckb-cli-690-build-head/execution.json) 的原生及 wrapper 退出码均为 0、`inputs_unchanged=true`。源码和锁文件未修改，各自二进制目录保存实际版本及 SHA-256。构建、设计复核与用例运行是不同结论。

## 本地验收结果

- #690 父提交 `d1e482b`：21 个参数化实例中 18 失败、3 通过。缺 token 启动与未认证请求仍被接受；未认证的合法转账返回 200，交易池出现 pending 交易，出块后收款地址增加 61 CKB。预检和带私钥监听限制这 3 个既有兼容性实例通过。原生 JUnit 与命令记录在本地 `reports/ckb-cli-690-base/`。
- #690 PR head `558a3648`：21 个实例全部通过，无失败、错误或跳过。未认证的单条和批量转账均返回 401、交易池与出块后双方余额不变；同一服务补正确认证头后，合法交易确认且收款增加 61 CKB。其他启动、读接口、预检、无私钥及监听边界断言也通过。原生 JUnit 与命令记录在本地 `reports/ckb-cli-690-head/`。
- 两次运行 `inputs_unchanged=true`；独立代码复核未发现阻塞项。`check_test_design.py` 对本 review 无错误或待决项，6 个 Case 均有唯一 `TEST-MAP`；`check_test_map.py` 整仓退出 1 的原因是另外 28 项既有 review 标记不一致，本 review 的映射为 6/6。#691 的测试与验收记录本轮未改动或重跑。
