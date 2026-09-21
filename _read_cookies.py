"""读取各平台 Chromium 原生 cookie 库（需先停掉后端释放文件锁）。
用法: python _read_cookies.py [平台...]  默认读全部 4 家。
输出: 每家的登录相关 cookie（tencent/qwen/doubao 等 host）是否有效、过期时间。
"""
import sys, os, time, sqlite3, datetime, tempfile, shutil

ROOT = r"D:\软件\XianRenZhangAgent"
BASE = os.path.join(ROOT, "xrz_data", ".xianrenzhang_agent", "browser_profiles")
EPOCH_DIFF = 11644473600.0  # 1601-01-01 -> 1970-01-01（秒）*1e6


def cr(us):
    if not us:
        return None
    try:
        return datetime.datetime(1970, 1, 1) + datetime.timedelta(microseconds=int(us - EPOCH_DIFF * 1e6))
    except Exception:
        return None


def read_plat(plat):
    src = os.path.join(BASE, plat, "Default", "Network", "Cookies")
    print(f"\n########## {plat}  {src} (exists={os.path.exists(src)} "
          f"size={os.path.getsize(src) if os.path.exists(src) else 0})")
    if not os.path.exists(src):
        print("  无原生 cookie 库")
        return
    # 复制到临时文件再读（避免锁）
    tmp = os.path.join(tempfile.gettempdir(), f"_xrz_ck_{plat}.db")
    if os.path.exists(tmp):
        os.remove(tmp)
    shutil.copy2(src, tmp)
    con = sqlite3.connect(tmp)
    cur = con.cursor()
    cols = [c[1] for c in cur.execute("PRAGMA table_info(cookies)")]
    host_c = "host_key" if "host_key" in cols else "host"
    exp_c = "expires_utc" if "expires_utc" in cols else None
    now_us = int((time.time() + EPOCH_DIFF) * 1e6)
    sel = [host_c] + ([exp_c] if exp_c else [])
    rows = cur.execute("SELECT " + ",".join(sel) + " FROM cookies").fetchall()
    con.close()
    os.remove(tmp)
    print(f"  cookie 总条数: {len(rows)}")
    valid = exp_n = 0
    # 只高亮登录相关的 host（域名去点）
    login_hosts = ("tencent", "qq.com", "qwen", "alibabacdn", "douyin", "byted", "doubaocom")
    for r in rows:
        h = r[0].replace(".", "")
        exp = r[1] if exp_c else 0
        if exp and exp > 0 and exp < now_us:
            exp_n += 1
            tag = "已过期"
        elif exp and exp >= now_us:
            valid += 1
            tag = "有效"
        else:
            tag = "会话"
        mark = " *" if any(l in h for l in login_hosts) else ""
        print(f"    {h:32s} {tag:4s} expire={cr(exp)}{mark}")
    print(f"  => 有效={valid} 已过期={exp_n} 会话={len(rows)-valid-exp_n}   当前={datetime.datetime.now():%m-%d %H:%M}")


if __name__ == "__main__":
    plats = sys.argv[1:] or ["yuanbao", "doubao", "tongyi", "deepseek"]
    for p in plats:
        read_plat(p)
