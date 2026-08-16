"""
subagent_runner.py - 子代理独立进程（脚本优先原则）
子代理不直接操作浏览器，而是编写 Python 脚本，再执行脚本
"""
import asyncio
import json
import sys
import os
import subprocess
import time
import re
from pathlib import Path

agent_core = Path(__file__).parent
sys.path.insert(0, str(agent_core))

from xrz_paths import SUBAGENT_OUTPUT_DIR

TASK_FILE = None
RESULT_FILE = None
for arg in sys.argv[1:]:
    if TASK_FILE is None:
        TASK_FILE = arg
    else:
        RESULT_FILE = arg


def write_script(path: Path, code: str) -> None:
    """先写脚本"""
    path.write_text(code, encoding="utf-8")
    print(f"[SubAgent] 脚本已写入: {path}")


def run_script(script_path: Path, timeout: int = 300) -> dict:
    """执行脚本，返回 stdout + stderr"""
    print(f"[SubAgent] 执行脚本: {script_path.name}")
    try:
        result = subprocess.run(
            [sys.executable, str(script_path)],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        out = result.stdout
        err = result.stderr
        # 打印最后20行
        lines = out.strip().split("\n")
        for line in lines[-20:]:
            print(f"  [SCRIPT] {line}")
        if err:
            print(f"  [ERR] {err[:200]}")
        return {"success": True, "stdout": out, "stderr": err}
    except subprocess.TimeoutExpired:
        return {"success": False, "error": f"脚本执行超时 ({timeout}s)"}
    except Exception as e:
        return {"success": False, "error": str(e)}


# ─────────────────────────────────────────────────────────
# 脚本构建器
# ─────────────────────────────────────────────────────────

def build_research_script(queries: list, output_dir: Path, max_pages: int = 10) -> str:
    """
    构建完整的研究脚本：搜索 → 爬取 → 保存截图+文字 → 质量评估
    """
    urls_json = json.dumps(queries, ensure_ascii=False)
    out_str = str(output_dir).replace("\\", "\\\\")
    return f'''
import asyncio
import json
import sys
from pathlib import Path
from playwright.async_api import async_playwright
import urllib.parse

async def scrape_one(page, url, idx, out_dir):
    result = {{"idx": idx, "url": url, "success": False, "text": "", "title": "", "screenshot": ""}}
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(2)
        # 截图
        shot = out_dir / f"page_{{idx}}.png"
        await page.screenshot(path=str(shot), full_page=True)
        result["screenshot"] = str(shot)
        # 提取文字（最多5000字）
        try:
            text = await page.evaluate(
                "() => {{ const b = document.querySelector('body'); return b ? b.innerText.slice(0,5000) : ''; }}"
            )
            result["text"] = text[:3000]
        except:
            pass
        # 标题
        try:
            result["title"] = await page.title()
        except:
            pass
        result["success"] = True
        print(f"OK {{idx}}: {{url[:60]}}")
    except Exception as e:
        result["error"] = str(e)[:200]
        print(f"FAIL {{idx}}: {{str(e)[:100]}}")
    return result

async def main():
    queries = {urls_json}
    out_dir = Path(r"{out_str}")
    out_dir.mkdir(parents=True, exist_ok=True)

    search_urls = []

    # Phase 1: Bing 搜索获取 URL
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        for q in queries[:5]:
            encoded = urllib.parse.quote(q)
            url = "https://www.bing.com/search?q=" + encoded
            print(f"搜索: {{q}}")
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                await asyncio.sleep(3)
                # 截图搜索结果
                shot = out_dir / f"search_{{q[:10]}}.png"
                await page.screenshot(path=str(shot), full_page=True)
                # 提取链接
                try:
                    links = await page.evaluate("""
                        () => {{
                            const u = new Set();
                            document.querySelectorAll('h2 a').forEach(a => {{
                                const h = a.href;
                                if (h && !h.includes('bing.com') && h.startsWith('http')
                                    && !h.includes('microsoft') && !h.includes('github.com/Claude'))
                                    u.add(h);
                            }});
                            return [...u].slice(0, 12);
                        }}
                    """)
                    for l in links:
                        search_urls.append({{"url": l, "query": q}})
                    print(f"  找到 {{len(links)}} 个链接")
                except Exception as e:
                    print(f"  提取链接失败: {{e}}")
            except Exception as e:
                print(f"  搜索失败: {{e}}")
            await asyncio.sleep(1)
        await browser.close()

    print(f"共收集到 {{len(search_urls)}} 个待爬取页面")

    # Phase 2: 爬取收集的页面
    results = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        target = min(len(search_urls), {max_pages})
        for i in range(target):
            item = search_urls[i]
            print(f"爬取 {{i+1}}/{{target}}: {{item['url'][:60]}}")
            r = await scrape_one(page, item["url"], i, out_dir)
            r["query"] = item.get("query", "")
            results.append(r)
            await asyncio.sleep(1)
        await browser.close()

    # 保存数据
    data_path = out_dir / "scraped_data.json"
    data_path.write_text(
        json.dumps(results, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
    print(f"完成，共 {{len(results)}} 个页面，成功 {{sum(1 for x in results if x.get('success'))}} 个")
    print(f"数据已保存: {{data_path}}")

asyncio.run(main())
'''


def build_url_visit_script(url: str, output_dir: Path) -> str:
    """构建访问 URL + 截图 + 提取文字的脚本"""
    out_str = str(output_dir).replace("\\", "\\\\")
    return f'''
import asyncio
import json
from pathlib import Path
from playwright.async_api import async_playwright

async def main():
    url = "{url}"
    out_dir = Path(r"{out_str}")
    out_dir.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        print(f"访问: {{url}}")
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(2)

            # 截图
            shot = out_dir / "screenshot.png"
            await page.screenshot(path=str(shot), full_page=True)
            print(f"截图: {{shot}}")

            # 提取文字
            try:
                text = await page.evaluate(
                    "() => {{ const b = document.querySelector('body'); return b ? b.innerText.slice(0,5000) : ''; }}"
                )
            except:
                text = ""
            text_path = out_dir / "page_text.txt"
            text_path.write_text(text, encoding="utf-8")
            print(f"文字已保存: {{text_path}} ({{len(text)}} 字)")

            # 标题
            try:
                title = await page.title()
            except:
                title = url
            print(f"标题: {{title}}")

            # PDF
            try:
                pdf_path = out_dir / "page.pdf"
                await page.pdf(path=str(pdf_path), print_background=True, format="A4")
                print(f"PDF: {{pdf_path}}")
            except Exception as e:
                print(f"PDF失败(忽略): {{e}}")

            result = {{
                "success": True,
                "url": url,
                "title": title,
                "text": text[:3000],
                "screenshot": str(shot),
                "pdf": str(out_dir / "page.pdf") if (out_dir / "page.pdf").exists() else ""
            }}
        except Exception as e:
            result = {{"success": False, "url": url, "error": str(e)[:200]}}
            print(f"失败: {{e}}")
        finally:
            await browser.close()

    # 保存结果
    (out_dir / "visit_result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
    print("完成")

asyncio.run(main())
'''


# ─────────────────────────────────────────────────────────
# 主执行流程
# ─────────────────────────────────────────────────────────

def run():
    print("[SubAgent] 启动（脚本优先模式）...")

    # 读取任务
    task = None
    for _ in range(600):
        if TASK_FILE and Path(TASK_FILE).exists():
            try:
                raw = Path(TASK_FILE).read_text(encoding="utf-8")
                task = json.loads(raw)
                print(f"[SubAgent] 收到任务: {task.get('instruction','')[:80]}")
                break
            except:
                pass
        time.sleep(1)

    if not task:
        result = {"status": "error", "error": "任务超时"}
    else:
        output_dir = Path(task.get("output_dir", str(SUBAGENT_OUTPUT_DIR)))
        # 修复：不要二次嵌套 artifacts/
        if output_dir.name == "artifacts" and (output_dir.parent.name == "artifacts" or "subagent_workspace" in str(output_dir)):
            pass  # 已经是正确路径
        else:
            output_dir = output_dir  # 使用 task.json 中的路径

        output_dir.mkdir(parents=True, exist_ok=True)
        instruction = task.get("instruction", "")
        queries = task.get("queries", [])
        max_pages = task.get("max_pages", 10)

        print(f"[SubAgent] 输出目录: {output_dir}")
        print(f"[SubAgent] 查询词: {queries}")
        print(f"[SubAgent] 最大页面数: {max_pages}")

        # ─── 判断任务类型 ───
        scraped_data = []

        # 从 instruction 中提取 URL（格式："访问URL: https://..."）
        url_match = re.search(r'https?://[^\s\)\"\'\]]+', instruction)
        is_url_task = bool(url_match)

        if is_url_task:
            # URL 访问任务
            target_url = url_match.group(0)
            print(f"[SubAgent] 检测到URL访问任务: {target_url}")
            script = output_dir / "visit_url.py"
            write_script(script, build_url_visit_script(target_url, output_dir))
            sr = run_script(script, timeout=120)
            if sr.get("success"):
                # 读取 visit_result.json
                vr_path = output_dir / "visit_result.json"
                if vr_path.exists():
                    vr = json.loads(vr_path.read_text(encoding="utf-8"))
                    scraped_data = [{
                        "url": target_url,
                        "title": vr.get("title", ""),
                        "text": vr.get("text", ""),
                        "screenshot": vr.get("screenshot", ""),
                        "success": vr.get("success", False)
                    }]
        else:
            # 搜索研究任务
            search_queries = queries if queries else [instruction]
            if not search_queries:
                search_queries = [instruction]

            print(f"[SubAgent] 执行搜索研究: {search_queries}")
            script = output_dir / "research.py"
            write_script(script, build_research_script(search_queries, output_dir, max_pages))
            sr = run_script(script, timeout=300)

            # 读取爬取结果
            scraped_file = output_dir / "scraped_data.json"
            if scraped_file.exists():
                scraped_data = json.loads(scraped_file.read_text(encoding="utf-8"))
                print(f"[SubAgent] 读取到 {len(scraped_data)} 条数据")

        # ─── 质量评估 ───
        successful = [d for d in scraped_data if d.get("success")]
        breadth = len(set(d.get("url", "") for d in successful))
        total_text = sum(len(d.get("text", "")) for d in successful)
        depth = total_text // max(1, len(successful)) if successful else 0
        relevance = min(10, breadth + depth // 500) if successful else 0

        summary = (
            f"已收集 {len(successful)} 个页面，来自 {breadth} 个不同网站，"
            f"总字数约 {total_text} 字，平均深度 {depth} 字/页"
        )
        if successful:
            titles = [d.get("title", "")[:40] for d in successful[:3] if d.get("title")]
            if titles:
                summary += f"\n主要来源: {', '.join(titles)}"

        print(f"[SubAgent] 质量: 广度={breadth}, 深度={depth}, 相关性={relevance}")

        # ─── 生成 result.json ───
        result_files = []
        for d in successful:
            shot = d.get("screenshot", "")
            if shot and Path(shot).exists():
                result_files.append({
                    "path": shot,
                    "source_url": d.get("url", ""),
                    "desc": d.get("title", "")[:50]
                })
            text = d.get("text", "")
            if text:
                text_file = output_dir / f"text_{d.get('idx', 0)}.txt"
                text_file.write_text(text, encoding="utf-8")
                result_files.append({
                    "path": str(text_file),
                    "source_url": d.get("url", ""),
                    "desc": "页面文字"
                })

        result = {
            "task_id": task.get("task_id", ""),
            "status": "done",
            "findings": summary,
            "files": result_files,
            "scraped_count": len(successful),
            "quality": {
                "breadth": breadth,
                "depth": depth,
                "relevance": relevance
            },
            "summary": summary
        }
        print("[SubAgent] 准备写入结果")

    # 写入结果
    if RESULT_FILE:
        Path(RESULT_FILE).write_text(
            json.dumps(result, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
        print(f"[SubAgent] 结果已写入: {RESULT_FILE}")
    else:
        print(f"[SubAgent] 结果: {result}")

    print("[SubAgent] 子代理退出")
    return result


if __name__ == "__main__":
    run()
