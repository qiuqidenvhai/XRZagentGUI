# -*- coding: utf-8 -*-
"""重打可迁移版 zip：dist/XianRenZhangAgent -> 桌面 zip（覆盖旧版）"""
import os, sys, time, zipfile

SRC = r"D:\软件\XianRenZhangAgent\dist\XianRenZhangAgent"
# 【2026-10-05 修"zip 根本没落盘"】之前直接往【桌面】写 1.3GB zip，沙箱对桌面大文件
# 写入会静默失败/中途被杀 —— 脚本却打印"DONE ... 1.278GB"（读到的是上一轮遗留的旧
# 文件大小），用户拿到桌面上根本不存在 zip。改为：先在项目内打包（沙箱可写），
# 校验通过后再由调用方拷到桌面。
DST = r"D:\软件\XianRenZhangAgent\XianRenZhangAgent_可迁移版_2026.10.05.zip"

t0 = time.time()
n = 0
with zipfile.ZipFile(DST, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=1, allowZip64=True) as z:
    for root, dirs, files in os.walk(SRC):
        dirs.sort(); files.sort()
        for f in files:
            p = os.path.join(root, f)
            arc = os.path.relpath(p, os.path.dirname(SRC))
            try:
                z.write(p, arc)
                n += 1
                if n % 3000 == 0:
                    print("  %d files, %.0fs" % (n, time.time()-t0), flush=True)
            except Exception as e:
                print("  SKIP %s: %r" % (p, e), flush=True)
sz = os.path.getsize(DST) if os.path.exists(DST) else 0
print("DONE %d entries, %.3f GB, %.0fs" % (n, sz/1e9, time.time()-t0), flush=True)

# 【真实验证】zip 必须真实存在 + 条目数与写入数一致 + 全部 CRC 正确。
# 旧实现只 print getsize()（读到上轮遗留旧文件的大小）就报 DONE，导致用户拿到
# 的桌面上根本没有 zip。这里不通过就直接抛错，绝不假成功。
if not os.path.exists(DST):
    raise RuntimeError("ZIP 未落盘: %s" % DST)
if sz < 100 * 1e6:
    raise RuntimeError("ZIP 体积异常偏小(%.3f GB)，疑似写入失败" % (sz / 1e9))
with zipfile.ZipFile(DST) as _zc:
    _names = _zc.namelist()
    if len(_names) != n:
        raise RuntimeError("ZIP 条目数不符: 写入 %d, 实际 %d" % (n, len(_names)))
    _bad = _zc.testzip()
    if _bad is not None:
        raise RuntimeError("ZIP CRC 校验失败: %s" % _bad)
print("VERIFY: zip 真实落盘 %.3f GB / %d 条目 / CRC 全部正确" % (sz/1e9, n), flush=True)

# 校验关键修复文件在包内且含标记
with zipfile.ZipFile(DST) as z:
    data = z.read("XianRenZhangAgent/_files_listing_fix.py").decode("utf-8", "replace")
    assert "_resolve_task_style_ids" in data, "映射修复缺失!"
    print("VERIFY: zip 内 _files_listing_fix.py 含 _resolve_task_style_ids (映射修复已入包)")
    ppt = z.read("XianRenZhangAgent/agent_core/pptx_builder.py").decode("utf-8", "replace")
    assert "_cover_write[_main_slot" in ppt and "cap>=20" in ppt, "PPT 模板分配修复缺失!"
    print("VERIFY: zip 内 pptx_builder.py 含 封面/大字槽分配修复 (PPT 挤字根治已入包)")
    bp = z.read("XianRenZhangAgent/agent_core/templates/pptx_templates/scripts/build_pptx.py").decode("utf-8", "replace")
    assert "if not (tf.text or \"\").strip():" in bp, "autofit 判定修复缺失!"
    print("VERIFY: zip 内 build_pptx.py 含 autofit 全文判定修复 (已入包)")
    gui = z.read("XianRenZhangAgent/gui.html").decode("utf-8", "replace")
    assert "nearBottom" in gui, "thinking 卡顿修复(增量追加+贴底滚)缺失!"
    assert "cleaned.startsWith(cur)" in gui, "thinking 增量追加修复缺失!"
    assert "classList.remove('collapsed')" in gui and "fileSidebarToggle" in gui, "预览自动展开修复缺失!"
    assert gui.count("flex-shrink: 0") >= 16, "thinking 块坍缩修复(flex-shrink:0)缺失!"
    print("VERIFY: zip 内 gui.html 含 thinking 卡顿 + 预览自动展开 + 坍缩修复 (flex-shrink:0 已入包)")
    da = z.read("XianRenZhangAgent/desktop_app.py").decode("utf-8", "replace")
    assert "def shutdown_backend_and_children" in da, "关 GUI 连带清理修复缺失!"
    assert "taskkill" in da and "/T" in da, "关 GUI 连带清理(taskkill 进程树)缺失!"
    assert "_cleanup_on_close" in da, "closeEvent 联动清理缺失!"
    print("VERIFY: zip 内 desktop_app.py 含 关GUI连带清理 (taskkill 后端+浏览器+子代理 已入包)")
    flf = z.read("XianRenZhangAgent/_files_listing_fix.py").decode("utf-8", "replace")
    assert "时间最接近" in flf, "产物按会话精确映射修复缺失!"
    assert "最近 N 个任务" in flf or "绝不再并" in flf, "去掉『最近N个任务』宽泛回退标记缺失!"
    assert 'startswith("task_")' in flf and "_current_conversation_id()" in flf, "register_artifacts 真会话兜底缺失!"
    print("VERIFY: zip 内 _files_listing_fix.py 含 产物栏按会话隔离 + 新产物登记兜底 (已入包)")
    g2 = z.read("XianRenZhangAgent/gui.html").decode("utf-8", "replace")
    assert "deleteFile" not in g2, "删除生成文件功能应已移除但仍在包内!"
    print("VERIFY: zip 内 gui.html 已移除删除生成文件功能 (无 deleteFile)")

# 完成标记（供分离进程轮询）
with open(r"D:\软件\XianRenZhangAgent\_zip_build_done.txt", "w", encoding="utf-8") as f:
    f.write("DONE %d entries %.3fGB %.0fs" % (n, sz/1e9, time.time()-t0))
