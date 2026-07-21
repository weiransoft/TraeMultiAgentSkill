# 文档对照代码审查报告模板

> **文档类型**: 审查报告
> **负责角色**: 多角色联合（架构师 + 独立开发者 + 测试专家）
> **文档位置**: `<project>-DOC-CODE-REVIEW-REPORT.md`

---

## 文档信息

| 项目 | 内容 |
|------|------|
| 项目名称 | {project_name} |
| 审查时间 | {check_time} |
| 审查角色 | 架构师、独立开发者、测试专家 |
| 最终判定 | ✅ 审查通过 / ❌ 审查不通过 |
| 报告版本 | v1.0 |

---

## 1. 审查概览

### 1.1 审查范围

- **功能点总数**: {total_features}
- **集成关系总数**: {total_integrations}
- **验收标准总数**: {total_acceptance_criteria}
- **测试用例总数**: {total_tests}

### 1.2 审查结果摘要

| 维度 | 检查项 | 通过项 | 不通过项 | 通过率 | 判定 |
|------|--------|--------|----------|--------|------|
| D1 功能完成度 | {total_features} | {implemented_features} | {missing_features} | {feature_rate}% | ✅/❌ |
| D2 集成完整性 | {total_integrations} | {connected_integrations} | {missing_integrations} | {integration_rate}% | ✅/❌ |
| D3 测试正确性 | {total_tests} | {passed_tests} | {failed_tests} | {test_pass_rate}% | ✅/❌ |
| D4 验收标准 | {total_acceptance_criteria} | {satisfied_criteria} | {unsatisfied_criteria} | {acceptance_rate}% | ✅/❌ |
| D5 TODO/FIXME | {total_todos} | {implemented_todos} | {unimplemented_todos} | {todo_rate}% | ✅/❌ |
| D6 文档意图 | {total_deviations} | 0 | {total_deviations} | - | ✅/❌ |

---

## 2. D1 功能完成度

### 2.1 功能对照清单

| 功能 ID | 功能名称 | 功能描述 | 文档来源 | 代码实现位置 | 实现状态 | 证据 |
|---------|----------|----------|----------|-------------|----------|------|
| F-001 | {name} | {desc} | PRD §{section} | {file}:{func}() | ✅ 已实现 | {evidence} |
| F-002 | {name} | {desc} | PRD §{section} | - | ❌ 未实现 | - |

### 2.2 功能完成度统计

- 已实现: {implemented_features} / {total_features}
- 未实现: {missing_features} / {total_features}
- 实现率: {feature_rate}%
- 判定: ✅ 通过（100%）/ ❌ 不通过（<100%）

---

## 3. D2 集成完整性

### 3.1 集成对照清单

| 集成关系 | 文档来源 | 代码集成位置 | 集成状态 | 证据 |
|----------|----------|-------------|----------|------|
| 模块A → 模块B | 架构 §{section} | {file}: import {module} | ✅ 已连通 | {evidence} |
| 模块B → 模块C | 架构 §{section} | - | ❌ 缺失 | - |

### 3.2 集成完整性统计

- 已连通: {connected_integrations} / {total_integrations}
- 缺失: {missing_integrations} / {total_integrations}
- 集成率: {integration_rate}%
- 判定: ✅ 通过（100%）/ ❌ 不通过（<100%）

---

## 4. D3 测试正确性

### 4.1 测试执行结果

- 测试命令: `{test_command}`
- 通过: {passed_tests}
- 失败: {failed_tests}
- 跳过: {skipped_tests}
- 执行时间: {test_duration}s

### 4.2 功能覆盖检查

| 功能 ID | 功能名称 | 是否有测试 | 测试文件 |
|---------|----------|-----------|----------|
| F-001 | {name} | ✅ | test_{module}.py |
| F-002 | {name} | ❌ | - |

### 4.3 测试正确性统计

- 判定: ✅ 通过（failed=0, passed>0）/ ❌ 不通过（failed>0）
- 覆盖判定: ✅ 全覆盖 / ❌ 未全覆盖（缺失 {uncovered_count} 个功能）

### 4.4 测试输出（末尾 2000 字符）

```
{test_output_tail}
```

---

## 5. D4 验收标准满足

### 5.1 验收标准对照清单

| 验收标准 ID | 验收标准描述 | 文档来源 | 验证方式 | 满足状态 | 证据 |
|-------------|-------------|----------|----------|----------|------|
| AC-001 | {desc} | PRD §{section} | 测试 | ✅ 满足 | {evidence} |
| AC-002 | {desc} | PRD §{section} | 代码 | ❌ 不满足 | - |

### 5.2 验收标准统计

- 已满足: {satisfied_criteria} / {total_acceptance_criteria}
- 不满足: {unsatisfied_criteria} / {total_acceptance_criteria}
- 满足率: {acceptance_rate}%
- 判定: ✅ 通过（100%）/ ❌ 不通过（<100%）

---

## 6. D5 TODO/FIXME 清零

### 6.1 TODO/FIXME 清单

| 文件 | 行号 | 类型 | 内容 | 是否有对应实现 |
|------|------|------|------|---------------|
| {file} | {line} | TODO | {content} | ✅ 已实现 |
| {file} | {line} | FIXME | {content} | ❌ 未实现 |

### 6.2 TODO/FIXME 统计

- 总数: {total_todos}
- 已实现: {implemented_todos}
- 未实现: {unimplemented_todos}
- 判定: ✅ 通过（未实现=0）/ ❌ 不通过（未实现>0）

---

## 7. D6 文档意图遵从

### 7.1 多角色审查结果

#### 架构师审查

- 架构一致性: ✅ 一致 / ❌ 偏离
- 模块划分: ✅ 一致 / ❌ 偏离
- 技术选型: ✅ 一致 / ❌ 偏离

#### 开发者审查

- 功能范围: ✅ 一致 / ❌ 偏离
- 过度实现: ✅ 无 / ❌ 有
- 遗漏功能: ✅ 无 / ❌ 有

#### 测试专家审查

- 测试覆盖: ✅ 一致 / ❌ 偏离
- 测试策略: ✅ 一致 / ❌ 偏离

### 7.2 偏离清单

| 偏离维度 | 文档意图 | 代码实际情况 | 严重程度 | 建议修复方式 |
|----------|----------|-------------|----------|-------------|
| {dimension} | {doc_intent} | {code_reality} | {severity} | {suggestion} |

### 7.3 文档意图统计

- 偏离数: {total_deviations}
- 判定: ✅ 通过（偏离=0）/ ❌ 不通过（偏离>0）

---

## 8. 缺口清单

> 仅在审查不通过时填写

| # | 维度 | 缺口描述 | 功能 ID | 优先级 | 建议修复方式 |
|---|------|----------|---------|--------|-------------|
| 1 | D1 功能完成度 | {description} | F-002 | P0 | {suggestion} |
| 2 | D2 集成完整性 | {description} | - | P0 | {suggestion} |

---

## 9. 改进建议

### 9.1 短期修复（审查不通过时必须修复）

1. {suggestion_1}
2. {suggestion_2}

### 9.2 长期优化（审查通过后的改进建议）

1. {suggestion_1}
2. {suggestion_2}

---

## 10. 审查结论

- **最终判定**: ✅ 审查通过，可发布 / ❌ 审查不通过，需回退修复
- **缺口总数**: {total_gaps}
- **建议操作**: {action}

---

> 审查人签名: ________________  日期: ________________
