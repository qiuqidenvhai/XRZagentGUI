// 验证噪声清洗逻辑（与 platform_browser.py 的 cleanPart/stripTok 保持一致）
// 用法: node _check_noise.js <noise.json>
const fs = require('fs');
const noise = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));

const HAN = /[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]/;
const stripTok = (s, n) => {
  if (!n) return s;
  let out = ''; let i = 0;
  while (true) {
    const k = s.indexOf(n, i);
    if (k < 0) { out += s.slice(i); break; }
    const before = k > 0 ? s[k - 1] : '';
    const after = (k + n.length) < s.length ? s[k + n.length] : '';
    const embedded = (before && HAN.test(before)) || (after && HAN.test(after));
    out += s.slice(i, embedded ? (k + n.length) : k);
    i = k + n.length;
  }
  return out;
};

const cleanPart = (raw) => {
  const lines = (raw || '').split(/\r?\n/);
  const kept = [];
  for (const line of lines) {
    const s = line.trim();
    if (!s) continue;
    let isUi = false;
    for (const n of noise) {
      if (s === n) { isUi = true; break; }
      if (n && s.startsWith(n) && s.length < n.length + 40) { isUi = true; break; }
    }
    if (isUi) continue;
    let stripped = s;
    for (const n of noise) { stripped = stripTok(stripped, n); }
    stripped = stripped.trim();
    if (!stripped) continue;
    kept.push(stripped);
  }
  return kept.join('\n').trim();
};

const cases = [
  ["Qwen 反馈面板 + 真实回复",
   "此反馈将帮助我们评估并提升 Qwen Studio 的体验。\n你更喜欢哪个回复？请选择一个以继续。\n回复 1\n明白了，感谢提醒。我已了解协议格式要求。\n@@@@\n{\"tool\":\"done\"}\n@@@@",
   "明白了，感谢提醒"],
  ["元宝思考状态行（整行）",
   "正在思考",
   ""],
  ["已经完成思考 + 正文",
   "已经完成思考\n我是 Qwen3.8。",
   "我是 Qwen3.8。"],
  ["真实句子里的「正在思考」不能被误删",
   "我正在思考如何解决这个问题，稍后给出结论。\n这是正文第二行。",
   "我正在思考如何解决这个问题"],
  ["免责声明整行",
   "这是正文。\n以上内容由人工智能生成，仅供参考",
   "这是正文"],
  ["正文里的「深度思考」作为普通词保留",
   "深度思考能力是本次升级的重点。",
   "深度思考能力是本次升级的重点。"],
];

let ok = true;
for (const [name, input, expect] of cases) {
  const out = cleanPart(input);
  const pass = expect === "" ? out === "" : out.includes(expect);
  if (!pass) ok = false;
  console.log(`[${pass ? 'PASS' : 'FAIL'}] ${name}`);
  console.log('   IN :', JSON.stringify(input));
  console.log('   OUT:', JSON.stringify(out));
}
console.log(ok ? 'ALL PASS' : 'HAS FAILURE');
process.exit(ok ? 0 : 1);
