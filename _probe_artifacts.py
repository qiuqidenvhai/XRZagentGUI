"""诊断：历史会话能否从 conv JSON 抽出真实产物，以及 /attachments 是否返回。"""
import json, re, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CONV = ROOT / "xrz_data" / "XianRenZhang_tasks" / "conversations"
TASKS = ROOT / "xrz_data" / "XianRenZhang_tasks"

ART = {".docx", ".doc", ".pptx", ".ppt", ".xlsx", ".xls", ".csv", ".pdf",
       ".txt", ".md", ".html", ".htm", ".json", ".js", ".py",
       ".png", ".jpg", ".jpeg", ".gif", ".svg"}
pat = re.compile(r'"(?:path|file|filename|output)"\s*:\s*"([^"]+)"')


def norm(p):
    return p.replace("\\\\", "/").replace("\\", "/")


def extract(cid, conv, xrz_paths=None):
    got = set()
    for m in (conv.get("messages") or []):
        for cand in pat.findall(str(m.get("content") or "")):
            p = norm(cand)
            fp = Path(p)
            if not fp.is_absolute():
                fp = TASKS / p
            try:
                if fp.is_file() and fp.suffix.lower() in ART:
                    got.add(str(fp).replace("\\", "/"))
            except Exception:
                pass
    return got


def main():
    files = sorted(CONV.glob("conv_*.json"))
    hits = []
    for f in files:
        try:
            c = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        cid = f.stem[5:]
        got = extract(cid, c)
        if got:
            hits.append((cid, len(got), sorted(got)[:2]))
    print("会话总数:", len(files), " | 能抽出真实产物的会话:", len(hits))
    for h in hits[:10]:
        print("   ", h)
    print()
    for cid, n, _ in hits[:6]:
        try:
            r = urllib.request.urlopen(
                "http://127.0.0.1:8888/attachments?conversation_id=" + cid, timeout=15)
            d = json.loads(r.read().decode())
            print("API", cid, "->", len(d.get("files") or []), "files")
        except Exception as e:
            print("API", cid, "ERR", e)


if __name__ == "__main__":
    main()
