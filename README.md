# 🌵 仙人掌 Agent（XianRenZhang Agent）

> 用浏览器 + 免费网页 AI，跑通一套完整的 Agent 架构。
> 不花钱、不要显卡，一个网页登录就拥有 Agent 自由。

---

## 为什么做这件事

在 AI 时代，获取算力基本只有三条路：

| 方式 | 门槛 | 成本 | 适合谁 |
|------|------|------|--------|
| **网页聊天** | 注册即用 | 免费 / 极低 | 大多数人 |
| **API 调用** | 企业级接入 | 高（按 token 计费） | 开发者 / 企业 |
| **本地部署** | 显卡 + 内存 | 很高 | 研究人员 / 重度用户 |

> 大多数人想体验 Agent，只能走后两条路，算力成本天然劝退。

**仙人掌 Agent 的思路：把第一条路用到极致。**

用浏览器自动化驱动网页端 AI（DeepSeek / 通义 / 豆包 / 元宝），让网页模型充当「大脑」，本地 Agent 框架充当「手脚」——模型下发指令，框架在本地执行命令、写入文件、操控浏览器、调度子代理并行工作。

**零 API 费用，零显卡要求，一条网络就能跑。**

---

## 架构一句话

```
浏览器（Playwright 控制）
   ↓  发送指令
网页 AI（DeepSeek / 通义 / 豆包 / 元宝 …）← 思考、决策
   ↓  返回工具调用指令（@@@@ 协议）
本地 Agent 框架（commander.py）← 执行：写文件、搜索、跑命令、调度子代理
   ↓
工具结果回传 → 网页 AI 继续思考 → 循环直到 done
```

核心逻辑极简：**浏览器控制网页 AI，AI 返回指令，本地框架执行。**

但工程实现并不轻松——约 **1.8 万行核心代码**（83 个文件），覆盖多平台 DOM 适配、并发子代理隔离、断线重连、文件写入截断处理、记忆持久化。

**"简单"说的是原理，不是代码量。**

---

## 定位声明（重要）

> **这是一个教育 / 演示模型，不是生产工具。**

它要证明的是一件事：**Agent 架构没有想象中那么复杂，一个浏览器 + 一个本地执行器就能跑通。**

它不是让你拿来做业务开发的，而是让你**看清楚 AI 到底怎么通过工具调用控制你的电脑、写入你的文件、调度多个子代理并行工作**。

### 为什么只能当教育用途

网页端 AI 模型有两层能力损耗：

1. **网页模型本身弱于 API 版**
   - 上下文窗口被压缩，长对话容易失忆
   - 推理深度不如 API 满血版

2. **交互协议有额外摩擦**
   - 浏览器 DOM 解析的精度损失
   - 单次文件写入长度受限
   - 多轮对话中指令漂移

所以它的定位是：**低成本、低门槛地让每个人「看见」Agent 是怎么运转的。**

### 适合谁

- 想搞懂 Agent 原理但不想花几千块买显卡 / 充 API 的人
- 学生 / 教师：一堂课就能跑完、能改、能断点调试
- 想向别人演示「AI 怎么操控电脑写文件」的开发者
- 纯粹好奇「免费网页 AI 到底能走多远」的极客

---

## 核心功能

### 工具系统（20+ 内置工具）

| 类别 | 工具 | 说明 |
|------|------|------|
| 文件操作 | `file_write` / `file_read` / `file_list` / `dir_create` / `file_delete` / `file_edit` | 读写文件，正则/字符串替换编辑 |
| 搜索 | `grep` / `web_fetch` | 按正则搜索本地文件；抓取网页正文 |
| Shell | `shell_exec` | 执行本地命令 |
| 浏览器 | `browser_navigate` / `browser_click` / `browser_fill` / `browser_screenshot` / `browser_search` | 浏览器自动化 |
| 对话控制 | `continue` / `done` / `ask` | 继续思考 / 结束对话 / 向用户提问 |
| 记忆 | `remember` / `recall` / `summarize` / `list_summaries` / `list_tasks` | 摘要与历史检索 |

### 多平台 LLM 支持（配置驱动）

- 内置 **12 个网页 AI**：DeepSeek（默认）、通义千问、豆包、元宝、ChatGPT、Gemini、Kimi、Claude、文心一言、智谱清言、Grok、Perplexity
- 平台注册表：`agent_core/platforms.json`（配置驱动，新增平台**只改 JSON，零代码**）
- GUI 侧边栏按钮、命令行切换均由 `/platforms` 接口动态生成
- 支持 **Ollama** 本地模型（如 qwen3.5:0.8b），不走浏览器

### 子代理系统

```
总工程师（Commander）
  ├── 任务规划、工具调度、结果整合
  ├── 浏览器子代理 A（独立进程，共享登录态）
  ├── 浏览器子代理 B
  └── 硬限制：子代理不能再调子代理（depth=1）
```

