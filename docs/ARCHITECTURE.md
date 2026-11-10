# 状态与接口约定

插件 `dsh-label-workbench` + Python 标准库后端 `dsh_label_workbench`。本机单用户、文本单标签、固定记录集。规则迭代是首版主线；编辑文本、追加数据请创建新项目。没有调用外部标注 API 的固定依赖，模型可通过 DSH `claim → submit` 提供建议。

## DSH 本身的能力

1. 正式 `defineTool` 注册 17 个结构化工具，完整参数/返回值可供原生调用和 PTC 使用，插件卸载撤销注册。
2. PTC 可在一次 `run_code` 中预览、切换版本、分批处理、检查状态；循环和结果筛选不必每次往返模型。自然语言标注仍由 DSH 模型解释规则。
3. 从执行上下文读取真实 Session ID 和 call ID，关联项目并生成默认幂等键；`label_workbench_resume` 恢复该 Session 关联项目。不同 PTC 重跑应使用稳定 `request_key`。显式键在整个 store 内唯一，建议包含项目和操作含义。
4. 接受工具取消信号和超时，卸载时停止短 RPC 子进程。后台标注是持久租约协议，外部 worker 可独立运行；取消/改规则保证结果不能落成当前标签，不保证外部 API 已经停止收费。

DSH 的 Windows PTC 工作区验证在临时 C 盘目录完成。后端 RPC 使用受信任配置的 Python 和固定 store 路径，**不是 DSH subprocess sandbox 的扩展**。工具不接收任意文件写路径或 Python 代码，只在 store 内写快照和导出。当前用户本来就可读写本机 store；这不构成抵御同账户恶意程序的权限边界。

## 关键状态

- `rules`、`records`、`decisions`、`results`、`events`：追加或不可变，数据库触发器拒绝更新/删除历史。
- `current`：只保存当前指针、待复核标记、人工保护标记和递增修订号。
- `jobs/items`：规则版本/哈希、记录内容哈希和创建时修订号固定；领取时发租约 token、截止时间。过期可重新领取，旧 token 不能提交。
- `results`：每次回调记录 applied / review_required / quarantined 及原因。相同 token/内容重复提交不重复落标，修改已提交 payload 会报冲突。
- `requests/sessions`：持久幂等结果和 Session 项目导航。读操作不会复用过期状态，claim 不缓存；网络丢失领取响应后等租约过期再领取。

重放成功回执会明确标记 `request_replayed`，其字段是历史操作结果；应重新查询 status 判断当前状态。无请求键的回调重放也返回 job 的 rule_version 和当前 active_rule_version，避免把 v1 曾经成功误当成 v2 新落标。

所有状态修改和版本检查在 SQLite `BEGIN IMMEDIATE` 事务内完成。版本激活把运行中旧 job 标为 superseded，旧回调仍保留为隔离证据。人工审核采用 `expected_version + expected_revision`，避免过期网页或同时到达的机器结果被静默覆盖。

## 影响筛选

`guidelines` 模式：只要产生新版本，全部记录待复核。`decision_list` 模式：只有完整的顺序子串规则、guidelines 文本不变、没有人工保护、原标签有效，且 old(text) == new(text) == current_label，才生成带 parent 指针的新版本 carry 决定。它是可执行规则上的精确判断，不是自然语言语义预测。关键词有重叠时首条匹配，使用 Unicode casefold，默认标签必须明确。

任何人工来源都不自动沿用到新标准；新机器标签仅作为建议，人工再次确认后形成新的审核记录。所有旧人工记录留存。

## 人工审核入口

Agent 工具和 RPC 不提供人工批准操作。本机 `serve-review` 页面、带 `--confirm-human-review` 的 CLI、确认过的 Label Studio 文件导入可写人工决定。审核者名称是本机用户自报，页面 token 只用于本机访问，**不是企业身份认证或多人权限系统**。具有同账户 shell 权限的 Agent 理论上也能调用 CLI；工具说明禁止冒充审核者，不声称能在相同 OS 权限下作密码学证明。

## 导出契约

要求没有待复核记录、没有 active job、所有当前决定属于指定 active version、内容和来源一致。ZIP 包含 `data.jsonl`、全部规则、全部决定、事件链和 manifest，离线 `verify` 再验哈希、规则版本、来源链和 carry 依据。文件原子落盘，导出固定一个版本的快照；导出完成后再改规则，不会修改已有 ZIP。

哈希用于发现意外损坏和不一致，不是外部签名；能重写完整存储的本机攻击者可重算哈希。ZIP 包含原始文本和审核历史，工具只本地生成，不上传数据。公开仓库只包含合成例子与统计。
