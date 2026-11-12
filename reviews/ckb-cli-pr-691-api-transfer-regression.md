# ckb-cli 转账容量参数回归用例

## 导读

- **这个改动要做什么**：钱包 HTTP 服务收到无法解析的最高手续费容量字符串时，向调用方返回参数错误，并继续处理后续请求。
- **行为变化**：修复前，非法字符串可能使处理请求的线程 panic，并导致后续钱包请求失败；修复后，非法请求返回 JSON-RPC 参数错误，服务无需重启即可继续使用。
- **最怕出什么问题**：只验证命令行的前置校验、把连接失败当成正确拒绝，或在重启服务后才检查可用性，导致旧版也被误判为通过。
- **能测到什么**：通过真实 `ckb-cli server` 的 HTTP 接口验证错误响应、后续查询和重复非法请求，并在原第二条场景末尾追加同一服务的合法转账，检查交易确认和收款余额增加；另外复用现有 100 CKB 命令行转账用例，检查省略参数及合法 `--max-tx-fee 0.001`。项目没有根 `AGENTS.md` 的用例偏好，本次沿用已确认的 Python 黑盒集成层。
- **本轮边界**：覆盖报告中的非法字符串、同进程后续 API 查询与合法转账、普通 CLI 转账和合法小数参数；完整容量解析边界、最高手续费阈值、并发压力和其他钱包接口不计入本轮覆盖。

