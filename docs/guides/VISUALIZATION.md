# 可视化功能详细说明

## 3D 代码地图可视化

基于 Three.js 的交互式代码结构可视化。

### 功能特性

- **Three.js 3D 引擎**：完整 3D 场景渲染，支持拖拽旋转、滚轮缩放
- **前后端分层展示**：前端层（蓝色）、后端层（红色）、共享层（灰色）
- **真实调用链路**：节点间的连线连接到实际代码节点
- **动态流动效果**：边使用虚线动画 + 流动粒子
- **深色/浅色主题**：一键切换

### JSON v2.0 数据结构

生成命令：
```bash
python3 scripts/code_map_generator_v2.py /path/to/project --visual
```

输出文件：`{project-name}-VISUAL-MAP.json`

```json
{
  "version": "2.0",
  "project": {
    "name": "项目名",
    "frontend": { "layers": ["frontend-ui", "frontend-service", "frontend-store"] },
    "backend": { "layers": ["api", "service", "domain", "data", "middleware"] }
  },
  "layers": [
    { "id": "frontend-ui", "name": "前端UI层", "side": "frontend" },
    { "id": "api", "name": "API层", "side": "backend" },
    { "id": "service", "name": "业务逻辑层", "side": "backend" }
  ],
  "nodes": [
    {
      "id": "file:path/to/file.py",
      "type": "file",
      "name": "文件名",
      "layerId": "service",
      "side": "backend",
      "calls": ["file:other.py"],
      "calledBy": []
    }
  ],
  "edges": [
    {
      "id": "e1",
      "source": "file:a.py",
      "target": "file:b.py",
      "type": "calls",
      "protocol": "local"
    }
  ]
}
```

**节点类型**:
- `module`: 模块节点
- `file`: 文件节点
- `class`: 类节点
- `function`: 函数/方法节点

**边类型**:
- `calls`: 方法调用
- `imports`: 导入关系
- `http`: HTTP API 调用（前后端通信）
- `layer-calls`: 层级间典型调用

**交互功能**:
- 点击展开/折叠模块、类、函数
- 双击函数高亮调用链路
- 调用链路面板展示关键流程
- 点击节点显示详情（层级、端、调用关系）

## 任务可视化页面

实时展现各角色任务状态、进度、依赖关系、交接过程。

### 功能特性

- **概览统计面板**：总任务数、待开始、进行中、已完成、被阻塞
- **角色任务卡片**：任务列表、状态、进度
- **任务依赖关系**：显示任务间的依赖和阻塞关系
- **任务交接记录时间线**：记录角色间的任务交接过程
- **Canvas 绘制协同关系图**：展示角色间的协作网络
- **定时刷新机制**：自动从 JSON 文件加载最新任务数据（默认30秒）

### 交互功能

- 点击任务卡片查看详情
- 查看任务依赖和交接记录
- 实时更新任务状态

### Workspace 安装说明

安装 skill 后，可视化文件会自动符号链接到 `~/.trae/skills/docs/` 目录，在任意 workspace 中都可直接打开使用。
