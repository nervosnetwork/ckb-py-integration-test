# rich-indexer 回滚与恢复用例评审

评审范围：CKB #5300 的空索引交易列表回滚，以及普通回滚、后续索引和重启恢复的黑盒回归。

源码版本：`develop@d40e4722525d45d5ce7a8ae29a1dc6d18d2f747f`。测试框架基线：`V210@112f47ba040467766cad16e4155f8254fdb09634`。执行前需固定实际待测二进制；本文件定义预期行为，不代表已执行。

## 修改背景

rich-indexer 是节点账本旁边的查询目录。它可以不收录某些区块的交易，但仍记住处理到了这些区块。主链切换时，它要撤销旧分叉的索引，再收录新分叉。

原问题是：回滚被过滤的区块时，虽然对应的索引交易列表为空，程序仍生成“恢复这些交易花费标记”的数据库命令，PostgreSQL 会拒绝其中的空列表。#5300 在没有索引交易时跳过这项恢复操作，继续完成其他回滚工作。测试要证明这条异常路径能完成，也要证明有交易的正常回滚仍恢复正确状态。

## 接口说明

- 接口作用：通过配置和节点分叉触发索引回滚，从公开查询判断索引是否正确切换到新主链。
- 输入：启用 rich-indexer 的 CKB 二进制、数据库配置、区块过滤规则，以及隔离 devnet 中的交易、分叉与主链切换。
- 观察入口：`get_indexer_tip`、节点区块查询、`get_cells`、`get_transactions` 和 `get_cells_capacity`。
- 成功结果：索引 tip 与选定主链的区块 hash 和高度一致；按过滤规则应保留、恢复或移除的 cell、交易和容量查询正确；后续索引及重启恢复正常。
- 失败表现：节点已切到新主链而索引停在旧分叉，或索引返回残留、缺失或错误的状态；仅节点进程存活不能判定通过。
- 范围边界：主链选择本身、共识安全、数据库中断恢复和性能压测不属于本文件；数据库内部表和 SQL 调用次数不作为断言。#5304 的用例另行评审。

## 共同前提与判定口径

- **数据库矩阵：** 下表每个用例在 PostgreSQL 和 SQLite 上采用相同的预期。PostgreSQL 是本次缺陷的必测后端，SQLite 是回归对照；后端不同不增加重复用例 ID，SQLite 通过不能替代 PostgreSQL 结果。
- **目标状态必须已形成：** 主链切换前，节点和 indexer 都已到达旧分叉目标区块 hash。对被过滤区块，同时通过节点区块查询确认测试交易确实在块内、通过索引查询确认未被收录。不能把尚未同步或尚未索引当成过滤成功。
- **真实发生主链切换：** 两条分叉具有不同区块 hash；新分叉具有明确更高的累计工作量。先确认节点实际采用新分叉，再在有界等待内核对 indexer 的 hash 和高度。等待超时用于检测停滞，不作为产品性能指标。
- **过滤在两条分叉上都生效：** 按高度过滤时，新分叉的相同高度也会被排除。用于证明后续索引恢复的交易必须位于允许收录的区块；被过滤区块中的测试交易不消费本轮用于断言保留/恢复的标记 cell，避免把过滤策略造成的查询差异当成回滚错误。
- **查询对象明确：** 每个用例在共同祖先中保留一个两条分叉都不花费的标记 cell S，防止错误清空索引也被判成成功。cell 按交易 hash 和输出序号精确识别；固定测试脚本的 cell/交易查询遍历完整结果，容量与预期保留的 cell 集合一致。“旧交易消失”指从 rich-indexer 的 `get_transactions` 中移除，不要求节点抹除旧分叉的原始交易记录。
- **隔离交易池影响：** 关闭 indexer 的交易池叠加，并控制新主链打包内容，防止旧分叉交易回池后再次上链。可让旧交易同时消费待恢复 cell X 和辅助 cell G，新主链只消费 G，使旧交易失效而 X 能恢复；具体输入安排留在自动化阶段。仅关闭交易池叠加不会阻止旧交易重新上链。
- **正常恢复含义：** 下表使用 X 表示应恢复的旧输入、Y 表示旧分叉创建的输出、Z 表示新主链上的新输出；各用例独立建立这些状态，不依赖前一用例留下的数据。

