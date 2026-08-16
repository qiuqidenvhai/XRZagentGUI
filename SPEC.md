# 仙人掌 Agent — 项目规格书

## 项目概述
**名称**：仙人掌 Agent（XianRenZhang Agent）  
**核心定位**：本地桌面 AI Agent，支持多平台 LLM + 浏览器自动化 + 子代理 + 记忆管理  
**技术栈**：Python 3.13 + Playwright + PySide6（QtWebEngine）+ 多平台 LLM  
**协议**：`@@@@` 双层包裹 JSON（与历史版本完全兼容，不可变）  
**平台注册表**：`agent_core/platforms.json`（配置驱动，新增网页 AI 零代码）

## 核心能力

### 1. 工具系统
| 工具 | 说明 |
|------|------|
| file_write(path, content) | 写入文件 |
| file_read(path) | 读取文件 |
| file_list(path) | 列出目录 |
| dir_create(path) | 创建目录 |
| file_delete(path) | 删除文件 |
| file_edit(path, old, new) | 编辑文件：字符串/正则替换（对标 opencode 行内编辑）|
| grep(pattern, path, glob) | 按正则搜索文件内容（代码搜索，对标 opencode/rg）|
| web_fetch(url) | 抓取网页 URL 返回可读正文（对标 workbuddy 研究能力）|
| shell_exec(command) | 执行 Shell 命令（用 #"..."# 包裹）|
| browser_navigate(url) | 浏览器导航 |
| browser_click(selector) | 点击元素 |
| browser_fill(selector, text) | 填写表单 |
| browser_screenshot(path) | 截图 |
| browser_search(query, engine) | 网络搜索 |
| continue | 继续上一步思考 |
| remember | 触发记忆摘要 |
| recall(task_name) | 检索历史记忆 |
| summarize(summary, decisions, tasks) | 保存摘要 |
| list_summaries | 列出所有摘要 |
| list_tasks | 列出所有任务 |
| done(message) | 结束对话 |
| ask(question) | 向用户提问 |
| tool_list | 显示工具列表 |

### 2. 子代理系统
- **总工程师**（Commander）：负责任务规划、工具调度、结果整合
- **浏览器子代理**（BrowserSubAgent）：执行具体浏览器操作
- 硬限制：子代理不能调用子代理（depth=1）
- 主 Agent 可调度 browser 子代理完成复杂浏览任务
- 子代理与母代理共享浏览器 context，复用 cookie/登录状态
- 独立进程模式：子代理以独立进程运行，通过文件/通知队列与母代理通信

### 3. 多平台 LLM 支持（配置化注册表）
- **内置 12 个网页 AI**：DeepSeek（默认）、通义千问、豆包、元宝、ChatGPT、Gemini、Kimi、Claude、文心一言、智谱清言、Grok、Perplexity
- 平台清单来自 `agent_core/platforms.json`（含 `<数据目录>/platforms.user.json` 用户覆盖）
- 新增网页 AI：**只改 JSON（URL + CSS 选择器 + 登录文案），无需改代码**
- GUI 侧边栏按钮、命令行切换、系统提示词均由 `/platforms` 接口动态生成
- **Ollama**：本地模型（如 qwen3.5:0.8b），不走浏览器

多平台通过 `multi_browser.py` + `platform_browser.py` 管理，复用母代理浏览器 context，为每个平台创建独立 page。`login_texts` 字段让通用登录检测支持 ChatGPT("Log in")/Claude("Sign in") 等非中文入口。

### 4. 记忆系统
- 使用 key-value 存储系统（JSON 文件持久化）
- 支持 `save()`, `search()`, `summarize()`, `list()`, `recall()` 操作
- 每 10 轮自动触发摘要提醒（可配置）
- 摘要格式：关键决策 + 待办任务 + 最近对话
- 持久化到 `~/XianRenZhang_tasks/{任务N}/memory/`
- `recall 任务名` 检索历史记忆

### 5. 对话文件夹
- 每次新建对话（`new` 命令）创建新文件夹：任务一、任务二、任务三...
- 文件夹结构：
  ```
  ~/XianRenZhang_tasks/
    任务一/
      memory/      # 记忆摘要
      files/       # 生成的文件
    任务二/
      ...
  ```

