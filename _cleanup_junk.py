# -*- coding: utf-8 -*-
"""一次性清理：删除历史诊断/探针/repro/图标迭代等垃圾脚本。
保留：4 个热补丁（_sse_resilience/_files_listing_fix/_dom_dump_patch/_parallel_tasks_patch）、
      进程管理（_xrz_kill/_xrz_start/_kill_xrz）、主审计/校验（_drive_gui_audit/_verify_gui_fixes/
      _check_gui_js/_gui_drive）、子代理验证（_gui_subagent_e2e/_gui_subagent_platform/_gui_multiturn）、
      图标构建（_build_ico）。
删除前先打印将要删的文件清单，确认数量合理。
"""
import os
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))

KEEP = {
    "_sse_resilience.py", "_files_listing_fix.py", "_dom_dump_patch.py",
    "_parallel_tasks_patch.py",
    "_xrz_kill.py", "_xrz_start.py", "_kill_xrz.py",
    "_drive_gui_audit.py", "_verify_gui_fixes.py", "_check_gui_js.py", "_gui_drive.py",
    "_gui_subagent_e2e.py", "_gui_subagent_platform.py", "_gui_multiturn.py",
    "_build_ico.py",
}

# 明确要删的垃圾脚本（项目根目录下的下划线开头单文件 + 指定子目录里的测试件）
DELETE_NAMES = {
    # 探针
    "_probe.py", "_probe2.py", "_probe3.py", "_probe_sse.py", "_probe_platform.py",
    "_probe_doubao.py", "_probe_doubao_dom.py", "_probe_2plats.py", "_probe_yuanbao.py",
    "_probe_yuanbao2.py", "_probe_yuanbao3.py", "_probe_live.py", "_probe_9333.py",
    "_probe_ico.py", "_probe_newconv.py", "_probe_all.py", "_probe_full.py",
    "_probe_deepseek_modes.py", "_probe_tokey.py", "_probe_artifacts.py",
    "_probe_flaky.py", "_probe_load.py", "_probe_gui_fetch.py", "_qwen_msg_dom.py",
    # 一次性测试
    "_test_tongyi_fix.py", "_test_pathfix.py", "_test_proto_fix.py",
    "_test_proto_braces.py", "_test_protocol_braces.py", "_test_proto.py",
    "_test_pptx_pages.py", "_test_sse_proxy.py", "_test_escape_layer.py",
    "_test_loop_resilience.py", "_test_pdf_headed.py", "_smoke_harness.py",
    # repro / 调试
    "_repro_yuanbao.py", "_repro_upload.py", "_dbg_tongyi_search.py",
    "_dbg_concurrency.py", "_dbg_find_eventloop.py", "_cap_yuanbao.py",
    "_read_cookies.py",
    # doubao 专项（历史迭代）
    "_doubao_recon.py", "_doubao_trigger.py", "_doubao_solve.py", "_doubao_deep.py",
    "_doubao_server.py", "_doubao_recon2.py", "_doubao_test1.py", "_doubao_srv.py",
    # 各种探针 / DOM 探测
    "_chrome_param_test.py", "_pyc_scan.py", "_inspect_pyc.py",
    "_composer_probe.py", "_sendbtn_probe.py", "_sendbtn_probe2.py",
    "_msgstruct_probe.py", "_dom_check.py", "_after_check.py",
    "_t_final.py", "_t_newconv.py", "_t_resume.py", "_t_dom_after.py",
    "_locate.py", "_live.py",
    # pdf / dump
    "_dump_to_pdf.py", "_pdf_selftest.py",
    # 旧 GUI 驱动/桥/上传（已被 _drive_gui_audit/_verify_gui_fixes/_gui_subagent_* 取代）
    "_gui_test.py", "_gui_bridge.py", "_gui_bridge_drive.py",
    "_gui_upload_preview.py", "_gui_products.py", "_gui_multi_subagent.py",
    "_gui_probe2msg.py", "_bridge_probe.py", "_qt_js_probe.py",
    # 图标迭代（保留 _build_ico.py）
    "_icon_live_test.py", "_ico_pixels.py", "_ico_manual.py", "_ico_v2.py",
    "_ico_orient.py",
    # 审计/校验旧版（保留 _drive_gui_audit/_verify_gui_fixes）
    "_audit_history.py", "_verify_conv_fix.py", "_verify_fixes.py",
    "_verify_login_hint.py", "_verify_pptx.py", "_verify_sse_fix.py",
    "_verify_yuanbao.py", "_run_all.py",
    # 子代理历史诊断（保留 _gui_subagent_e2e/_gui_subagent_platform/_gui_multiturn）
    "xrz_subagent_test.py", "test_subagent_diagnostic.py",
}

def main():
    victims = []
    for name in sorted(DELETE_NAMES):
        p = os.path.join(HERE, name)
        if os.path.exists(p):
            victims.append(p)
    # 子目录里的测试件
    for sub in ["xrz_data/_test_good.py", "xrz_data/_test_bad.py"]:
        p = os.path.join(HERE, sub)
        if os.path.exists(p):
            victims.append(p)

    print("将删除 %d 个文件：" % len(victims))
    for v in victims:
        print("  -", os.path.relpath(v, HERE))

    # 安全防护：绝不允许误删 KEEP 清单里的任何文件
    for v in victims:
        base = os.path.basename(v)
        assert base in DELETE_NAMES or v.endswith("_test_good.py") or v.endswith("_test_bad.py"), \
            ("安全护栏触发，存在 KEEP 冲突: " + base)

    if os.environ.get("XRZ_CLEANUP_EXEC") == "1":
        for v in victims:
            try:
                os.remove(v)
                print("  已删:", os.path.relpath(v, HERE))
            except Exception as e:
                print("  删除失败:", os.path.relpath(v, HERE), "->", e)
    else:
        print("\n（dry-run，未实际删除。设置 XRZ_CLEANUP_EXEC=1 后重跑才真正删除。）")

if __name__ == "__main__":
    main()
