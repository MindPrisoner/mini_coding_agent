# Mini Coding Agent

一个轻量级本地 Coding Agent 原型，支持读取项目、搜索代码、运行安全命令、分析 Python 报错、生成 patch、审查 patch，并在人类确认后自动应用和验证。

## 核心能力

- 项目文件浏览
- 文件读取
- 关键词搜索
- 安全命令执行
- Traceback 分析
- Patch 生成
- Patch 审查
- Human-in-the-loop 自动应用
- 自动验证命令执行

## 安全设计

本项目默认不会自动修改代码。  
只有在同时满足以下条件时，才会自动应用 patch：

1. 用户显式传入 `--apply`
2. 用户显式传入 `--yes`
3. Patch Review 判断 `safe_to_apply=true`
4. Patch Review 判断 `risk_level=low`

## 运行示例

### 1. 测试工具层

```bash
python agent_cli.py --project examples/buggy_import_project --tool list_files

2. 自动分析项目
python coding_agent.py \
  --project examples/buggy_import_project \
  --task "请查看这个项目结构，并说明主要文件作用"
3. 分析报错
python debug_agent.py \
  --project examples/buggy_import_project \
  --error-file examples/buggy_import_project/error.txt
4. 生成 patch
python patch_agent.py \
  --project examples/buggy_import_project \
  --error-file examples/buggy_import_project/error.txt \
  --save-patch
5. 审查 patch
python patch_review_agent.py \
  --project examples/buggy_import_project \
  --patch-file patches/你的patch文件.patch \
  --error-file examples/buggy_import_project/error.txt
6. 一体化修复流程
python fix_agent.py \
  --project examples/buggy_import_project \
  --error-file examples/buggy_import_project/error.txt \
  --apply \
  --yes
当前限制
目前主要针对 Python 报错
patch 生成仍依赖 LLM，需审查
自动应用仅允许 low risk patch
命令执行有安全白名单
不支持复杂多文件重构
项目价值

这个项目展示了一个安全、可控的 Coding Agent 设计思路：

Debug
→ Patch
→ Review
→ Human Confirm
→ Apply
→ Verify