- 子代理与母代理共享浏览器 context，复用 cookie / 登录状态
- 独立进程运行，通过文件 / 通知队列通信，互不阻塞

### 记忆系统

- key-value JSON 持久化存储
- 每 10 轮自动触发摘要
- `recall 任务名` 检索历史记忆
- 每次新对话自动创建 `~/XianRenZhang_tasks/任务N/` 文件夹，文件与记忆自动归档

### GUI 图形界面

- PySide6 无边框桌面窗口 + QtWebEngine 加载 `gui.html`
- 深色主题，消息通过 SSE 实时流式显示
- 侧边栏平台按钮由 `/platforms` 动态渲染
- 支持多子代理同时工作、消息排队插话、用户中断

---

## 协议格式（不可变）

```
@@@@
{"type":"tool_call","tool":"file_write","params":{"path":"hello.txt","content":"你好"},"id":"UUID"}
@@@@
```

- 双层 `@@@@` 包裹，兼容单引号 / 未加引号 key / Python 字面量 / 尾随逗号等 AI 产出的各种不规范 JSON
- 脚本自动修复，不依赖 AI 重新回答

---

## 目录结构

```
XianRenZhangAgent/
├── terminal.py              # 主入口（HTTP 服务 + 事件循环，端口 8888）
├── desktop_app.py           # PySide6 桌面壳（无边框 + QtWebEngine）
├── gui.html                 # Web 控制面板（SSE 消息流 + 平台侧边栏）
├── buffer_store.py          # 文本缓冲区（长内容写入）
├── launcher.py              # 便携版启动器
├── 启动仙人掌.bat           # 一键启动（自动探测 Python，支持 XRZ_PYTHON 覆盖）
├── 安装依赖.bat             # 一键安装 playwright + Chromium（不写 C 盘）
├── requirements.txt
├── SPEC.md                  # 项目规格书（完整功能文档）
│
├── agent_core/
│   ├── protocol.py          # 协议解析器（@@@@ 双层包裹 + JSON 自动修复）
│   ├── commander.py         # 总工程师：工具编排 + 子代理调度 + 事件流
│   ├── browser.py           # Playwright 浏览器管理（持久化登录态）
│   ├── session.py           # 会话管理（历史追溯）
│   ├── platform_browser.py  # 平台适配器（读 platforms.json，新增平台零代码）
│   ├── multi_browser.py     # 多平台浏览器管理器
│   ├── subagent.py          # 子代理基类（depth=1 硬限制）
│   ├── subagent_manager.py  # 子代理管理器（共享登录态 + 并发隔离）
│   ├── subagent_runner.py   # 子代理独立进程运行器
│   ├── memory_manager.py    # 记忆管理（key-value JSON 持久化）
│   ├── pptx_builder.py      # PPT 生成引擎
│   ├── auto_update.py       # 自动更新
│   ├── platforms.json       # 12 个网页 AI 平台注册表
│   ├── user_paths.py        # 用户路径管理（可迁移）
│   └── tools/               # 工具注册
│
├── build_dist.py            # 便携版构建脚本
└── build_tools/             # PyInstaller 构建工具链
```

---

## 快速开始

### 方式一：源码启动（需要本地 Python 环境）

```bash
# 1. 安装依赖（自动下载 Chromium 到项目目录，不写 C 盘）
双击  安装依赖.bat

# 2. 启动（自动探测装有 playwright+PySide6 的 Python，可用 XRZ_PYTHON 覆盖）
双击  启动仙人掌.bat
```

等 GUI 窗口弹出后，在侧边栏选平台，扫码登录对应网页 AI，即可开始对话。

### 方式二：便携版（无需 Python 环境）

下载仓库中的 **`XianRenZhangAgent_可迁移版_*.zip`**（Git LFS，约 1.3GB），解压到任意目录，双击启动即可。
内置完整 Python 运行时，**无需安装任何东西**，适用于没有 Python 环境的 Windows 电脑。

> 便携版 zip 通过 Git LFS 存储，首次 clone 需安装 [git-lfs](https://git-lfs.com)：
> ```bash
> git lfs install && git clone <repo-url>
> ```

---

## 已知限制

- 网页端模型上下文窗口远小于 API 版，长任务需要分段
- 文件写入单次有长度限制，超长内容需拆分多次写入
- 不同网页平台 DOM 结构不同，`platforms.json` 需按平台配置对应 CSS 选择器
- 部分平台（元宝 / Gemini）登录检测较不稳定，需要人工确认

---

## 许可证

**AGPL v3**（最严格的开源协议）

- 所有衍生作品必须开源
- 通过网络提供服务的 SaaS 也必须开源
- 不可用于闭源商业产品而不发布源码

---

> 别人用 API 跑 Agent，我们用浏览器跑 Agent。
> 算力从「买」变成「借」，门槛从「企业级」降到「会注册网页」。
> 目的不是替代 API，是让你**看见**这件事有多简单。
