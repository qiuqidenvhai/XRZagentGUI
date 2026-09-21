"""验证 QtWebEngine 测试桥是否把 JS 字符串参数正则化（e.key 返回 /Enter/ 的根因排查）。"""
import json
import urllib.request


def ev(js, t=60):
    req = urllib.request.Request(
        "http://127.0.0.1:9333/",
        data=json.dumps({"js": js}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=t) as r:
        return json.loads(r.read().decode("utf-8")).get("value")


TESTS = [
    ("literal dq", 'new KeyboardEvent("keydown",{key:"Enter"}).key'),
    ("from var", '(function(){var k="Enter";return new KeyboardEvent("keydown",{key:k}).key;})()'),
    ("String()", 'new KeyboardEvent("keydown",{key:String("Enter")}).key'),
    ("array join", 'new KeyboardEvent("keydown",{key:["En","ter"].join("")}).key'),
    ("charCodes", 'new KeyboardEvent("keydown",{key:String.fromCharCode(69,110,116,101,114)}).key'),
    ("assign prop", '(function(){var d={};d.key="Enter";return new KeyboardEvent("keydown",d).key;})()'),
    ("plain obj", 'JSON.stringify({key:"Enter"})'),
    ("just string", '"Enter"'),
    ("concat", 'new KeyboardEvent("keydown",{key:"En"+"ter"}).key'),
]

if __name__ == "__main__":
    for name, js in TESTS:
        print("%-14s -> %s" % (name, ev(js)))
