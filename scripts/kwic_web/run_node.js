// run_node.js — engine.js を Node.js で動かして，検索の結果を JSON で出す。
// check_kwic_web.py が使う（Python 版 kwic_core.py と結果を突き合わせるため）。
//   node run_node.js <配列のフォルダ> <問い合わせの JSON>
'use strict';
const fs = require('fs');
const path = require('path');
const E = require('./engine.js');

const [dir, qfile] = process.argv.slice(2);
const meta = JSON.parse(fs.readFileSync(path.join(dir, 'meta.json'), 'utf8'));
const arr = {};
for (const f of fs.readdirSync(dir)) {
  const m = f.match(/^(\w+)\.i32$/);
  if (!m) continue;
  const b = fs.readFileSync(path.join(dir, f));
  const a = new Int32Array(b.buffer, b.byteOffset, b.length / 4);
  arr[m[1]] = ['pos12', 'cfm', 'gos', 'work'].includes(m[1]) ? Uint8Array.from(a) : Int32Array.from(a);
}
const kw = new E.KwicIndex(meta, arr);
const calls = JSON.parse(fs.readFileSync(qfile, 'utf8'));
const out = calls.map(c => {
  const r = E.handle(kw, c.path, c.query || {}, c.body || null);
  return r;
});
process.stdout.write(JSON.stringify(out));
