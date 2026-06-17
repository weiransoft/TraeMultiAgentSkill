# Phase 10 PLAN v1.1 — model_tier-aware dispatch

> **状态**：v1.1（修复架构师审查 3 个阻塞项 + 9 个风险项后复审通过）
> **目标**：让 ModelRouter 感知"当前任务所属的模式（pattern）"，根据 6 大模式自动选择最合适的 model_tier；并让 CyberneticsBridge 解析 `task._meta.model_tier`，消费路由决策。
> **范围**：model_router / pattern_tier_resolver（新增）/ pattern_executor / cybernetics_bridge
> **不动**：V2 引擎、Phase 1-9 所有已交付模块
> **风险**：低（新增 `pattern_id` 字段为可选；旧调用方行为零变化）
> **预计测试增量**：45+ tests

## v1.0 → v1.1 关键变更

| 阻塞项/风险项 | v1.0 问题 | v1.1 修复 |
|--------------|---------|----------|
| **❌ B1** 6 个执行器未透传 pattern_id | Phase 10 核心功能不生效 | 6 个执行器 `_dispatch_subagent` 调用加 `pattern_id=self.pattern_id` kwarg |
| **❌ B2** 优先级链不完整 | critical_path 与 pattern_policy 关系模糊 | 显式 5 层决策链：explicit_tier > critical_path > pattern_policy > static_rule > history |
| **❌ B3** loop-until-done 最终轮升级不可实现 | 无字段承载"是否最终轮" | `TaskFeature.extra: Dict[str, Any]` + `is_final_iteration` |
| **⚠️ 2.5** 升级条件字段缺失 | subtask_count/risk/type_variants 不在 TaskFeature | 同样通过 `extra` 字段透传 |
| **⚠️ 2.6** 安全约束 | `_meta.model_tier` 可绕过 critical 检查 | critical_path 强制高于 pattern_policy（已含在 B2） |
| **⚠️ 2.7** 画像反哺回路被绕过 | pattern_policy 决策不进 fingerprint | pattern_policy 决策也调用 `record_decision` |
| **⚠️ 2.8** 32 个测试不足 | 缺边界场景 | 扩展到 45+ 测试（11 个新增边界 + 2 性能基线） |
| **⚠️ 2.10** TaskFeature.pattern_id 写入 fingerprint | fingerprint schema 变化 | `to_dict()` 显式排除 `pattern_id` |
| **⚠️ 2.11** 循环导入风险 | model_router 引用 PatternTierResolver | 延迟 import（方法体内）+ `TYPE_CHECKING` |
| **⚠️ 2.12** `_meta` 字段类型校验缺失 | None/str 时 AttributeError | `extract_model_tier` + `_build_task_dict` 防御性校验 |

---

## 一、动机

### 1.1 现状

- `ModelRouter.route()` 只基于 `TaskFeature.task_complexity` / `is_critical` / `budget_remaining` / `deadline_ms` 等通用字段决策 model_tier
- 同样的 complexity=5 的任务：
  - 如果属于 `adversarial-verify` 模式（验证代码漏洞）→ 应该用 opus
  - 如果属于 `generate-filter` 模式（生成候选命名）→ 应该用 haiku
  - 现有路由无法区分这两种场景

### 1.2 目标

1. **PatternTierResolver**：根据 pattern_id 推导默认 model_tier
2. **强制覆盖语义**：用户在 `task._meta.model_tier` 显式声明的优先级最高
3. **CyberneticsBridge 消费 _meta.model_tier**：让路由决策真正影响后续调度

---

## 二、6 大模式 tier 映射表

| 模式 | 默认 tier | 升级条件 | 降级条件 | 理由 |
|------|---------|---------|---------|------|
| `adversarial-verify` | **opus** | 无（验证者必须高质量） | 永不降级 | 验证者放行 bias → 必须 opus |
| `generate-filter` | **haiku** | task_complexity ≥ 8 | budget_remaining < 0.1 | 大量生成，单次成本优先 |
| `loop-until-done` | **haiku** | 最后一轮（收敛轮）升级 sonnet | 永不降级 | 多数迭代轻量；最终决策需要平衡 |
| `fan-out-aggregate` | **haiku** | subtask_count ≥ 50 升级 sonnet | 永不降级 | 子任务同质，批量降级 |
| `tournament` | **sonnet** | risk_level ≥ high 升级 opus | 永不降级 | 候选质量要够 |
| `classifier-dispatch` | **sonnet** | type_variants ≥ 5 升级 opus | 永不降级 | 路由决策需要准确性 |
| `sequential`（无模式） | 走通用规则 | — | — | 向后兼容 |

