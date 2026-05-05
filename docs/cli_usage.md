# Mini Fix CLI 使用说明

`mini-fix` 是 Mini Coding Agent 的产品化命令入口。

它封装了完整流程：

```text
Debug
→ Structured Edit
→ Patch Build
→ Patch Review
→ Human Confirm
→ Apply
→ Verify
→ Report


安装

在项目根目录执行：

pip install -e .

安装后可以使用：

mini-fix --help
基础用法

只生成 patch 和审查结果，不自动应用：

mini-fix \
  --project ../rag_lite_buglab \
  --error-file ../rag_lite_buglab/bug_schema.txt
自动应用

只有在同时满足以下条件时才会自动应用：

传入 --apply
传入 --yes
Patch Review 判断 safe_to_apply=true
Patch Review 判断 risk_level=low

命令：

mini-fix \
  --project ../rag_lite_buglab \
  --error-file ../rag_lite_buglab/bug_schema.txt \
  --apply \
  --yes
输出报告

默认会把完整 JSON 结果保存到：

reports/

如果不想保存报告：

mini-fix \
  --project ../rag_lite_buglab \
  --error-file ../rag_lite_buglab/bug_schema.txt \
  --no-report

如果想只输出完整 JSON：

mini-fix \
  --project ../rag_lite_buglab \
  --error-file ../rag_lite_buglab/bug_schema.txt \
  --json
安全设计
默认不会自动修改代码
自动应用必须显式传入 --apply --yes
patch 必须通过 Patch Review
只允许 low risk patch 自动应用
命令执行走安全白名单
python -c 会经过 AST 安全检查
.env、patch 文件、运行报告不会被提交到 Git
典型输出摘要
========== Mini Fix Summary ==========
ok: True
stage: completed
project_dir: ../rag_lite_buglab
saved_patch_path: patches/rag_lite_buglab_20260505_030401.patch
edit_need_patch: True
patch_need_patch: True
target_files: ['schemas.py']
safe_to_apply: True
risk_level: low
auto_apply_ok: True
verify_ok: True
next_status: ready_to_apply

---

# 六、安装 CLI

在 `mini_coding_agent` 根目录执行：

```bash
pip install -e .

然后测试：

mini-fix --help

如果能看到帮助信息，说明 CLI 安装成功。


## 子命令

### 初始化

```bash
mini-fix init

生成：

.mini-fix.json
patches/
reports/
环境诊断
mini-fix diagnose --project ../rag_lite_buglab
运行修复流程
mini-fix run \
  --project ../rag_lite_buglab \
  --error-file ../rag_lite_buglab/bug_schema.txt
兼容旧写法
mini-fix \
  --project ../rag_lite_buglab \
  --error-file ../rag_lite_buglab/bug_schema.txt
