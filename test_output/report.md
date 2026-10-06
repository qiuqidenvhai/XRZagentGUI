# 仙人掌 Agent 端到端测试总报告

生成时间：2026-09-25 18:55:05

## 结果汇总

- [PASS] T1 DeepSeek 生成Word
- [FAIL] T2 DeepSeek PDF摘要
- [PASS] T3 Qwen file_edit
- [FAIL] T4 Qwen 多轮对话
- [PASS] T5 思考过程事件管线(共259条thinking)

**通过 3/5**

## 偷懒检查（禁止 ask/question）
- 全任务 ask/question 工具调用次数: **0**（应为 0） ✅ agent 未把任务踢回给用户

## 说明
- 本测试通过后端 HTTP（/command）驱动，与 GUI 点发送等价，验证 AI 行为与事件管线。
- GUI 像素层（📎按钮/拖拽视觉/思考块渲染）因沙箱 WebEngine 渲染子进程无法启动而未能在此环境验证，需在用户真实显示器上验证（你双击启动仙人掌.bat 即可看到）。