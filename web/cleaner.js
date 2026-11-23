// School Cleaner: hides a school's details in Word documents, and puts them back.
// Everything runs in this browser. No document, name or picture is ever sent anywhere:
// there is no server, only these files. What was hidden is kept in this browser's own
// storage (IndexedDB), which Claude cannot read.
//
// Text: each confidential string becomes a placeholder of exactly the same length,
// e.g. "Hollybrook" -> "QZAQQQQQQQ" ("QZ", a letter code without Q, Q padding).
// Pictures: the original is stored; the document gets a white picture of the same
// pixel size in the same place. Its first two pixels carry a code (a marker colour,
// then the picture's number) so it is recognised even after being copied elsewhere.
'use strict';

const W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main';
const A = 'http://schemas.openxmlformats.org/drawingml/2006/main';
const XML_NS = 'http://www.w3.org/XML/1998/namespace';
const XMLNS_NS = 'http://www.w3.org/2000/xmlns/';
const DOCX_MIME = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document';
// (paragraph, its text nodes). Word cuts sentences into runs, so a name can be split
// across text nodes; matching is done on the joined paragraph. The DrawingML pair
// covers text in shapes, charts and SmartArt.
const GROUPS = [[W, 'p', W, 't'], [A, 'p', A, 't']];
const IMAGE_EXT = new Set(['png', 'jpg', 'jpeg', 'jfif', 'gif', 'bmp', 'tif', 'tiff', 'webp',
  'emf', 'wmf', 'svg', 'wdp', 'heic', 'heif']);
const BROWSER_SHOWS = {png: 'image/png', jpg: 'image/jpeg', jpeg: 'image/jpeg', jfif: 'image/jpeg',
  gif: 'image/gif', bmp: 'image/bmp', webp: 'image/webp', svg: 'image/svg+xml'};
const WHOLE_META = new Set(['creator', 'lastModifiedBy', 'Company', 'Manager']);
const PERSON_ATTRS = new Set(['author', 'initials', 'userId', 'providerId']);
const PERSON_ELEMENTS = new Set(['author', 'person']);
const MAGIC = [7, 77, 177];
const ID_LETTERS = 'ABCDEFGHIJKLMNOPRSTUVWXYZ'; // no Q: Q is the padding
const TOKEN_RE = /QZ[A-PR-Z]+Q*/g;
const PATTERNS = {
  email_addresses: String.raw`[\w.+-]+@[\w-]+(?:\.[\w-]+)+`,
  web_addresses: String.raw`(?:https?://|www\.)[^\s<>"']+|\b[\w-]+(?:\.[\w-]+)*\.(?:edu|sch|school|k12)(?:\.[a-z]{2,})+\b`,
  phone_numbers: String.raw`(?<![\w.])\+?\d[\d ()-]{6,}\d(?!\w)`,
};
const PHONE_DIGITS = 8;
const NOTE = `About these documents

Words made of capital letters that start with QZ and are padded with Q (for example
QZAQQQQQQQ) stand in for confidential names, addresses and contact details. Keep each
one exactly as written, letter for letter, wherever it belongs in your work. They are
swapped back afterwards.

White pictures stand in for the original pictures. Leave them where they are; you may
copy them into new documents. They are swapped back afterwards too.

Save your finished work as Word documents (.docx).
`;

const esc = s => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
const extOf = name => (name.split('.').pop() || '').toLowerCase();
const isThumbnail = name => name.startsWith('docProps/'); // a picture of page one: always hidden

async function sha(bytes) {
  const d = await crypto.subtle.digest('SHA-256', bytes);
  return [...new Uint8Array(d)].map(b => b.toString(16).padStart(2, '0')).join('');
}

// ---------------------------------------------------------------- storage

const dbReady = new Promise((resolve, reject) => {
  const r = indexedDB.open('school-cleaner', 1);
  r.onupgradeneeded = () => { r.result.createObjectStore('kv'); r.result.createObjectStore('pictures'); };
  r.onsuccess = () => resolve(r.result);
  r.onerror = () => reject(r.error);
});
async function store(name, mode, fn) {
  const db = await dbReady;
  return new Promise((resolve, reject) => {
    const tx = db.transaction(name, mode);
    const req = fn(tx.objectStore(name));
    tx.oncomplete = () => resolve(req && req.result);
    tx.onerror = () => reject(tx.error);
  });
}
const kvGet = k => store('kv', 'readonly', s => s.get(k));
const kvSet = (k, v) => store('kv', 'readwrite', s => s.put(v, k));
const picGet = id => store('pictures', 'readonly', s => s.get(id));
const picSet = (id, bytes) => store('pictures', 'readwrite', s => s.put(bytes, id));
const picAll = () => store('pictures', 'readonly', s => s.getAllKeys());
async function forgetEverything() {
  await store('kv', 'readwrite', s => s.clear());
  await store('pictures', 'readwrite', s => s.clear());
}

