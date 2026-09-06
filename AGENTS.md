# RabbitProjects — 项目指南入口

本仓库的完整工作指南（环境要求 / 常用命令 / 测试纪律 / 已知坑 / 文档体系 / 工作流约定）统一维护在 **[CLAUDE.md](CLAUDE.md)**，开始任何工作前必须先通读。本文件只做指路，不复制内容，两处不允许漂移。

## CodeGraph（代码知识图谱）

本仓库已接入 CodeGraph（MCP 服务器 + CLI，索引目录 `.codegraph/` 已 gitignore）。写测试用例与实现测试脚本前，必须先用 CodeGraph 评估被测符号的影响范围再圈用例边界——具体规则见 CLAUDE.md「测试脚本规范」首条（强制）。
