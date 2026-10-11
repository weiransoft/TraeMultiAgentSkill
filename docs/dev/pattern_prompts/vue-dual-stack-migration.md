# vue-dual-stack-migration 模式使用提示词模板

> **模式 ID**：`vue-dual-stack-migration`  
> **适用**：React → Vue3 双栈迁移（后端 API + TypeScript 类型共享）  
> **配套示例**：[vue-dual-stack-migration.json](../pattern_examples/vue-dual-stack-migration.json)  
> **配套手册**：[PATTERNS_REFERENCE.md §7](../PATTERNS_REFERENCE.md)

---

## 1. 模式定义

```yaml
pattern_id: vue-dual-stack-migration
name: React→Vue3 双栈迁移
one_liner: React 仓逐块迁移到 Vue3，后端 API + TypeScript 类型共享，保持功能 parity
core_problem: React 仓已有完整实现 + 后端 API + 类型定义，但 Vue 仓只有占位符
key_insight: "后端类型即事实契约"——先锁定真实 API/类型，再写代码，避免臆造字段
```

### 1.1 适用场景

✅ **适合**：
- 后端 API 已稳定，React 仓是参照源，Vue 仓待迁移
- 双栈要求功能 parity，后端 TypeScript 类型共享
- 分批迁移（每次一个功能模块），逐块替换占位组件

❌ **不适合**：
- 后端 API 还在频繁变动（类型契约不稳定）
- 无 React 仓参照源（Vue 仓需从零实现）
- Vue 仓 API/类型模块尚未就绪（需先补 API 层）

---

## 2. 迁移工作流（6 步，必须按序执行）

### 第 1 步：情报收集（**严禁跳过**）

1. **确认 Vue 仓 API 模块就绪**：`<vue_repo>/src/api/**/*.ts` 是否已有目标 API 方法签名
2. **锁定类型契约**：从 `<vue_repo>/src/**/types*.ts` 或 `<vue_repo>/src/types/**/*.ts` 确认目标接口的**真实字段名和类型**（后端命名风格与前端可能不同）
3. **读 React 源文件逻辑**：看 API 调用和数据流向，不照搬 UI 样式（Vue 用 Element Plus）
4. **确认路由守卫元数据**：目标路由是否需要 requireAdmin / requireAuth 等权限门控

### 第 2 步：路由替换（先铺路径）

把占位组件替换为真实懒加载 import，保持原 meta 不变。

### 第 3 步：组件创建（按复杂度拆分）

**拆分决策树**：
- 单页 ≤ 300 行 → 单文件
- 300-600 行 → 主文件 + 自管 mutation 的子组件
- ≥ 600 行 → 主文件（列表+工具栏）+ N 个自管 mutation 的 Drawer/Dialog 子组件

### 第 4 步：类型检查（**零错误才能继续**）

每次写完一个文件立即跑类型检查；报错优先查真实类型定义，其次查 composable 泛型签名。

### 第 5 步：门禁回归

类型检查零错误 → 单元测试全绿 → 构建成功，才能进下一批。

### 第 6 步：运行时验证

导航新路由确认无白屏 + Network 面板检查请求 + Console 面板确认无 error。

---

## 3. 踩坑 Checklist（14 条，跨项目通用）

> **分类说明**：按根因分类，每条给出**通用 pattern** 和 **通用解法方法论**。具体到某个项目时，需对照后端类型定义文件锁定真实字段名。

### 3.1 类型契约类（4 条）