### 6. GUI 图形界面
- 原生桌面窗口（PySide6 无边框 + 内嵌 QtWebEngine 加载 `gui.html`）
- 标题栏左侧显示仙人掌图标（`__xianrenzhang_icon.png`，缺失则回退 🌵）
- 深色主题，消息实时显示（SSE 事件流）
- 侧边栏平台按钮由 `/platforms` 动态渲染（支持用户自定义平台）
- 通过 HTTP 服务器（端口 **8888**）提供后端 API 与 Web 界面

### 7. 会话历史追溯
- 每次对话会生成唯一的 DeepSeek URL，可通过该 URL 追溯历史
- 使用 `save_conversation()` 保存当前对话上下文
- 使用 `load_conversation(url=...)` 加载历史对话

## 协议格式
```
@@@@
{"type":"tool_call","tool":"工具名","params":{...},"id":"UUID"}
@@@@
```

## 启动方式
1. （首次）双击 `安装依赖.bat` —— 自动把 Chromium 下载到项目内 `xrz_data/playwright_browsers`（不写 C 盘）
2. 双击 `启动仙人掌.bat` —— 自动探测装有 playwright+PySide6 的 Python（可用 `XRZ_PYTHON` 覆盖），拉起桌面 GUI + 后端
3. 待 GUI 弹出后，在侧边栏选平台、扫码登录对应网页 AI，即可对话

> 启动器不再硬编码任何 Python 路径，可在任意装有依赖的机器上迁移运行。

## 目录结构
```
D:\软件\XianRenZhangAgent\
  terminal.py              # 主入口（HTTP 服务 + 事件循环，端口 8888）
  启动仙人掌.bat            # 启动批处理（自动探测 Python，支持 XRZ_PYTHON）
  安装依赖.bat              # 一键安装 playwright + 下载 Chromium（落到项目目录）
  requirements.txt          # 依赖清单
  agent_core/
    __init__.py
    protocol.py            # 协议解析器（@@@@ 双层包裹，不可变）
    platforms.json         # 网页 AI 平台注册表（配置驱动，新增平台零代码）
    browser.py             # 浏览器管理（多平台深度思考支持）
    session.py             # 会话管理（历史持久化、追溯）
    commander.py           # 主控制器（多平台 LLM 适配 + 工具编排）
    subagent.py            # 子代理系统
    subagent_manager.py    # 子代理管理器（凭据共享）
    memory_manager.py      # 记忆管理（key-value 存储）
    multi_browser.py       # 多平台浏览器管理器
    platform_browser.py    # 平台适配器（URL+选择器+登录文案，读 platforms.json）
    tools/__init__.py      # 工具清单
  gui.html                  # 桌面 GUI 内嵌页面（侧边栏平台按钮由 /platforms 动态渲染）
  __xianrenzhang_icon.png   # 窗口/标题栏仙人掌图标（256×256）
  __xianrenzhang_icon.ico   # 任务栏图标
```

## 当前状态
- ✅ 核心架构完成
- ✅ 浏览器启动 + DeepSeek 登录
- ✅ 工具注册 + 协议解析（@@@@ 协议不变）
- ✅ continue / remember / recall / summarize 指令
- ✅ 子代理浏览器系统
- ✅ 任务文件夹自动创建
- ✅ 记忆摘要持久化（key-value 存储）
- ✅ 深度思考功能修复（多平台支持）
- ✅ 多平台 LLM 支持（multi_browser.py + platform_browser.py）
- ✅ GUI 图形界面（PySide6 + QtWebEngine，平台按钮动态渲染）
- ✅ 配置化平台注册表（platforms.json，内置 12 个网页 AI，新增零代码）
- ✅ 新增工具：file_edit / grep / web_fetch（对标 opencode / workbuddy）
- ✅ 可移植启动器（启动仙人掌.bat / 安装依赖.bat，自动探测 Python）
- ✅ 修复并端到端验证：.bat 启动 + 图标 + /health + /platforms + 新工具实跑
- ⚠️ 完整端到端测试待人工配合登录各网页 AI（尤其元宝/Gemini 登录检测）
