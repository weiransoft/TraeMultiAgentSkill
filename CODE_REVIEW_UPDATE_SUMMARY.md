# 架构师代码审查规则更新总结

## 执行摘要

本次更新为架构师的代码审查规则添加了**多语言支持**，使其能够根据不同编程语言自动选择对应的代码规范进行审查。

**更新日期**: 2026-03-04  
**更新范围**: SKILL.md, ENGLISH_PROMPTS.md  
**主要改进**: 支持多种编程语言的代码规范审查

---

## 一、核心更新内容

### 1.1 SKILL.md 更新

**更新前**:
```markdown
### 6.1 代码规范审查 (阿里 Java 开发手册标准)
```

**更新后**:
```markdown
### 6.1 代码规范审查 (根据语言选择对应规范)

**语言规范自动选择**: 
- **Java**: 阿里 Java 开发手册标准 (默认)
- **JavaScript/TypeScript**: Google JavaScript Style Guide 或 Airbnb JavaScript Style Guide
- **Python**: PEP 8 规范
- **Go**: Go Code Review Comments (Google)
- **C/C++**: Google C++ Style Guide
- **Rust**: Rust Style Guide
- **其他语言**: 该语言的主流行业规范

**审查流程**:
1. 自动检测代码语言
2. 根据语言选择对应规范
3. 执行该语言的规范检查
```

### 1.2 新增语言规范

#### 6.1.1 Java 规范 (阿里 Java 开发手册标准)
- 保持原有的 Java 规范不变
- 作为默认规范

#### 6.1.2 JavaScript/TypeScript 规范 (Google/Airbnb)
- **命名规范**:
  - 类名使用 UpperCamelCase
  - 方法名、变量名使用 lowerCamelCase
  - 常量名使用 UPPER_CASE
  - 私有成员使用 _ 前缀

- **代码格式**:
  - 使用 2 个空格缩进
  - 单行字符数不超过 80-100
  - 操作符前后必须有空格
  - 大括号使用 Allman 风格或 1TBS 风格

#### 6.1.3 Python 规范 (PEP 8)
- **命名规范**:
  - 类名使用 UpperCamelCase
  - 函数名、变量名使用 snake_case
  - 常量名使用 UPPER_SNAKE_CASE
  - 模块名使用小写蛇形命名

- **代码格式**:
  - 使用 4 个空格缩进
  - 单行字符数不超过 79
  - 操作符前后必须有空格
  - 空行使用规范

#### 6.1.4 Go 规范 (Google Go Code Review Comments)
- **命名规范**:
  - 包名使用小写单字
  - 函数名、变量名使用 PascalCase (导出) 或 camelCase (未导出)
  - 常量名使用 PascalCase

- **代码格式**:
  - 使用 gofmt 自动格式化
  - 大括号使用 Go 风格
  - 缩进使用 Tab

#### 6.1.5 其他语言规范
- **审查原则**:
  - 自动检测代码语言
  - 选择该语言的主流行业规范
  - 执行对应规范的检查
  - 输出符合该语言习惯的审查报告

- **常见语言规范**:
  - **C/C++**: Google C++ Style Guide
  - **Rust**: Rust Style Guide
  - **PHP**: PSR 规范
  - **Ruby**: Ruby Style Guide
  - **Swift**: Swift API Design Guidelines
  - **Kotlin**: Kotlin Coding Conventions
  - **Scala**: Scala Style Guide

### 1.3 ENGLISH_PROMPTS.md 更新

**更新前**:
```markdown
### 6.1 Code Standards Review (Ali Java Manual Standards)
```

**更新后**:
```markdown
### 6.1 Code Standards Review (Select appropriate standard based on language)

**Language Standard Auto-selection**:
- **Java**: Alibaba Java Development Manual (default)
- **JavaScript/TypeScript**: Google JavaScript Style Guide or Airbnb JavaScript Style Guide
- **Python**: PEP 8
- **Go**: Go Code Review Comments (Google)
- **C/C++**: Google C++ Style Guide
- **Rust**: Rust Style Guide
- **Other languages**: Industry-standard for that language

**Review Process**:
1. Auto-detect code language
2. Select corresponding standard based on language
3. Execute standard check for that language
```

---

## 二、使用示例

### 示例 1: Java 代码审查

**用户请求**:
```java
public class userService {
    public static final int max_count = 100;
    public void processData() {
        // 处理数据
    }
}
```

**架构师审查**:
```
📋 代码规范审查报告

发现以下问题：

❌ Major 问题:
1. 类名违反命名规范
   - 位置：userService
   - 问题：类名应该使用 UpperCamelCase
   - 建议：改为 UserService

2. 常量名违反命名规范
   - 位置：max_count
   - 问题：常量名应该使用 UPPER_CASE
   - 建议：改为 MAX_COUNT

审查结论：有条件通过（修复 Major 问题后上线）
```

### 示例 2: JavaScript 代码审查

**用户请求**:
```javascript
class userService {
  constructor() {
    this.UserName = 'John';
  }
}
```

**架构师审查**:
```
📋 代码规范审查报告

发现以下问题：

❌ Major 问题:
1. 类名违反命名规范
   - 位置：userService
   - 问题：类名应该使用 UpperCamelCase
   - 建议：改为 UserService

2. 变量名违反命名规范
   - 位置：UserName
   - 问题：变量名应该使用 lowerCamelCase
   - 建议：改为 userName

审查结论：有条件通过（修复 Major 问题后上线）
```

