"""简化测试 - 绕过权限问题"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, "/d/软件/XianRenZhangAgent")

TEST_DIR = Path("/d/软件/test_output")
TEST_DIR.mkdir(parents=True, exist_ok=True)

async def test_deepseek_word():
    """T1: DeepSeek 生成 Word"""
    print("\n=== T1: DeepSeek 生成 Word ===")
    
    # 直接调用后端 API
    import requests
    r = requests.post("http://127.0.0.1:8888/command", 
                      json={"command": f"请生成Word报告保存到{TEST_DIR}/report.docx，标题《测试报告》"},
                      timeout=10)
    print(f"  提交结果: {r.json().get('text', '')}")
    
    # 等待完成
    import time
    time.sleep(60)
    
    # 检查结果
    report = TEST_DIR / "report.docx"
    if report.exists():
        size = report.stat().st_size
        print(f"  >>> PASS OK {size}B report.docx")
        return True, f"OK {size}B report.docx"
    else:
        print(f"  >>> FAIL report.docx 未生成")
        return False, "report.docx 未生成"

async def test_qwen_text():
    """T3: 千问写文件"""
    print("\n=== T3: 千问写文件 ===")
    
    import requests
    note_file = TEST_DIR / "note.txt"
    note_file.write_text("初始内容\n", encoding="utf-8")
    
    r = requests.post("http://127.0.0.1:8888/switch_platform",
                      json={"platform": "tongyi"}, timeout=10)
    print(f"  切换结果: {r.json().get('text', '')}")
    
    r = requests.post("http://127.0.0.1:8888/command",
                      json={"command": f"请用file_write在{note_file}写入TEST_OK_仙人掌自测，然后done()"},
                      timeout=10)
    print(f"  提交结果: {r.json().get('text', '')}")
    
    import time
    time.sleep(60)
    
    if note_file.exists():
        content = note_file.read_text(encoding="utf-8", errors="replace")
        if "TEST_OK_仙人掌自测" in content:
            print(f"  >>> PASS note.txt 含 TEST_OK")
            return True, "note.txt 含 TEST_OK"
    
    print(f"  >>> FAIL note.txt 未含 TEST_OK")
    return False, "note.txt 未含 TEST_OK"

async def main():
    overall = []
    
    # T1
    ok, extra = await test_deepseek_word()
    overall.append(("T1 DeepSeek Word", ok))
    
    # T3
    ok, extra = await test_qwen_text()
    overall.append(("T3 Qwen file_write", ok))
    
    # 汇总
    npass = sum(1 for _, ok in overall if ok)
    print(f"\n=== 总裁判: {npass}/{len(overall)} ===")
    for name, ok in overall:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")

if __name__ == "__main__":
    asyncio.run(main())
