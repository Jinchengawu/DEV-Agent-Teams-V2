# 交付失败案例离线回归计划

状态：Final Plan / Implemented

## 目标与边界

将已经定位并修复的交付合同问题固化为可版本化、可校验、可重复执行的离线案例包。回放直接调用产品现有的 Workcell Acceptance 与 Design Contract 验证器，不调用模型、不访问数据库或 API，也不进入浏览器闭环、Live Release Gate、Approval 或 Apply。

该证据的固定范围是 `offline_contract_regression`。通过结果只能证明当前 Revision 下，已登记输入与现有验证器的结果符合预期。

## Draft Plan

1. 建立 `delivery-failure-regression/1.0.0` 数据集，登记来源种类、修复 Revision、预期结果与错误码。
2. 使用固定分派器调用现有验证器，输出 JSON 与中文 Markdown 报告。
3. 对数据集文件执行 SHA-256、JSON Schema、唯一 ID、案例分布、路径与文件集合校验。
4. 覆盖 15 个首批案例，并验证篡改、重复 ID、路径逃逸、符号链接、未登记文件、结果不匹配及未知异常。

## Architecture Review

- **Architecture Impact：Local**
- **Findings：**现有 `validate_workcell_acceptance` 与 `design` 已经拥有合同判定权；回放工具应复用它们，不能复制判断逻辑。该能力属于开发期离线回归，不应伪装成通用 Evaluation、Live Agent 或 Release Gate 证据。
- **Required Revisions：**使用独立开发工具；按案例族固定分派；区分 `sanitized_capture`、`regression_fixture` 与 `synthetic_negative`；未知异常必须记为 `execution_error`，不能作为预期拒绝通过。
- **ADR Required：No**
- **Architecture Document Delta：None**
- **Outcome：Approved**

## Final Plan

1. 数据集只允许 `manifest.json`、`schema.json`、`fixtures.json`、`cases.jsonl` 四个直接子文件；清单绑定其内容哈希和 4+11 案例分布。
2. Workcell 案例覆盖冻结 literal、ArtifactAttachment 消费、跨仓直接执行与缺失 Workcell；Design 案例覆盖 v1、三类 v2 等价表达、拆分向量及五类拒绝路径。
3. 报告记录 Git Revision/dirty、数据集哈希、验证器源码哈希、Python 与关键依赖版本、逐案例输入哈希和结果。
4. CLI 全匹配返回 0，预期不匹配返回 1，数据集或环境错误返回 2。
5. 使用针对性 pytest、Ruff、Mypy 和两次独立目录回放完成验证；两次回放忽略时间字段后语义一致。

## 使用

```bash
.venv/bin/python scripts/replay_delivery_failures.py \
  --dataset evaluation/datasets/delivery-failure-regression/1.0.0 \
  --output-dir /tmp/delivery-failure-regression
```

输出为 `report.json` 与 `report.md`。新增真实失败案例时应先脱敏，说明来源类型与归因，增加版本目录，并重新生成清单哈希；不得原地改写已经发布的版本。

## Architecture Reconciliation

实现保留现有验证器的权威归属，没有新增持久化、外部集成、权限、Apply、补偿或恢复语义，也没有改变模块依赖方向。架构影响仍为 `Local`，无需更新 ADR 或 `docs/architecture/ARCHITECTURE.md`。