## 待评审用例

| 用例 | 场景 | 预期结果 | 防止的问题 | 优先级 |
| --- | --- | --- | --- | --- |
| `RICH-ROLLBACK-01` | - [x] 旧分叉末尾只有一个区块需要回滚，该区块有实际交易但整块交易被过滤，索引已到达该块；切换到保留共同祖先 cell S 的新主链。 | 索引追到新主链的区块 hash 和高度；S 及其容量保持正确；新主链允许收录的交易可以查询，被过滤的交易仍不出现在索引中。 | 空索引交易列表使 PostgreSQL 报错，索引停在旧分叉。 | P0 |
| `RICH-ROLLBACK-02` | - [x] 旧分叉末尾连续三个需要回滚的区块均有实际交易但被过滤，索引已到达最后一块；切换到保留共同祖先 cell S、并延伸到过滤范围之外的新主链。 | 连续回滚完成，索引追到新主链；S 及其容量保持正确，过滤范围之外的新交易正常收录。 | 只跳过第一个空列表后仍在后续过滤区块停滞，或连续回滚误清理已有索引。 | P1 |
| `RICH-ROLLBACK-03` | - [x] 不过滤区块；旧分叉中已收录的交易消费共同祖先 cell X 并生成 Y，另一 cell S 始终未花费；切换到未消费 X、也未收录该旧交易的新主链。 | 切换前 X 不可用、Y 可查；切换后索引追到新主链，X 恢复可查，Y 和旧交易的索引记录消失，S 保留，容量与恢复后的 cell 集合一致。 | 空列表修复误跳过正常恢复，造成已花费标记或旧输出、旧交易残留。 | P0 |
| `RICH-ROLLBACK-04` | - [x] 一次主链切换撤销同时含过滤区块和已索引交易区块的旧分叉；已索引交易消费 X 并生成 Y，新主链不消费 X、不收录该旧交易且保留 S；分别覆盖回滚时先遇过滤区块、先遇已索引交易区块两种顺序。 | 两种顺序都完成回滚并追到新主链；X 恢复，Y 和旧交易的索引记录消失，S 保留，cell 查询及容量与新主链的收录规则一致。 | 空回滚与普通回滚交替时发生停滞、漏恢复或多删除。 | P1 |
| `RICH-ROLLBACK-05` | - [x] 完成包含过滤区块的混合回滚，已观察到 X 恢复、旧输出 Y 消失且 S 保留；在新主链允许收录的区块中，将 X 花费并生成 Z，然后继续出块。 | 索引持续跟随新主链；新交易可查，X 再次变为不可用，Z 可查，S 保留，容量与新 cell 集合一致；旧输出 Y 和旧交易不重新出现。 | 回滚后索引表面恢复，但无法继续处理交易，或旧分叉状态再次污染查询。 | P1 |
| `RICH-ROLLBACK-06` | - [x] 完成包含过滤区块的混合回滚，索引已追到新主链且 X 恢复、Y 和旧交易消失、S 保留；保留节点数据、索引数据库和相同配置，正常停止并重启，再增加允许收录的新区块和交易。 | 重启就绪后原有 tip hash、高度及 cell、交易、容量查询结果一致；随后能追到新增区块并收录新交易，旧分叉记录不重新出现。 | 回滚结果未正确持久化，或重启后从错误位置继续索引。 | P1 |

## 构造与观察补充

单区块过滤场景可采用共同祖先第 101 块、旧分叉第 102 块 A、新分叉第 102 块 B 和第 103 块 B。过滤规则仅排除高度 102：先确认索引已到达 102A，再触发切换，最终检查索引到达 103B，且只收录规则允许的交易。区块编号只是示意，实际高度需满足交易成熟和提交条件。

连续过滤场景将这个窗口扩展为连续三个高度。混合场景中的顺序指实际撤销的顺序：旧分叉按高度“已索引 → 被过滤”对应回滚时“先过滤 → 后已索引”；交换排列后覆盖反方向。需先观察 X 恢复，再观察再次消费后的 Z，不能只检查最后的状态。

