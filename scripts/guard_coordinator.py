#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
守护协调器模块

基于工程控制论的守护协调机制：
- 执行前预验证（Guard）
- 异常检测与处理
- AI 大模型增强的动态规划

参考：Profile-Aware Maneuvering 架构 + trae-multi-agent 多角色协作机制
"""

import json
import threading
from pathlib import Path
from typing import Dict, List, Any, Optional, Callable
from dataclasses import dataclass, asdict, field
from datetime import datetime
from enum import Enum


class RiskLevel(Enum):
    """
    风险等级枚举
    """
    LOW = "low"       # 低风险
    MEDIUM = "medium" # 中风险
    HIGH = "high"     # 高风险
    CRITICAL = "critical"  # 严重风险


@dataclass
class ValidationWarning:
    """
    验证警告数据类
    """
    warning_code: str
    warning_type: str
    message: str
    severity: str  # info, warning, error
    recommended_action: str


@dataclass
class CompensationStrategy:
    """
    补偿策略数据类
    """
    strategy_id: str
    error_type: str  # 对应的错误类型
    strategy_type: str  # 前馈、反馈、混合
    actions: List[str]  # 具体行动列表
    priority: int  # 优先级 1-5
    confidence: float  # 置信度 0-1


@dataclass
class ValidationResult:
    """
    验证结果数据类
    
    表示执行前验证的结果
    """
    passed: bool  # 是否通过验证
    risk_level: RiskLevel  # 风险等级
    warnings: List[ValidationWarning] = field(default_factory=list)  # 警告列表
    recommended_compensations: List[CompensationStrategy] = field(default_factory=list)  # 推荐的补偿策略
    alternative_strategies: List[str] = field(default_factory=list)  # 备选策略
    validation_time: float = 0.0  # 验证耗时（秒）
    validation_details: Dict[str, Any] = field(default_factory=dict)  # 验证详情
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())


@dataclass
class AnomalyPattern:
    """
    异常模式数据类
    """
    pattern_id: str
    pattern_type: str  # 模式类型
    trigger_conditions: List[Dict[str, Any]]  # 触发条件
    anomaly_indicators: List[str]  # 异常指标
    recommended_response: str  # 推荐响应
    severity: RiskLevel  # 严重程度


@dataclass
class MonitorResult:
    """
    监控结果数据类
    """
    status: str  # normal, warning, anomaly, critical
    detected_patterns: List[str] = field(default_factory=list)  # 检测到的模式
    anomalies: List[Dict[str, Any]] = field(default_factory=list)  # 异常列表
    recommended_actions: List[str] = field(default_factory=list)  # 推荐行动
    metrics: Dict[str, Any] = field(default_factory=dict)  # 监控指标
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())


@dataclass
class ReviewResult:
    """
    审查结果数据类
    """
    outcome: str  # SUCCESS, PARTIAL_SUCCESS, FAILURE
    patterns_learned: List[str] = field(default_factory=list)  # 学到的模式
    fingerprint_updates: List[Dict[str, Any]] = field(default_factory=list)  # 画像更新
    lessons_learned: List[str] = field(default_factory=list)  # 经验教训
    improvement_suggestions: List[str] = field(default_factory=list)  # 改进建议
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())


class GuardCoordinator:
    """
    守护协调器类
    
    核心功能：
    1. 执行前预验证（Pre-execution Validation）
    2. 实时执行监控（Real-time Monitoring）
    3. 执行后审查（Post-execution Review）
    4. AI 大模型增强的动态风险评估
    
    参考 Profile-Aware Maneuvering 架构
    """
    
    def __init__(self, agent_id: str, ai_provider: Optional[Any] = None):
        """
        初始化守护协调器
        
        Args:
            agent_id: 智能体ID
            ai_provider: 可选的 AI 提供者（用于动态规划）
        """
        self.agent_id = agent_id
        self.ai_provider = ai_provider  # AI 大模型能力提供者
        
        # 补偿策略库
        self.compensation_strategies: Dict[str, CompensationStrategy] = {}
        self._init_default_strategies()
        
        # 异常模式库
        self.anomaly_patterns: Dict[str, AnomalyPattern] = {}
        self._init_default_patterns()
        
        # 验证规则库
        self.validation_rules: List[Dict[str, Any]] = []
        self._init_default_rules()
        
        # 锁
        self._lock = threading.Lock()
        
        # 验证历史
        self.validation_history: List[ValidationResult] = []
    
    def _init_default_strategies(self):
        """初始化默认补偿策略"""
        default_strategies = [
            CompensationStrategy(
                strategy_id="strat_timeout",
                error_type="timeout",
                strategy_type="feedforward",
                actions=["增加超时时间", "简化任务", "启用缓存"],
                priority=3,
                confidence=0.8
            ),
            CompensationStrategy(
                strategy_id="strat_memory",
                error_type="memory_error",
                strategy_type="feedback",
                actions=["减少并发", "清理内存", "优化数据结构"],
                priority=4,
                confidence=0.75
            ),
            CompensationStrategy(
                strategy_id="strat_syntax",
                error_type="syntax_error",
                strategy_type="feedforward",
                actions=["语法检查", "格式化代码", "使用lint工具"],
                priority=2,
                confidence=0.9
            ),
            CompensationStrategy(
                strategy_id="strat_network",
                error_type="network_error",
                strategy_type="hybrid",
                actions=["重试连接", "使用备用节点", "降级服务"],
                priority=4,
                confidence=0.7
            ),
            CompensationStrategy(
                strategy_id="strat_unknown",
                error_type="unknown",
                strategy_type="feedback",
                actions=["记录详细日志", "切换保守策略", "通知监控系统"],
                priority=5,
                confidence=0.5
            )
        ]
        
        for strategy in default_strategies:
            self.compensation_strategies[strategy.error_type] = strategy
    
    def _init_default_patterns(self):
        """初始化默认异常模式"""
        default_patterns = [
            AnomalyPattern(
                pattern_id="pattern_timeout_repeated",
                pattern_type="repeated_timeout",
                trigger_conditions=[
                    {"type": "timeout_count", "operator": ">=", "value": 3}
                ],
                anomaly_indicators=["执行时间持续增长", "超时频率增加"],
                recommended_response="降低任务复杂度或启用快速失败模式",
                severity=RiskLevel.HIGH
            ),
            AnomalyPattern(
                pattern_id="pattern_error_concentration",
                pattern_type="error_concentration",
                trigger_conditions=[
                    {"type": "error_rate", "operator": ">", "value": 0.3}
                ],
                anomaly_indicators=["错误率超过30%", "特定类型错误集中"],
                recommended_response="暂停任务并分析根因",
                severity=RiskLevel.CRITICAL
            ),
            AnomalyPattern(
                pattern_id="pattern_memory_leak",
                pattern_type="memory_leak",
                trigger_conditions=[
                    {"type": "memory_trend", "operator": ">", "value": 0.1}
                ],
                anomaly_indicators=["内存使用持续增长", "GC频率增加"],
                recommended_response="触发内存清理或重启执行环境",
                severity=RiskLevel.HIGH
            )
        ]
        
        for pattern in default_patterns:
            self.anomaly_patterns[pattern.pattern_id] = pattern
    
    def _init_default_rules(self):
        """初始化默认验证规则（含 Karpathy 四大核心原则规则）"""
        self.validation_rules = [
            {
                "rule_id": "rule_complexity",
                "name": "复杂度验证",
                "check": lambda task: task.get('complexity', 5) <= 10,
                "error_message": "任务复杂度超出范围 (1-10)",
                "severity": "error"
            },
            {
                "rule_id": "rule_timeout",
                "name": "超时时间验证",
                "check": lambda task: 0 < task.get('timeout', 300) <= 3600,
                "error_message": "超时时间超出范围 (1-3600秒)",
                "severity": "warning"
            },
            {
                "rule_id": "rule_required_fields",
                "name": "必填字段验证",
                "check": lambda task: 'type' in task and 'id' in task,
                "error_message": "缺少必填字段 (type, id)",
                "severity": "error"
            },
            {
                "rule_id": "rule_karpathy_no_placeholder",
                "name": "Karpathy原则-禁止占位符代码",
                "check": lambda task: not self._contains_placeholder_code(task),
                "error_message": "任务包含占位符代码（pass/TODO/mock/简化实现），违反 Surgical Changes 原则",
                "severity": "critical"
            },
            {
                "rule_id": "rule_karpathy_no_speculative",
                "name": "Karpathy原则-禁止投机性代码",
                "check": lambda task: not self._contains_speculative_code(task),
                "error_message": "任务包含投机性代码（为未来预留/以后可能用到），违反 Simplicity First 原则",
                "severity": "error"
            },
            {
                "rule_id": "rule_karpathy_goal_defined",
                "name": "Karpathy原则-目标必须明确",
                "check": lambda task: self._has_clear_goals(task),
                "error_message": "任务缺少明确目标定义（goals或description），违反 Goal-Driven 原则",
                "severity": "warning"
            },
            {
                "rule_id": "rule_karpathy_no_assumption",
                "name": "Karpathy原则-禁止未验证假设",
                "check": lambda task: not self._contains_unverified_assumptions(task),
                "error_message": "任务描述包含未验证的假设，违反 Think Before Coding 原则",
                "severity": "warning"
            }
        ]

    def _contains_placeholder_code(self, task: Dict[str, Any]) -> bool:
        """
        检查任务是否包含占位符代码标记

        对应 Karpathy 原则：Surgical Changes（精准修改）
        禁止使用 pass/TODO/FIXME/mock/简化/占位 等标记

        Args:
            task: 任务字典

        Returns:
            bool: 是否包含占位符代码
        """
        import re
        description = task.get('description', '')
        code_snippet = task.get('code', '')
        combined = f"{description} {code_snippet}"

        placeholder_patterns = [
            r'pass\s*#\s*(占位|placeholder|TODO)',
            r'mock|Mock|stub|Stub',
            r'简化实现|模拟实现|占位实现',
            r'#.*TODO|#.*FIXME|#.*HACK|#.*XXX'
        ]

        for pattern in placeholder_patterns:
            if re.search(pattern, combined, re.IGNORECASE):
                return True
        return False

    def _contains_speculative_code(self, task: Dict[str, Any]) -> bool:
        """
        检查任务是否包含投机性代码标记

        对应 Karpathy 原则：Simplicity First（简单优先）
        禁止为未来预留代码、添加"以后可能用到"的功能

        Args:
            task: 任务字典

        Returns:
            bool: 是否包含投机性代码
        """
        import re
        description = task.get('description', '')
        code_snippet = task.get('code', '')
        combined = f"{description} {code_snippet}"

        speculative_patterns = [
            r'#.*以后|#.*future|#.*预留|#.*reserve',
            r'为未来|以后可能|暂时不用|先留着',
            r'class.*Factory|class.*Builder(?!\s*\()'
        ]

        for pattern in speculative_patterns:
            if re.search(pattern, combined, re.IGNORECASE):
                return True
        return False

    def _has_clear_goals(self, task: Dict[str, Any]) -> bool:
        """
        检查任务是否有明确的目标定义

        对应 Karpathy 原则：Goal-Driven Execution（目标驱动执行）
        任务必须有 goals 或 description 字段

        Args:
            task: 任务字典

        Returns:
            bool: 是否有明确目标
        """
        has_goals = 'goals' in task and len(task.get('goals', [])) > 0
        has_description = 'description' in task and len(task.get('description', '')) > 5
        return has_goals or has_description

    def _contains_unverified_assumptions(self, task: Dict[str, Any]) -> bool:
        """
        检查任务描述是否包含未验证的假设

        对应 Karpathy 原则：Think Before Coding（三思而后行）
        检测"假设"、"assume"等关键词

        Args:
            task: 任务字典

        Returns:
            bool: 是否包含未验证假设
        """
        import re
        description = task.get('description', '')
        code_snippet = task.get('code', '')
        combined = f"{description} {code_snippet}"

        assumption_patterns = [
            r'#.*假设|#.*assume|#.*可能|#.*maybe',
            r'假设.*是|assume.*is',
            r'假设'
        ]

        for pattern in assumption_patterns:
            if re.search(pattern, combined, re.IGNORECASE):
                return True
        return False
    
    def set_ai_provider(self, ai_provider: Any):
        """
        设置 AI 提供者
        
        Args:
            ai_provider: AI 提供者对象
        """
        self.ai_provider = ai_provider
    
    def pre_execute_validation(self, task: Dict[str, Any]) -> ValidationResult:
        """
        执行前验证
        
        验证任务是否符合执行条件，识别潜在风险并提供补偿策略
        
        Args:
            task: 任务字典
            
        Returns:
            ValidationResult: 验证结果
        """
        import time
        start_time = time.time()
        
        warnings = []
        recommendations = []
        details = {"rule_checks": []}
        passed = True
        
        # 1. 规则检查
        for rule in self.validation_rules:
            try:
                check_result = rule["check"](task)
                details["rule_checks"].append({
                    "rule_id": rule["rule_id"],
                    "passed": check_result,
                    "message": rule.get("error_message") if not check_result else None
                })
                
                if not check_result:
                    passed = False
                    warnings.append(ValidationWarning(
                        warning_code=rule["rule_id"],
                        warning_type="validation_rule",
                        message=rule["error_message"],
                        severity=rule["severity"],
                        recommended_action="修正任务参数"
                    ))
            except Exception as e:
                warnings.append(ValidationWarning(
                    warning_code=rule["rule_id"],
                    warning_type="validation_error",
                    message=f"规则检查异常: {str(e)}",
                    severity="warning",
                    recommended_action="跳过该规则检查"
                ))
        
        # 2. AI 增强的风险评估
        if self.ai_provider:
            try:
                ai_assessment = self._ai_enhanced_risk_assessment(task)
                details["ai_assessment"] = ai_assessment
                
                if ai_assessment.get("risk_detected"):
                    passed = False
                    for risk in ai_assessment.get("risks", []):
                        warnings.append(ValidationWarning(
                            warning_code=f"ai_risk_{risk['type']}",
                            warning_type="ai_enhanced",
                            message=risk["message"],
                            severity=risk.get("severity", "warning"),
                            recommended_action=risk.get("recommendation", "人工审核")
                        ))
                
                # AI 推荐的补偿策略
                for strat in ai_assessment.get("recommended_strategies", []):
                    recommendations.append(CompensationStrategy(
                        strategy_id=f"ai_strat_{strat['type']}",
                        error_type=strat["type"],
                        strategy_type="ai_recommended",
                        actions=strat.get("actions", []),
                        priority=strat.get("priority", 3),
                        confidence=strat.get("confidence", 0.7)
                    ))
            except Exception:
                pass  # AI 评估失败不影响主流程
        
        # 3. 基于历史风险的补偿策略推荐
        task_type = task.get('type', 'unknown')
        complexity = task.get('complexity', 5)
        
        if complexity > 7:  # 高复杂度任务
            recommendations.append(self.compensation_strategies.get("strat_timeout", 
                CompensationStrategy("default", "timeout", "feedforward", ["启用保守策略"], 3, 0.8)))
        
        # 4. 确定风险等级
        risk_level = RiskLevel.LOW
        if any(w.severity == "error" for w in warnings):
            risk_level = RiskLevel.MEDIUM
        if any(w.severity == "critical" for w in warnings):
            risk_level = RiskLevel.CRITICAL
        if passed and not warnings:
            risk_level = RiskLevel.LOW
        
        validation_time = time.time() - start_time
        
        result = ValidationResult(
            passed=passed,
            risk_level=risk_level,
            warnings=warnings,
            recommended_compensations=recommendations[:5],  # 最多5个策略
            alternative_strategies=self._get_alternative_strategies(task, risk_level),
            validation_time=validation_time,
            validation_details=details
        )
        
        # 保存验证历史
        with self._lock:
            self.validation_history.append(result)
        
        return result
    
    def _ai_enhanced_risk_assessment(self, task: Dict[str, Any]) -> Dict[str, Any]:
        """
        AI 增强的风险评估
        
        使用 AI 大模型分析任务特征，预测潜在风险
        
        Args:
            task: 任务字典
            
        Returns:
            Dict[str, Any]: AI 评估结果
        """
        if not self.ai_provider:
            return {"risk_detected": False, "risks": [], "recommended_strategies": []}
        
        try:
            # 构建提示
            prompt = self._build_risk_assessment_prompt(task)
            
            # 调用 AI
            response = self.ai_provider.generate(prompt)
            
            # 解析响应
            return self._parse_ai_response(response)
            
        except Exception:
            return {"risk_detected": False, "risks": [], "recommended_strategies": []}
    
    def _build_risk_assessment_prompt(self, task: Dict[str, Any]) -> str:
        """
        构建风险评估提示
        
        Args:
            task: 任务字典
            
        Returns:
            str: 提示文本
        """
        return f"""分析以下任务的潜在风险：

