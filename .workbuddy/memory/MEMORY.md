# 仙人掌 Agent 项目长期备忘

## 部署/运行关键事实（2026-08-28 更新）
- **唯一工作目录：`D:/软件/XianRenZhangAgent/`** —— 绝对禁止创建或修改任何 `D:/software/` 下的文件
- 正确解释器：`D:/软件/Python/python.exe|pythonw.exe`（PySide6 6.11.1 + playwright）
- Playwright 浏览器目录：`D:/软件/XianRenZhangAgent/xrz_data/playwright_browsers/`
  - chromium-1217（有头模式，用于持久化上下文）
  - chromium-1234（headless shell，供测试脚本使用）
  - ffmpeg-1011 / winldd-1007
- 后端独立启动：`XRZ_NO_GUI=1 python terminal.py`（端口 8888）
- **`/shutdown` 端点实测返回 404，不可用**（旧记录有误）。停后端需杀进程：
  先 `netstat -ano | grep 8888` 取 PID，再 `MSYS_NO_PATHCONV=1 taskkill /T /F /PID <pid>`
  （Git Bash 下不加 `MSYS_NO_PATHCONV=1` 参数会被路径转换破坏）
- PDF 读取：PyPDF2（需 `pip install PyPDF2`）；PPT 预览：python-pptx（项目解释器已装 1.0.2）

## 启动与健康检查（2026-08-29 实测）
- 后端**冷启动约 91 秒**，其中浏览器初始化占约 78 秒。
- **`/health` 在启动期间就返回 HTTP 200**，但 `agent_ready=false` / `commander="not ready"`。
  判断可用性必须检查 `agent_ready` 字段，只看 `resp.ok` 会把消息发给「还没醒」的 Agent。
- 就绪标志：日志出现 `Agent 启动完成`；`/health` 返回 `agent_ready:true`。
- `desktop_app.py` 的 `closeEvent` 用 `taskkill /T /F` 杀整棵后端进程树 —— **关窗即退出后端**。

## 任务删除策略（2026-08-29 重构，用户明确要求）
- **保留产物文件，只清理临时文件与记忆文件。**
- 旧 `shutil.rmtree(GUI_SESSION_DIR / task_id)` 是**死代码**：索引 id 形如
  `deepseek_20260812_113400_60879`，而 gui_session 下实际是 `subagent_1787790847328`，
  两者对不上，该路径永远不存在。
- 现改为**登记制**：`_record_task()` 登记当前任务 → 工具把临时资源登记到任务条目的
  `temp_files` / `memory_ids` → 删除时只清理登记过的资源，绝不误伤产物。
- 会话 JSON 只有 `platform/url/session_id/messages`，**不含产物记录**，无法事后追溯产物归属。

## 平台思考模式配置（已验证）
| 平台 | thinking_mode | 特点 |
|------|--------------|------|
| DeepSeek | toggle | 一键切换，无超时 |
| 通义千问 | select | 三档：自动/思考/快速，超时60s后自动跳过 |
| 豆包 | toggle | 一键切换（实际按钮文字是"深入研究"） |
| 元宝 | toggle | 一键切换 |

## 绝对禁止
- **永远不准在 `D:/software/` 下创建、写入、修改任何文件！**
- **永远不准用 `playwright install` 下载浏览器！** 系统已有 Chrome (`C:/Program Files/Google/Chrome/Application/chrome.exe`) 和 Playwright 浏览器，自动使用即可。
- 所有修改必须在 `D:/软件/XianRenZhangAgent/` 内进行。

## 关键文件修改记录
- `agent_core/platforms.json`: 通义千问 thinking_mode 从 "toggle" 改为 "select"
- `agent_core/platform_browser.py`: PlatformProfile 支持 thinking_mode="select"
- `desktop_app.py`: QtBridge setTheme，四向缩放手柄
- `gui.html`: renderThinkingControl 支持 select 模式
- `terminal.py`: /platforms 端点返回 models/thinking_timeout
- `agent_core/pptx_builder.py`: 修复结构化输入解析（处理 items 字段和 kind 字段）；
  `finally` 中清理 `tmp_edits` 改为 try/except（否则删除失败会吞掉已成功的返回值）

## 2026-08-29 改动
- `gui.html`: 发送前探活并校验 `agent_ready`；`sending` 卡住时明确提示而非静默 return；
  失败时回填输入框；SSE onerror 释放发送锁、onopen 恢复 online
- `agent_core/session.py`: 新增任务上下文（`set/get_current_task_id`、`register_temp_file`、
  `register_memory_id`）；`_record_task` 登记当前任务；`_delete_task` 重写为按登记清理
- `agent_core/memory_manager.py`: `save()` 写入 `task_id`；新增 `delete_by_task()` /
  `delete_entries()`；`save_summary()` 把摘要登记到任务名下
- 新增 `test_delete_task.py`（删除行为验证，7/7 通过）

## 系统测试状态（2026-08-29）
- `test_full_system_v2.py` 用**项目解释器** `D:/软件/Python/python.exe` 跑：16/16 全部通过
- 注意：用 WorkBuddy 的 python 跑会因缺少 python-pptx 而误报 PPT 预览失败

## 系统测试状态（2026-08-28）
- 测试脚本：`test_full_system_v2.py`
- 测试报告：`xrz_data/test_report_v2.json`
- 结果：16/16 全部通过
- 验证功能：模块导入、平台配置、记忆管理、语法检查、PPT生成、文件预览、日志、缓冲区、HTTP、任务删除、模板完整性
