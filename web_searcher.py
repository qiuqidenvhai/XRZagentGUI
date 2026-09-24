#!/usr/bin/env python3
"""
web_searcher.py - 网页搜索和抓取工具（子代理 browser_search 的后端）

【2026-09-21 彻底重写】旧实现是坏的：build_search_urls 生成的是【必应搜索结果页
本身】（first=1,11,21...），从头到尾没有解析过结果链接、没有抓过文章正文 ——
所谓 findings 全是必应 SERP 的导航菜单/边栏垃圾，这正是「子代理只获取到搜索
引擎导航页，提不到任何实质内容」的根因。

新实现（纯标准库，不依赖浏览器，不占母代理浏览器状态）：
1. 搜索：DuckDuckGo HTML 端点（html.duckduckgo.com/html/?q=，静态页、好解析）
   为主，Bing SERP（li.b_algo）为备 —— 两家都解析【真实结果链接】，不再把搜索
   页自己当结果。
2. 抓正文：抓 top N 个结果页，剥 script/style/nav/footer/aside 后优先收集
   <p> 段落（≥40 字的算正文段），没有段落再退回全文剥标签 —— 拿到的是文章
   内容，不是页面导航。
3. 兼容旧 CLI：python web_searcher.py <查询> [最大页数] [输出文件]
   输出 JSON：{query, scraped_count, findings}
"""
import sys
import time
import json
import re
import base64
import html as _html
import urllib.request
import urllib.parse
from typing import List, Dict, Optional

QUERY = ""
MAX_PAGES = 5
OUTPUT_FILE = ""

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
_HEADERS = {
    "User-Agent": _UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}


def fetch(url: str, timeout: int = 10) -> tuple:
    """抓取单个页面，自动按 HTTP 头 / meta charset 解码。返回 (html或None, 最终url或错误)"""
    try:
        req = urllib.request.Request(url, headers=_HEADERS)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            final_url = resp.geturl()
        enc = "utf-8"
        m = re.search(r"charset=([\w-]+)", resp.headers.get("Content-Type", "") or "",
                      re.I)
        if m:
            enc = m.group(1)
        else:
            m = re.search(rb'charset=["\']?([\w-]+)', raw[:2048], re.I)
            if m:
                enc = m.group(1).decode("ascii", "ignore")
        try:
            return raw.decode(enc, errors="replace"), final_url
        except (LookupError, UnicodeDecodeError):
            for e2 in ("utf-8", "gbk", "big5"):
                try:
                    return raw.decode(e2, errors="replace"), final_url
                except Exception:
                    continue
            return raw.decode("utf-8", errors="replace"), final_url
    except Exception as e:
        return None, str(e)


def _strip_noise(html: str) -> str:
    """剥掉 script/style/注释 和整个结构噪声块"""
    html = re.sub(r"(?is)<!--.*?-->", " ", html)
    for tag in ("script", "style", "noscript", "svg", "iframe", "form"):
        html = re.sub(rf"(?is)<{tag}\b.*?</{tag}>", " ", html)
    html = re.sub(r"(?is)<header\b.*?</header>", " ", html)
    html = re.sub(r"(?is)<footer\b.*?</footer>", " ", html)
    html = re.sub(r"(?is)<nav\b.*?</nav>", " ", html)
    html = re.sub(r"(?is)<aside\b.*?</aside>", " ", html)
    return html