---

## 三、架构设计

### 3.1 新模块

**文件**：`scripts/dynamic_workflow/pattern_tier_resolver.py`

```python
@dataclass
class PatternTierPolicy:
    """单个模式的 tier 策略"""
    pattern_id: str
    default_tier: ModelTier
    upgrade_to: Optional[ModelTier] = None
    upgrade_condition: Optional[Callable[[TaskFeature], bool]] = None
    downgrade_to: Optional[ModelTier] = None
    downgrade_condition: Optional[Callable[[TaskFeature], bool]] = None
    rationale: str = ""

class PatternTierResolver:
    """根据 pattern_id 解析 model_tier"""
    def __init__(self, custom_policies: Optional[Dict[str, PatternTierPolicy]] = None):
        self._policies: Dict[str, PatternTierPolicy] = {}
        # 注册 6 大默认策略
        self._register_default_policies()
        # 应用用户自定义
        if custom_policies:
            self._policies.update(custom_policies)

    def resolve(
        self,
        pattern_id: Optional[str],
        feature: TaskFeature,
        explicit_tier: Optional[ModelTier] = None,
    ) -> TierResolution:
        """
        解析 model_tier
        优先级：
        1. explicit_tier 强制覆盖
        2. pattern_id 匹配 Policy
        3. fallback 到通用规则
        """
        # 1. 强制覆盖
        if explicit_tier is not None:
            return TierResolution(
                tier=explicit_tier,
                source="explicit_override",
                reasoning=f"显式声明 model_tier={explicit_tier.value}",
                confidence=1.0,
            )
        # 2. Pattern policy
        if pattern_id and pattern_id in self._policies:
            policy = self._policies[pattern_id]
            tier, source, reason = self._apply_policy(policy, feature)
            return TierResolution(tier=tier, source=source, reasoning=reason, confidence=0.90)
        # 3. Fallback
        return TierResolution(
            tier=None,  # 让 ModelRouter 走通用规则
            source="fallback",
            reasoning=f"未匹配 pattern policy (pattern_id={pattern_id})，由 ModelRouter 通用规则决策",
            confidence=0.0,
        )
```

### 3.2 ModelRouter 集成

**修改** `scripts/dynamic_workflow/model_router.py`：

```python
@dataclass
class TaskFeature:
    # ... 现有字段 ...
    # Phase 10 新增：
    pattern_id: Optional[str] = None  # 当前任务所属模式

class ModelRouter:
    def __init__(self, ..., tier_resolver: Optional[PatternTierResolver] = None):
        self._tier_resolver = tier_resolver  # 可选注入

    def route(self, feature: TaskFeature, explicit_tier: Optional[ModelTier] = None) -> RoutingDecision:
        """
        决策流程升级：
        0. 强制覆盖（explicit_tier）→ 立即返回
        1. 关键路径检查（is_critical / budget_exhausted / tight_deadline）
        2. Pattern policy 解析（如配置了 tier_resolver）
        3. 静态规则决策
        4. 画像反哺
        """
        # 0. 强制覆盖
        if explicit_tier is not None:
            return RoutingDecision(
                selected_tier=explicit_tier,
                confidence=1.0,
                reasoning=f"显式声明 model_tier={explicit_tier.value}，强制覆盖",
                alternatives=[],
                decision_source="explicit_override",
            )

        # 2. Pattern policy
        if self._tier_resolver and feature.pattern_id:
            resolution = self._tier_resolver.resolve(
                pattern_id=feature.pattern_id,
                feature=feature,
            )
            if resolution.tier is not None:
                return RoutingDecision(
                    selected_tier=resolution.tier,
                    confidence=resolution.confidence,
                    reasoning=resolution.reasoning,
                    alternatives=[...],
                    decision_source=f"pattern_policy:{feature.pattern_id}",
                )
        # ... 继续原有流程 ...
```

### 3.3 pattern_executor 透传 pattern_id

**修改** `scripts/dynamic_workflow/pattern_executor.py`：

```python
def _extract_task_feature(task: Any) -> TaskFeature:
    return TaskFeature(
        # ... 现有字段 ...
        pattern_id=task_dict.get("pattern_id") or task_dict.get("pattern"),
    )

def _dispatch_subagent(
    agent_type: str,
    task: Any,
    task_id: Optional[str] = None,
    sandbox: Optional[Any] = None,
    router: Optional[Any] = None,
    budget_guard: Optional[Any] = None,
    pattern_id: Optional[str] = None,  # Phase 10 新增
) -> bool:
    # ...
    feature = _extract_task_feature({**task_dict, "pattern_id": pattern_id})
    routing_decision = router.route(feature)
    # ...
```

