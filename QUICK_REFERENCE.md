# Trae Multi-Agent 多语言快速参考

## 语言切换

| 用户语言 | AI 响应 |
|---------|--------|
| 中文 | 中文 |
| English | English |
| 混合 | 首次使用的语言 |
| 明确要求 | 指定语言 |

## 角色映射

| 中文 | English |
|------|---------|
| 架构师 | Architect |
| 产品经理 | Product Manager |
| 测试专家 | Test Expert |
| 独立开发者 | Solo Coder |

## 常用短语

| 中文 | English |
|------|---------|
| 已接收任务 | Task received |
| 开始分析 | Starting analysis |
| 进度 | Progress |
| 已完成 | Completed |
| 进行中 | In progress |
| 待处理 | Pending |
| 被阻塞 | Blocked |

## 既有系统理解 SU 命令速查 (v2.9)

| 阶段 | 命令 | 说明 |
|------|------|------|
| 采集 | `python3 scripts/system_understanding.py --config config.json --skip-llm-phase` | 登录 + BFS 遍历 + DB/Redis 只读内省，产出骨架文档 |
| 回填 | 宿主 LLM 按 `docs/spec/role-prompts/su-llm-backfill.md` 契约写 `understanding.json` 的 findings 段 | 脚本不参与语义结论 |
| 渲染 | `python3 scripts/system_understanding.py --out <前次输出根目录> --system-id <id> --render-only` | 校验 findings 并重渲染全部产物，不启动浏览器/不连库 |

**关键参数**：

| 参数 | 说明 |
|------|------|
| `--max-pages N` | 页面预算上限（默认 100） |
| `--skip-llm-phase` | 只跑确定性采集，产出"待 LLM 语义回填"骨架 |
| `--render-only` | 仅校验 findings + 幂等重渲染（失败退出码 2） |
| `--storage-state PATH` | 人工已登录态旁路注入（验证码/2FA 场景） |
| `--resume` / `--fresh` | 断点续跑（默认）/ 归档旧状态后重跑 |

**退出码**：0 成功；2 配置/findings 校验错误；3 系统不可达；4 登录失败；5 playwright 缺失；130 SIGINT（可 `--resume`）

### 专家详说 SFD 速查 (v2.9.1)

| 阶段 | 命令 | 说明 |
|------|------|------|
| 详说准备 | `python3 scripts/system_understanding.py --out docs/system-understanding --system-id <id> --detailed-doc` | 前置校验（锚定 run_id、findings 双源一致）→ 五视角素材包 + 8 节大纲骨架，零浏览器/零连库 |
| 专家撰写 | 宿主 LLM 派发五专家（`docs/spec/role-prompts/su-detailed-*.md`），产出 `detailed/sections/0N-xxx.doc.md` | 架构师/产品经理/走读开发/UI/测试 |
| 装配终稿 | `python3 scripts/system_understanding.py --out docs/system-understanding --system-id <id> --assemble` | 产出 `SYSTEM_FUNCTION_DOC.md` + `assembly-report.json`（终稿保护需 `--force`） |

**产物路径**：`<out>/<system_id>/detailed/inputs/`（素材包）、`detailed/sections/`（分节稿）、`SYSTEM_FUNCTION_DOC.md`（终稿）、`assembly-report.json`（装配报告）

**约束**：详说模式拒绝与 `--fresh` / `--resume` / `--skip-llm-phase` 组合（退出码 2）

📄 详细指南：[docs/guides/SYSTEM_UNDERSTANDING_GUIDE.md](docs/guides/SYSTEM_UNDERSTANDING_GUIDE.md)

## 示例

### 中文
```
用户：设计系统架构
AI: 📋 已接收任务，开始分析...
```

### English
```
User: Design system architecture
AI: 📋 Task received, starting analysis...
```

## 代码注释

- 中文注释代码 → 新增中文注释
- English comments → New English comments
- 无明确偏好 → 默认英文

## 文档

- 📄 [MULTILINGUAL_GUIDE.md](MULTILINGUAL_GUIDE.md) - 详细使用指南
- 📄 [ENGLISH_PROMPTS.md](ENGLISH_PROMPTS.md) - 英文 Prompt 文档
- 📄 [MULTILINGUAL_UPDATE_SUMMARY.md](MULTILINGUAL_UPDATE_SUMMARY.md) - 更新总结
