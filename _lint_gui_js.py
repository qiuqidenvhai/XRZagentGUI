"""用 V8 真实编译 gui.html 的内联 JS，精确抓出 await/async 错配。

node --check 只做语法解析（不检查 await 位置），所以漏掉了 newConversation
这类「用了 await 但函数没 async」的运行期错误。这里改用真实编译：
把 JS 包进 async 包装里跑一次 AST 级别的检查，并逐函数单测。
"""
import re
import subprocess
import sys
import tempfile
import os
import json

# 【2026-10-06 修"写死本机路径"】改为动态查找 node
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _find_node import find_node
NODE = find_node()
HERE = os.path.dirname(os.path.abspath(__file__))
GUI = os.path.join(HERE, "gui.html")

src = open(GUI, encoding="utf-8").read()
m = re.search(r"<script[^>]*>(.*?)</script>", src, re.S)
js = m.group(1)

probe = r"""
const fs = require('fs');
const vm = require('vm');
const code = fs.readFileSync(process.argv[2], 'utf8');

// 1) 整体编译（能抓出解析期错误）
try {
  new vm.Script(code, { filename: 'gui.js' });
  console.log('PARSE: OK');
} catch (e) {
  console.log('PARSE: FAIL ' + e.message);
}

// 2) 逐函数抽出，用 async 上下文编译，精确抓 await-in-non-async
const names = [...code.matchAll(/function\s+(\w+)\s*\(/g)].map(m => m[1]);
const uniq = [...new Set(names)];
const bad = [];
for (const n of uniq) {
  const i = code.indexOf('function ' + n + '(');
  if (i < 0) continue;
  const j = code.indexOf('{', i);
  if (j < 0) continue;
  let depth = 0, k = j;
  while (k < code.length) {
    if (code[k] === '{') depth++;
    else if (code[k] === '}') { depth--; if (depth === 0) break; }
    k++;
  }
  const sig = code.slice(i, j).trim();
  const body = code.slice(j, k + 1);
  // 只在本函数**直接**使用 await 才算错（简单起见：本函数体内出现 await，
  // 且本函数不是 async —— 再用编译验证，避免嵌套误报）
  if (!/\bawait\b/.test(body)) continue;
  if (/^async\b/.test(sig)) continue;
  const wrapped = 'async function __w(){ ' + body.replace(/^\{/, '').replace(/\}$/, '') + ' }';
  try {
    new vm.Script(wrapped, { filename: n + '.js' });
    // 编译通过 => await 其实在嵌套 async 里，本函数没问题
  } catch (e) {
    if (/await is only valid|await/i.test(e.message)) {
      bad.push(n);
    }
  }
}
console.log('NON_ASYNC_AWAIT: ' + JSON.stringify(bad));
"""

tp = tempfile.mktemp(suffix=".js")
open(tp, "w", encoding="utf-8").write(probe)
jp = tempfile.mktemp(suffix=".js")
open(jp, "w", encoding="utf-8").write(js)

r = subprocess.run([NODE, tp, jp], capture_output=True, text=True)
print(r.stdout)
if r.stderr:
    print("STDERR:", r.stderr[:1500])
