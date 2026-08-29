"""PPTX 构建器：基于开源模板库生成演示文稿。

设计目标：
1. 使用 D:/软件/XianRenZhangAgent/agent_core/templates/pptx_templates/ 中的
   19 套专业中文 PPT 模板（GordenPPTSkill 仓库）。
2. 通过 build_pptx.py 脚本 + edits.json 驱动，保留模板原有布局/字体/配色/图片。
3. 支持根据主题/场景自动选模板，智能分页，杜绝「一页塞满全部文字」。
4. 内置 fallback：如果模板构建失败，回退到纯色主题绘制（不丢失内容）。
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any, Dict, List

# ── 模板库路径 ───────────────────────────────────────────────────────────
TEMPLATE_ROOT = Path(__file__).parent / "templates" / "pptx_templates"
TEMPLATES_DIR = TEMPLATE_ROOT / "templates"
BUILD_SCRIPT  = TEMPLATE_ROOT / "scripts" / "build_pptx.py"

# ── 模板元数据 ────────────────────────────────────────────────────────────
TEMPLATE_CATALOG: Dict[str, Dict[str, Any]] = {
    "minimal-business-summary": {
        "name": "简约商务总结汇报",
        "slides": 16,
        "style": "极简商务留白",
        "scenes": ["季度汇报", "年度总结", "工作总结"],
        "recommended_slides": [1, 2, 5, 7, 9, 10, 12, 13, 14, 16],
    },
    "geometric-summary": {
        "name": "多彩几何工作总结",
        "slides": 21,
        "style": "几何切片 + 大字",
        "scenes": ["工作总结", "活力汇报"],
        "recommended_slides": [1, 3, 4, 6, 8, 10, 12, 14, 16, 18, 21],
    },
    "mckinsey-style": {
        "name": "麦肯锡风专业模板",
        "slides": 37,
        "style": "咨询逻辑结构",
        "scenes": ["商务提案", "方案路演", "咨询报告"],
        "recommended_slides": [1, 2, 5, 8, 11, 14, 17, 20, 23, 26, 29, 32, 35, 37],
    },
    "architecture-deck": {
        "name": "架构图合辑",
        "slides": 37,
        "style": "大厂架构图",
        "scenes": ["架构设计", "系统拓扑", "流程图"],
        "recommended_slides": [1, 3, 5, 7, 9, 11, 13, 15, 17, 19, 21, 23, 25, 27, 29, 31, 33, 35, 37],
    },
    "data-viz-deck": {
        "name": "数据可视化合辑",
        "slides": 41,
        "style": "原生 chart 页",
        "scenes": ["数据汇报", "数据分析", "业绩展示"],
        "recommended_slides": [1, 2, 4, 6, 8, 10, 12, 14, 16, 18, 20, 22, 24, 26, 28, 30, 32, 34, 36, 38, 41],
    },
    "report-savior": {
        "name": "汇报救命合辑",
        "slides": 44,
        "style": "商业汇报全场景",
        "scenes": ["SWOT", "PEST", "鱼骨图", "树状图"],
        "recommended_slides": [1, 3, 5, 7, 9, 11, 13, 15, 17, 19, 21, 23, 25, 27, 29, 31, 33, 35, 37, 39, 41, 44],
    },
    "competition-speech": {
        "name": "竞聘述职合辑",
        "slides": 59,
        "style": "竞聘/述职/PDCA/SWOT",
        "scenes": ["述职", "竞聘", "晋升答辩"],
        "recommended_slides": [1, 3, 5, 7, 9, 11, 13, 15, 17, 19, 21, 23, 25, 27, 29, 31, 33, 35, 37, 39, 41, 43, 45, 47, 49, 51, 53, 55, 57, 59],
    },
    "red-patriot-youth": {
        "name": "新时代新青年红色教育",
        "slides": 16,
        "style": "党政红 + 飘带",
        "scenes": ["思政课件", "党课", "爱国教育"],
        "recommended_slides": [1, 2, 4, 6, 8, 10, 12, 14, 16],
    },
    "cute-orange-class": {
        "name": "橙色可爱卡通教学",
        "slides": 17,
        "style": "卡通手绘",
        "scenes": ["教学", "培训", "幼儿园"],
        "recommended_slides": [1, 2, 4, 6, 8, 10, 12, 14, 16, 17],
    },
}

# 主题名 → 推荐模板 slug 映射（模糊匹配）
THEME_TO_TEMPLATE: Dict[str, str] = {
    "blue": "minimal-business-summary",
    "business": "minimal-business-summary",
    "简约": "minimal-business-summary",
    "商务": "minimal-business-summary",
    "green": "geometric-summary",
    "purple": "mckinsey-style",
    "架构图": "architecture-deck",
    "架构": "architecture-deck",
    "数据": "data-viz-deck",
    "图表": "data-viz-deck",
    "汇报": "report-savior",
    "述职": "competition-speech",
    "竞聘": "competition-speech",
    "红色": "red-patriot-youth",
    "党政": "red-patriot-youth",
    "教学": "cute-orange-class",
    "卡通": "cute-orange-class",
}


# ── 内容解析 ──────────────────────────────────────────────────────────────

def _norm_lines(value: Any) -> List[str]:
    """把任意形态的内容规整成「行」列表。"""
    if value is None:
        return []
    if isinstance(value, dict):
        return [f"{k}: {v}" for k, v in value.items()]
    if isinstance(value, (list, tuple)):
        out: List[str] = []
        for item in value:
            out.extend(_norm_lines(item))
        return out
    text = str(value)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [ln.rstrip() for ln in text.split("\n")]
    return [ln for ln in lines if ln.strip()]


def _strip_md(line: str) -> str:
    s = line.strip()
    s = re.sub(r"^#{1,6}\s*", "", s)
    s = re.sub(r"^[-*+]\s+", "", s)
    s = re.sub(r"^\d+[.)、]\s*", "", s)
    s = s.replace("**", "").replace("`", "")
    return s.strip()


def _truncate(line: str, limit: int = 80) -> str:
    line = line.strip()
    return line if len(line) <= limit else line[: limit - 1] + "…"


def parse_to_slides(raw_content: Any, deck_title: str) -> List[Dict[str, Any]]:
    """把 AI 传来的任意内容解析成幻灯片列表。"""
    slides: List[Dict[str, Any]] = []

    # ── 形态 A：结构化 dict ──
    if isinstance(raw_content, dict) and isinstance(raw_content.get("slides"), list):
        title = str(raw_content.get("title") or deck_title)
        for i, sd in enumerate(raw_content["slides"], start=1):
            if isinstance(sd, dict):
                heading = sd.get("heading") or sd.get("title") or f"第 {i} 页"
                # 优先取 items，然后 content/body/text
                body = sd.get("items") or sd.get("content") or sd.get("body") or sd.get("text") or ""
                kind = sd.get("kind") or "content"
            else:
                heading = f"第 {i} 页"
                body = sd
                kind = "content"
            bullets = [_truncate(_strip_md(x)) for x in _norm_lines(body)]
            bullets = [b for b in bullets if b]
            slides.append({"heading": str(heading).strip(), "bullets": bullets, "kind": kind})
        if not slides:
            slides.append({"heading": title, "bullets": [], "kind": "content"})
        return slides

    # ── 形态 B / C：文本 ──
    lines = _norm_lines(raw_content)

    has_md_heading = any(re.match(r"^#{1,6}\s+\S", ln) for ln in lines)
    if has_md_heading:
        cur_heading: str | None = None
        cur_bullets: List[str] = []
        for ln in lines:
            m = re.match(r"^(#{1,6})\s+(.*)", ln)
            if m:
                if cur_heading is not None:
                    slides.append({"heading": cur_heading, "bullets": cur_bullets, "kind": "content"})
                level = len(m.group(1))
                cur_heading = _truncate(m.group(2).strip() or "标题")
                cur_bullets = []
                if level == 1 and slides:
                    cur_heading = None
            else:
                txt = _truncate(_strip_md(ln))
                if txt:
                    cur_bullets.append(txt)
        if cur_heading is not None:
            slides.append({"heading": cur_heading, "bullets": cur_bullets, "kind": "content"})

        slides = [
            s for s in slides
            if not (s.get("kind") == "content"
                    and not s.get("bullets")
                    and s.get("heading", "").strip() == str(deck_title).strip())
        ]
        return _split_long_slides(slides) if slides else [
            {"heading": str(deck_title), "bullets": [], "kind": "content"}
        ]

    # 形态 C：纯文本 → 按行分页
    if not lines:
        return [{"heading": deck_title, "bullets": [], "kind": "content"}]

    MAX_LINES_PER_SLIDE = 7
    chunks = [lines[i: i + MAX_LINES_PER_SLIDE]
              for i in range(0, len(lines), MAX_LINES_PER_SLIDE)]
    multi = len(chunks) > 1
    for i, chunk in enumerate(chunks, start=1):
        if not chunk:
            continue
        heading = f"{deck_title}（{i}）" if multi else deck_title
        bullets = [_truncate(_strip_md(x)) for x in chunk]
        bullets = [b for b in bullets if b]
        slides.append({"heading": heading, "bullets": bullets, "kind": "content"})
    return _split_long_slides(slides)


def _split_long_slides(slides: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """单页要点过多时，拆成多页。"""
    out: List[Dict[str, Any]] = []
    for sd in slides:
        bullets = sd.get("bullets") or []
        if sd.get("kind") == "section" or len(bullets) <= 7:
            out.append(sd)
            continue
        for i in range(0, len(bullets), 7):
            part = bullets[i: i + 7]
            suffix = "" if i == 0 else f"（续 {i // 7 + 1}）"
            out.append({
                "heading": f"{sd['heading']}{suffix}",
                "bullets": part,
                "kind": "content",
            })
    return out


# ── 模板选择 ──────────────────────────────────────────────────────────────

def select_template(theme_name: str = "blue", context_hint: str = "") -> str:
    """根据主题名和上下文提示，选择最适合的模板 slug。"""
    hint = (theme_name + " " + context_hint).lower()

    if theme_name in TEMPLATE_CATALOG:
        return theme_name

    for keyword, slug in THEME_TO_TEMPLATE.items():
        if keyword in hint:
            return slug

    return "minimal-business-summary"


# ── 构建 edits.json ───────────────────────────────────────────────────────

def build_edits_json(
    template_slug: str,
    slides: List[Dict[str, Any]],
    deck_title: str,
    subtitle: str = "",
) -> Dict[str, Any]:
    """把解析好的幻灯片列表转成 build_pptx.py 需要的 edits.json 结构。"""
    template_dir = TEMPLATES_DIR / template_slug
    detail_path = template_dir / "detail.json"
    template_path = template_dir / "template.pptx"

    if not detail_path.exists() or not template_path.exists():
        raise FileNotFoundError(f"模板不存在: {template_slug}")

    with open(detail_path, encoding="utf-8") as f:
        detail = json.load(f)

    page_roles = detail.get("page_roles", {})
    all_pages = detail.get("pages", [])

    # 收集各 role 的幻灯片编号（1-indexed）
    cover_slides    = page_roles.get("cover", [1])
    agenda_slides   = page_roles.get("agenda", [])
    section_slides  = page_roles.get("section_divider", [])
    content_slides  = page_roles.get("content", [])
    ending_slides   = page_roles.get("ending", [])

    selected: List[int] = []
    edits: List[Dict] = []

    # ── 1. 封面（必选） ──
    cover_s = cover_slides[0] if cover_slides else 1
    selected.append(cover_s)

    cover_page = next((p for p in all_pages if p["slide_number"] == cover_s), None)
    if cover_page:
        for slot in cover_page.get("text_slots", []):
            role = slot.get("role", "")
            slot_id = slot["slot_id"]
            # 判断英文副标还是中文主标题
            if "英文" in role or "副标" in role:
                new_text = subtitle[: slot.get("max_chars", 14)] if subtitle else ""
            elif "中文" in role or "主标题" in role:
                new_text = deck_title[: slot.get("max_chars", 20)]
            else:
                # 根据位置判断：第一个槽位放英文，第二个放中文
                slot_idx = next((i for i, s in enumerate(cover_page["text_slots"]) if s["slot_id"] == slot_id), 0)
                if slot_idx == 0:
                    new_text = subtitle[: slot.get("max_chars", 14)] if subtitle else ""
                else:
                    new_text = deck_title[: slot.get("max_chars", 20)]
            edits.append({"slide": cover_s, "slot_id": slot_id, "new_text": new_text})

    # ── 2. 目录页（如果有多个章节） ──
    if len(slides) > 2 and agenda_slides:
        agenda_s = agenda_slides[0]
        selected.append(agenda_s)
        agenda_page = next((p for p in all_pages if p["slide_number"] == agenda_s), None)
        if agenda_page:
            for slot in agenda_page.get("text_slots", []):
                role = slot.get("role", "")
                sid = slot["slot_id"]
                if "目录" in role and "英文" not in role:
                    edits.append({"slide": agenda_s, "slot_id": sid, "new_text": "目录"})
                elif "英文" in role and "目录" in role:
                    edits.append({"slide": agenda_s, "slot_id": sid, "new_text": "Contents"})
            # 填充章节名
            ch_names = [sd.get("heading", "") for sd in slides[:4]]
            for i, ch in enumerate(ch_names, start=1):
                cn_slot = f"agenda_ch{i}_cn"
                en_slot = f"agenda_ch{i}_en"
                for slot in agenda_page.get("text_slots", []):
                    if slot["slot_id"] == cn_slot:
                        edits.append({"slide": agenda_s, "slot_id": cn_slot,
                                      "new_text": ch[: slot.get("max_chars", 6)]})
                    elif slot["slot_id"] == en_slot:
                        edits.append({"slide": agenda_s, "slot_id": en_slot,
                                      "new_text": ch[: slot.get("max_chars", 10)]})

    # ── 3. 内容页：按章节分配 ──
    content_used = 0
    section_idx = 0

    for sd in slides:
        heading = sd.get("heading", "")
        bullets = sd.get("bullets", [])
        if not bullets:
            continue

        # 选一个 section_divider
        if section_idx < len(section_slides):
            sec_s = section_slides[section_idx]
            selected.append(sec_s)
            sec_page = next((p for p in all_pages if p["slide_number"] == sec_s), None)
            if sec_page:
                for slot in sec_page.get("text_slots", []):
                    sid = slot["slot_id"]
                    if sid.endswith("_cn") and not sid.endswith("_number"):
                        edits.append({"slide": sec_s, "slot_id": sid,
                                      "new_text": heading[: slot.get("max_chars", 8)]})
                    elif sid.endswith("_en"):
                        edits.append({"slide": sec_s, "slot_id": sid,
                                      "new_text": heading[: slot.get("max_chars", 14)]})
                section_idx += 1

        # 为当前章节分配 content slides
        per_slide = 7
        for bi in range(0, len(bullets), per_slide):
            if content_used >= len(content_slides):
                break
            chunk = bullets[bi: bi + per_slide]
            cont_s = content_slides[content_used]
            selected.append(cont_s)
            cont_page = next((p for p in all_pages if p["slide_number"] == cont_s), None)
            if cont_page:
                # 面包屑：只替换中文和英文，序号保持原样
                for slot in cont_page.get("text_slots", []):
                    role = slot.get("role", "")
                    sid = slot["slot_id"]
                    if "面包屑章节中文" in role:
                        edits.append({"slide": cont_s, "slot_id": sid,
                                      "new_text": heading[: slot.get("max_chars", 14)]})
                    elif "面包屑章节英文" in role:
                        edits.append({"slide": cont_s, "slot_id": sid,
                                      "new_text": heading[: slot.get("max_chars", 14)]})
                # 把 bullets 填入 title 槽位（body 保持原样）
                # 提取所有非 body 的 slot（title/num 类）
                content_slots = [s for s in cont_page.get("text_slots", [])
                                 if "body" not in s.get("role", "").lower()
                                 and "面包屑" not in s.get("role", "")]
                for ci, bullet_text in enumerate(chunk):
                    if ci < len(content_slots):
                        edits.append({"slide": cont_s, "slot_id": content_slots[ci]["slot_id"],
                                      "new_text": bullet_text[: content_slots[ci].get("max_chars", 72)]})
                content_used += 1

    # ── 4. 结尾页 ──
    if ending_slides:
        selected.append(ending_slides[0])

    selected = list(dict.fromkeys(selected))

    return {
        "template_slug": template_slug,
        "selected_slides": selected,
        "edits": edits,
        "_meta": {
            "template_path": str(template_path),
            "detail_path": str(detail_path),
            "total_selected": len(selected),
            "total_edits": len(edits),
        },
    }


# ── 主入口 ────────────────────────────────────────────────────────────────

def build_pptx(out_path, raw_content: Any, deck_title: str = "演示文稿",
               theme_name: str = "blue", subtitle: str = "") -> Dict[str, Any]:
    """生成 PPT 文件。返回统计信息 dict。"""
    from pathlib import Path as _P

    out_path = _P(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    slides = parse_to_slides(raw_content, deck_title)
    if not slides:
        slides = [{"heading": deck_title, "bullets": [], "kind": "content"}]

    template_slug = select_template(theme_name)
    print(f"[pptx_builder] 选用模板: {template_slug} ({TEMPLATE_CATALOG.get(template_slug, {}).get('name', '?')})")

    try:
        edits_spec = build_edits_json(template_slug, slides, deck_title, subtitle)
    except FileNotFoundError as e:
        print(f"[pptx_builder] ⚠ 模板文件缺失，回退到纯色绘制: {e}")
        return _fallback_build(out_path, slides, deck_title, theme_name, subtitle)

    tmp_edits = out_path.with_suffix(".edits.json")
    try:
        tmp_edits.write_text(json.dumps(edits_spec, ensure_ascii=False, indent=2), encoding="utf-8")

        py_path = "D:/软件/Python/python.exe"
        result = subprocess.run(
            [py_path, str(BUILD_SCRIPT),
             str(TEMPLATES_DIR / template_slug / "template.pptx"),
             str(tmp_edits),
             str(out_path),
             "--detail", str(TEMPLATES_DIR / template_slug / "detail.json"),
             "--no-lint"],
            capture_output=True, text=True, timeout=60,
        )

        if result.returncode != 0:
            print(f"[pptx_builder] build_pptx.py 失败: {result.stderr}")
            return _fallback_build(out_path, slides, deck_title, theme_name, subtitle)

        print(f"[pptx_builder] ✅ PPT 生成成功: {out_path} ({edits_spec['_meta']['total_selected']} 页, {edits_spec['_meta']['total_edits']} 处修改)")
        return {
            "path": str(out_path),
            "slides": edits_spec["_meta"]["total_selected"],
            "theme": template_slug,
            "template_name": TEMPLATE_CATALOG.get(template_slug, {}).get("name", template_slug),
        }
    finally:
        # 中间文件只是过程产物：清理失败（文件被占用 / 权限受限 / 回收站不可用）
        # 绝不能影响已经生成好的 PPT，否则会让调用方误判为「生成失败」。
        try:
            if tmp_edits.exists():
                tmp_edits.unlink(missing_ok=True)
        except Exception as e:
            print(f"[pptx_builder] ⚠ 中间文件清理失败（不影响 PPT）: {tmp_edits.name}: {e}")


def _fallback_build(out_path, slides, deck_title, theme_name, subtitle):
    """回退：纯色主题绘制。"""
    from pptx import Presentation
    from pptx.util import Inches, Pt
    from pptx.enum.text import PP_ALIGN, MSO_ANCHOR

    THEMES = {
        "blue":   {"primary": "1F3A5F", "accent": "2E86AB", "text": "2B2B2B", "muted": "6B7A8F", "cover_bg": "1F3A5F"},
        "green":  {"primary": "1B4332", "accent": "2D6A4F", "text": "2B2B2B", "muted": "6B7A8F", "cover_bg": "1B4332"},
        "purple": {"primary": "3C2A5E", "accent": "6C4AB6", "text": "2B2B2B", "muted": "6B7A8F", "cover_bg": "3C2A5E"},
        "orange": {"primary": "7C3A10", "accent": "D97706", "text": "2B2B2B", "muted": "6B7A8F", "cover_bg": "7C3A10"},
    }
    theme = THEMES.get(theme_name, THEMES["blue"])

    def _rgb(h):
        from pptx.dml.color import RGBColor
        h = h.lstrip("#")
        return RGBColor(int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))

    def _tb(slide, l, t, w, h, text, sz, color, bold=False, align="left", valign="top"):
        box = slide.shapes.add_textbox(l, t, w, h)
        tf = box.text_frame
        tf.word_wrap = True
        tf.vertical_anchor = {"top": MSO_ANCHOR.TOP, "middle": MSO_ANCHOR.MIDDLE}.get(valign, MSO_ANCHOR.TOP)
        p = tf.paragraphs[0]
        p.alignment = {"left": PP_ALIGN.LEFT, "center": PP_ALIGN.CENTER, "right": PP_ALIGN.RIGHT}.get(align, PP_ALIGN.LEFT)
        run = p.add_run()
        run.text = text
        run.font.size = Pt(sz)
        run.font.bold = bold
        run.font.name = "微软雅黑"
        run.font.color.rgb = color
        return box

    prs = Presentation()
    prs.slide_width = Inches(13.333)
    prs.slide_height = Inches(7.5)

    # 封面
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    W, H = prs.slide_width, prs.slide_height
    bg = slide.shapes.add_shape(1, 0, 0, W, H)
    bg.fill.solid()
    bg.fill.fore_color.rgb = _rgb(theme["cover_bg"])
    bg.line.fill.background()
    bg.shadow.inherit = False
    bar = slide.shapes.add_shape(1, 0, 0, W, Inches(0.12))
    bar.fill.solid()
    bar.fill.fore_color.rgb = _rgb(theme["accent"])
    bar.line.fill.background()
    bar.shadow.inherit = False
    n = len(deck_title)
    size = 40 if n <= 18 else (32 if n <= 34 else 26)
    _tb(slide, Inches(1.0), H * 0.34, W - Inches(2.0), Inches(1.8),
        deck_title, size, _rgb("FFFFFF"), bold=True, align="center", valign="middle")
    if subtitle:
        _tb(slide, Inches(1.0), H * 0.34 + Inches(1.9), W - Inches(2.0), Inches(0.6),
            subtitle, 16, _rgb(theme["accent"]), align="center")

    # 内容页
    content_slides = [s for s in slides if s.get("kind") != "cover"]
    total = len(content_slides)
    for idx, sd in enumerate(content_slides, start=1):
        slide = prs.slides.add_slide(prs.slide_layouts[6])
        W, H = prs.slide_width, prs.slide_height
        header = slide.shapes.add_shape(1, 0, 0, W, Inches(1.15))
        header.fill.solid()
        header.fill.fore_color.rgb = _rgb(theme["primary"])
        header.line.fill.background()
        header.shadow.inherit = False
        h = sd.get("heading", "")
        sz = 26 if len(h) <= 22 else (22 if len(h) <= 40 else 18)
        _tb(slide, Inches(0.6), Inches(0.18), W - Inches(1.2), Inches(0.8),
            h, sz, _rgb("FFFFFF"), bold=True, align="left", valign="middle")
        body_top, body_left, body_w, body_h = Inches(1.55), Inches(0.75), W - Inches(1.5), H - Inches(2.3)
        if sd.get("bullets"):
            box = slide.shapes.add_textbox(body_left, body_top, body_w, body_h)
            tf = box.text_frame
            tf.word_wrap = True
            gap = Pt(10)
            for i, b in enumerate(sd["bullets"]):
                p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
                p.alignment = PP_ALIGN.LEFT
                p.space_after = gap
                run = p.add_run()
                run.text = "●  " + b
                run.font.size = Pt(16)
                run.font.name = "微软雅黑"
                run.font.color.rgb = _rgb(theme["text"])
        else:
            _tb(slide, body_left, body_top, body_w, body_h, "（本页无正文内容）", 14, _rgb(theme["muted"]))
        _tb(slide, W - Inches(1.6), H - Inches(0.55), Inches(1.2), Inches(0.4),
            f"{idx} / {total}", 11, _rgb(theme["muted"]), align="right")
        line = slide.shapes.add_shape(1, Inches(0.6), H - Inches(0.62), W - Inches(1.2), Inches(0.02))
        line.fill.solid()
        line.fill.fore_color.rgb = _rgb(theme["accent"])
        line.line.fill.background()
        line.shadow.inherit = False

    prs.save(str(out_path))
    print(f"[pptx_builder] ⚠ 回退到纯色绘制: {out_path} ({total + 1} 页)")
    return {
        "path": str(out_path),
        "slides": total + 1,
        "theme": f"fallback-{theme_name}",
        "note": "模板构建失败，已使用纯色主题回退",
    }


# ── 测试入口 ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("input", help="输入文件或 stdin")
    ap.add_argument("output", help="输出 .pptx 路径")
    ap.add_argument("--title", default="演示文稿")
    ap.add_argument("--theme", default="blue")
    args = ap.parse_args()

    try:
        with open(args.input, encoding="utf-8") as f:
            raw = json.load(f)
    except (json.JSONDecodeError, FileNotFoundError):
        with open(args.input, encoding="utf-8") as f:
            raw = f.read()

    info = build_pptx(args.output, raw, args.title, args.theme)
    print(json.dumps(info, ensure_ascii=False, indent=2))