async function getConfig() {
  return (await kvGet('config')) || {terms: [], email_addresses: true, web_addresses: true, phone_numbers: true};
}
async function setConfig(cfg) {
  const terms = [...new Set(cfg.terms.map(t => t.trim()).filter(Boolean))];
  if (!terms.length) throw new Error("Add at least the school's name.");
  if (terms.some(t => t.length < 3)) throw new Error('Each detail must be at least 3 characters long.');
  await kvSet('config', {...cfg, terms});
}

class Vault {
  static async load() {
    const v = new Vault();
    v.texts = (await kvGet('texts')) || {};
    v.images = (await kvGet('images')) || {};
    v.index();
    return v;
  }
  index() {
    this.byToken = Object.fromEntries(Object.entries(this.texts).map(([k, t]) => [t, k]));
    this.byBlank = Object.fromEntries(Object.entries(this.images).map(([id, m]) => [m.blank_sha, id]));
    this.rx = null;
  }
  async save() { await kvSet('texts', this.texts); await kvSet('images', this.images); }

  tokenFor(original) {
    if (original in this.texts) return this.texts[original];
    let n = Object.keys(this.texts).length, ident = '';
    do { ident = ID_LETTERS[n % 25] + ident; n = Math.floor(n / 25); } while (n > 0);
    let token = 'QZ' + ident;
    if (token.length > original.length) throw new Error('Too many different short details to give each a same-length placeholder.');
    token += 'Q'.repeat(original.length - token.length);
    this.texts[original] = token;
    this.byToken[token] = original;
    this.rx = null;
    return token;
  }
  untokenise(text, counts) {
    if (!text || !text.includes('QZ')) return text;
    const tokens = Object.keys(this.byToken);
    if (!tokens.length) return text;
    if (!this.rx) this.rx = new RegExp(tokens.sort((a, b) => b.length - a.length).map(esc).join('|'), 'g');
    return text.replace(this.rx, m => { counts.text++; return this.byToken[m]; });
  }

  async blankFor(bytes, name, source) {
    const id = String(Object.keys(this.images).length + 1);
    await picSet(id, bytes);
    const e = extOf(name);
    const {blank, size} = await makeBlank(bytes, e, Number(id));
    const blankSha = await sha(blank);
    this.images[id] = {ext: e, size, blank_sha: blankSha, from: source};
    this.byBlank[blankSha] = id;
    return blank;
  }
  async blankId(bytes) {
    return this.byBlank[await sha(bytes)] || await blankIdFromPixels(bytes);
  }
}

// ---------------------------------------------------------------- pictures

async function imageSize(bytes, e) {
  try {
    const bmp = await createImageBitmap(new Blob([bytes], {type: BROWSER_SHOWS[e] || ''}));
    const s = [bmp.width, bmp.height];
    bmp.close();
    return s;
  } catch { /* not a format the browser draws; read the header instead */ }
  const dv = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  if (e === 'emf' && bytes.length > 24) {
    const [l, t, r, b] = [8, 12, 16, 20].map(o => dv.getInt32(o, true));
    if (r > l && b > t) return [r - l + 1, b - t + 1];
  }
  if (e === 'wmf' && bytes.length > 16 && dv.getUint32(0, true) === 0x9AC6CDD7) {
    const [l, t, r, b] = [6, 8, 10, 12].map(o => dv.getInt16(o, true));
    const inch = dv.getUint16(14, true) || 1440;
    return [Math.round((r - l) * 96 / inch), Math.round((b - t) * 96 / inch)];
  }
  if (e === 'svg') {
    const m = new TextDecoder().decode(bytes).match(/width="([\d.]+)[a-z]*"[^>]*height="([\d.]+)[a-z]*"/);
    if (m) return [Math.round(+m[1]), Math.round(+m[2])];
  }
  return [100, 100];
}

