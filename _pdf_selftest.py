"""独立复现 pdf_create 工具逻辑，验证 reportlab 能出真 PDF + 中文。"""
from pathlib import Path
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.lib.colors import HexColor
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.styles import ParagraphStyle

font_name = "Helvetica"
try:
    pdfmetrics.registerFont(TTFont("XRZSimSun", r"C:\Windows\Fonts\simsun.ttc"))
    font_name = "XRZSimSun"
    print("用 SimSun")
except Exception as e:
    try:
        pdfmetrics.registerFont(TTFont("XRZSimHei", r"C:\Windows\Fonts\simhei.ttf"))
        font_name = "XRZSimHei"
        print("用 SimHei:", )
    except Exception:
        font_name = "Helvetica"
        print("退回 Helvetica(中文会方块)")

title_style = ParagraphStyle("t", fontName=font_name, fontSize=18, leading=24, textColor=HexColor("#1f2937"))
h2 = ParagraphStyle("h", fontName=font_name, fontSize=14, leading=20, textColor=HexColor("#1d4ed8"))
body = ParagraphStyle("b", fontName=font_name, fontSize=10.5, leading=16, textColor=HexColor("#374151"))
bullet = ParagraphStyle("bl", fontName=font_name, fontSize=10.5, leading=16, leftIndent=14, textColor=HexColor("#374151"))

def esc(s): return str(s).replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")

out = Path(r"C:/temp/_pdf_tool_selftest.pdf")
doc = SimpleDocTemplate(str(out), pagesize=A4, leftMargin=2*cm, rightMargin=2*cm,
                        topMargin=1.8*cm, bottomMargin=1.8*cm)
story = [Paragraph(esc("PDF 工具自检"), title_style), Spacer(1,6)]
text = "# PDF 工具自检\n## 第一节 中文\n- 要点一\n- 要点二\n```python\nprint('hello')\n```"
started=True
for ln in text.splitlines():
    s=ln.strip()
    if not s: continue
    if started: started=False; continue
    if s.startswith("## "): story.append(Paragraph(esc(s[3:]),h2))
    elif s.startswith("# "): story.append(Paragraph(esc(s[2:]),title_style))
    elif s.startswith(("- ","* ")): story.append(Paragraph("• "+esc(s[2:]),bullet))
    else: story.append(Paragraph(esc(s),body))
doc.build(story)
print("生成:", out, out.stat().st_size, "bytes, PDF头:", open(out,'rb').read(5))