任务信息：
- 类型: {task.get('type', 'unknown')}
- 复杂度: {task.get('complexity', 5)}/10
- 描述: {task.get('description', '无')}
- 特征: {json.dumps(task.get('features', {}), ensure_ascii=False)}

请分析并返回JSON格式：
{{
    "risk_detected": true/false,
    "risks": [
        {{
            "type": "风险类型",
            "message": "风险描述",
            "severity": "warning/error/critical",
            "recommendation": "建议措施"
        }}
    ],
    "recommended_strategies": [
        {{
            "type": "策略类型",
            "actions": ["具体行动1", "具体行动2"],
            "priority": 1-5,
            "confidence": 0.0-1.0
        }}
    ]
}}"""
    
    def _parse_ai_response(self, response: str) -> Dict[str, Any]:
        """
        解析 AI 响应
        
        Args:
            response: AI 响应文本
            
        Returns:
            Dict[str, Any]: 解析后的结果
        """
        try:
            # 尝试提取 JSON
            import re
            json_match = re.search(r'\{.*\}', response, re.DOTALL)
            if json_match:
                return json.loads(json_match.group())
        except Exception:
            pass
        
        return {"risk_detected": False, "risks": [], "recommended_strategies": []}
    
    def _get_alternative_strategies(self, task: Dict[str, Any], 
                                   risk_level: RiskLevel) -> List[str]:
        """
        获取备选策略
        
        Args:
            task: 任务字典
            risk_level: 风险等级
            
        Returns:
            List[str]: 备选策略列表
        """
        alternatives = []
        
        if risk_level in [RiskLevel.HIGH, RiskLevel.CRITICAL]:
            alternatives.append("保守策略")
            alternatives.append("分步执行")
            alternatives.append("人工审核")
        elif risk_level == RiskLevel.MEDIUM:
            alternatives.append("平衡策略")
            alternatives.append("增加监控")
        
        alternatives.append("默认策略")
        
        return alternatives
    
    def monitor_execution(self, execution_id: str, 
                        result: Dict[str, Any]) -> MonitorResult:
        """
        执行监控
        
        实时监控执行状态，检测异常模式
        
        Args:
            execution_id: 执行ID
            result: 执行结果
            
        Returns:
            MonitorResult: 监控结果
        """
        status = "normal"
        detected_patterns = []
        anomalies = []
        recommended_actions = []
        metrics = {}
        
        # 1. 基础指标计算
        if result.get('execution_time'):
            metrics['execution_time'] = result['execution_time']
            metrics['timeout'] = result.get('execution_time', 0) > result.get('timeout', 300)
        
        metrics['success'] = result.get('success', False)
        
        # 2. 异常模式检测
        for pattern_id, pattern in self.anomaly_patterns.items():
            if self._match_pattern(result, pattern):
                status = "anomaly"
                detected_patterns.append(pattern_id)
                anomalies.append({
                    "pattern_id": pattern_id,
                    "type": pattern.pattern_type,
                    "indicators": pattern.anomaly_indicators,
                    "response": pattern.recommended_response
                })
                recommended_actions.append(pattern.recommended_response)
        
        # 3. AI 增强的异常检测
        if self.ai_provider and status == "anomaly":
            try:
                ai_recommendations = self._ai_enhanced_anomaly_detection(result, anomalies)
                recommended_actions.extend(ai_recommendations)
            except Exception:
                pass
        
        # 4. 确定最终状态
        if any(a.get('type') == 'error_concentration' for a in anomalies):
            status = "critical"
        
        return MonitorResult(
            status=status,
            detected_patterns=detected_patterns,
            anomalies=anomalies,
            recommended_actions=recommended_actions[:5],
            metrics=metrics
        )
    
    def _match_pattern(self, result: Dict[str, Any], 
                      pattern: AnomalyPattern) -> bool:
        """
        匹配异常模式
        
        Args:
            result: 执行结果
            pattern: 异常模式
            
        Returns:
            bool: 是否匹配
        """
        for condition in pattern.trigger_conditions:
            cond_type = condition.get('type')
            operator = condition.get('operator', '==')
            value = condition.get('value')
            
            # 获取实际值
            actual_value = result.get(cond_type, 0)
            
            # 比较
            if operator == '>=':
                if not (actual_value >= value):
                    return False
            elif operator == '>':
                if not (actual_value > value):
                    return False
            elif operator == '<=':
                if not (actual_value <= value):
                    return False
            elif operator == '<':
                if not (actual_value < value):
                    return False
            elif operator == '==':
                if not (actual_value == value):
                    return False
        
        return True
    
    def _ai_enhanced_anomaly_detection(self, result: Dict[str, Any],
                                      anomalies: List[Dict[str, Any]]) -> List[str]:
        """
        AI 增强的异常检测
        
        Args:
            result: 执行结果
            anomalies: 已检测到的异常
            
        Returns:
            List[str]: AI 推荐的额外行动
        """
        if not self.ai_provider:
            return []
        
        try:
            prompt = f"""分析以下执行异常并推荐额外处理措施：