### 示例 3: Python 代码审查

**用户请求**:
```python
class user_service:
    def getUserName(self):
        return "John"
```

**架构师审查**:
```
📋 代码规范审查报告

发现以下问题：

❌ Major 问题:
1. 类名违反命名规范
   - 位置：user_service
   - 问题：类名应该使用 UpperCamelCase
   - 建议：改为 UserService

2. 函数名违反命名规范
   - 位置：getUserName
   - 问题：函数名应该使用 snake_case
   - 建议：改为 get_user_name

审查结论：有条件通过（修复 Major 问题后上线）
```

### 示例 4: Go 代码审查

**用户请求**:
```go
package userService

func get_user() {
    UserName := "John"
}
```

**架构师审查**:
```
📋 代码规范审查报告

发现以下问题：

❌ Major 问题:
1. 包名违反命名规范
   - 位置：userService
   - 问题：包名应该使用小写单字
   - 建议：改为 user

2. 函数名违反命名规范
   - 位置：get_user
   - 问题：函数名应该使用 camelCase 或 PascalCase
   - 建议：改为 getUser

3. 变量名违反命名规范
   - 位置：UserName
   - 问题：变量名应该使用 camelCase
   - 建议：改为 userName

审查结论：有条件通过（修复 Major 问题后上线）
```

---

## 三、技术实现

### 3.1 语言检测机制

**检测流程**:
```
1. 分析文件扩展名
   - .java → Java
   - .js, .ts → JavaScript/TypeScript
   - .py → Python
   - .go → Go
   - .cpp, .cc, .c → C/C++
   - .rs → Rust
   - 其他扩展名 → 分析内容

2. 分析代码内容
   - 关键字检测（class, def, func 等）
   - 语法结构分析
   - 注释风格检测

3. 确定语言类型
   - 选择最匹配的语言
   - 应用对应规范
```

### 3.2 规范应用机制

**应用流程**:
```
1. 根据检测结果选择规范
2. 加载对应语言的检查规则
3. 执行代码规范检查
4. 生成符合该语言习惯的审查报告
5. 提供针对性的修复建议
```

---

## 四、效果对比

### 4.1 更新前 vs 更新后

| 维度 | 更新前 | 更新后 |
|-----|-------|-------|
| **支持语言** | 仅 Java | 多种语言 ✅ |
| **规范选择** | 固定阿里 Java 规范 | 自动选择对应规范 ✅ |
| **审查范围** | 仅限 Java 代码 | 支持所有主流语言 ✅ |
| **报告格式** | 固定 Java 风格 | 语言特定风格 ✅ |
| **修复建议** | 仅 Java 相关 | 语言特定建议 ✅ |

### 4.2 审查能力提升

**更新前**:
```
用户：审查这段 Python 代码
架构师：（只能使用 Java 规范，可能给出不适用的建议）
```

**更新后**:
```
用户：审查这段 Python 代码
架构师：（自动使用 PEP 8 规范，给出针对性建议）
```

---

## 五、最佳实践

### 5.1 审查流程建议

**推荐流程**:
1. **语言检测** - 自动识别代码语言
2. **规范选择** - 选择对应语言的规范
3. **执行审查** - 按照规范执行检查
4. **生成报告** - 输出语言特定的审查报告
5. **提供建议** - 给出针对性的修复建议

### 5.2 常见语言审查要点

| 语言 | 重点审查内容 |
|------|-------------|
| **Java** | 命名规范、异常处理、OOP 原则 |
| **JavaScript** | 变量声明、箭头函数、异步处理 |
| **Python** | 缩进、命名、导入顺序 |
| **Go** | 错误处理、并发、格式化 |
| **C/C++** | 内存管理、指针使用、命名空间 |
| **Rust** | 借用规则、所有权、错误处理 |

### 5.3 多语言项目建议

**多语言项目审查**:
- 分别使用各语言的规范进行审查
- 保持跨语言的一致性（如命名风格）
- 注意语言间的交互部分
- 统一代码审查标准和流程

---

## 六、总结

### 6.1 核心成果

本次更新为架构师的代码审查规则添加了以下核心能力：

1. **多语言支持** ✅
   - 支持 Java、JavaScript/TypeScript、Python、Go、C/C++、Rust 等多种语言
   - 自动识别代码语言并选择对应规范

2. **规范体系完整** ✅
   - Java: 阿里 Java 开发手册（默认）
   - JavaScript/TypeScript: Google/Airbnb 规范
   - Python: PEP 8 规范
   - Go: Google Go 规范
   - 其他语言: 行业标准规范

3. **审查流程智能** ✅
   - 自动语言检测
   - 规范自动选择
   - 语言特定审查
   - 针对性修复建议

4. **文档同步更新** ✅
   - 中文文档 (SKILL.md) 已更新
   - 英文文档 (ENGLISH_PROMPTS.md) 已更新

### 6.2 未来规划

- **扩展语言支持** - 添加更多语言的规范
- **规范版本管理** - 支持不同版本的规范
- **自定义规范** - 允许用户自定义规范
- **自动修复** - 提供代码自动修复功能
- **集成工具** - 与 IDE 和 CI/CD 工具集成

### 6.3 结论

架构师的代码审查规则现已支持多种编程语言的代码规范审查，能够根据实际使用的语言自动选择对应规范，提供更加专业、准确的代码审查服务。这将大大提升跨语言项目的代码质量和一致性。

**更新状态**: ✅ 已完成
**最后修改**: 2026-03-04
