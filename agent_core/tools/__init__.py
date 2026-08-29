"""
tools/__init__.py — 工具注册表（内置工具）
所有工具由 Commander 在初始化时注册，此文件仅作参考备份
"""
# 工具定义参考（实际注册在 commander.py 中）
TOOL_MANIFEST = [
    # 文件操作
    ("file_write", "写入文件", ["path", "content"]),
    ("file_read", "读取文件", ["path"]),
    ("file_list", "列出目录", ["path"]),
    ("dir_create", "创建目录", ["path"]),
    ("file_delete", "删除文件/目录", ["path"]),
    # Shell
    ("shell_exec", "执行Shell命令", ["command", "timeout"]),
    # 浏览器 (母代理直接执行)
    ("browser_click", "点击元素", ["selector"]),
    ("browser_fill", "填写输入框", ["selector", "text"]),
    ("browser_screenshot", "截图", ["path"]),
    ("browser_search", "搜索", ["query", "max_pages", "output_file"]),
    # 浏览器 (子代理独立进程)
    ("browser_research", "启动研究子代理（独立进程）", ["query", "max_pages"]),
    ("browser_visit", "启动访问子代理（独立进程）", ["url"]),
    ("check_task", "检查子代理任务状态", ["task_id"]),
    ("wait_task", "等待子代理任务完成", ["task_id", "timeout"]),
    # 特殊指令
    ("done", "任务完成", []),
    ("ask", "提问用户", ["question"]),
    ("deep_think", "深度思考开关", ["enable"]),
    # 文档生成
    ("docx_create", "生成 Word 文档", ["content", "filename", "path"]),
    ("pptx_create", "生成 PPT", ["content", "filename", "path"]),
    # 记忆
    ("remember", "保存记忆", ["content", "tags"]),
    ("recall", "回忆记忆", ["query"]),
    ("summarize", "生成摘要", ["period"]),
    ("list_summaries", "列出记忆摘要", ["limit"]),
    # 子代理结果
    ("get_subagent_result", "获取子代理结果", ["task_id"]),
]
