# 首版验证记录

2026-11-10，本机 Windows / Python 3.12.0 / Node 24.18.0。

- `python -m unittest discover -s tests -q`：22 个测试通过，包括独立后台进程、租约、并发、人审 HTTP、旧词汇导入与历史成功回执重放。
- `npm run check`：TypeScript 编译、类型复查和 2 个真实 Python RPC 的 Node 测试通过。
- `scripts/benchmark.py`：三个规模的配对故障注入，记录于 `benchmark-results.json`。
- `scripts/dsh-check.mjs`：本机真实 DSH + PTC 验证 17 个工具、11 次内层调用、Session 恢复和幂等创建。没有模型调用。
- `scripts/agent_pilot.py`：真实 DSH 两组各两轮；最终独立审计均无错误。普通脚本第一轮 max-tokens 保留；插件没有 token 成本优势。
- `pip wheel` 后在全新 `.venv-release` 中只安装 wheel，`scripts/smoke_installed.py` 验证确实从 site-packages 导入，完成改规则、两次人工审核历史和 ZIP 离线复验。
- npm tarball 与 Python wheel 均生成于本机 `dist/`。它们是本地安装包，不表示 npm 发布或插件广场审核通过。

模型小试后又完善了删除标签词汇时的 Label Studio 提示、审核页说明，以及旧成功回执的历史标识；对应回归测试通过。未为这些改动重新调用模型，不把先前小试成本当作最终版本的精确成本保证。

页面视觉验收受工具阻断：浏览器接入服务不可用，Windows UI 工具无法可靠识别当前 URL，因此停止了 Computer Use。未完成视觉检查或宣称视觉通过。HTTP 和数据一致性验收已完成。

首次实现检查发现 SQLite 写入字段数量、连接释放和 TypeScript 输出类型错误，修复后重新通过相关检查。没有把这些开发中失败伪报成首次全通过。