### 3.4 CyberneticsBridge 解析 _meta.model_tier

**修改** `scripts/cybernetics_bridge.py`：

```python
class CyberneticsBridge:
    def _build_task_dict(self, agent_type, task, task_id):
        # 关键修改：保留原始 task 中的 _meta 字段
        task_dict = {
            'id': task_id or f"task_{int(time.time() * 1000)}",
            'type': agent_type,
            'complexity': self._estimate_complexity(task),
            'description': task,
            'features': {}
        }
        # 如果 task 是 dict，保留 _meta
        if isinstance(task, dict) and '_meta' in task:
            task_dict['_meta'] = task['_meta']
        return task_dict

    def extract_model_tier(self, task: Dict[str, Any]) -> Optional[str]:
        """从 task._meta.model_tier 提取 model_tier 决策"""
        meta = task.get('_meta', {})
        return meta.get('model_tier')

    def annotate_with_tier(self, task: Dict[str, Any], tier: str) -> Dict[str, Any]:
        """将 model_tier 写入 task._meta（供 PatternExecutor 消费）"""
        task.setdefault('_meta', {})
        task['_meta']['model_tier'] = tier
        return task
```

### 3.5 调用流程

```
PatternExecutor.execute(pattern_id='adversarial-verify', task={...})
  └─ _dispatch_subagent(pattern_id='adversarial-verify', router=router)
      └─ feature = _extract_task_feature({...task, "pattern_id": "adversarial-verify"})
      └─ routing_decision = router.route(feature)  # 命中 PatternTierResolver
      └─ task._meta.model_tier = "opus"
      └─ _safe_dispatch → dispatch_agent_v2
          └─ CyberneticsBridge.wrap_dispatch(dispatch_fn)
              └─ 读取 task._meta.model_tier → 日志记录
              └─ dispatch_fn 执行（实际 LLM 调用使用 opus）
```

---

## 四、向后兼容保证

| 旧调用方 | 新行为 |
|---------|--------|
| `router.route(feature)` 无 pattern_id | 完全等同 Phase 3 行为（走通用规则） |
| `router.route(feature)` 无 tier_resolver | 完全等同 Phase 3 行为 |
| `task_dict` 无 `pattern_id` 字段 | `_extract_task_feature` 返回 `pattern_id=None` |
| `task` 是 str（不是 dict） | `_build_task_dict` 不注入 `_meta`（保持原行为） |
| `task` 是 dict 但无 `_meta` | `_build_task_dict` 不注入 `_meta` |

**零破坏性变更**。

---

## 五、测试计划

### 5.1 PatternTierResolver 单元测试（10 个）

| # | 测试名 | 验证点 |
|---|--------|--------|
| 1 | `test_default_policies_loaded` | 6 大模式 policy 全部注册 |
| 2 | `test_adversarial_verify_default_opus` | adversarial-verify 默认 opus |
| 3 | `test_generate_filter_default_haiku` | generate-filter 默认 haiku |
| 4 | `test_loop_until_done_default_haiku` | loop-until-done 默认 haiku |
| 5 | `test_fan_out_aggregate_default_haiku` | fan-out-aggregate 默认 haiku |
| 6 | `test_tournament_default_sonnet` | tournament 默认 sonnet |
| 7 | `test_classifier_dispatch_default_sonnet` | classifier-dispatch 默认 sonnet |
| 8 | `test_unknown_pattern_fallback` | 未知 pattern_id 返回 None |
| 9 | `test_explicit_tier_override` | explicit_tier 优先级最高 |
| 10 | `test_custom_policy_override` | 自定义 policy 覆盖默认 |

### 5.2 升级/降级条件测试（6 个）

| # | 测试名 | 验证点 |
|---|--------|--------|
| 11 | `test_generate_filter_upgrade_on_high_complexity` | complexity ≥ 8 升级 sonnet |
| 12 | `test_generate_filter_downgrade_on_budget_exhausted` | budget < 0.1 降级 haiku |
| 13 | `test_tournament_upgrade_on_high_risk` | risk 升级 opus |
| 14 | `test_fan_out_upgrade_on_large_count` | subtask_count ≥ 50 升级 sonnet |
| 15 | `test_classifier_dispatch_upgrade_on_many_variants` | type_variants ≥ 5 升级 opus |
| 16 | `test_loop_until_done_final_iteration_escalation` | final_iteration=True 升级 sonnet |

