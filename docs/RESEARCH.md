# 成熟项目与适配范围

核查日期：2026-11-10。并非声称其他工具做不到，也未对全球插件做穷尽检索。

## Label Studio

- 仓库：[HumanSignal/label-studio](https://github.com/HumanSignal/label-studio)，核查 develop commit `fb5dddfe36c826ff996e59f7c113701d6ef36b65`，Apache-2.0。
- 阅读 [tasks/models.py](https://github.com/HumanSignal/label-studio/blob/fb5dddfe36c826ff996e59f7c113701d6ef36b65/label_studio/tasks/models.py) 及[官方预测格式](https://labelstud.io/guide/predictions.html)。成熟系统已分离预测和人工标注，预测有 model_version。模型版本不等同于本工作台的业务规则版本，但已有能力应复用，不应宣称新发明。
- 本项目输出 `data`、`meta`、`predictions.model_version`、`choices` 和匹配 XML 配置，已有历史标签只作为预测提示。移除标签词汇时，不把无效旧标签塞进新配置的预测选项。
- 导入明确由本机审核者确认的 Label Studio JSON，逐条校验 project、rule_version、content_hash、revision 和唯一完成标注；有旧版本或并发修改则整批回滚。
- 已测 JSON 格式往返及旧版本导入拒绝；**未运行完整 Label Studio 服务、浏览器多人协作或生产连接测试**。

## Argilla

- 仓库：[argilla-io/argilla](https://github.com/argilla-io/argilla)，核查 main commit `eb689f8af0b02b52644d6b7a32b51513b1c784a9`。
- 官方[记录文档](https://docs.argilla.io/latest/how_to_guides/record/)区分 suggestions、responses、metadata；[审核文档](https://docs.argilla.io/latest/how_to_guides/annotate/)支持按建议、响应和元数据筛选。其 README 当前说明代码成熟、以修复和补丁维护为主。
- 借鉴建议/人审分离和元数据筛选，不重新实现完整标注平台。首版没有接入 Argilla 服务，也不宣称功能对齐。

## 本项目承担什么

在 DSH 侧给出不可变规则版本、事务切换、过期任务隔离、保守影响筛选、人工保护、恢复导航及导出门禁。Python 用标准库 SQLite 事务、主键/唯一约束和追加日志实现状态管理；不引入自制数据库或新标注算法。

差异在流程组合和可核验的约束，不是“无人做过”的研究结论。对任意自然语言规则自动缩小复核范围仍未解决；首版选择全量复核。即使专业平台也能搭建相同流程，DSH 适配仍可减少每次重新编写协调代码，但节省多少需要对照实验。
