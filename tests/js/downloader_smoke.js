// Run downloader.html's own script in a DOM shim and press #download-all.
// Exits non-zero if the script throws, navigates away (the fallback), or the
// 16-range reassembly differs from the full download.
const fs = require('fs');
const vm = require('vm');
const crypto = require('crypto');

const BASE = process.argv[2];   // e.g. http://127.0.0.1:PORT/
const HTML = process.argv[3];   // path to downloader.html

const html = fs.readFileSync(HTML, 'utf8');
const scripts = [...html.matchAll(/<script[^>]*>([\s\S]*?)<\/script>/g)].map((m) => m[1]).join('\n');

const elements = new Map();
function makeEl(id) {
  const handlers = {};
  const el = {
    id, textContent: '', value: '', src: '', disabled: false, href: '', download: '',
    style: {}, dataset: {}, children: [],
    classList: { _s: new Set(), add(c) { this._s.add(c); }, remove(c) { this._s.delete(c); },
                 toggle(c, on) { on ? this._s.add(c) : this._s.delete(c); }, contains(c) { return this._s.has(c); } },
    appendChild(c) { this.children.push(c); return c; }, remove() {}, select() {}, click() { this.clicked = true; },
    querySelector() { return null; }, querySelectorAll() { return []; },
    addEventListener(type, fn) { (handlers[type] ||= []).push(fn); },
    fire(type, ev = {}) { return Promise.all((handlers[type] || []).map((f) => f(ev))); },
  };
  elements.set(id, el);
  return el;
}
for (const id of ['toast', 'qr', 'progress-bar', 'progress-text', 'eta', 'speed', 'download-all',
                  'filter', 'empty', 'devices', 'copy-url', 'copy-command', 'copy-aria', 'share-url']) makeEl(id);
elements.get('share-url').value = BASE;

const created = [];
const cards = [];
const document = {
  getElementById: (id) => elements.get(id) || null,
  querySelectorAll: (sel) => (sel === '.card.file' ? cards : []),
  createElement: () => { const el = makeEl('made-' + created.length); created.push(el); return el; },
  body: { appendChild() {}, removeChild() {} },
  execCommand: () => true,
};

const ctx = {
  document, console, setTimeout, clearTimeout,
  setInterval: () => 0, clearInterval: () => {}, requestAnimationFrame: () => 0,
  fetch: (url, opts) => fetch(new URL(url, BASE).href, opts),
  URL, URLSearchParams, Blob, TextDecoder, JSON, parseInt, parseFloat, Math, Array,
  Promise, Error, encodeURIComponent, crypto,
  navigator: {}, window: { location: { href: '' }, isSecureContext: false },
};
ctx.globalThis = ctx;

function load() {
  vm.createContext(ctx);
  vm.runInContext(scripts, ctx, { filename: 'downloader-inline.js' });
}

(async () => {
  load();                                   // first pass: the page's own bootstrap
  const manifest = await (await fetch(BASE + 'api/manifest')).json();

  cards.length = 0;
  for (const f of manifest.files) {
    cards.push({
      dataset: { id: f.id },
      classList: { toggle() {}, add() {}, remove() {} },
      querySelector: (sel) => (sel === 'h3' ? { textContent: f.name } : sel === 'p' ? { textContent: String(f.bytes) } : null),
      querySelectorAll: () => [],
    });
  }
  load();                                   // second pass with cards, as the real page has

  await elements.get('download-all').fire('click');
  await new Promise((r) => setTimeout(r, 200));

  const anchors = created.filter((el) => el.download);
  if (anchors.length !== manifest.files.length) {
    console.error(`expected ${manifest.files.length} saved files, got ${anchors.length}`);
    process.exit(1);
  }
  if (ctx.window.location.href) {
    console.error('page fell back to a plain navigation: ' + ctx.window.location.href);
    process.exit(1);
  }

  let ok = true;
  for (const f of manifest.files) {
    const url = `${BASE}f/${f.id}`;
    const len = parseInt((await fetch(url, { method: 'HEAD' })).headers.get('content-length'), 10);
    const chunk = Math.ceil(len / 16);
    const parts = [];
    for (let i = 0; i < 16; i++) {
      const start = i * chunk, end = Math.min(len - 1, start + chunk - 1);
      if (start > end) break;
      parts.push({ start, end });
    }
    const bufs = await Promise.all(parts.map(async (p) =>
      Buffer.from(await (await fetch(url, { headers: { Range: `bytes=${p.start}-${p.end}` } })).arrayBuffer())));
    const joined = Buffer.concat(bufs);
    const full = Buffer.from(await (await fetch(url)).arrayBuffer());
    const same = joined.length === len &&
      crypto.createHash('sha256').update(joined).digest('hex') ===
      crypto.createHash('sha256').update(full).digest('hex');
    if (!same) { console.error(`${f.name}: reassembly differs (${joined.length}/${len})`); ok = false; }
  }
  console.log(ok ? 'ok' : 'failed');
  process.exit(ok ? 0 : 1);
})().catch((e) => { console.error(e); process.exit(1); });