| # | 根因分类 | 通用 pattern | 通用解法方法论 |
|---|---------|------------|--------------|
| T1 | 命名风格不一致 | 后端序列化/反序列化使用 snake_case（如 `is_active`），前端习惯 camelCase（如 `enabled`），直接用前端习惯名导致 TypeScript 类型错误 | 每次引用后端字段前，**先 grep 后端类型定义文件**确认真实字段名，不要凭习惯猜测 |
| T2 | 嵌套数据结构误判 | 后端返回的关联字段是**完整对象数组**（如 `roles: PlatformRoleRecord[]`），前端误以为是简单 ID 数组（`roles: string[]`），解构/遍历时报类型错误 | 看后端类型定义的**嵌套层级**，不要简化；需要简单 ID 时显式 `.map(r => r.id)` 提取 |
| T3 | 请求体字段边界误判 | 后端 Update 类型只暴露**部分可写字段**（如只有 email/status/scenario），前端误以为 Create 类型的所有字段都可用于 Update | grep Update/Edit 专用类型定义，对比 Create 类型，**缺失的字段就是不可更新的边界** |
| T4 | 分页参数命名不一致 | 不同项目后端约定不同：有的 `page_size`、有的 `size`、有的 `limit`；前端照搬另一个项目的参数名导致请求 400 | 查 UsersQuery/ListQuery 专用类型定义，**以定义文件为准**，不要凭经验猜 |

### 3.2 Vue3 框架类（2 条）

| # | 根因分类 | 通用 pattern | 通用解法方法论 |
|---|---------|------------|--------------|
| V1 | 内置组件 v-model 冲突 | Element Plus 的 Drawer/Dialog 等内置组件的 `v-model` 绑定的是组件自身的 `modelValue` prop，当子组件**自定义了同名 visible prop** 时，vite build 阶段编译器报错 | 用显式桥接写法：`:model-value="visible" @update:model-value="(v) => emit('update:visible', v)"` 替代 `v-model` |
| V2 | 编译器类型安全限制 | `defineEmits` 混用对象签名 `{ close: [] }` 和函数签名 `(e: 'xxx') => void` 会导致 TypeScript 类型推断混乱 | 统一用**对象形式**声明 emits，每个事件名一个数组元素 |

### 3.3 Composable 类（useRequest / useMutation，5 条）

| # | 根因分类 | 通用 pattern | 通用解法方法论 |
|---|---------|------------|--------------|
| C1 | 方法名记忆错误 | useRequest 暴露的是 `refetch()` 不是 `refresh()` | 每次用新 composable 时，**先 grep 它的导出 API 签名**，不要凭记忆写 |
| C2 | Ref 嵌套不解包 | useRequest 的 `loading`、useMutation 的 `loading` 都是 `Ref<boolean>`，在 template 直接用没问题，但在 script 里需要 `.value` 或 `computed` 解包 | script 里写 `computed(() => xxxReq.loading.value)`，template 直接用 |
| C3 | onSuccess 参数数量不确定 | 不同 composable 的 `onSuccess` 回调参数数量不同：有的只有 `(data)`，有的有 `(data, vars)`，前端凭 React 版习惯写多参数导致 TS 错误 | **读 composable 源码**或类型定义文件确认回调签名，不要假设与 React 版一致 |
| C4 | ComputedRef 类型不兼容 | useRequest 的 `params` 选项期望的是普通 Ref，但前端传了 `computed(() => ...)`，两者泛型签名不兼容导致 TS 错误 | 不用 params 选项，改用 `watch(trigger, () => req.run(trigger), { immediate: true })` 手动触发 |
| C5 | mutation loading 在 button 上直接用 | template 里 `:loading="mutation.loading"` 会报 TS 错误（loading 是 Ref 不是 boolean） | 统一 `computed(() => mutation.loading.value)` 解包 |

### 3.4 Element Plus 类（2 条）

| # | 根因分类 | 通用 pattern | 通用解法方法论 |
|---|---------|------------|--------------|
| E1 | ElTable slot row 类型 | ElTable 的 `#default="{ row }"` 中 row 的类型是 Element Plus 内部的 DefaultRow（Record<string, unknown>），不是业务类型 | 用 `(row as any).fieldName` 显式断言访问业务字段；**或者**在模板外创建一个 wrapper computed 转业务类型 |
| E2 | ElForm :rules 类型严格 | ElForm 的 :rules prop 期望的类型签名与通用数组推导类型不匹配，TS 报错 | 用 `:rules="rules as any"` 强制转换（这是 Element Plus 社区公认的 workaround） |

