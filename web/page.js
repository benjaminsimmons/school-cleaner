// The page: wires the buttons to cleaner.js. Nothing here sends data anywhere.
'use strict';

const $ = id => document.getElementById(id);
const FLAGS = ['email_addresses', 'web_addresses', 'phone_numbers'];
function say(el, text, err) { el.textContent = text; el.className = 'msg' + (err ? ' err' : ''); }
const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`;

// ---- saving files: into a chosen folder where the browser allows it, else as downloads
function download(blob, name) {
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = name;
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 10000);
}
async function saveAll(items) {
  if (window.showDirectoryPicker) {
    let dir;
    try { dir = await showDirectoryPicker({mode: 'readwrite'}); } catch { return false; }
    for (const {name, blob} of items) {
      const handle = await dir.getFileHandle(name, {create: true});
      const w = await handle.createWritable();
      await w.write(blob);
      await w.close();
    }
    return true;
  }
  for (const {name, blob} of items) { download(blob, name); await new Promise(r => setTimeout(r, 400)); }
  return true;
}

function listResults(ul, results, kind) {
  ul.innerHTML = '';
  for (const r of results) {
    const li = document.createElement('li');
    const info = document.createElement('div');
    info.innerHTML = '<div><span class="status"></span><span class="name"></span></div><div class="detail"></div>';
    info.querySelector('.status').textContent = r.status;
    info.querySelector('.name').textContent = r.name;
    info.querySelector('.detail').textContent = kind === 'clean'
      ? `Hid ${plural(r.counts.text, 'piece')} of text and ${plural(r.counts.pictures, 'picture')}; kept ${plural(r.counts.kept, 'picture')}.`
      : `Put back ${plural(r.counts.text, 'piece')} of text and ${plural(r.counts.pictures, 'picture')}.`;
    for (const n of r.notes) {
      const d = document.createElement('div'); d.className = 'bad'; d.textContent = '! ' + n; info.append(d);
    }
    li.append(info);
    if (r.blob) {
      const b = document.createElement('button'); b.textContent = 'Save';
      b.onclick = () => download(r.blob, r.name);
      li.append(b);
    }
    ul.append(li);
  }
}

// ---- settings
async function showSettings() {
  const cfg = await getConfig();
  $('terms').value = cfg.terms.join('\n');
  for (const k of FLAGS) $(k).checked = cfg[k] !== false;
  $('cleanCard').hidden = $('restoreCard').hidden = !cfg.terms.length;
}
$('save').onclick = async () => {
  const cfg = {terms: $('terms').value.split('\n')};
  for (const k of FLAGS) cfg[k] = $(k).checked;
  try { await setConfig(cfg); await showSettings(); say($('saveMsg'), 'Saved.'); }
  catch (e) { say($('saveMsg'), e.message, true); }
};

// ---- clean
let cleanFiles = [], pics = [], hide = new Set(), cleaned = [];
function drawPictures() {
  const grid = $('grid'); grid.innerHTML = '';
  pics.forEach((p, i) => {
    const card = document.createElement('button');
    card.type = 'button';
    card.className = 'pic' + (hide.has(p.sha) ? ' hide' : '');
    card.setAttribute('aria-pressed', hide.has(p.sha));
    card.innerHTML = `<span class="tag">${hide.has(p.sha) ? 'Hide' : 'Keep'}</span>`;
    if (BROWSER_SHOWS[p.ext]) {
      const img = document.createElement('img');
      img.alt = `Picture ${i + 1}`;
      img.src = p.url || (p.url = URL.createObjectURL(new Blob([p.data], {type: BROWSER_SHOWS[p.ext]})));
      card.append(img);
    } else {
      card.insertAdjacentHTML('beforeend', '<span class="none">No preview<br>(Word drawing)</span>');
    }
    const docs = document.createElement('span'); docs.className = 'docs'; docs.textContent = 'In: ' + p.docs.join(', ');
    card.append(docs);
    card.onclick = () => { hide.has(p.sha) ? hide.delete(p.sha) : hide.add(p.sha); drawPictures(); };
    grid.append(card);
  });
  $('picCount').textContent = `${hide.size} of ${pics.length} will be hidden`;
}
async function addCleanFiles(list) {
  const docs = [...list].filter(f => f.name.toLowerCase().endsWith('.docx') && !f.name.startsWith('~$'));
  if (!docs.length) return say($('cleanMsg'), 'Only Word documents (.docx) can be cleaned.', true);
  const names = new Set(cleanFiles.map(f => f.name));
  cleanFiles.push(...docs.filter(f => !names.has(f.name)));
  say($('cleanMsg'), 'Reading the pictures…');
  try {
    const known = new Set(pics.map(p => p.sha));
    pics = await collectPictures(cleanFiles);
    pics.forEach(p => { if (!known.has(p.sha)) hide.add(p.sha); });
    $('picsBox').hidden = !pics.length;
    $('cleanRow').hidden = false;
    drawPictures();
    say($('cleanMsg'), `${plural(cleanFiles.length, 'document')} added${pics.length ? '' : ', with no pictures'}. You can add more.`);
  } catch (e) { say($('cleanMsg'), 'One of those documents could not be read: ' + e.message, true); }
}
function resetClean() {
  cleanFiles = []; pics = []; hide = new Set(); cleaned = [];
  $('picsBox').hidden = $('cleanRow').hidden = $('cleanDone').hidden = true;
  $('cleanDrop').hidden = false;
  say($('cleanMsg'), '');
}
$('clean').onclick = async () => {
  $('clean').disabled = true; say($('cleanMsg'), 'Cleaning…');
  try {
    cleaned = await cleanDocuments(cleanFiles, hide);
    listResults($('cleanList'), cleaned, 'clean');
    $('cleanDone').hidden = false;
    $('cleanDrop').hidden = $('picsBox').hidden = true;
    $('cleanRow').hidden = true;
    say($('cleanMsg'), '');
  } catch (e) { say($('cleanMsg'), e.message, true); } finally { $('clean').disabled = false; }
};
$('cleanSave').onclick = () => saveAll([...cleaned.filter(r => r.blob),
  {name: 'ABOUT-THESE-DOCUMENTS.txt', blob: new Blob([NOTE], {type: 'text/plain'})}]);
$('cleanReset').onclick = resetClean;
$('hideAll').onclick = () => { pics.forEach(p => hide.add(p.sha)); drawPictures(); };
$('keepAll').onclick = () => { hide.clear(); drawPictures(); };

// ---- restore
let restored = [];
async function addRestoreFiles(list) {
  say($('restoreMsg'), 'Putting the details back…');
  try {
    restored = await restoreDocuments([...list]);
    listResults($('restoreList'), restored, 'restore');
    $('restoreDone').hidden = false;
    say($('restoreMsg'), restored.some(r => r.notes.length)
      ? 'Check the documents marked "Check by hand": search them for QZ.' : '');
  } catch (e) { say($('restoreMsg'), e.message, true); }
}
$('restoreSave').onclick = () => saveAll(restored.filter(r => r.blob));

// ---- drop zones
function dropZone(zone, input, onFiles) {
  zone.onclick = e => { if (e.target === zone) input.click(); };
  zone.onkeydown = e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); input.click(); } };
  input.onchange = () => { onFiles(input.files); input.value = ''; };
  zone.ondragover = e => { e.preventDefault(); zone.classList.add('over'); };
  zone.ondragleave = () => zone.classList.remove('over');
  zone.ondrop = e => { e.preventDefault(); zone.classList.remove('over'); onFiles(e.dataTransfer.files); };
}
dropZone($('cleanDrop'), $('cleanFiles'), addCleanFiles);
dropZone($('restoreDrop'), $('restoreFiles'), addRestoreFiles);

// ---- backup
$('backup').onclick = async () => download(await makeBackup(), `school-cleaner-backup-${new Date().toISOString().slice(0, 10)}.json`);
$('restoreBackup').onclick = () => $('backupFile').click();
$('backupFile').onchange = async () => {
  const f = $('backupFile').files[0]; $('backupFile').value = '';
  if (!f || !confirm('Loading a backup replaces everything this browser holds now. Continue?')) return;
  try { await loadBackup(f); await showSettings(); say($('backupMsg'), 'Backup loaded.'); }
  catch (e) { say($('backupMsg'), e.message, true); }
};
$('forget').onclick = async () => {
  if (!confirm('This deletes the school details and every hidden picture from this browser. ' +
    'Documents cleaned earlier can then never be put back. Continue?')) return;
  await forgetEverything(); resetClean(); await showSettings(); say($('backupMsg'), 'Everything forgotten.');
};

// ---- start
if (navigator.storage && navigator.storage.persist) navigator.storage.persist();
if ('serviceWorker' in navigator) navigator.serviceWorker.register('sw.js');
showSettings();
