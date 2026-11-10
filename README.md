# DSH 可迭代数据标注工作台

**标注标准改变后，让已有工作可靠地继续。** 首版 `0.1.0` 支持本地文本单标签分类：固定规则版本，隔离旧任务回调，保留人工审核，筛出待复核记录，检查后导出可追溯的数据集。

这不是自动保证 AI 标签正确的系统，也不是完整替代 Label Studio/Argilla。项目仍处于首版验证阶段，未提交插件广场。

## 使用流程

1. 导入 `id/text` 数据，创建明确的标签表和规则 v1。
2. DSH 通过工具领取任务，用模型给出建议，或执行完整的 `decision_list` 规则；结果绑定规则和记录修订。
3. 修改规则先预览影响，再以预期版本激活 v2；旧 job 被隔离。
4. 新机器结果不能覆盖人工决定。审核者在本地页面或 Label Studio 中复核。
5. 所有记录符合当前标准、后台任务已完成/取消后，导出带规则、历史、哈希和审计信息的 ZIP。

自然语言标准变化默认全量复核。精确减少复核只适用于完整可执行的顺序关键词规则；不把关键词筛选包装成语义理解。原始文本在项目内不可变。

## 安装

需要 Python 3.11+、Node 24.9+、DSH `0.2.2-alpha.3` 系列。已在 Windows / Python 3.12 的本机 DSH 构建上验证。

```powershell
git clone https://github.com/shinerain1/dsh-project-2.git
cd dsh-project-2
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install .
npm.cmd ci --ignore-scripts
npm.cmd run build
```

将插件插入 DSH 配置（绝对路径按本机调整），后端环境必须先安装：

```yaml
- insert:
    - id: label-workbench
      name: E:/dsh插件/dsh-project-2/lib/index.js
      config:
        pythonExecutable: E:/dsh插件/dsh-project-2/.venv/Scripts/python.exe
        root: E:/dsh插件/dsh-project-2/.label-workbench
        requestTimeoutMs: 120000
```

通过 DSH 支持的 profile/插件安装入口加载这段配置；不要把它当成独立 Node 程序运行。`cordis.patch.yml` 是包安装后的入口，需配置实际 Python 路径。

## 本地样例与人工复核

```powershell
.\.venv\Scripts\python.exe -m dsh_label_workbench --root .label-workbench import --name 客服标注 --data examples/support-records.json --rule examples/rules-v1.json --key demo-create
```

保留返回的 project_id，让 DSH 创建 job、执行规则、预览 `examples/rules-v2.json` 再激活。人工审核由用户启动：

```powershell
.\.venv\Scripts\python.exe -m dsh_label_workbench --root .label-workbench serve-review --project <project_id>
```

打开终端返回的本机 URL，显示当前规则、历史标签和记录修订；确认时再检查版本。页面不是多人身份认证系统。Agent 不应代人填写审核结果。

也可用 `label_review_bundle` 生成 Label Studio JSON/XML；导出审核结果后，由审核者导入：

```powershell
.\.venv\Scripts\python.exe -m dsh_label_workbench --root .label-workbench review-import --project <project_id> --input reviewed.json --reviewer 你的名字 --confirm-human-review
.\.venv\Scripts\python.exe -m dsh_label_workbench verify <export.zip>
```

## 证据与边界

- [真实数据案例：CFPB 分类迁移](docs/CFPB-CASE-STUDY.md)：1,200 条官方公开投诉、真实发生的类别拆分、三组对照及逐记录审计；旧回调隔离有效，但全量复核增加了处理量，有保护脚本同样正确。
- [实验预案](docs/PROTOCOL.md)与[对照结果](docs/benchmark-results.json)：合成规则变更、迟到结果及人工保护，含有完整保护措施的普通脚本基线。
- [真实 DSH PTC 验证](docs/dsh-ptc-results.json)：正式工具、批量调用、插件重载后 Session 恢复；不是模型对照。
- [真实 Agent 试验预案](docs/AGENT-PILOT-PROTOCOL.md)与[结果](docs/agent-pilot-results.json)：普通脚本与插件分别运行，保留不利结果。
- [成熟项目调研](docs/RESEARCH.md)、[状态与接口说明](docs/ARCHITECTURE.md)、[实验解读](docs/EXPERIMENTS.md)。
- [首版验证记录](docs/VERIFICATION.md)：22 个 Python 测试、2 个 Node 测试、独立 wheel 安装，以及尚未完成的浏览器视觉验收。

有保护的脚本也能解决这些一致性问题。插件的贡献是把事务、版本、租约、人审保护和导出约束固化成 DSH 可复用工具，不是发明标注算法。已验证公开真实数据的分类迁移回放；未验证企业生产部署、语义影响筛选、多人权限和大规模长期运行。

## 复现实验

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
npm.cmd run check
.\.venv\Scripts\python.exe scripts/benchmark.py
$env:DSH_CHECKOUT = 'E:/dsharness'
node scripts/dsh-check.mjs
```

真实模型实验需要本机 DSH 已登录；`scripts/agent_pilot.py prepare` 后依次运行两个 arm 的两轮，再 `audit`。原始会话日志、凭据、用户数据不提交 GitHub。CLI deterministic worker 仅执行明确关键词规则，`claim/submit` 可接其他标注后端。

MIT；Label Studio/Argilla 仅作设计参考/格式互通，不打包其服务或源码。