### 3.5 构建类（1 条）

| # | 根因分类 | 通用 pattern | 通用解法方法论 |
|---|---------|------------|--------------|
| B1 | vite build 不能动态 import 不存在的文件 | `import('./Xxx.vue').catch(...)` 在 vite build 阶段会尝试解析 Xxx.vue，文件不存在直接报错 | 未实现组件**直接 import 占位组件**，不用 `.catch()` 降级；vite 是静态分析工具，不能做运行时 fallback |

---

## 4. 类型映射速查规则（通用分类）

### 4.1 命名风格映射
```
后端 snake_case → 前端需原样使用，不要臆造 camelCase
  example: is_active（后端）≠ enabled（前端习惯）
  解法：每次 grep 后端类型定义确认真实字段名
```

### 4.2 数据结构映射
```
后端：嵌套对象数组 → 前端：需显式 map 提取或访问嵌套属性
  example: roles: RoleRecord[] → .map(r => r.id) 提取 ID 数组
         description: { value, threshold, pass } → (.description as any).value
  解法：看后端类型嵌套层级，不要简化
```

### 4.3 枚举值映射
```
后端：Union type（如 type Status = 'a' | 'b' | 'c'）→ 前端：需为每个枚举值配置 Tag type / 中文标签
  解法：创建 Record<EnumValue, TagType> 映射表，Tag type 只能是 Element Plus 合法值（success/warning/danger/info/primary）
```

### 4.4 API 契约边界映射
```
后端：Update/Edit 类型字段有限（可写字段白名单）→ 前端：Update 请求体只传可写字段
  example: UpdateUserRequest 没有 tenant_id（后端 create 时绑定不可改）
  解法：对比 Create/Update 两种类型定义，缺失字段就是不可更新的边界
```

---

## 5. 可复用代码模板（泛型占位）

### 模板 A：自管 mutation 的 Drawer 子组件

```vue
<template>
  <el-drawer
    :model-value="visible"
    @update:model-value="(v: boolean) => emit('update:visible', v)"
    :title="`${mode === 'create' ? '创建' : '编辑'} — ${target?.name ?? ''}`"
    size="480px"
    destroy-on-close
    @close="handleClose"
  >
    <!-- 表单内容 -->
    <template #footer>
      <el-button @click="handleClose">取消</el-button>
      <el-button
        type="primary"
        :loading="(mode === 'create' ? createMutation : updateMutation).loading.value"
        @click="handleSubmit"
      >保存</el-button>
    </template>
  </el-drawer>
</template>

<script setup lang="ts">
import { ref, reactive, watch } from 'vue';
import { ElMessage } from 'element-plus';
import { <entity>Api } from '@/api/<module>';        // ← 泛型占位
import { useMutation } from '@/composables/useMutation';
import type { <Entity>Record, Create<Entity>Request, Update<Entity>Request } from '@/types/<module>';

interface Props { visible: boolean; mode: 'create' | 'edit'; target: <Entity>Record | null; }
const props = defineProps<Props>();
const emit = defineEmits<{ 'update:visible': [v: boolean]; success: []; close: [] }>();

const createMutation = useMutation<unknown, Create<Entity>Request>(
  (body) => <entity>Api.create(body),
  { onSuccess: () => { ElMessage.success('创建成功'); emit('success'); handleClose(); },
    onError: (err) => handleError('创建失败', err) },
);
const updateMutation = useMutation<unknown, { id: string; body: Update<Entity>Request }>(
  ({ id, body }) => <entity>Api.update(id, body),
  { onSuccess: () => { ElMessage.success('更新成功'); emit('success'); handleClose(); },
    onError: (err) => handleError('更新失败', err) },
);

function handleClose(): void { emit('update:visible', false); emit('close'); }
function handleError(prefix: string, err: any): void {
  if (err?.response?.status === 403) ElMessage.error(`${prefix}：权限不足（仅 admin）`);
  else ElMessage.error(`${prefix}：${err?.message || '未知错误'}`);
}
</script>
```