重启场景在固定链头、暂停继续出块时先比较重启前后已有状态，然后再产生新区块，避免持续增长的链掩盖持久化问题。全程保留原数据库；清空后重新索引不算验证恢复成功。

具备可运行的修复前二进制时，用相同 PostgreSQL 输入对照 `RICH-ROLLBACK-01` 的旧版本回滚失败与修复后完成；旧版本的失败应关联到空列表数据库错误，不能拿无关启动失败充当复现。该对照增强缺陷归因，不另设一个重复用例 ID，也不能替代对修复后数据正确性的断言。

## 覆盖依据与执行边界

- [#5300 的空列表判断](https://github.com/nervosnetwork/ckb/blob/d40e4722525d45d5ce7a8ae29a1dc6d18d2f747f/util/rich-indexer/src/indexer/remove.rs#L86)直接对应单区块、连续区块和混合回滚；[同文件的回滚流程](https://github.com/nervosnetwork/ckb/blob/d40e4722525d45d5ce7a8ae29a1dc6d18d2f747f/util/rich-indexer/src/indexer/remove.rs#L7)支持 cell 恢复及旧交易/输出清理预期。
- 既有[过滤区块回滚单测](https://github.com/nervosnetwork/ckb/blob/d40e4722525d45d5ce7a8ae29a1dc6d18d2f747f/util/rich-indexer/src/tests/rollback.rs#L146)采用 SQLite；本文件补充的是外部节点行为及 PostgreSQL 验证。既有 Python [分叉回滚测试](../test_cases/rpc/test_rich_indexer_state_regressions.py)提供节点分叉和索引查询基础；本文件六条用例映射到 [独立黑盒测试](../test_cases/rpc/test_rich_indexer_rollback.py)，共同使用 PostgreSQL/SQLite 两个后端。
- 执行所需的 PostgreSQL 服务、隔离数据库、二进制选择及命令见下方运行说明。未配置 PostgreSQL 或启用不支持的 Docker 运行模式时明确跳过，跳过不算覆盖；配置不完整或连接失败仍报错。若修复前二进制不可用，应明确缺少前后对照证据。复选框表示映射存在，不等同于任意环境下已通过。
- 六个用例覆盖本轮确认的异常触发、正常回滚、混合边界、继续索引和重启恢复。数据库服务中断、进程强制崩溃及资源压力属于不同故障模型，不计入本次修复覆盖。

## 运行说明

测试位于 [test_cases/rpc/test_rich_indexer_rollback.py](../test_cases/rpc/test_rich_indexer_rollback.py)，复用现有 `CkbTest`、`CkbNode` 及 `test_cases/conftest.py`。六条用例在 PostgreSQL/SQLite 上执行，混合回滚包含两种顺序，共 **14 个测试**。完整验收要求这 14 个测试全部通过且没有跳过；跳过结果不能作为 PostgreSQL 或原生节点行为的覆盖证据。

### 环境与二进制

从仓库根目录运行，使用 Python 3.10 至 3.12；现有框架依赖 `telnetlib`，不支持 Python 3.13。安装现有依赖和 PostgreSQL 驱动：

```bash
python3.10 -m venv venv
venv/bin/python -m pip install -r requirements.txt
venv/bin/python -m pip install 'psycopg[binary]==3.2.10'
unset DOCKER
```

本文件管理原生节点，不支持框架的 Docker 运行模式；该模式下用例明确跳过。执行前取消 `DOCKER`，仅设置 `DOCKER=0` 仍会被视为启用。

节点模板准备需要 `download/current/ckb` 和 `download/current/ckb-cli`。`make prepare` 可准备基础依赖，但不保证下载的 CKB 包含 #5300；本测试通过 RPC 构造交易，不调用 CLI 签名。

`CKB_TEST_BINARY` 默认是 `download/current/ckb`，可指向已确认包含修复的构建；可选 `CKB_TEST_BINARY_SHA256` 校验实际可执行文件。测试打印实际路径、版本和 SHA-256。执行前需核对构建提交、来源及摘要，不能只靠版本号认定包含修复；上方源码分析基线也不是本机二进制的执行证据。

### PostgreSQL

使用独立 PostgreSQL 测试服务。账号需要连接管理库 `postgres`、创建数据库，并删除自己创建的数据库。例如启动临时服务：

```bash
docker run --detach --rm --name ckb-rich-indexer-test-pg -e POSTGRES_PASSWORD=ckb-test-only -p 127.0.0.1:55432:5432 postgres:16
docker exec ckb-rich-indexer-test-pg pg_isready -U postgres
```

确认服务就绪后配置连接参数，并将二进制示例路径替换为实际构建：

```bash
export CKB_TEST_PG_HOST=127.0.0.1
export CKB_TEST_PG_PORT=55432
export CKB_TEST_PG_USER=postgres
export CKB_TEST_PG_PASSWORD=ckb-test-only
export CKB_TEST_BINARY=/absolute/path/to/ckb-with-5300
# 可选：export CKB_TEST_BINARY_SHA256=<实际二进制文件的 sha256>
```

以上凭据只用于临时测试服务。每例创建唯一 `ckb_5300_<随机值>` 数据库，节点退出后只删除该例创建的库，不读取或修改 CKB 内部索引表；重启用例始终保留原数据库。

现有 [原生 CI](../.github/workflows/ci_integration_tests.yml) 为每次任务启动临时 PostgreSQL 16 服务，健康检查通过后执行测试；安装上述驱动，并向 `make test` 传入完整连接参数及动态分配的端口。因此 CI 会实际执行全部 7 项 PostgreSQL 参数用例和 7 项 SQLite 对照，任务结束后服务由 GitHub Actions 清理。服务启动失败会使任务失败，测试连接失败也会报错，不会转为跳过。

在本地或其他未配置 PostgreSQL 的入口，`CKB_TEST_PG_HOST`、`CKB_TEST_PG_PORT`、`CKB_TEST_PG_USER`、`CKB_TEST_PG_PASSWORD` 四个变量均未配置时，PostgreSQL 参数用例明确跳过，SQLite 对照仍可运行。只要任一 PG 变量已配置，就要求 HOST、USER、PASSWORD 完整，PORT 可省略并默认使用 `5432`；缺少必需项、驱动不可用或数据库连接失败仍报错。跳过不能算作 PostgreSQL 验收通过。

### 执行

```bash
venv/bin/python -m pytest -vv test_cases/rpc/test_rich_indexer_rollback.py
# 仅单区块 PostgreSQL 缺陷路径
venv/bin/python -m pytest -vv test_cases/rpc/test_rich_indexer_rollback.py -k 'single_filtered_block and postgres'
# SQLite 对照，不代表 PostgreSQL 验证通过
venv/bin/python -m pytest -vv test_cases/rpc/test_rich_indexer_rollback.py -k sqlite
```

测试通过后删除本例节点目录；失败时将诊断日志复制到 `report/rich_indexer_rollback/<本例>/`，原始 `tmp/rich_indexer_rollback/<本例>/` 数据和 `node.log` 也保留，直到 runner 清理。归档前会将本例原始 `ckb.toml` 的数据库密码脱敏；无法安全脱敏的配置文件不予保留。只终止本例启动的进程，数据库在成功或失败时都会清理。自行启动的示例服务使用结束后运行 `docker stop ckb-rich-indexer-test-pg`。

### 实现边界

- 隔离 devnet 使用现有 `always_success` 合约；第 31 块提案、第 32 块为共同祖先，从第 33 块开始打包旧分叉交易，正常共识验证始终启用。
- cell 和交易查询遍历完整分页；逐笔比较交易分组的完整输入/输出类型和序号，拒绝缺失、多余或重复明细。旧交易只匹配 X 输入和 Y 输出，辅助输入 G 不应混入；创世交易中的 S、X 输出保留在交易历史中，X 被消费只改变 live cell 查询。
- `get_transactions` 使用 `group_by_transaction=true`，每页两条并读取至结束。非分组分页不属于本轮回滚验证范围。

#5304 的移除兼容性及过期告警重放见 [Alert 移除评审](ckb-pr-5304-alert-removal.md)，不属于本文件自动化范围。