async function makeBlank(bytes, e, id) {
  const [w, h] = (await imageSize(bytes, e)).map(v => Math.max(1, Math.min(v || 1, 8000)));
  if (e === 'svg') {
    const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="${h}"><desc>blank picture ${id}</desc>` +
      '<rect width="100%" height="100%" fill="white"/></svg>';
    return {blank: new TextEncoder().encode(svg), size: [w, h]};
  }
  const canvas = document.createElement('canvas');
  canvas.width = w; canvas.height = h;
  const ctx = canvas.getContext('2d');
  ctx.fillStyle = '#ffffff';
  ctx.fillRect(0, 0, w, h);
  if (w * h >= 2) {
    const px = ctx.createImageData(1, 1);
    px.data.set([...MAGIC, 255]);
    ctx.putImageData(px, 0, 0);
    px.data.set([(id >> 16) & 255, (id >> 8) & 255, id & 255, 255]);
    ctx.putImageData(px, w > 1 ? 1 : 0, w > 1 ? 0 : 1);
  }
  const type = ['jpg', 'jpeg', 'jfif'].includes(e) ? 'image/jpeg' : e === 'webp' ? 'image/webp' : 'image/png';
  const blob = await new Promise(r => canvas.toBlob(r, type, 0.95));
  return {blank: new Uint8Array(await blob.arrayBuffer()), size: [w, h]};
}

async function blankIdFromPixels(bytes) {
  let bmp;
  try { bmp = await createImageBitmap(new Blob([bytes])); } catch { return null; }
  if (bmp.width * bmp.height < 2) return null;
  const canvas = document.createElement('canvas');
  canvas.width = Math.min(bmp.width, 2); canvas.height = bmp.width > 1 ? 1 : 2;
  const ctx = canvas.getContext('2d');
  ctx.drawImage(bmp, 0, 0);
  const d = ctx.getImageData(0, 0, canvas.width, canvas.height).data;
  if (d[0] !== MAGIC[0] || d[1] !== MAGIC[1] || d[2] !== MAGIC[2]) return null;
  return String((d[4] << 16) | (d[5] << 8) | d[6]);
}

// ---------------------------------------------------------------- finding text

class Finder {
  // With a vault, swaps confidential strings for placeholders; without, only records them.
  constructor(cfg, vault) {
    this.vault = vault;
    this.rules = Object.keys(PATTERNS).filter(k => cfg[k] !== false).map(k => [k, new RegExp(PATTERNS[k], 'gi')]);
    for (const t of [...cfg.terms].sort((a, b) => b.length - a.length)) {
      this.rules.push(['terms', new RegExp(String.raw`(?<!\w)` + esc(t) + String.raw`(?!\w)`, 'gi')]);
    }
    this.counts = {text: 0, pictures: 0, kept: 0};
    this.found = [];
  }
  tokenise(text, {phones = true, termsOnly = false} = {}) {
    for (const [name, rx] of this.rules) {
      if ((termsOnly && name !== 'terms') || (name === 'phone_numbers' && !phones)) continue;
      text = text.replace(rx, s => {
        if (name === 'phone_numbers' && (s.match(/\d/g) || []).length < PHONE_DIGITS) return s;
        if (!this.vault) { this.found.push(name); return s; }
        this.counts.text++;
        return this.vault.tokenFor(s);
      });
    }
    return text;
  }
  textFn(text, kind) {
    if (kind === 'text') return this.tokenise(text);
    if (kind === 'leaf') return this.tokenise(text, {phones: false});
    if (kind === 'attr') return this.tokenise(text, {termsOnly: true});
    // 'whole': author names and external link targets go entirely, listed or not.
    if (!this.vault) return this.tokenise(text, {phones: false});
    if (text.length < 3 || /^QZ[A-PR-Z]+Q*$/.test(text)) return text;
    this.counts.text++;
    return this.vault.tokenFor(text);
  }
}

// ---------------------------------------------------------------- Word documents

function nearest(node, ns, local) {
  for (let n = node.parentNode; n && n.nodeType === 1; n = n.parentNode) {
    if (n.namespaceURI === ns && n.localName === local) return n;
  }
  return null;
}

// Run fn(text, kind) over every piece of text in one XML part. fn keeps lengths.
// Returns the new XML, or null when nothing changed (the part is then left byte for byte).
function xmlTransform(source, part, fn) {
  const doc = new DOMParser().parseFromString(source, 'application/xml');
  if (doc.getElementsByTagName('parsererror').length) throw new Error('unreadable part ' + part);
  let changed = false;
  for (const [pns, pl, tns, tl] of GROUPS) {
    for (const p of [...doc.getElementsByTagNameNS(pns, pl)]) {
      const nodes = [...p.getElementsByTagNameNS(tns, tl)].filter(t => nearest(t, pns, pl) === p);
      const texts = nodes.map(n => n.textContent);
      const joined = texts.join('');
      if (!joined) continue;
      const next = fn(joined, 'text');
      if (next !== joined) {
        let pos = 0; // same length, so each run gets back exactly its own slice
        nodes.forEach((n, i) => {
          n.textContent = next.slice(pos, pos + texts[i].length);
          pos += texts[i].length;
          n.setAttributeNS(XML_NS, 'xml:space', 'preserve');
        });
        changed = true;
      }
    }
  }
  const isMeta = part.startsWith('docProps/');
  for (const el of doc.getElementsByTagName('*')) {
    const name = el.localName;
    const groupText = GROUPS.some(([, , tns, tl]) => el.namespaceURI === tns && name === tl);
    // Other text: tracked deletions, field codes, document properties, chart values.
    if (!el.childElementCount && el.textContent && !groupText) {
      const next = fn(el.textContent, isMeta && WHOLE_META.has(name) ? 'whole' : 'leaf');
      if (next !== el.textContent) { el.textContent = next; changed = true; }
    }
    for (const at of [...el.attributes]) {
      if (at.namespaceURI === XMLNS_NS) continue;
      const k = at.localName;
      const whole = PERSON_ATTRS.has(k) || (PERSON_ELEMENTS.has(name) && k === 'name') ||
        (name === 'Relationship' && k === 'Target' && el.getAttribute('TargetMode') === 'External');
      const next = fn(at.value, whole ? 'whole' : 'attr');
      if (next !== at.value) { at.value = next; changed = true; }
    }
  }
  if (!changed) return null;
  const out = new XMLSerializer().serializeToString(doc);
  return out.startsWith('<?xml') ? out : '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\r\n' + out;
}

// Copy a .docx, passing its text through textFn and its pictures through imageFn.
async function docxTransform(buffer, textFn, imageFn, {build = true} = {}) {
  const zip = await JSZip.loadAsync(buffer);
  const flags = new Set();
  for (const name of Object.keys(zip.files)) {
    const file = zip.files[name];
    if (file.dir) continue;
    const e = extOf(name);
    if (IMAGE_EXT.has(e)) {
      const data = await file.async('uint8array');
      const next = await imageFn(data, name);
      if (next !== data) zip.file(name, next, {createFolders: false});
    } else if (/\.(xml|rels|vml)$/i.test(name)) {
      const next = xmlTransform(await file.async('string'), name, textFn);
      if (next !== null) zip.file(name, next, {createFolders: false});
    } else if (name.includes('/embeddings/') || name.endsWith('.bin')) {
      flags.add('contains embedded objects or macros this page cannot clean');
    } else if (/\.(mp4|mov|m4a|mp3|wav|wmv|avi)$/i.test(name)) {
      flags.add('contains audio or video this page cannot clean');
    }
  }
  const blob = build ? await zip.generateAsync({type: 'blob', compression: 'DEFLATE', mimeType: DOCX_MIME}) : null;
  return {blob, flags: [...flags]};
}

// Every distinct picture across the documents, in order of first appearance.
async function collectPictures(files) {
  const pics = new Map();
  for (const f of files) {
    const zip = await JSZip.loadAsync(await f.arrayBuffer());
    for (const name of Object.keys(zip.files)) {
      if (zip.files[name].dir || !IMAGE_EXT.has(extOf(name)) || isThumbnail(name)) continue;
      const data = await zip.files[name].async('uint8array');
      const key = await sha(data);
      if (!pics.has(key)) pics.set(key, {sha: key, data, ext: extOf(name), docs: []});
      const p = pics.get(key);
      if (!p.docs.includes(f.name)) p.docs.push(f.name);
    }
  }
  return [...pics.values()];
}

async function cleanDocuments(files, hide) {
  const cfg = await getConfig();
  if (!cfg.terms.length) throw new Error('Fill in the school details first.');
  const vault = await Vault.load();
  const results = [];
  for (const f of files) {
    const finder = new Finder(cfg, vault);
    const name = finder.tokenise(f.name, {phones: false});
    try {
      const {blob, flags} = await docxTransform(await f.arrayBuffer(), (t, k) => finder.textFn(t, k),
        async (data, part) => {
          if (!isThumbnail(part) && !hide.has(await sha(data))) { finder.counts.kept++; return data; }
          finder.counts.pictures++;
          return vault.blankFor(data, part, `${f.name} / ${part}`);
        });
      await vault.save();
      const notes = flags.concat(await checkClean(blob, cfg, vault, hide));
      results.push({name, blob, counts: finder.counts, notes, status: notes.length ? 'Check by hand' : 'Clean'});
    } catch (e) {
      await vault.save();
      results.push({name, blob: null, counts: finder.counts, notes: [`could not be cleaned (${e.message})`], status: 'Not cleaned'});
    }
  }
  return results;
}

// Re-read a cleaned document: anything from the settings left, or a picture meant to be hidden?
async function checkClean(blob, cfg, vault, hide) {
  const probe = new Finder(cfg, null);
  const problems = [];
  await docxTransform(await blob.arrayBuffer(), (t, k) => probe.textFn(t, k), async (data, part) => {
    if ((isThumbnail(part) || hide.has(await sha(data))) && !(await vault.blankId(data))) {
      problems.push('a picture was not replaced: ' + part);
    }
    return data;
  }, {build: false});
  return problems.concat([...new Set(probe.found)].map(r => r.replace('_', ' ') + ' still in the text'));
}

async function restoreDocuments(files) {
  const vault = await Vault.load();
  const results = [];
  for (const f of files) {
    if (f.name === 'ABOUT-THESE-DOCUMENTS.txt') continue;
    const counts = {text: 0, pictures: 0};
    const name = vault.untokenise(f.name, counts);
    let blob, notes = [];
    try {
      if (extOf(f.name) === 'docx') {
        ({blob} = await docxTransform(await f.arrayBuffer(), t => vault.untokenise(t, counts), async data => {
          const id = await vault.blankId(data);
          if (!id || !(id in vault.images)) return data;
          counts.pictures++;
          return picGet(id);
        }));
        await docxTransform(await blob.arrayBuffer(), t => {
          if (t.match(TOKEN_RE)) notes.push('placeholder text still there');
          return t;
        }, async (data, part) => {
          if (await vault.blankId(data)) notes.push('placeholder picture still there: ' + part);
          return data;
        }, {build: false});
      } else if (IMAGE_EXT.has(extOf(f.name))) {
        const bytes = new Uint8Array(await f.arrayBuffer());
        const id = await vault.blankId(bytes);
        blob = new Blob([id ? await picGet(id) : bytes]);
        if (id) counts.pictures++;
      } else {
        const text = vault.untokenise(await f.text(), counts);
        if (text.match(TOKEN_RE)) notes.push('placeholder text still there');
        blob = new Blob([text], {type: f.type || 'text/plain'});
      }
    } catch (e) {
      blob = null;
      notes.push(`could not be read (${e.message})`);
    }
    notes = [...new Set(notes)];
    results.push({name, blob, counts, notes, status: blob ? (notes.length ? 'Check by hand' : 'Restored') : 'Not restored'});
  }
  return results;
}

// ---------------------------------------------------------------- backup

const toB64 = bytes => { let s = ''; for (let i = 0; i < bytes.length; i += 0x8000) s += String.fromCharCode(...bytes.subarray(i, i + 0x8000)); return btoa(s); };
const fromB64 = b64 => Uint8Array.from(atob(b64), c => c.charCodeAt(0));

async function makeBackup() {
  const pictures = {};
  for (const id of await picAll()) pictures[id] = toB64(await picGet(id));
  const data = {app: 'school-cleaner', version: 1, saved: new Date().toISOString(),
    config: await getConfig(), texts: (await kvGet('texts')) || {}, images: (await kvGet('images')) || {}, pictures};
  return new Blob([JSON.stringify(data)], {type: 'application/json'});
}

async function loadBackup(file) {
  const data = JSON.parse(await file.text());
  if (data.app !== 'school-cleaner') throw new Error('That is not a School Cleaner backup.');
  await forgetEverything();
  await kvSet('config', data.config);
  await kvSet('texts', data.texts);
  await kvSet('images', data.images);
  for (const [id, b64] of Object.entries(data.pictures)) await picSet(id, fromB64(b64));
}
