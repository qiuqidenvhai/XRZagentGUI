# -*- coding: utf-8 -*-
"""一条龙回归运行器：后端 + 测试跑在【同一个后台任务】里。

为什么要这样：本沙箱里 run_in_background 的后台 Bash 任务，会在再执行其它 Bash
命令时被回收；而脱离子进程（DETACHED_PROCESS）在父命令结束后也会被一起回收。
结果就是「测到一半后端没了」。把后端和测试放进同一条长任务里，
两者同生共死，中途不会再被回收。

流程：
  1. 杀掉残留的 terminal.py / 测试进程（避免端口 8888 冲突 + 双开 DeepSeek）
  2. 启动后端（XRZ_NO_GUI=1），日志写 _run_backend.log
  3. 等 /health agent_ready
  4. 跑 test_all_platforms_full.py，输出写指定日志
  5. 结束后杀掉后端，打印退出码
用法: python _run_all.py <输出日志名>
"""
import os, subprocess, sys, time, signal
import psutil
import urllib.request, json

PY = r"D:\软件\Python\python.exe"
ROOT = r"D:\软件\XianRenZhangAgent"
sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def kill_stale():
    n = 0
    for p in psutil.process_iter(["pid", "name", "cmdline"]):
        if p.info.get("pid") == os.getpid():
            continue
        if p.info.get("name") != "python.exe":
            continue
        cl = " ".join(p.info.get("cmdline") or [])
        if "test_all_platforms_full" in cl or cl.rstrip().endswith("terminal.py"):
            try:
                p.kill()
                n += 1
                print(f"  killed stale pid={p.info['pid']} {cl[:60]}", flush=True)
            except Exception as e:
                print(f"  kill fail {e}", flush=True)
    return n


def wait_ready(timeout=240):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            d = json.loads(urllib.request.urlopen(
                "http://127.0.0.1:8888/health", timeout=3).read().decode())
            if d.get("agent_ready"):
                print("  READY:", d, flush=True)
                return True
        except Exception:
            pass
        time.sleep(4)
    return False


def main():
    out_log = sys.argv[1] if len(sys.argv) > 1 else "_regression_run.log"
    args = sys.argv[2:]
    print("== 1) 清理残留进程 ==", flush=True)
    kill_stale()
    time.sleep(3)

    env = dict(os.environ)
    env["XRZ_NO_GUI"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    # 打开网页交互留痕：每轮把平台页面整页截图存到 ui_dumps/<平台>/
    if os.environ.get("XRZ_NO_DUMP") != "1":
        env["XRZ_UI_DUMP"] = "1"

    print("== 2) 启动后端 ==", flush=True)
    blog = open(os.path.join(ROOT, "_run_backend.log"), "w", encoding="utf-8")
    backend = subprocess.Popen([PY, "-u", "terminal.py"], cwd=ROOT, env=env,
                               stdout=blog, stderr=subprocess.STDOUT)
    try:
        print("== 3) 等待后端就绪 ==", flush=True)
        if not wait_ready():
            print("后端未就绪，放弃", flush=True)
            return 2
        print("== 4) 跑全平台回归 ==", flush=True)
        tlog = open(os.path.join(ROOT, out_log), "w", encoding="utf-8")
        rc = subprocess.call([PY, "-u", "test_all_platforms_full.py"] + args,
                             cwd=ROOT, env=env, stdout=tlog, stderr=subprocess.STDOUT)
        tlog.close()
        print(f"== 测试退出码 {rc} ==", flush=True)
        # 汇总本轮生成的网页留痕 PDF 清单
        try:
            from pathlib import Path as _P
            base = _P(ROOT) / "xrz_data" / "XianRenZhang_tasks" / "ui_dumps"
            if base.is_dir():
                print("== 网页留痕 PDF ==", flush=True)
                for d in sorted(base.iterdir()):
                    if not d.is_dir():
                        continue
                    fs = sorted(d.glob("*.pdf"), key=lambda p: p.stat().st_mtime)
                    print(f"  {d.name}: {len(fs)} 个 PDF", flush=True)
                    for f in fs[-5:]:
                        print(f"    {f}", flush=True)
        except Exception as e:
            print(f"  列留痕失败: {e}", flush=True)
        return rc
    finally:
        print("== 5) 关闭后端 ==", flush=True)
        try:
            backend.terminate()
            backend.wait(timeout=15)
        except Exception:
            try:
                backend.kill()
            except Exception:
                pass
        blog.close()


if __name__ == "__main__":
    sys.exit(main())