### 模板 B：ElDrawer 双向绑定桥接（通用）

```vue
<el-drawer
  :model-value="visible"
  @update:model-value="(v: boolean) => emit('update:visible', v)"
  @close="handleClose"
>
  <!-- Drawer 内容 -->
</el-drawer>

<script setup lang="ts">
interface Props { visible: boolean; }
defineProps<Props>();
const emit = defineEmits<{ 'update:visible': [v: boolean]; close: [] }>();
function handleClose(): void { emit('update:visible', false); emit('close'); }
</script>
```

### 模板 C：useRequest 按需加载 + watch 触发

```typescript
// 按需加载（immediate: false，watch props.xxx 触发）
const detailReq = useRequest<DetailResponse, string>(
  (id) => <entity>Api.getDetail(id),
  { immediate: false },
);
watch(() => props.record?.id, (id) => {
  if (id) detailReq.run(id);
}, { immediate: true });
```

### 模板 D：自管 mutation 的 Dialog 子组件（通用）

```vue
<el-dialog
  :model-value="visible"
  @update:model-value="(v: boolean) => emit('update:visible', v)"
  :title="dialogTitle"
  width="420px"
  destroy-on-close
  :close-on-click-modal="false"
  @close="() => emit('update:visible', false)"
>
  <el-form ref="formRef" :model="form" :rules="rules as any" label-width="90px">
    <!-- 表单项 -->
  </el-form>
  <template #footer>
    <el-button @click="emit('update:visible', false)">取消</el-button>
    <el-button type="primary" :loading="submitMutation.loading.value" @click="handleSubmit">确认</el-button>
  </template>
</el-dialog>
```

---

## 6. 组件通信模式（自管 mutation）

```
父组件只负责：
  - v-model:visible 双向绑定 Drawer/Dialog 开关
  - :target-record 传递目标记录（只读，不做表单校验）
  - @success 监听子组件操作成功 → listReq.refetch() 刷新列表

子组件自管：
  - useRequest（按需加载，watch props.xxx 触发）
  - useMutation（create / update / delete / reset）
  - 表单校验
  - 403 权限门控错误处理
```

**核心原则**：避免 prop drilling（不把 mutation 实例往下传）——每个子组件是**自包含单元**。

---

## 7. 成功标准

- [ ] 所有占位组件替换为真实懒加载 import
- [ ] 类型检查零错误
- [ ] 单元测试全绿（允许 1 个 flaky 重跑通过）
- [ ] 构建成功（警告可忽略）
- [ ] React/Vue 双栈 title/icon/parentPath 元数据完全一致
- [ ] 关键 API/类型字段与后端定义文件**真实字段名匹配**（无臆造）

---

## 8. 来源案例（参考，非规范正文）

本模式的 14 条踩坑经验来自**真实项目迁移**的实测验证。以下是来源批次记录，用于说明 pattern 已被实战检验过，但**规范正文不依赖这些项目专属细节**。

| 批次 | 迁移范围 | 交付组件数 | 踩坑命中数 |
|---|---|---|---|
| B5 | 审计/红队/合规/评测 | 10 | 3 |
| B6 | 引擎配置/LoRA/灰度发布 | 3 | 4 |
| B7 | 系统管理/用户管理/个人信息 | 11 | 7 |
| **合计** | | **24** | **14（全部覆盖）** |

> **重要**：规范正文中的类型名/字段名是**抽象描述**，不是上述具体项目的真实类型。每个新项目迁移时，必须重新 grep 该项目的后端类型定义文件锁定真实字段名。
