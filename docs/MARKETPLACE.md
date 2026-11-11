# 安装 DSH 可迭代数据标注工作台

非官方社区插件，面向可信本地单用户。版本 0.1.0 是早期可用版本，已在 Windows、Python 3.12、Node 24.18 和 DSH 0.2.2-alpha.3 上验证。Python 至少 3.11，Node 至少 24.9。声明兼容范围不代表所有平台/版本都经过实测。

插件和 Python 后端需要分别安装；安装 DSH 包不会自动安装 Python，也不会上传你的数据。

## 1. 安装后端

从 [v0.1.0 发布页](https://github.com/shinerain1/dsh-label-workbench/releases/tag/v0.1.0)下载 `dsh_label_workbench-0.1.0-py3-none-any.whl`，在专用目录运行：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --no-deps .\dsh_label_workbench-0.1.0-py3-none-any.whl
```

后端仅使用 Python 标准库，无额外运行时依赖。记下这个虚拟环境中 Python 的绝对路径。

## 2. 安装 DSH 插件

在实际使用的 DSH 插件界面安装预构建包：

```text
https://github.com/shinerain1/dsh-label-workbench/releases/download/v0.1.0/dsh-label-workbench-0.1.0.tgz
```

自行管理 `web` profile 时也可执行：

```powershell
dsh plugin --profile web add https://github.com/shinerain1/dsh-label-workbench/releases/download/v0.1.0/dsh-label-workbench-0.1.0.tgz
```

Desktop 用户请在实际的桌面应用插件界面安装；`--profile web` 不会安装到 Desktop。预构建包已包含 JavaScript，无需在本机编译 TypeScript。不要把仓库地址当成已经构建好的包；源码开发安装见主 README。

## 3. 配置并检查

在插件设置中填写 Python 与标注存储的绝对路径。例如：

```yaml
- id: label-workbench
  config:
    pythonExecutable: C:/label-workbench/.venv/Scripts/python.exe
    root: C:/label-workbench/data
    requestTimeoutMs: 120000
```

安装后默认 Python 为 `python`，默认存储为用户目录下 `.dsh-label-workbench`；使用虚拟环境时必须改成实际解释器路径。上面是修改已安装插件的配置，不要再次插入同 ID 的插件。

让 DSH 调用 `label_health`，应返回后端版本 `0.1.0`。再让它导入文本、建立规则 v1，创建/领取任务，提交机器标签；修改标准时先预览再激活 v2，处理待复核项后导出。

人工审核由用户启动本地审核页面，或使用 Label Studio JSON 导入/导出；Agent 的工具只提交机器建议，不能通过人审接口冒充用户。页面是本机可信用户工具，不是多人身份认证系统；同一 OS 用户的通用终端权限不在该隔离范围内。

## 验证和限制

- [真实 CFPB 分类迁移](CFPB-CASE-STUDY.md)：1,200 条投诉、三个固定故障落点、三组实现。插件和完整保护脚本均保持一致；没有证明标签语义准确率或处理量优势。
- [首版测试](VERIFICATION.md)：22 个 Python 测试、2 个 Node 测试、实际 DSH 集成和独立 wheel 测试。
- [公开安装验证](publication-v0.1.0.json)：发布后从公开链接安装到临时 DSH profile，并加载真实宿主工具执行。
- 自然语言标准变化默认全部复核；精确沿用仅适用于完整可执行规则。文本在项目内不可变，首版单项目最多 20,000 条。
- 本地状态和 ZIP 的哈希用于一致性检查，不是对恶意篡改的第三方签名证明；导出不会保证业务标签本身正确。
- 后端经宿主本地 RPC 访问受配置约束的存储，没有宣称它运行在 DSH 子进程沙箱中。浏览器视觉验收、多人权限和企业生产部署尚未完成。

源码、实验、安装包公开不等于已获社区广场收录；以[投稿状态](../market/submission/README.md)为准。