执行结果：
{json.dumps(result, ensure_ascii=False, indent=2)}

已检测异常：
{json.dumps(anomalies, ensure_ascii=False, indent=2)}

请推荐3-5个额外的处理措施，以JSON数组格式返回：
["措施1", "措施2", "措施3"]"""
            
            response = self.ai_provider.generate(prompt)
            
            import re
            list_match = re.search(r'\[.*\]', response, re.DOTALL)
            if list_match:
                return json.loads(list_match.group())
                
        except Exception:
            pass
        
        return []
    
    def post_execute_review(self, execution_id: str,
                           result: Dict[str, Any]) -> ReviewResult:
        """
        执行后审查
        
        分析执行结果，提取经验教训，更新模式库
        
        Args:
            execution_id: 执行ID
            result: 执行结果
            
        Returns:
            ReviewResult: 审查结果
        """
        patterns_learned = []
        lessons_learned = []
        improvement_suggestions = []
        
        success = result.get('success', False)
        error_type = result.get('error_type')
        
        # 1. 结果分析
        if success:
            patterns_learned.append("任务成功完成")
            lessons_learned.append("当前策略适用于此类任务")
        else:
            patterns_learned.append(f"任务失败: {error_type}")
            lessons_learned.append(f"错误类型: {error_type}")
            
            if error_type:
                strategy = self.compensation_strategies.get(error_type)
                if strategy:
                    improvement_suggestions.append(f"下次遇到{error_type}时使用策略: {strategy.strategy_id}")
                else:
                    improvement_suggestions.append(f"建议为{error_type}添加补偿策略")
        
        # 2. AI 增强的经验提取
        if self.ai_provider:
            try:
                ai_insights = self._ai_extract_lessons(result)
                lessons_learned.extend(ai_insights.get('lessons', []))
                improvement_suggestions.extend(ai_insights.get('suggestions', []))
            except Exception:
                pass
        
        return ReviewResult(
            outcome="SUCCESS" if success else "FAILURE",
            patterns_learned=patterns_learned,
            fingerprint_updates=[],  # 传递给性能画像模块
            lessons_learned=lessons_learned,
            improvement_suggestions=improvement_suggestions
        )
    
    def _ai_extract_lessons(self, result: Dict[str, Any]) -> Dict[str, List[str]]:
        """
        AI 提取经验教训
        
        Args:
            result: 执行结果
            
        Returns:
            Dict[str, List[str]]: 经验教训和建议
        """
        if not self.ai_provider:
            return {"lessons": [], "suggestions": []}
        
        try:
            prompt = f"""从以下执行结果中提取经验教训和改进建议：

