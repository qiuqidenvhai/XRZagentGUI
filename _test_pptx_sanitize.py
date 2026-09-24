#!/usr/bin/env python3
"""回归测试：确认中文 PPT 生成不再混入模板残留的英文/拉丁占位文本。"""
import json, zipfile, re, sys
from pathlib import Path

sys.path.insert(0, r"D:/软件/XianRenZhangAgent")
from agent_core import pptx_builder as pb

OUT = Path(r"D:/软件/XianRenZhangAgent/xrz_data/XianRenZhang_tasks/gui_session/_sanitize_test.pptx")
OUT.parent.mkdir(parents=True, exist_ok=True)

# 模拟用户最新那次「7 页 AI PPT」的中文内容（仅中文，无任何英文占位）
content = """第1页「人工智能：从概念到未来」
第2页「什么是人工智能」
人工智能（AI）是让机器模拟人类智能的科学，涵盖学习、推理、感知与决策。
第3页「AI 发展历程」
1956 达特茅斯会议正式命名；2012 深度学习突破；2016 AlphaGo 战胜人类；2022 大模型爆发。
第4页「核心技术」
机器学习、深度学习、自然语言处理、计算机视觉、强化学习。
第5页「典型应用场景」
医疗影像诊断、自动驾驶、智能教育、内容创作、金融风控。
第6页「挑战与风险」
深度伪造、算法偏见、数据隐私、版权伦理、算力能耗。
第7页「未来展望」
端侧 AI、具身智能、多模态融合、人机协同、智能体自主规划。"""

info = pb.build_pptx(str(OUT), content, "人工智能：从概念到未来", theme_name="blue")
print("BUILD INFO:", json.dumps(info, ensure_ascii=False))

# 抽取成品所有文本
z = zipfile.ZipFile(str(OUT))
all_text = []
for n in z.namelist():
    if re.match(r"ppt/slides/slide\d+\.xml$", n):
        data = z.read(n).decode("utf-8", errors="ignore")
        all_text += re.findall(r"<a:t>(.*?)</a:t>", data, re.S)
joined = "\n".join(all_text)

JUNK = ["Vivamus", "Quam Dolor", "Tempor Ac Gravida", "Porta Fermentum",
        "Aliquam Euismod", "Key Words Here", "Question", "Contents",
        "Work completion", "Work experience summary", "Follow up objectives",
        "Work schedule", "Rice Husk", "Annual Review", "Project Brief"]

found = [j for j in JUNK if j in joined]
print("\n=== 成品文本（逐行）===")
for t in all_text:
    if t.strip():
        print(repr(t))

print("\n=== 残留垃圾英文检测 ===")
if found:
    print("❌ 仍发现残留:", found)
    sys.exit(1)
else:
    print("✅ 无任何模板残留英文/拉丁占位文本")

# 对比旧文件（应当含有垃圾）
old = r"D:/软件/XianRenZhangAgent/xrz_data/XianRenZhang_tasks/gui_session/AI_Overview_7pages.pptx"
zo = zipfile.ZipFile(old)
old_text = []
for n in zo.namelist():
    if re.match(r"ppt/slides/slide\d+\.xml$", n):
        old_text += re.findall(r"<a:t>(.*?)</a:t>", zo.read(n).decode("utf-8", errors="ignore"))
old_joined = "\n".join(old_text)
old_found = [j for j in JUNK if j in old_joined]
print("\n=== 旧文件对照（预期有垃圾，证明修复有效）===")
print("旧文件残留:", old_found)