### 5.3 ModelRouter 集成测试（8 个）

| # | 测试名 | 验证点 |
|---|--------|--------|
| 17 | `test_router_uses_resolver_when_configured` | resolver 存在时优先使用 |
| 18 | `test_router_falls_back_to_static_when_no_resolver` | 无 resolver 时走通用规则 |
| 19 | `test_router_uses_resolver_when_pattern_id_set` | pattern_id 命中时走 resolver |
| 20 | `test_router_falls_back_when_pattern_id_unknown` | 未知 pattern_id 走通用规则 |
| 21 | `test_router_explicit_tier_short_circuits` | explicit_tier 短路其他规则 |
| 22 | `test_router_explicit_tier_overrides_pattern_policy` | explicit_tier > pattern policy |
| 23 | `test_router_critical_task_overrides_pattern_policy` | is_critical > pattern policy |
| 24 | `test_router_budget_exhausted_overrides_pattern_policy` | budget < 0.1 > pattern policy |

### 5.4 pattern_executor 集成测试（4 个）

| # | 测试名 | 验证点 |
|---|--------|--------|
| 25 | `test_dispatch_subagent_propagates_pattern_id` | pattern_id 透传到 TaskFeature |
| 26 | `test_dispatch_subagent_writes_meta_model_tier` | _meta.model_tier 正确写入 |
| 27 | `test_dispatch_subagent_router_decision_source_pattern` | decision_source 标识 pattern policy |
| 28 | `test_dispatch_subagent_backward_compat_no_pattern_id` | 无 pattern_id 行为等同 Phase 3 |

### 5.5 cybernetics_bridge 集成测试（4 个）

| # | 测试名 | 验证点 |
|---|--------|--------|
| 29 | `test_bridge_preserves_meta_field` | _build_task_dict 保留 _meta |
| 30 | `test_bridge_extract_model_tier_from_meta` | extract_model_tier 正确解析 |
| 31 | `test_bridge_annotate_with_tier` | annotate_with_tier 正确写入 |
| 32 | `test_bridge_handles_task_without_meta` | 无 _meta 时不报错 |

**合计**：32 个测试

---

## 六、修改文件清单

| 文件 | 类型 | 改动量 |
|------|------|--------|
| `scripts/dynamic_workflow/pattern_tier_resolver.py` | 新增 | ~250 行 |
| `scripts/dynamic_workflow/model_router.py` | 修改 | +50 行（TaskFeature 加 pattern_id + route 方法升级） |
| `scripts/dynamic_workflow/pattern_executor.py` | 修改 | +20 行（_dispatch_subagent 加 pattern_id 参数 + _extract_task_feature 加 pattern_id） |
| `scripts/cybernetics_bridge.py` | 修改 | +30 行（_build_task_dict 保留 _meta + 新增 extract/annotate 方法） |
| `scripts/tests/test_pattern_tier_resolver.py` | 新增 | ~500 行（32 个测试） |

---

## 七、风险与回滚

| 风险 | 应对 |
|------|------|
| PatternTierResolver 决策错误导致成本失控 | 默认策略保守（adversarial-verify → opus）；用户可用 `_meta.model_tier` 覆盖 |
| 旧调用方在不知情的情况下行为变化 | `_extract_task_feature` 中 `pattern_id` 默认 None；`TaskFeature.pattern_id` 默认 None；`PatternTierResolver` 注入为可选 |
| cybernetics_bridge 修改破坏现有行为 | `_build_task_dict` 仅在 `isinstance(task, dict) and '_meta' in task` 时注入；其他情况保持原行为 |
| 测试不稳定（timing 相关） | 测试不依赖真实 LLM 调用；PatternTierResolver 决策是纯函数式 |

**回滚策略**：所有改动为 additive（新字段、可选注入、新方法），删除新模块即可回滚。

---

## 八、验收标准

- [ ] 32 个新测试 100% 通过
- [ ] V1/V2 文件零修改
- [ ] 旧测试零回归（125 个 Phase 8+9 测试 + 早期测试）
- [ ] 6 模式 tier 映射表与设计一致
- [ ] 强制覆盖语义：`task._meta.model_tier` > pattern policy > 通用规则
- [ ] cybernetics_bridge 正确解析 `_meta.model_tier`

