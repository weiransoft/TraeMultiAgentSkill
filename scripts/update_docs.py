#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SKILL.md 和 README.md 文档更新脚本

添加 Cybernetics 工程控制论增强章节
"""

import re

def add_cybernetics_to_skill_md():
    """更新 SKILL.md 添加 Cybernetics 章节"""
    
    cybernetics_section = '''## Cybernetics 工程控制论增强 (v2.5 新增)

> 基于 cybernetics-agent 工程控制论思路，实现 AI Agent 的反馈闭环、自适应和可观测性增强。

### 核心架构

- **三环控制模型**：战略层（任务规划、角色配置、AI动态规划）、战术层（Guard验证、异常检测、补偿计算）、执行层（任务执行、反馈收集、结果评估）
- **反馈控制环**：感知-决策-执行-反馈完整闭环，基于案例的策略选择（非PID）
- **性能画像**：执行案例记录、失败/成功模式提取、相似案例检索（非预测）、冷启动优雅降级
- **守护协调器**：执行前预验证、实时异常检测、执行后审查、AI增强的风险评估

### 核心组件

| 组件 | 文件 | 功能 |
|------|------|------|
| **FeedbackControlLoop** | `scripts/feedback_control_loop.py` | 反馈控制环核心实现 |
| **PerformanceFingerprint** | `scripts/performance_fingerprint.py` | 性能画像 |
| **GuardCoordinator** | `scripts/guard_coordinator.py` | 守护协调器 |
| **HierarchicalControl** | `scripts/hierarchical_control.py` | 层次化控制器 |
| **CyberneticsIntegration** | `scripts/cybernetics_integration.py` | 统一集成接口 |
| **ContextFingerprintIntegration** | `scripts/context_fingerprint_integration.py` | 上下文集成 |

### 使用方式

```python
from cybernetics_integration import CyberneticsIntegration, CyberneticsConfig

# 创建配置
config = CyberneticsConfig(
    feedback_loop_enabled=True,
    fingerprint_enabled=True,
    guard_enabled=True,
    hierarchical_enabled=False
)

# 创建集成实例
integration = CyberneticsIntegration(
    agent_id="my_agent",
    config=config,
    ai_provider=my_ai_provider
)

# 执行前验证
validation = integration.pre_execute_validation(task)

# 带反馈执行
result = integration.execute_with_feedback(task, executor)

# 获取建议
recommendations = integration.get_recommendations(task)
```

### 预期收益

- **执行成功率**：85% → 93% (+8%)
- **执行方差**：0.15 → 0.05 (-67%)
- **人工介入率**：降低 70%
- **经验复用率**：显著提升

### 文档索引

| 文档 | 路径 | 内容 |
|------|------|------|
| **整合方案** | `docs/dev/CYBERNETICS_INTEGRATION_PLAN.md` | 完整工程控制论整合方案（v2.0，多角色审核版） |
| **代码分析** | `docs/dev/CYBERNETICS_ANALYSIS.md` | 代码更新分析文档 |
| **反馈控制环** | `scripts/feedback_control_loop.py` | 反馈控制环核心实现 |
| **性能画像** | `scripts/performance_fingerprint.py` | 性能画像实现 |
| **守护协调器** | `scripts/guard_coordinator.py` | 守护协调器实现 |
| **层次化控制器** | `scripts/hierarchical_control.py` | 三层控制器实现 |
| **集成层** | `scripts/cybernetics_integration.py` | 统一集成接口 |

'''

    # 读取文件
    with open('/Users/wangwei/claw/.trae/skills/trae-multi-agent/SKILL.md', 'r', encoding='utf-8') as f:
        content = f.read()
    
    # 找到"## 快速开始"的位置
    pattern = r'(## 核心能力\n\n### AI 增强能力.*?)(## 快速开始)'
    match = re.search(pattern, content, re.DOTALL)
    
    if match:
        # 在 AI 增强能力后面插入 Cybernetics 章节
        new_content = content[:match.end(1)] + cybernetics_section + content[match.start(2):]
        
        with open('/Users/wangwei/claw/.trae/skills/trae-multi-agent/SKILL.md', 'w', encoding='utf-8') as f:
            f.write(new_content)
        
        print("✅ SKILL.md 更新成功")
        return True
    else:
        print("⚠️  未找到插入位置")
        return False

def add_cybernetics_to_readme():
    """更新 README.md 添加 Cybernetics 章节"""
    
    cybernetics_section = '''### Cybernetics 工程控制论增强 (v2.5 新增)

基于 cybernetics-agent 工程控制论思路，为 AI Agent 引入**反馈闭环**、**自适应**和**可观测性**：

1. **三环控制模型** 🔄
   - 战略层：任务规划、角色配置、AI动态规划
   - 战术层：Guard验证、异常检测、补偿计算
   - 执行层：任务执行、反馈收集、结果评估

2. **反馈控制环** 💫
   - 感知-决策-执行-反馈完整闭环
   - 基于案例的策略选择（非PID，适配认知任务）
   - 案例库自动积累和复用

3. **性能画像** 📊
   - 执行案例记录
   - 失败/成功模式提取
   - 相似案例检索（非预测，避免不可靠）
   - 冷启动优雅降级

4. **守护协调器** 🛡️
   - 执行前预验证
   - 实时异常检测
   - 执行后审查
   - AI增强的风险评估

**使用示例**:
```python
from cybernetics_integration import CyberneticsIntegration

integration = CyberneticsIntegration(agent_id="my_agent")

# 带反馈执行
result = integration.execute_with_feedback(task, executor)

# 获取建议
recommendations = integration.get_recommendations(task)
```

**预期收益**: 执行成功率 +8%，方差 -67%，人工介入 -70%

**详细文档**: [CYBERNETICS_INTEGRATION_PLAN.md](docs/dev/CYBERNETICS_INTEGRATION_PLAN.md)

'''
    
    # 读取文件
    with open('/Users/wangwei/claw/.trae/skills/trae-multi-agent/README.md', 'r', encoding='utf-8') as f:
        lines = f.readlines()
    
    # 找到"### AI 增强能力"部分结束的位置
    insert_idx = None
    for i, line in enumerate(lines):
        if '### AI 增强能力 (v2.1 新增)' in line:
            # 找到这个章节结束的位置（下一个 ### 开始）
            for j in range(i+1, len(lines)):
                if lines[j].startswith('### ') or lines[j].startswith('### 长程'):
                    insert_idx = j
                    break
            break
    
    if insert_idx:
        # 插入 Cybernetics 章节
        lines.insert(insert_idx, cybernetics_section + '\n')
        
        with open('/Users/wangwei/claw/.trae/skills/trae-multi-agent/README.md', 'w', encoding='utf-8') as f:
            f.writelines(lines)
        
        print("✅ README.md 更新成功")
        return True
    else:
        print("⚠️  未找到插入位置")
        return False

if __name__ == '__main__':
    print("开始更新文档...")
    
    success1 = add_cybernetics_to_skill_md()
    success2 = add_cybernetics_to_readme()
    
    if success1 and success2:
        print("\n🎉 所有文档更新完成！")
    else:
        print("\n⚠️  部分文档更新失败")
