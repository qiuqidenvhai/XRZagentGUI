"""
subagent_main.py — 子代理独立进程入口

核心改进：
1. 子代理直接复用 USER_DATA_DIR，不再复制凭据到临时目录
2. 启动前清理 Chromium 锁文件，防止重复启动冲突
3. 共享同一套 cookies，不再重新登录
"""
import asyncio
import sys
import json
import argparse
from pathlib import Path

# 添加项目路径
PROJECT_ROOT = Path(__file__).parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT))

from agent_core.browser import BrowserManager, USER_DATA_DIR
from agent_core.session import DeepSeekSession
from agent_core.commander import Commander
from agent_core.xrz_paths import DEEPSEEK_DATA_DIR


class SubAgentProcess:
    """子代理进程 - 独立运行，共享登录态"""
    
    def __init__(self, task_dir: str, query: str, task_type: str, parent_pid: int = None,
                 user_data_dir: str = None):
        self.task_dir = Path(task_dir)
        self.task_dir.mkdir(parents=True, exist_ok=True)
        self.query = query
        self.task_type = task_type
        self.parent_pid = parent_pid
        
        self.work_dir = self.task_dir / "work"
        self.work_dir.mkdir(exist_ok=True)
        
        # 子代理特定目录（统一落到 D 盘 xrz_data 内）
        self.subagent_data_dir = user_data_dir or str(DEEPSEEK_DATA_DIR)
        
        self.browser = None
        self.session = None
        self.commander = None
        
        # 结果文件路径
        self.result_file = self.task_dir / "result.json"
        self.status_file = self.task_dir / "status.txt"
        self.output_dir = self.task_dir / "output"
        self.output_dir.mkdir(exist_ok=True)
        
    def _cleanup_locks(self):
        """清理 Chromium 锁文件（关键步骤，防止子代理启动失败）"""
        lock_files = ["SingletonLock", "SingletonCookieLock", "SingletonSocketLock", 
                       "SingletonPipeline", "Chrome_Port", "chrome_debug_port", 
                       "SingletonCookie", "lock.file", "SingletonVar"]
        for p in Path(self.subagent_data_dir).iterdir():
            if any(x in p.name.lower() for x in lock_files):
                try:
                    p.unlink(missing_ok=True)
                    print(f"[INFO] 已清理锁文件: {p.name}")
                except Exception:
                    pass
    
    def _write_status(self, status: str, message: str = ""):
        """写入状态文件通知母代理"""
        status_data = {
            "status": status,
            "message": message,
            "task_dir": str(self.task_dir),
            "output_dir": str(self.output_dir),
        }
        self.status_file.write_text(json.dumps(status_data), encoding="utf-8")
        print(f"[STATUS] {status}: {message}")
    
    def _write_result(self, success: bool, output: str = "", error: str = "", files: list = None):
        """写入结果文件"""
        result = {
            "success": success,
            "output": output,
            "error": error,
            "files": files or [],
            "query": self.query,
            "task_type": self.task_type,
        }
        self.result_file.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[RESULT] 结果已写入: {self.result_file}")
    
    async def run(self):
        """运行子代理任务"""
        print(f"\n{'='*50}")
        print(f"[子代理启动] 任务类型: {self.task_type}")
        print(f"[子代理启动] 工作目录: {self.task_dir}")
        print(f"[子代理启动] 查询: {self.query[:100]}...")
        print(f"[子代理启动] 使用共享浏览器: {self.subagent_data_dir}")
        print(f"{'='*50}\n")
        
        # 0. 验证浏览器数据目录
        if not os.path.exists(self.subagent_data_dir):
            print(f"[ERROR] 浏览器数据目录不存在: {self.subagent_data_dir}")
            self._write_status("FAILED", f"浏览器数据目录不存在: {self.subagent_data_dir}")
            self._write_result(False, error=f"浏览器数据目录不存在: {self.subagent_data_dir}")
            return False
            
        print(f"[INFO] 浏览器数据目录存在: {os.path.exists(os.path.join(self.subagent_data_dir, 'Default'))}")
        
        # 1. 通知母代理启动
        self._write_status("STARTED", f"子代理已启动，工作目录: {self.task_dir}")
        
        try:
            # 2. 清理锁文件（关键！）
            print("[INFO] 清理 Chromium 锁文件...")
            self._cleanup_locks()
            
            # 3. 启动浏览器（使用共享用户数据目录）
            print("[INFO] 启动浏览器...")
            self._write_status("INITIALIZING", "正在启动浏览器...")
            
            self.browser = BrowserManager(
                headless=False,
            )
            self.browser._user_data_dir_override = self.subagent_data_dir
            await self.browser.launch()
            
            # 4. 检查登录状态
            print("[INFO] 检查登录状态...")
            await self.browser.navigate()
            
            if not await self.browser.check_login():
                print("[WARN] 未登录！")
                self._write_status("ERROR", "需要重新登录")
                self._write_result(False, error="凭据失效，需要重新登录")
                await self.browser.close()
                return False
            
            print("[OK] 已登录")
            self._write_status("RUNNING", "正在执行任务...")
            
            # 5. 初始化会话和 Commander
            self.session = DeepSeekSession(self.browser)
            self.commander = Commander(
                browser_manager=self.browser,
                session=self.session,
                work_dir=str(self.work_dir),
            )
            await self.commander.start(session=self.session)
            
            # 6. 执行任务
            print(f"[INFO] 开始执行任务: {self.query}")
            
            # 构建任务提示
            task_prompt = self._build_task_prompt()
            
            # 运行任务
            reply = await self.commander.run_with_loop(
                user_instruction=task_prompt,
                file_path=None,
                context_hints=f"这是一个子代理任务，类型: {self.task_type}",
            )
            
            # 7. 收集输出文件
            output_files = []
            for f in self.work_dir.rglob("*"):
                if f.is_file():
                    # 复制到输出目录
                    import shutil
                    dest = self.output_dir / f.name
                    shutil.copy2(f, dest)
                    output_files.append(str(dest.relative_to(self.task_dir)))
            
            # 8. 写入结果
            self._write_status("COMPLETED", "任务完成")
            self._write_result(
                success=True,
                output=reply,
                files=output_files
            )
            
            print(f"\n[OK] 子代理任务完成")
            print(f"[OK] 结果文件: {self.result_file}")
            print(f"[OK] 输出文件数: {len(output_files)}")
            
            return True
            
        except Exception as e:
            error_msg = f"子代理执行错误: {e}"
            print(f"[ERROR] {error_msg}")
            import traceback
            traceback.print_exc()
            self._write_status("FAILED", error_msg)
            self._write_result(False, error=error_msg)
            return False
            
        finally:
            # 9. 清理
            if self.browser:
                await self.browser.close()
                print("[INFO] 浏览器已关闭")
    
    def _build_task_prompt(self) -> str:
        """构建任务提示"""
        base_prompt = f"""请完成以下任务：

{self.query}

任务要求：
1. 这是一个子代理任务，你不能创建新的子代理（MAX_DEPTH=1）
2. 所有输出文件请保存在当前工作目录
3. 完成后调用 done() 工具报告结果
4. 如果遇到问题，详细记录错误信息

任务类型: {self.task_type}
工作目录: {self.work_dir}
"""
        return base_prompt


def main():
    parser = argparse.ArgumentParser(description="仙人掌 Agent - 子代理进程")
    parser.add_argument("--task-dir", required=True, help="任务工作目录")
    parser.add_argument("--query", required=True, help="任务查询内容")
    parser.add_argument("--type", default="research", help="任务类型")
    parser.add_argument("--parent-pid", type=int, default=None, help="母代理进程ID")
    parser.add_argument("--user-data-dir", default=None, help="用户数据目录（共享浏览器登录态）")
    
    args = parser.parse_args()
    
    # 创建并运行子代理
    subagent = SubAgentProcess(
        task_dir=args.task_dir,
        query=args.query,
        task_type=args.type,
        parent_pid=args.parent_pid,
        user_data_dir=args.user_data_dir,
    )
    
    success = asyncio.run(subagent.run())
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
