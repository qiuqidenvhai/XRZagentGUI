# 仙人掌 Agent GUI (XRZagentGUI)

本地桌面 AI Agent 应用，支持多平台 LLM + 浏览器自动化 + 子代理 + 记忆管理。

## 技术栈

- **Python 3.10+** + **Playwright** + **PySide6**
- 多平台 LLM：DeepSeek / 通义千问 / 豆包 / 元宝 / ChatGPT / Gemini / Kimi / Claude / 文心一言 / 智谱清言 / Grok / Perplexity / Ollama（可配置扩展）

## 核心能力

| 功能 | 说明 |
|------|------|
| 🤖 工具系统 | 文件读写/编辑(`file_edit`)、统一 diff 补丁(`apply_patch`)、代码搜索(`grep`)、网页抓取(`web_fetch`)、Shell 执行、浏览器操作等（对标 opencode / codex / workbuddy）；工具结果在 GUI 中可折叠展示，diff 自动着色 |
| 🌐 多平台切换 | 12 个内置网页 AI，侧边栏按钮由 `/platforms` 动态生成 |
| 👶 子代理系统 | 工程师分工协作，共享浏览器上下文 |
| 🧠 记忆管理 | Key-value 持久化存储，自动摘要 |
| 💬 历史追溯 | URL 追溯 + JSON 恢复，每次对话独立任务 |
| 🖥️ 原生 GUI | PySide6 无边框窗口，集成 Web 控制面板 |
| 📎 文件附件 | 拖放上传 + 全局搜索，随消息发送 |

## 快速启动

```bash
# 1) 首次：安装依赖 + 下载 Chromium（已自动落盘到 D:\软件\XianRenZhangAgent\xrz_data\playwright_browsers）
安装依赖.bat

# 2) 启动（自动检测装有 playwright+PySide6 的 Python；可用 XRZ_PYTHON 覆盖）
启动仙人掌.bat

# 或命令行直接跑（需先设置 PLAYWRIGHT_BROWSERS_PATH）
python terminal.py
```

> **可迁移说明**：`启动仙人掌.bat` 不再硬编码 WorkBuddy 的 Python 路径，而是自动检测 PATH / 常见目录里同时具备
> `playwright` 与 `PySide6` 的解释器；也可用环境变量 `XRZ_PYTHON` 指定。所有用户数据与浏览器二进制都落在
> `xrz_data/` 内，绝不写 C 盘。

## 适配更多网页 AI（无需改代码）

平台清单集中在 `agent_core/platforms.json`（随包发布）与 `<数据目录>/platforms.user.json`（用户覆盖）两个 JSON 文件。
新增一个网页 AI 只需加一段配置（URL + 输入框/发送/回复/生成中等 CSS 选择器 + `login_texts`），
GUI 与命令行会自动识别，无需触碰任何 Python 代码：

```json
"myai": {
  "platform": "myai",
  "name": "MyAI",
  "display": "MyAI",
  "icon": "🤖",
  "url": "https://my.ai/chat",
  "chat_url": "https://my.ai/chat",
  "input_selector": "textarea",
  "send_selector": "button[type=submit]",
  "response_selector": "[class*=message]:last-child",
  "generating_selector": ".loading",
  "login_texts": ["登录", "Log in"]
}
```

## 项目结构

```
terminal.py             # 主入口（HTTP服务器 + 事件循环）
desktop_app.py          # PySide6 原生桌面壳
gui.html                # Web 控制面板前端
agent_core/             # 核心模块
  browser.py            # 浏览器管理
  commander.py          # 主控制器（多平台LLM适配）
  memory_manager.py     # 记忆管理
  multi_browser.py      # 多平台浏览器管理器
  platform_browser.py   # 单平台浏览器封装
  protocol.py           # 协议解析器
  session.py            # 会话管理（历史持久化/追溯）
  subagent*.py          # 子代理系统
  xrz_paths.py          # 路径配置
buffer_store.py         # 文本缓冲区
```

## 命令速查

```
help          显示帮助
status        查看状态
clear         清屏
deep / think  切换深度思考
switch to platform: <key>     切换到任意已注册平台（key 见 /platforms 或侧边栏）
buffer write <名> <内容>      写入缓冲区
buffer get <名>               读取缓冲区
新对话 / 恢复对话              新建或恢复历史对话
quit                            退出
```

## 依赖安装

```bash
pip install -r requirements.txt
set PLAYWRIGHT_BROWSERS_PATH=<项目目录>\xrz_data\playwright_browsers
playwright install chromium
```
（一键版见 `安装依赖.bat`）

## 许可证

私有项目，仅供内部使用。