def _tags_to_text(html: str) -> str:
    text = re.sub(r"(?s)<[^>]+>", "\n", html)
    text = _html.unescape(text)
    text = re.sub(r"[ \t\u3000]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    return text.strip()


def extract_article(html: str, cap: int = 3200) -> str:
    """提取正文：优先 <p> 段落聚合（≥40 字算正文段），退化到全文剥标签。"""
    html = _strip_noise(html)
    paras = []
    try:
        for pm in re.finditer(r"(?is)<p\b[^>]*>(.*?)</p>", html):
            t = _tags_to_text(pm.group(1))
            t = t.strip()
            if len(t) >= 40:
                paras.append(t)
    except Exception:
        pass
    if sum(len(p) for p in paras) >= 200:
        out = "\n".join(paras)
        return out[:cap] + ("…[已截断]" if len(out) > cap else "")
    # 退化：全文文本（去掉短行/菜单行）
    text = _tags_to_text(html)
    lines = [l.strip() for l in text.split("\n") if len(l.strip()) >= 12]
    out = "\n".join(lines)
    return out[:cap] + ("…[已截断]" if len(out) > cap else "")


# ───────────────────────── 搜索引擎解析 ─────────────────────────

def _ddg_decode(href: str) -> Optional[str]:
    """DuckDuckGo 的 /l/?uddg=<urlencoded> 包装链接 → 真实 URL；直链原样返回。"""
    if not href:
        return None
    if href.startswith("//"):
        href = "https:" + href
    m = re.search(r"uddg=([^&]+)", href)
    if m:
        return urllib.parse.unquote(m.group(1))
    if href.startswith("http://") or href.startswith("https://"):
        return href
    return None


def search_ddg(query: str, want: int) -> List[Dict]:
    """DuckDuckGo HTML 端点：解析 result__a 链接 + result__snippet 摘要。"""
    q = urllib.parse.quote(query)
    url = f"https://html.duckduckgo.com/html/?q={q}"
    content, info = fetch(url, timeout=12)
    if not content:
        print(f"[DDG] 失败: {info}", flush=True)
        return []
    results = []
    # 每个 <div class="result..."> 块里取 result__a（链接+标题）与 result__snippet
    for blk in re.finditer(
            r'(?is)<a[^>]+class="[^"]*result__a[^"]*"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
            content):
        real = _ddg_decode(_html.unescape(blk.group(1)))
        title = _tags_to_text(blk.group(2)).strip()
        if real and title:
            results.append({"url": real, "title": title, "snippet": ""})
        if len(results) >= want:
            break
    # 补摘要（与链接同块，宽松匹配）
    if results:
        snips = [_tags_to_text(m.group(1)).strip()
                 for m in re.finditer(r'(?is)<a[^>]+class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</a>',
                                      content)]
        for i, r in enumerate(results):
            if i < len(snips):
                r["snippet"] = snips[i][:300]
    print(f"[DDG] 解析到 {len(results)} 条结果", flush=True)
    return results


def _bing_decode(href: str) -> Optional[str]:
    """Bing 的 /ck/a?...&u=a1<base64url> 包装 → 真实 URL；直链原样返回。"""
    if not href:
        return None
    if href.startswith("//"):
        href = "https:" + href
    m = re.search(r"[?&]u=a1([A-Za-z0-9_\-=%]+)", href)
    if m:
        try:
            b = m.group(1).replace("%3D", "=").replace("%2F", "/")
            b += "=" * (-len(b) % 4)
            return base64.urlsafe_b64decode(b).decode("utf-8", "replace")
        except Exception:
            return None
    if href.startswith("http://") or href.startswith("https://"):
        return href
    return None


def search_bing(query: str, want: int) -> List[Dict]:
    """Bing SERP：解析 li.b_algo 里的 h2>a 真实链接。"""
    q = urllib.parse.quote(query)
    url = f"https://www.bing.com/search?q={q}&count={max(want, 10)}"
    content, info = fetch(url, timeout=12)
    if not content:
        print(f"[Bing] 失败: {info}", flush=True)
        return []
    results = []
    for blk in re.finditer(r'(?is)<h2[^>]*>\s*<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
                           content):
        real = _bing_decode(_html.unescape(blk.group(1)))
        title = _tags_to_text(blk.group(2)).strip()
        if real and title and "bing.com" not in urllib.parse.urlparse(real).netloc:
            results.append({"url": real, "title": title, "snippet": ""})
        if len(results) >= want:
            break
    print(f"[Bing] 解析到 {len(results)} 条结果", flush=True)
    return results


def search_baidu(query: str, want: int) -> List[Dict]:
    """百度 SERP：解析 h3>a 链接（/link?url= 跳转在抓取时自动 302 解析）。
    【国内环境主力引擎】DDG 被墙、Bing 中国区对 CJK 长查询相关性极差（实测
    「GitHub 2026 star 增长最快 AI Agent」只回 GitHub 官网导航页），百度对
    中文技术查询的相关性最好。"""
    q = urllib.parse.quote(query)
    url = f"https://www.baidu.com/s?wd={q}&rn={max(want, 10)}"
    content, info = fetch(url, timeout=12)
    if not content:
        print(f"[Baidu] 失败: {info}", flush=True)
        return []
    results = []
    for m in re.finditer(r'(?is)<h3[^>]*>\s*<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
                         content):
        href = _html.unescape(m.group(1))
        title = _tags_to_text(m.group(2)).strip()
        if not title:
            continue
        # 跳过百度自家内容页（贴吧/知道/百科以外的内链），link 跳转保留
        if ("/link?url=" in href
                or "baijiahao.baidu.com" in href
                or not href.startswith("http")):
            pass  # /link?url= 和 baijiahao 都是有效结果
        results.append({"url": href, "title": title, "snippet": ""})
        if len(results) >= want:
            break
    print(f"[Baidu] 解析到 {len(results)} 条结果", flush=True)
    return results


def _has_cjk(s: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", s or ""))


def search(query: str, want: int) -> List[Dict]:
    """引擎选择：中文查询 → Baidu → Bing → DDG；英文查询 → DDG → Baidu → Bing。
    （DDG 在国内被墙，连接直接被拒，所以只作海外查询首选/末位兜底）"""
    if _has_cjk(query):
        order = [search_baidu, search_bing, search_ddg]
    else:
        order = [search_ddg, search_baidu, search_bing]
    results = []
    seen = set()
    for fn in order:
        if len(results) >= want:
            break
        try:
            for r in fn(query, want - len(results)):
                if r["url"] in seen:
                    continue
                results.append(r)
                seen.add(r["url"])
                if len(results) >= want:
                    break
        except Exception as e:
            print(f"[{fn.__name__}] 异常: {e}", flush=True)
    return results[:want]


def search_and_scrape(query: str, max_pages: int = 5) -> Dict:
    """搜索 → 逐个抓【真实结果】的正文。

    【超额候选池】目标 max_pages 页正文，但搜索时多拿一倍候选：总有站点挂掉
    （5xx）、JS 渲染抓不到正文或跳转失败，只搜刚好 N 条会浪费大半配额。
    抓成功满 max_pages 页或候选耗尽即停（总尝试 ≤ max_pages*3，控时）。"""
    print(f"[搜索] 查询: {query}", flush=True)
    max_pages = max(1, min(int(max_pages or 3), 6))   # 防呆：1~6 页
    pool = search(query, max_pages * 2 + 2)
    print(f"[搜索] 候选 {len(pool)} 条真实结果链接（目标抓 {max_pages} 页正文）", flush=True)

    findings = []
    scraped = 0
    attempts = 0
    max_attempts = max_pages * 3
    idx = 0
    while idx < len(pool) and scraped < max_pages and attempts < max_attempts:
        r = pool[idx]
        idx += 1
        attempts += 1
        content, info = fetch(r["url"], timeout=10)
        # 百度 /link?url= 跳转：抓取时自动 302，最终 URL 才是真来源
        real_url = info if (content and info.startswith("http")) else r["url"]
        head = (f"### 结果{scraped + 1}: {r['title']}\nURL: {real_url}\n"
                + (f"摘要: {r['snippet']}\n" if r["snippet"] else ""))
        if content:
            body = extract_article(content)
            if len(body) >= 80:
                scraped += 1
                findings.append(head + f"正文:\n{body}\n")
                print(f"[完成] {real_url} - 正文 {len(body)} 字符", flush=True)
                time.sleep(0.4)
                continue
            print(f"[过短] {real_url} - {len(body)} 字符（疑似 JS 渲染页），换下一个", flush=True)
        else:
            print(f"[失败] {real_url} - {info}，换下一个", flush=True)
        time.sleep(0.4)   # 避免请求过快

    if not pool:
        findings.append("搜索无结果（可能网络受限或查询过于生僻），请换关键词或用其它信息来源。\n")

    result = {
        "query": query,
        "scraped_count": scraped,
        "result_count": len(pool),
        "findings": "\n\n---\n\n".join(findings),
    }

    if OUTPUT_FILE:
        try:
            with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False, indent=2)
            print(f"[保存] 结果已保存到: {OUTPUT_FILE}", flush=True)
        except Exception as e:
            print(f"[保存失败] {e}", flush=True)

    return result


def main():
    global QUERY, MAX_PAGES, OUTPUT_FILE

    if len(sys.argv) < 2:
        print("用法: python web_searcher.py <查询内容> [最大页数] [输出文件]")
        sys.exit(1)

    QUERY = sys.argv[1]
    try:
        MAX_PAGES = int(sys.argv[2]) if len(sys.argv) > 2 else 5
    except ValueError:
        MAX_PAGES = 5
    OUTPUT_FILE = sys.argv[3] if len(sys.argv) > 3 else ""

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    print(f"[开始] 查询: {QUERY}, 最大页数: {MAX_PAGES}", flush=True)
    result = search_and_scrape(QUERY, MAX_PAGES)
    print(f"\n[完成] 解析 {result.get('result_count', 0)} 条结果，"
          f"抓到 {result['scraped_count']} 页正文", flush=True)
    # stdout 里带一份 findings（commander 在结果文件缺失时读 stdout）
    print(result["findings"], flush=True)
    return result


if __name__ == "__main__":
    main()
