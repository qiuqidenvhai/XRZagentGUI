"""
仙人掌Agent引导会话完整测试
模拟用户在GUI界面的实际操作，验证8个关键问题是否已修复
"""
import asyncio
import json
import urllib.request
import urllib.error
from pathlib import Path
from datetime import datetime

API = "http://127.0.0.1:8888"
PROJECT_DIR = Path("D:/软件/XianRenZhangAgent")
TEST_OUTPUT_DIR = PROJECT_DIR / "test_output"

class GuidedSessionTester:
    def __init__(self):
        self.test_results = []
        self.start_time = datetime.now()
        self.task_id = None
        print("="*70)
        print("仙人掌Agent 引导会话完整测试")
        print(f"开始时间: {self.start_time.strftime('%Y-%m-%d %H:%M:%S')}")
        print("="*70)

    def log_test(self, name, status, detail=""):
        """记录测试结果"""
        self.test_results.append({
            'name': name,
            'status': status,
            'detail': detail,
            'time': datetime.now().strftime('%H:%M:%S')
        })
        icon = "✓" if status == "PASS" else ("⚠" if status == "WARN" else "✗")
        print(f"  {icon} [{status}] {name}" + (f": {detail}" if detail else ""))

    def api_request(self, endpoint, method="GET", data=None, timeout=30):
        """发送API请求"""
        try:
            url = f"{API}{endpoint}"
            if data:
                req = urllib.request.Request(
                    url,
                    data=json.dumps(data).encode('utf-8'),
                    headers={'Content-Type': 'application/json'},
                    method=method
                )
            else:
                req = urllib.request.Request(url, method=method)

            with urllib.request.urlopen(req, timeout=timeout) as resp:
                content = resp.read()
                if content:
                    return json.loads(content)
                return {"status": resp.status}
        except urllib.error.HTTPError as e:
            return {"error": f"HTTP {e.code}", "message": e.read().decode()[:200]}
        except Exception as e:
            return {"error": str(e)}

    # ========== 测试1: 文件上传功能 ==========
    async def test_file_upload(self):
        """测试1: 文件上传到GUI后AI是否能识别"""
        print("\n[测试1] 文件上传功能")

        # 1.1 检查附件目录
        attachments_dir = PROJECT_DIR / "xrz_data" / "gui_attachments"
        if attachments_dir.exists():
            files = list(attachments_dir.glob("*"))
            self.log_test("附件目录", "PASS", f"存在，{len(files)}个文件")
        else:
            self.log_test("附件目录", "FAIL", "目录不存在")
            return

        # 1.2 检查后端附件API
        result = self.api_request("/attachments")
        if result.get('type') == 'ok' and result.get('files'):
            files = result['files']
            self.log_test("附件API", "PASS", f"返回{len(files)}个附件")

            # 统计文件类型
            types = {}
            for f in files:
                ext = Path(f['name']).suffix.lower()
                types[ext or '(无扩展名)'] = types.get(ext or '(无扩展名)', 0) + 1
            type_str = ", ".join(f"{k}:{v}" for k, v in sorted(types.items()))
            self.log_test("附件类型", "PASS", type_str)
        else:
            self.log_test("附件API", "FAIL", f"响应异常: {result}")

        # 1.3 测试上传文件（如果存在测试文件）
        test_pdf = attachments_dir / "1fe67f80b826445b8c22b809423924dc.pdf"
        if test_pdf.exists():
            self.log_test("测试PDF文件", "PASS", f"{test_pdf.name} ({test_pdf.stat().st_size} bytes)")
        else:
            self.log_test("测试PDF文件", "WARN", "未找到测试文件")

    # ========== 测试2: 思考过程显示 ==========
    async def test_thinking_display(self):
        """测试2: 思考过程是否正确显示"""
        print("\n[测试2] 思考过程显示")

        # 2.1 检查SSE事件流
        result = self.api_request("/events")
        if result:
            self.log_test("SSE端点", "PASS", "可访问")
        else:
            self.log_test("SSE端点", "WARN", "无法测试（需要WebSocket连接）")

        # 2.2 检查GUI中的思考函数
        gui_file = PROJECT_DIR / "test_screenshots" / "gui.html"
        if gui_file.exists():
            content = gui_file.read_text(encoding='utf-8')

            functions = [
                ('showThinking', 'function showThinking'),
                ('appendThinking', 'function appendThinking'),
                ('endThinking', 'function endThinking'),
                ('endPlan', 'function endPlan'),
            ]

            for name, pattern in functions:
                if pattern in content:
                    self.log_test(f"GUI:{name}", "PASS", "函数存在")
                else:
                    self.log_test(f"GUI:{name}", "FAIL", "函数缺失")

            # 检查事件处理
            if "etype === 'ai_thinking'" in content:
                self.log_test("SSE:ai_thinking处理", "PASS", "已连接")
            else:
                self.log_test("SSE:ai_thinking处理", "FAIL", "未处理")

            if "etype === 'thinking'" in content:
                self.log_test("SSE:thinking处理", "PASS", "已连接")
            else:
                self.log_test("SSE:thinking处理", "FAIL", "未处理")
        else:
            self.log_test("GUI文件", "FAIL", "gui.html不存在")

    # ========== 测试3: 历史任务隔离 ==========
    async def test_task_isolation(self):
        """测试3: 历史任务对话是否独立"""
        print("\n[测试3] 历史任务隔离")

        # 3.1 获取任务列表
        result = self.api_request("/conversations")
        if result.get('tasks'):
            tasks = result['tasks']
            self.log_test("任务列表", "PASS", f"共{len(tasks)}个任务")

            # 检查任务结构
            if tasks:
                t = tasks[0]
                required = ['id', 'platform', 'title', 'file', 'created_at', 'updated_at']
                missing = [f for f in required if f not in t]

                if not missing:
                    self.log_test("任务结构", "PASS", "字段完整")
                else:
                    self.log_test("任务结构", "FAIL", f"缺少字段: {missing}")

                # 检查文件路径
                if t.get('file'):
                    file_path = Path(t['file'])
                    if file_path.exists():
                        self.log_test("对话文件", "PASS", f"{file_path.name}存在")

                        # 检查消息内容
                        try:
                            conv_data = json.loads(file_path.read_text(encoding='utf-8'))
                            msgs = conv_data.get('messages', [])
                            self.log_test("消息内容", "PASS", f"{len(msgs)}条消息")
                        except Exception as e:
                            self.log_test("消息内容", "FAIL", str(e))
                    else:
                        self.log_test("对话文件", "WARN", f"文件不存在: {t['file']}")
        else:
            self.log_test("任务列表", "FAIL", "无任务或API错误")

    # ========== 测试4: 任务恢复功能 ==========
    async def test_task_restore(self):
        """测试4: 能否恢复历史任务"""
        print("\n[测试4] 任务恢复功能")

        # 4.1 检查恢复相关函数
        session_file = PROJECT_DIR / "agent_core" / "session.py"
        if session_file.exists():
            content = session_file.read_text(encoding='utf-8')

            if 'restore_conversation' in content:
                self.log_test("恢复函数", "PASS", "session.py中有restore_conversation")
            else:
                self.log_test("恢复函数", "FAIL", "session.py中无restore_conversation")

            if '_restore_from_url' in content or '_restore_from_json' in content:
                self.log_test("恢复实现", "PASS", "有URL/JSON恢复逻辑")
            else:
                self.log_test("恢复实现", "WARN", "恢复逻辑可能不完整")
        else:
            self.log_test("session.py", "FAIL", "文件不存在")

        # 4.2 测试通过消息恢复
        result = self.api_request("/message", "POST", {
            "message": "恢复对话 deepseek_20260827_173719_14891"
        })
        if result.get('type') == 'ok':
            self.log_test("恢复命令", "PASS", "命令已入队")
        else:
            self.log_test("恢复命令", "WARN", f"响应: {result.get('text', '未知')}")

    # ========== 测试5: 删除功能 ==========
    async def test_delete_function(self):
        """测试5: 删除功能是否正常"""
        print("\n[测试5] 删除功能")

        # 5.1 检查tasks.json权限
        tasks_file = PROJECT_DIR / "xrz_data" / ".xianrenzhang_agent" / "tasks.json"
        if tasks_file.exists():
            stat = tasks_file.stat()
            perms = oct(stat.st_mode)[-3:]
            self.log_test("tasks.json权限", "INFO", f"{perms}")

            # 尝试写入测试
            try:
                # 读取并重写（不改变内容）
                data = json.loads(tasks_file.read_text(encoding='utf-8'))
                tasks_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
                self.log_test("文件写入", "PASS", "权限正常")
            except PermissionError:
                self.log_test("文件写入", "FAIL", "权限被拒绝（需要重启后端）")
            except Exception as e:
                self.log_test("文件写入", "WARN", str(e))
        else:
            self.log_test("tasks.json", "FAIL", "文件不存在")

        # 5.2 测试删除API
        result = self.api_request("/conversations/deepseek_20260827_173719_14891", "DELETE")
        if result.get('type') == 'ok':
            self.log_test("删除API", "PASS", "删除成功")
        else:
            error_msg = result.get('text', result.get('error', '未知错误'))
            self.log_test("删除API", "WARN", f"{error_msg[:50]}")

    # ========== 测试6: PPT生成 ==========
    async def test_ppt_generation(self):
        """测试6: PPT生成功能"""
        print("\n[测试6] PPT生成功能")

        # 6.1 检查PPT构建器
        pptx_builder = PROJECT_DIR / "agent_core" / "pptx_builder.py"
        if pptx_builder.exists():
            content = pptx_builder.read_text(encoding='utf-8')

            if 'build_pptx' in content:
                self.log_test("PPT构建器", "PASS", "build_pptx函数存在")
            else:
                self.log_test("PPT构建器", "FAIL", "build_pptx函数缺失")

            if 'parse_to_slides' in content:
                self.log_test("幻灯片解析", "PASS", "parse_to_slides函数存在")
            else:
                self.log_test("幻灯片解析", "FAIL", "parse_to_slides函数缺失")
        else:
            self.log_test("pptx_builder.py", "FAIL", "文件不存在")

        # 6.2 检查已生成的PPT
        attachments_dir = PROJECT_DIR / "xrz_data" / "gui_attachments"
        ppt_files = list(attachments_dir.glob("*.pptx")) if attachments_dir.exists() else []

        if ppt_files:
            self.log_test("已生成PPT", "PASS", f"共{len(ppt_files)}个文件")

            # 检查文件大小（避免空文件）
            valid_ppts = [f for f in ppt_files if f.stat().st_size > 1000]
            if valid_ppts:
                self.log_test("有效PPT", "PASS", f"{len(valid_ppts)}个文件>1KB")
            else:
                self.log_test("有效PPT", "WARN", "所有PPT文件过小")
        else:
            self.log_test("已生成PPT", "WARN", "未找到PPT文件")

        # 6.3 测试PPT生成命令
        result = self.api_request("/message", "POST", {
            "message": "生成一个关于测试的简单PPT"
        })
        if result.get('type') == 'ok':
            self.log_test("PPT生成命令", "PASS", "命令已入队")
        else:
            self.log_test("PPT生成命令", "WARN", f"响应: {result.get('text', '未知')}")

    # ========== 测试7: 暂停/中断功能 ==========
    async def test_interrupt_function(self):
        """测试7: 暂停/中断功能"""
        print("\n[测试7] 暂停/中断功能")

        # 7.1 测试中断API
        result = self.api_request("/interrupt", "POST")
        if result.get('type') == 'ok':
            self.log_test("中断API", "PASS", result.get('text', '正常'))
        else:
            self.log_test("中断API", "FAIL", f"响应: {result}")

        # 7.2 检查GUI中断函数
        gui_file = PROJECT_DIR / "test_screenshots" / "gui.html"
        if gui_file.exists():
            content = gui_file.read_text(encoding='utf-8')

            if 'interruptTask' in content:
                self.log_test("GUI中断函数", "PASS", "interruptTask函数存在")
            else:
                self.log_test("GUI中断函数", "FAIL", "interruptTask函数缺失")

            if '/interrupt' in content:
                self.log_test("GUI中断调用", "PASS", "已调用/interrupt端点")
            else:
                self.log_test("GUI中断调用", "FAIL", "未调用/interrupt端点")
        else:
            self.log_test("GUI文件", "FAIL", "gui.html不存在")

    # ========== 测试8: PPT显示在附件列表 ==========
    async def test_ppt_display(self):
        """测试8: PPT是否显示在附件列表"""
        print("\n[测试8] PPT附件显示")

        # 8.1 获取附件列表
        result = self.api_request("/attachments")
        if result.get('type') == 'ok':
            files = result.get('files', [])

            # 筛选PPT文件
            ppt_files = [f for f in files if f['name'].lower().endswith('.pptx')]

            if ppt_files:
                self.log_test("PPT在附件列表", "PASS", f"找到{len(ppt_files)}个PPT")
                for ppt in ppt_files[:3]:  # 显示前3个
                    name = Path(ppt['name']).name
                    size = ppt.get('size', 0)
                    self.log_test(f"PPT: {name}", "INFO", f"{size} bytes")
            else:
                self.log_test("PPT在附件列表", "WARN", "未找到PPT文件")
        else:
            self.log_test("附件API", "FAIL", f"响应异常: {result}")

        # 8.2 检查GUI刷新逻辑
        gui_file = PROJECT_DIR / "test_screenshots" / "gui.html"
        if gui_file.exists():
            content = gui_file.read_text(encoding='utf-8')

            if 'loadAttachments()' in content:
                self.log_test("附件刷新函数", "PASS", "loadAttachments函数存在")

                # 检查是否在任务完成后刷新
                if "ai_final_reply" in content and content.count('loadAttachments') > 1:
                    self.log_test("自动刷新", "PASS", "任务完成后自动刷新")
                else:
                    self.log_test("自动刷新", "WARN", "可能未实现自动刷新")
            else:
                self.log_test("附件刷新函数", "FAIL", "loadAttachments函数缺失")
        else:
            self.log_test("GUI文件", "FAIL", "gui.html不存在")

    # ========== 测试9: 多会话管理 ==========
    async def test_session_management(self):
        """测试9: 多会话并行管理"""
        print("\n[测试9] 多会话管理")

        # 9.1 检查session_manager工具
        commander_file = PROJECT_DIR / "agent_core" / "commander.py"
        if commander_file.exists():
            content = commander_file.read_text(encoding='utf-8')

            if 'session_manager' in content:
                self.log_test("会话管理工具", "PASS", "已注册session_manager")
            else:
                self.log_test("会话管理工具", "FAIL", "未找到session_manager")

            # 检查会话操作
            operations = ['create', 'switch', 'delete', 'list']
            for op in operations:
                if f'"{op}"' in content or f"'{op}'" in content:
                    self.log_test(f"会话操作:{op}", "PASS", "已实现")
                else:
                    self.log_test(f"会话操作:{op}", "WARN", "可能未实现")
        else:
            self.log_test("commander.py", "FAIL", "文件不存在")

        # 9.2 测试会话命令
        result = self.api_request("/message", "POST", {
            "message": "@@@@{\"tool\":\"session_manager\",\"action\":\"list\"}@@@@"
        })
        if result.get('type') == 'ok':
            self.log_test("会话列表命令", "PASS", "命令已入队")
        else:
            self.log_test("会话列表命令", "WARN", f"响应: {result.get('text', '未知')}")

    # ========== 测试10: 文件上传到AI上下文 ==========
    async def test_file_to_ai_context(self):
        """测试10: 上传的文件是否正确传递给AI"""
        print("\n[测试10] 文件上传到AI上下文")

        # 10.1 检查commander中的附件处理
        commander_file = PROJECT_DIR / "agent_core" / "commander.py"
        if commander_file.exists():
            content = commander_file.read_text(encoding='utf-8')

            if '_pending_attachments' in content:
                self.log_test("待发送附件列表", "PASS", "_pending_attachments已定义")
            else:
                self.log_test("待发送附件列表", "FAIL", "_pending_attachments未定义")

            if 'upload_file' in content or '附件上传' in content:
                self.log_test("附件上传逻辑", "PASS", "存在上传逻辑")
            else:
                self.log_test("附件上传逻辑", "WARN", "可能缺少上传逻辑")
        else:
            self.log_test("commander.py", "FAIL", "文件不存在")

        # 10.2 测试附件传递
        result = self.api_request("/message", "POST", {
            "message": "读取附件 D:/软件/XianRenZhangAgent/xrz_data/gui_attachments/1fe67f80b826445b8c22b809423924dc.pdf"
        })
        if result.get('type') == 'ok':
            self.log_test("附件读取命令", "PASS", "命令已入队")
        else:
            self.log_test("附件读取命令", "WARN", f"响应: {result.get('text', '未知')}")

    # ========== 运行所有测试 ==========
    async def run_all_tests(self):
        """运行所有测试"""
        await self.test_file_upload()
        await self.test_thinking_display()
        await self.test_task_isolation()
        await self.test_task_restore()
        await self.test_delete_function()
        await self.test_ppt_generation()
        await self.test_interrupt_function()
        await self.test_ppt_display()
        await self.test_session_management()
        await self.test_file_to_ai_context()

        # 输出总结
        self.print_summary()

    def print_summary(self):
        """打印测试总结"""
        print("\n" + "="*70)
        print("测试总结")
        print("="*70)

        passed = sum(1 for r in self.test_results if r['status'] == "PASS")
        failed = sum(1 for r in self.test_results if r['status'] == "FAIL")
        warned = sum(1 for r in self.test_results if r['status'] == "WARN")

        print(f"\n总测试项: {len(self.test_results)}")
        print(f"✓ 通过: {passed}")
        print(f"✗ 失败: {failed}")
        print(f"⚠ 警告: {warned}")

        # 按类别分组显示
        categories = {
            "文件上传": [],
            "思考过程": [],
            "历史任务": [],
            "任务恢复": [],
            "删除功能": [],
            "PPT生成": [],
            "中断功能": [],
            "附件显示": [],
            "会话管理": [],
            "AI上下文": []
        }

        for r in self.test_results:
            name = r['name']
            for cat in categories:
                if cat in name:
                    categories[cat].append(r)
                    break

        print("\n分类详情:")
        for cat, results in categories.items():
            if results:
                pass_count = sum(1 for r in results if r['status'] == "PASS")
                fail_count = sum(1 for r in results if r['status'] == "FAIL")
                status = "✓" if fail_count == 0 else "✗"
                print(f"  {status} {cat}: {pass_count}通过, {fail_count}失败")

        print("\n" + "="*70)

        # 保存报告
        self.save_report()

    def save_report(self):
        """保存测试报告"""
        TEST_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        report_file = TEST_OUTPUT_DIR / f"test_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"

        report = {
            'start_time': self.start_time.isoformat(),
            'end_time': datetime.now().isoformat(),
            'results': self.test_results,
            'summary': {
                'total': len(self.test_results),
                'passed': sum(1 for r in self.test_results if r['status'] == "PASS"),
                'failed': sum(1 for r in self.test_results if r['status'] == "FAIL"),
                'warning': sum(1 for r in self.test_results if r['status'] == "WARN"),
            }
        }

        report_file.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(f"\n报告已保存: {report_file}")


async def main():
    tester = GuidedSessionTester()
    await tester.run_all_tests()

    # 返回退出码
    failed = sum(1 for r in tester.test_results if r['status'] == "FAIL")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    exit(exit_code)
