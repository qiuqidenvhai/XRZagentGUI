#!/usr/bin/env python3
"""
web_searcher.py - 网页搜索和抓取工具
不依赖浏览器，直接用 HTTP 请求抓取页面内容
"""
import sys
import time
import json
import re
import urllib.request
from typing import List, Dict

QUERY = ""
MAX_PAGES = 5
OUTPUT_FILE = ""


def build_search_urls(query: str, max_results: int = 5) -> List[str]:
    """构建 Bing 搜索 URL"""
    import urllib.parse
    encoded = urllib.parse.quote(query)
    return [f"https://www.bing.com/search?q={encoded}&first={i*10+1}" for i in range(max_results)]


def fetch(url: str, timeout: int = 15) -> tuple:
    """抓取单个页面"""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace"), resp.geturl()
    except Exception as e:
        return None, str(e)


def extract_text(html: str) -> str:
    """从 HTML 中提取纯文本"""
    html = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r"<style[^>]*>.*?</style>", "", html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r"<[^>]+>", " ", html)
    html = re.sub(r"\s{2,}", " ", html)
    text = html.strip()
    text = text.replace("\u3000", " ").replace("&nbsp;", " ").replace("&amp;", "&")
    text = text.replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"')
    return text[:8000]


def search_and_scrape(query: str, max_pages: int = 5) -> Dict:
    """执行搜索并抓取页面"""
    print(f"[搜索] 查询: {query}", flush=True)
    
    findings = []
    scraped = 0
    
    urls = build_search_urls(query, max_pages)
    print(f"[搜索] 生成 {len(urls)} 个搜索URL", flush=True)
    
    for url in urls:
        print(f"[抓取] {url}", flush=True)
        content, final_url = fetch(url)
        
        if content:
            text = extract_text(content)
            findings.append(f"## 来源: {final_url}\n\n{text}\n")
            scraped += 1
            print(f"[完成] {final_url} - {len(text)} 字符", flush=True)
        else:
            findings.append(f"## 来源: {url}\n失败: {content}\n")
            print(f"[失败] {url} - {final_url}", flush=True)
        
        time.sleep(0.5)  # 避免请求过快
    
    result = {
        "query": query,
        "scraped_count": scraped,
        "findings": "\n\n---\n\n".join(findings),
    }
    
    if OUTPUT_FILE:
        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"[保存] 结果已保存到: {OUTPUT_FILE}", flush=True)
    
    return result


def main():
    global QUERY, MAX_PAGES, OUTPUT_FILE
    
    if len(sys.argv) < 2:
        print("用法: python web_searcher.py <查询内容> [最大页数] [输出文件]")
        sys.exit(1)
    
    QUERY = sys.argv[1]
    MAX_PAGES = int(sys.argv[2]) if len(sys.argv) > 2 else 5
    OUTPUT_FILE = sys.argv[3] if len(sys.argv) > 3 else ""
    
    # 设置输出编码
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    
    print(f"[开始] 查询: {QUERY}, 最大页数: {MAX_PAGES}", flush=True)
    
    result = search_and_scrape(QUERY, MAX_PAGES)
    
    print(f"\n[完成] 抓取 {result['scraped_count']} 个页面", flush=True)
    
    if OUTPUT_FILE:
        print(f"\n[文件] 完整结果: {OUTPUT_FILE}")
    
    return result


if __name__ == "__main__":
    main()