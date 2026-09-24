=== 修复前（旧后端，被我杀掉前）： ===
health: {"agent_ready": false, "browser": "connected", "commander": "not ready", "platform": "yuanbao"}
→ commander 死亡，/command 只 accepted 不执行，元宝发不出消息

=== 修复后（browser.py 代理自愈改为 close→null→launch 干净重建）： ===
2026-09-24 17:32:27,537 [session] INFO DeepSeek 已登录
[OK] Commander 就绪
  ✓ Agent 启动完成
  Agent 已就绪！

health: {"status": "ok", "agent_ready": true, "browser": "connected", "commander": "ready", "platform": "yuanbao", "port": 8888}

=== 元宝真实发送验证（后端日志） ===
2026-09-24 17:35:17,716 [platform_browser] INFO [元宝] send_message: 输入 461 字符
2026-09-24 17:35:21,584 [platform_browser] INFO [元宝] 已通过按钮发送
2026-09-24 17:35:39,751 [platform_browser] INFO [元宝] send_message: 输入 461 字符
2026-09-24 17:35:49,181 [platform_browser] INFO [元宝] 已通过按钮发送
2026-09-24 17:36:12,401 [platform_browser] INFO [元宝] send_message: 输入 461 字符
2026-09-24 17:36:16,721 [platform_browser] INFO [元宝] 已通过按钮发送
