"""
subagent.py — 子代理系统
- 主代理是总工程师，子代理是各专业工人
- 子代理不能调用子代理（MAX_DEPTH=1 硬限制）
- 目前实现：BrowserSubAgent（浏览器子代理）
"""
import asyncio
import logging
from abc import ABC, abstractmethod
from typing import Optional, Dict, Any
from dataclasses import dataclass, field

logger = logging.getLogger("subagent")

MAX_SUBAGENT_DEPTH = 1  # 硬限制：子代理不能嵌套


@dataclass
class SubAgentResult:
    success: bool
    output: str
    error: Optional[str] = None
    agent_name: str = ""


class BaseSubAgent(ABC):
    """子代理基类"""

    name: str = "base"
    max_depth: int = MAX_SUBAGENT_DEPTH

    def __init__(self, depth: int = 0):
        self.depth = depth
        self._tools: Dict[str, callable] = {}

    @property
    def can_spawn(self) -> bool:
        return self.depth < self.max_depth

    def register_tool(self, name: str, fn: callable):
        self._tools[name] = fn

    async def execute(self, task: str, params: Optional[dict] = None) -> SubAgentResult:
        """执行子代理任务"""
        params = params or {}
        logger.info(f"[{self.name}] 接收任务: {task[:80]}, depth={self.depth}")

        # 分发到具体工具
        if task in self._tools:
            try:
                fn = self._tools[task]
                result = fn(**params)
                if asyncio.iscoroutine(result):
                    result = await result
                return SubAgentResult(success=True, output=str(result), agent_name=self.name)
            except Exception as e:
                return SubAgentResult(success=False, output="", error=str(e), agent_name=self.name)

        return SubAgentResult(success=False, output="", error=f"未知任务: {task}", agent_name=self.name)


class BrowserSubAgent(BaseSubAgent):
    """
    浏览器子代理
    复用主代理的 BrowserManager 实例，避免启动新浏览器
    """

    name = "browser"

    def __init__(self, bm, depth: int = 0):
        super().__init__(depth)
        self._bm = bm

        # 注册浏览器工具
        self.register_tool("click", self._click)
        self.register_tool("fill", self._fill)
        self.register_tool("screenshot", self._screenshot)
        self.register_tool("get_text", self._get_text)
        self.register_tool("get_html", self._get_html)
        self.register_tool("search", self._search)
        self.register_tool("wait", self._wait)


    async def _click(self, selector: str, **kwargs) -> str:
        return await self._bm.browser_click(selector)

    async def _fill(self, selector: str = "", text: str = "", **kwargs) -> str:
        if not selector:
            return "错误: 缺少 selector 参数"
        return await self._bm.browser_fill(selector, text)

    async def _screenshot(self, path: str = "subagent_shot.png", **kwargs) -> str:
        return await self._bm.browser_screenshot(path)

    async def _get_text(self, selector: str = "body", **kwargs) -> str:
        return await self._bm.browser_get_text(selector)

    async def _get_html(self, **kwargs) -> str:
        return await self._bm.browser_get_html()

    async def _search(self, query: str = "", engine: str = "deepseek", **kwargs) -> str:
        """执行搜索（跳转到搜索引擎 + 抓取结果）"""
        if engine == "bing":
            url = f"https://www.bing.com/search?q={query}"
        elif engine == "ddg":
            url = f"https://duckduckgo.com/?q={query}"
        else:
            # DeepSeek 搜索
            url = f"https://chat.deepseek.com/search?q={query}"


        await asyncio.sleep(2)

        # 提取搜索结果标题
        try:
            titles = await self._bm._page.evaluate("""
                () => {
                    const results = document.querySelectorAll('h2, [class*="title"]');
                    return Array.from(results).slice(0, 10).map(el => el.innerText).join('\\n');
                }
            """)
            return f"已搜索: {query}\n\n{result}\n\n搜索结果:\n{titles}"
        except Exception as e:
            return f"{result}\n\n抓取结果失败: {e}"

    async def _wait(self, seconds: int = 2, **kwargs) -> str:
        await asyncio.sleep(seconds)
        return f"等待了 {seconds}s"


class SubAgentManager:
    """
    子代理管理器
    - 创建/管理子代理
    - 防止子代理递归
    """

    def __init__(self, bm):
        self._bm = bm
        self._agents: Dict[str, BaseSubAgent] = {}

        # 预注册浏览器子代理
        self._agents["browser"] = BrowserSubAgent(bm, depth=1)

    async def dispatch(self, agent_name: str, task: str, params: Optional[dict] = None) -> SubAgentResult:
        """分发任务到指定子代理"""
        if agent_name not in self._agents:
            return SubAgentResult(
                success=False,
                output="",
                error=f"未知子代理: {agent_name}",
                agent_name=agent_name,
            )

        agent = self._agents[agent_name]
        if not agent.can_spawn:
            return SubAgentResult(
                success=False,
                output="",
                error=f"[{agent_name}] 子代理深度已达上限，禁止再调用子代理",
                agent_name=agent_name,
            )

        return await agent.execute(task, params or {})

    def list_agents(self) -> list:
        return [
            {"name": name, "tasks": list(agent._tools.keys())}
            for name, agent in self._agents.items()
        ]