{json.dumps(result, ensure_ascii=False, indent=2)}

请返回JSON格式：
{{
    "lessons": ["经验1", "经验2"],
    "suggestions": ["建议1", "建议2"]
}}"""
            
            response = self.ai_provider.generate(prompt)
            
            import re
            json_match = re.search(r'\{{.*\}}', response, re.DOTALL)
            if json_match:
                return json.loads(json_match.group())
                
        except Exception:
            pass
        
        return {"lessons": [], "suggestions": []}
    
    def add_compensation_strategy(self, strategy: CompensationStrategy):
        """
        添加补偿策略
        
        Args:
            strategy: 补偿策略
        """
        with self._lock:
            self.compensation_strategies[strategy.error_type] = strategy
    
    def add_anomaly_pattern(self, pattern: AnomalyPattern):
        """
        添加异常模式
        
        Args:
            pattern: 异常模式
        """
        with self._lock:
            self.anomaly_patterns[pattern.pattern_id] = pattern
    
    def get_statistics(self) -> Dict[str, Any]:
        """
        获取统计信息
        
        Returns:
            Dict[str, Any]: 统计信息
        """
        with self._lock:
            recent_validations = self.validation_history[-100:]
            
            return {
                'agent_id': self.agent_id,
                'total_validations': len(self.validation_history),
                'recent_validations': len(recent_validations),
                'pass_rate': sum(1 for v in recent_validations if v.passed) / len(recent_validations) if recent_validations else 1.0,
                'risk_distribution': self._get_risk_distribution(recent_validations),
                'strategy_count': len(self.compensation_strategies),
                'pattern_count': len(self.anomaly_patterns)
            }
    
    def _get_risk_distribution(self, validations: List[ValidationResult]) -> Dict[str, int]:
        """
        获取风险分布
        
        Args:
            validations: 验证结果列表
            
        Returns:
            Dict[str, int]: 风险等级分布
        """
        distribution = {level.value: 0 for level in RiskLevel}
        for v in validations:
            distribution[v.risk_level.value] += 1
        return distribution


# 导出主要类
__all__ = [
    'GuardCoordinator',
    'ValidationResult',
    'ValidationWarning',
    'CompensationStrategy',
    'MonitorResult',
    'ReviewResult',
    'AnomalyPattern',
    'RiskLevel'
]