依据：[PR #691](https://github.com/nervosnetwork/ckb-cli/pull/691) 的问题描述、完整单文件 diff 和下述固定版本源码；两条设计及首条自动化写法已获确认，运行证据单独记录。

## 需要你决定

无。本次保留已确认的 Python 黑盒层，根 `AGENTS.md` 不作修改。

## 用例

共同前提与口径：

- 测试仓库分支 `scz/ckb-cli-691-api-server-regression` 从 `origin/main` 的 `40a62da75597b107a249f80e3c5b445fc6f7b017` 创建；按规范新增 `suites/ckb_cli/tests/test_api_server_transfer.py`，继续复用现有 framework 和原生 pytest。自动化阶段给 Makefile 增加该套件入口，不搬迁既有测试，不新建另一套测试框架；新旧对照直接调用 pytest，不经带全局清理的 `test.sh`。
- 被测旧 CLI 固定为 PR 的父提交 `ccca1c9c37e58919619e9ea0e421e66228af6f18`，新 CLI 固定为 `023b3aa89081e47fc9a1d48c985398e2d3315cea`。两版用同一工具链、各自的锁文件和相同构建参数构建；不使用来源不明的 bundled CLI 替代这组对照。
- HTTP 两例使用独立 CLI 服务进程、CLI home、本地端口和公开的 dev genesis 账户 `ACCOUNT_PRIVATE_1`；账户容量来自现有 dev spec，不额外转账注资。负请求前不出块、不发交易；同一用例内不重启服务。第二条仅将同一请求的容量参数改为合法 `0.001` 后发送 61 CKB，使用真实节点的 IntegrationTest RPC 出块，检查 committed 及 indexer 同步后的余额变化。
- CLI 正例复用 `TestCkbCli.test_06_account1_transfer_account2` 的 100 CKB 转账流程和原有断言；原用例默认行为保留，helper 只增加缺省为 `None` 的可选参数。将省略参数与 `--max-tx-fee 0.001` 分为两个参数变体，均等待交易 committed，再检查收款余额增加 100 CKB；旧版和新版均预期通过。新场景放入 `suites/ckb_cli/tests/test_wallet_transfer.py`，通过可识别的 `TEST-MAP` 关联本文件，不复制原转账流程。运行时在独立 checkout 的 `source/ckb-cli` 和 `download/current/ckb-cli` 放置同一已确认构建，避免原 helper 固定路径误用旧二进制。
- 启动参数含节点 URL、`server --listen 127.0.0.1:<port> --privkey-path <test-key>`（实际子命令在 `src/main.rs` 注册为 `server`）；就绪检查必须成功调用真实 `get_capacity_by_address`。服务就绪总等待上限 30 秒，单次 HTTP 请求超时 5 秒；启动失败、缺少二进制或节点不可用均是前置失败，不能作为旧版复现证据或新版通过。
- JSON-RPC 直接使用 `requests.post(json=..., timeout=...)`；不用会丢失错误码的现有 RPC wrapper，也不调用 `wallet transfer --max-tx-fee`。`transfer` 的 `params` 是只含一个对象的数组，`capacity=6100000000`（61 CKB，字段单位 shannon）、`fee_rate=1000`、有效 dev/testnet 收款地址，只有 `force_small_change_as_fee="NOT_A_CAPACITY"` 非法。可选字段省略；不得以错误 JSON 类型替代该非法字符串。
- 参数错误必须返回 HTTP 200、可解析的 JSON-RPC `2.0` 响应、匹配的请求 id、`error.code=-32602` 且无 `result`。不把 HTTP 连接失败、超时或任意异常当成预期错误；不固定完整错误文案。
- 余额查询使用新的 HTTP 请求和不同 id，必须包含 `result.total/immature/dao` 三个非负整数且没有 `error`；非法请求后、合法转账前与基线一致。合法 API 转账确认后，收款 `total` 必须在基线上增加 `6100000000` shannon（61 CKB），`immature`、`dao` 不变；API 进程 pid 保持一致且仍存活。确认最多出块 20 次，并在 30 秒期限内等待 committed 和 indexer 追上确认块；仅调用现有节点 RPC，不实现新的矿工。
- 服务 stdout/stderr 写入本例日志；清理只终止本例启动的进程，`terminate` 后限时 `wait`，必要时 `kill` 并再次等待，避免全局 `pkill`。新旧执行分别保留原生 JUnit、命令退出码、HTTP 故障和服务日志。

<!-- TEST-CASES-BEGIN -->
| 用例 | 场景 | 预期结果 | 防止的问题 | 优先级 |
| --- | --- | --- | --- | --- |
| `CLI-API-691-01` | - [x] 已通过真实余额查询确认服务就绪，仅将转账的最高手续费容量设为非法字符串后发送请求。 | 有限超时内收到完整 JSON-RPC 参数错误，错误码为 `-32602`、id 匹配且无 `result`。 | worker panic、断连或被其他参数提前拒绝，却被误认为正确处理了非法容量。 | P1 |
| `CLI-API-691-02` | - [x] 非法请求后不重启服务，查询余额并重复非法请求，再将参数改为合法 `0.001` 提交 61 CKB 转账。 | 非法请求返回 `-32602`、初次余额不变；同一 API 的合法转账 committed，收款增加 61 CKB，pid 不变且存活。 | 仅证明还能查询或报错，却漏掉后续合法转账仍不可用；或用重启掩盖锁中毒。 | P1 |
| `CLI-TRANSFER-691-01` | - [x] 复用已注资账户间的 100 CKB 转账流程，分别省略参数及指定 `--max-tx-fee 0.001`。 | 两个变体在旧版和新版均成功确认交易，收款余额增加恰好 100 CKB。 | 负例通过却使正常转账不可用，或将合法小数容量误当整数解析而拒绝。 | P1 |
<!-- TEST-CASES-END -->

## 测试树

<!-- TEST-TREE-BEGIN -->
- HTTP 钱包转账的非法容量处理
  - 前置条件有效、输入确实进入容量解析
    - 非法字符串的本次响应
      - 完整参数错误，而非 panic 或连接失败：CLI-API-691-01 [primary] -> SPEC-01
    - 非法请求之后的服务状态（不重启服务）
      - 新余额查询成功，结果与健康基线一致：CLI-API-691-02 [primary] -> SPEC-02
      - 新转账请求仍返回完整参数错误：CLI-API-691-02 [ref] -> SPEC-01 SPEC-02
      - 合法转账在原 API 进程提交并确认，收款余额增加 61 CKB：CLI-API-691-02 [ref] -> SPEC-02
- 命令行转账的容量参数兼容性
  - 已注资测试账户转账 100 CKB
    - 省略参数及合法小数参数均确认交易、收款增量正确：CLI-TRANSFER-691-01 [primary] -> SPEC-03
<!-- TEST-TREE-END -->

## Spec

### SPEC-01：非法容量字符串返回参数错误
- Condition: 带有效测试私钥的 HTTP 钱包服务及节点正常，转账对象的其他字段有效，`force_small_change_as_fee` 为无法解析的字符串。
- Expected: 请求返回 JSON-RPC 参数错误（`-32602`），而不是使请求处理线程 panic；响应标识对应原请求。
- Observable: 新的 HTTP 响应中 `jsonrpc`、`id`、`error.code` 和 `result` 的有无。
- Basis: [PR 的 Summary/Change](https://github.com/nervosnetwork/ckb-cli/pull/691) 明确要求 invalid_params；[修复点](https://github.com/nervosnetwork/ckb-cli/blob/023b3aa89081e47fc9a1d48c985398e2d3315cea/src/subcommands/wallet.rs#L184-L186) 和 [API 错误映射](https://github.com/nervosnetwork/ckb-cli/blob/023b3aa89081e47fc9a1d48c985398e2d3315cea/src/subcommands/api_server.rs#L219-L230) 限定实际入口。
- Source: explicit
- Testing: required

### SPEC-02：非法请求不破坏后续钱包请求
- Condition: 已收到非法字符串转账请求，服务和节点未重启，发送账户有现有 dev genesis 容量。
- Expected: 后续余额查询正常、重复非法请求仍返回参数错误；随后同一 API 的合法 61 CKB 转账可确认，收款余额增加 61 CKB。
- Observable: 原进程的新查询和转账响应、合法交易哈希、真实节点的 committed 状态和 indexer 同步后的新收款余额。
- Basis: [PR 问题描述](https://github.com/nervosnetwork/ckb-cli/pull/691) 明确说明旧版锁中毒会影响后续 transfer/get_capacity_by_address，修复后 server keeps working；用户明确要求改造原第二条场景，追加同进程合法转账、交易确认和余额变化验证；[共享锁路径](https://github.com/nervosnetwork/ckb-cli/blob/023b3aa89081e47fc9a1d48c985398e2d3315cea/src/subcommands/api_server.rs#L193-L251) 与 [余额响应结构](https://github.com/nervosnetwork/ckb-cli/blob/023b3aa89081e47fc9a1d48c985398e2d3315cea/src/subcommands/api_server.rs#L410-L415) 用于核对观察位置。
- Source: explicit
- Testing: required

### SPEC-03：省略或指定合法容量参数均可正常转账
- Condition: dev 节点和 indexer 正常，使用已有测试账户间 100 CKB 转账流程，最高手续费参数分别省略或为 `0.001`。
- Expected: 旧版和新版均可提交并确认交易，收款余额增加 100 CKB。
- Observable: 原用例的交易 committed 等待与收款前后余额断言。
- Basis: 用户明确要求复用现有用例并增加 `--max-tx-fee 0.001` 变体；`test_cases/framework/test_01_helper_ckb_cli.py:64-91` 提供原有确认和余额 oracle，产品 `src/utils/arg.rs:140-147` 明确参数单位为 CKB、示例为 `0.001`。
- Source: explicit
- Testing: required

## 验证设计

- **独立预期**：即时拒绝和服务继续可用来自 PR 的明确目标；合法 API 后续转账来自用户确认。HTTP 正例按请求中的固定数额独立检查收款增量 `6100000000` shannon，并直接查询真实节点的 committed 状态；预期不由被测费用解析器计算。CLI 正例沿用原用例的 committed 确认和“收款增量 = 请求的 100 CKB”，不复制或弱化原断言。
- **实际入口与新观察**：从待测二进制启动真实 HTTP 服务，连接真实本地 dev 节点与 indexer。先成功查询建立健康基线，再发送非法字符串；后续通过全新 HTTP 请求观察，避免复用缓存响应。第二条用例即使首次响应异常，也应尝试后续查询并保留诊断；首次异常本身仍算失败，不被吞掉。
- **验证层与边界**：HTTP 两例证明错误处理、同进程后续请求及合法 API 转账确认和余额变化；CLI 参数变体证明普通命令行转账兼容性。均使用真实 dev 节点，不使用 RPC mock、产品测试钩子或产品代码补丁，不扩大为每种容量边界、费用阈值或所有并发情况。
- **旧失败／新通过**：两版运行完全相同的测试文件、用例选择器、节点版本、请求参数和超时。先跑旧版再跑新版，每条用例都重建服务；新版两条均通过且旧版在建立健康基线后出现与非法容量 panic 对应的失败，才支持修复有效。旧版构建失败、节点不可用、断言无关字段失败都不算成功复现。旧版原生测试保留失败退出码，不改成 xfail、不倒置断言；结合服务 panic/poison 日志核对失效来源。
- **执行证据**：使用 `ai-test-agent/scripts/run_with_evidence.py`；最终四项原生测试组合执行，旧、新 scope 为 `ckb-cli-691-base`、`ckb-cli-691-head`，分别保留 compact receipt、JUnit 和日志；此前单独的 CLI 正例记录保存在 `ckb-cli-691-valid-fee-base`、`ckb-cli-691-valid-fee-head`。声明测试文件、节点/配置/helper/依赖等实际输入，构建记录绑定对应产品提交和锁文件；记录实际二进制路径、版本和 SHA-256，避免仅凭相同版本号认定构建身份。`reports/` 为本地忽略目录，运行证据不随本次代码提交发布；文中的相对报告链接用于本地复核。使用规范提供的证据工具，不自写哈希或汇总报告脚本。
- **检查器的现有基线**：`check_test_design.py --review reviews/ckb-cli-pr-691-api-transfer-regression.md` 检查本次 Spec/树/用例关系；`check_test_map.py` 即使指定该 review，仍对全仓历史文档计算勾选差异，且不扫描原生 `test_cases` 目录。当前因此有 28 个其他既有 Case 的 mismatch、命令退出码为 1；本次关联统计另行报告，不是运行结果。新增测试放入可扫描的 `suites/ckb_cli/tests/` 后仍保留这项全仓基线失败，不能声称检查全绿，也不修改其他文档、规范工具或建立假映射绕过。工具的目录兼容维护不属于本轮范围。
- **独立设计审查**：由无父会话历史的 B 审查者读取原始 PR、完整 diff、固定版本源码、项目约定及本设计；[审查意见与处理结果](../reports/ckb-cli-pr-691/design-review.md) 保存在同范围报告中。设计结构检查与 B 审查均不能替代运行结果。

## 自动化与执行验证

HTTP 两例见 [test_api_server_transfer.py](../suites/ckb_cli/tests/test_api_server_transfer.py)，CLI 正例的两个参数变体见 [test_wallet_transfer.py](../suites/ckb_cli/tests/test_wallet_transfer.py)；均有函数前 `TEST-MAP`、编号中文步骤和对应代码块注释。HTTP fixture 显式定位本 worktree 的真实 dev 模板；CLI fixture 复用框架节点准备和原转账方法，使用随机端口，并复用相同的 `Popen` 进程管理，保留节点日志后清理自己的目录，不调用按端口杀进程的 `node.stop()`。HTTP 第二条保留原有三个请求及失败汇总，末尾追加同进程合法转账、确认和余额验证，首次断连仍会继续尝试合法 transfer。Makefile 的独立入口 `make test-ckb-cli-api` 会运行整个 `suites/ckb_cli/tests`。

CLI 正例沿用框架根目录定位规则，要求 checkout 目录名为 `ckb-py-integration-test`；不满足时明确失败，避免误用其他工作区。当前 worktree 的复测通过独立 checkout 执行，运行代码与工作树中的文件逐一比对相同。普通本地调试命令（先准备 Python 环境，将路径替换为已确认的二进制）：

```sh
mkdir -p source download/current
cp /absolute/path/to/ckb download/current/ckb
cp /absolute/path/to/ckb-cli source/ckb-cli
cp /absolute/path/to/ckb-cli download/current/ckb-cli
CKB_CLI_BIN="$PWD/source/ckb-cli" CKB_TEST_BINARY="$PWD/download/current/ckb" \
  venv/bin/python -m pytest -vv -s -o addopts= \
  suites/ckb_cli/tests
```

当前聚焦对照使用 Python 3.10.16、requirements 中固定的 pytest 7.3.2，以及同一个 CKB 0.209.0（`d166e28`）和真实 dev/indexer 配置。新旧 CLI 均通过 Rust 1.95.0、`cargo build --locked --release` 构建，[旧构建记录](../reports/ckb-cli-691-build-base/execution.json)、[新构建记录](../reports/ckb-cli-691-build-head/execution.json) 和各自相邻 `binary-identity.txt` 保存源提交、构建命令与实际二进制版本/摘要。

本轮将两个 HTTP 用例及两个 CLI 参数变体组合执行：[旧版执行记录](../reports/ckb-cli-691-base/execution.json) 为 2 failed、2 passed、0 errors、0 skipped、退出码 1。旧版两个 HTTP 用例均复现容量解析 panic；第二条在原进程继续发送查询、重复非法请求及合法转账，[服务日志](../reports/ckb-cli-691-base/runtime/test_wallet_requests_survive_i0/api-server.log) 均显示锁中毒。合法请求没有返回交易哈希，确认和余额变化检查未执行，这作为失败诊断保留，不视为跳过或通过。

[新版执行记录](../reports/ckb-cli-691-head/execution.json) 为 4 passed、0 errors、0 skipped、退出码 0。第二条先收到 id 2、4 的 `-32602` 响应和 id 3 的正常余额，再由同一 API pid 28615 的 id 5 请求成功返回交易哈希；真实节点确认该交易 committed、indexer 到达确认块后，id 6 查询显示收款余额从 0 增加到 `6100000000` shannon（61 CKB），其他容量字段不变。服务全程未重启。

CLI 默认参数及 `--max-tx-fee 0.001` 两个变体在本轮旧、新两版均通过：交易确认，收款余额从 `5198735037` 增加到 `5198735137` CKB。两次 receipt 均为 `inputs_unchanged=true`，非 CLI 输入摘要一致。原始执行在独立 checkout 中进行，报告目录保留 receipt、JUnit 和服务日志副本，实际 cwd、命令和原始路径保持不变。

`black --check`、`git diff --check` 和 `check_test_design.py` 均退出 0；`check_test_map.py` 本次关联为 3/3、无本次 mismatch，仍因已说明的 28 项全仓既有差异退出 1。勾选仅表示映射；运行结论来自本轮原生 JUnit 与真实交易、余额及服务日志。不扩大为手续费阈值、完整容量边界或并发覆盖。

## 合入与 CI 策略

本次保留正常断言，不增加 `skip` 或 `xfail`。默认 PR CI 通过 `make prepare` 和 `make test` 运行既有 `test_cases/`，不会执行新增的 `suites/ckb_cli/tests`；新增用例目前需要显式调用 `make test-ckb-cli-api`。因此常规 CI 通过不代表本组用例已经自动执行。

不必将正式发版作为测试代码合入的前提。后续接入自动回归时，应由独立的源码构建任务构建包含修复的 CLI 维护分支或指定 PR ref，记录实际提交，将同一二进制安装到 `source/ckb-cli` 和 `download/current/ckb-cli`，再显式执行上述入口。固定修复提交可用于首次验收，但持续回归应跟随包含修复的维护分支。

现有 `develop-ci.yml` 和 `develop_prepare.sh` 已有按分支构建 CLI 的选项，但默认关闭，并且定时事件的空 `TestCases` 存在不执行测试的路径；本次不修改该全局 workflow，也不声称它已提供持续覆盖。正式 CLI 发版也不会自动改变 `prepare.sh` 固定使用的 CKB 0.205.0 包内 CLI，后续接入必须同时明确二进制来源与测试入口。
