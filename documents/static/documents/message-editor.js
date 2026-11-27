const messagePrefix = "docstation-rich:v1\n";
const messageTags = new Set("a b blockquote br div em font i ins li ol p s span strike strong sub sup table tbody td th thead tr u ul h1 h2 h3 pre code hr cite".split(" "));
const messageStyles = {
  color: /^(#[a-f\d]{3,8}|rgb\(\d{1,3},\s*\d{1,3},\s*\d{1,3}\))$/i,
  backgroundColor: /^(#[a-f\d]{3,8}|rgb\(\d{1,3},\s*\d{1,3},\s*\d{1,3}\))$/i,
  textAlign: /^(left|center|right|justify)$/,
  marginLeft: /^(40|80|120|160)px$/,
  fontSize: /^(small|medium|large|x-large)$/,
};

function messageHtml(html) {
  const template = document.createElement("template");
  template.innerHTML = html;

  function clean(parent) {
    const fragment = document.createDocumentFragment();
    for (const node of parent.childNodes) {
      if (node.nodeType === Node.TEXT_NODE) {
        fragment.append(node.textContent);
      } else if (node.nodeType === Node.ELEMENT_NODE) {
        const tag = node.localName;
        if (!messageTags.has(tag)) {
          if (!["script", "style", "iframe", "svg", "math", "template"].includes(tag)) fragment.append(clean(node));
          continue;
        }
        const element = document.createElement(tag);
        if (tag === "a") {
          const href = node.getAttribute("href") || "";
          if (/^(https?:|mailto:)/i.test(href) && !/[\u0000-\u001f]/.test(href)) element.setAttribute("href", href);
        }
        if (tag === "font") {
          const size = node.getAttribute("size");
          const color = node.getAttribute("color");
          if (/^[2-5]$/.test(size)) element.setAttribute("size", size);
          if (/^#[a-f\d]{6}$/i.test(color)) element.setAttribute("color", color);
        }
        const alignment = node.getAttribute("align");
        if (["left", "center", "right", "justify"].includes(alignment)) element.style.textAlign = alignment;
        for (const [property, pattern] of Object.entries(messageStyles)) {
          if (pattern.test(node.style?.[property] || "")) element.style[property] = node.style[property];
        }
        element.append(clean(node));
        fragment.append(element);
      }
    }
    return fragment;
  }

  return clean(template.content);
}

for (const editor of document.querySelectorAll("[data-message-editor]")) {
  const frame = editor.querySelector("[data-editor-frame]");
  const doc = frame.contentDocument;
  doc.open();
  doc.write("<!doctype html><html lang='ru'><head><meta charset='utf-8'></head><body></body></html>");
  doc.close();
  const sheet = doc.createElement("link");
  sheet.rel = "stylesheet";
  sheet.href = frame.dataset.css;
  doc.head.append(sheet);
  doc.body.append(doc.importNode(editor.querySelector("[data-editor-markup]").content, true));
  const $ = selector => doc.querySelector(selector);
  const ed = $("#editor"), src = $("#source"), panel = $("#panel");
  const value = editor.querySelector("textarea[name]");
  const form = value.form;
  const taskMessage = value.name === "purpose";
  let mode = "visual";
  let savedRange = null;
  function sanitize(html) {
    const container = document.createElement("div");
    container.append(messageHtml(html));
    return container.innerHTML;
  }
  function sync() {
    const body = document.createElement("div");
    body.append(messageHtml(mode === "visual" ? ed.innerHTML : src.value));
    const text = body.textContent;
    value.value = !text.trim() ? "" : !body.children.length && !text.startsWith(messagePrefix)
      ? text : messagePrefix + body.innerHTML;
  }
  var slice = Array.prototype.slice;
  var BLOCK = /^(P|H[1-6]|UL|OL|BLOCKQUOTE|PRE|TABLE|HR|DIV)$/;

  var blockSelect = $('#blockSelect'), quoteBtn = $('#quoteBtn'), authorBtn = $('#authorBtn');
  var undoBtn = $('#undoBtn'), redoBtn = $('#redoBtn');
  var tblBtns = slice.call(doc.querySelectorAll('[data-tbl]'));

  /* ---------- helpers ---------- */
  function esc(s) { return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'); }
  function elOf(n) { return n.nodeType === 1 ? n : n.parentElement; }

  /* ---------- history (own undo stack, so structural edits can be undone too) ---------- */
  var hist = [], hIdx = -1, hTimer = null;
  function pathOf(node) {
    var p = [];
    while (node && node !== ed) {
      p.unshift(slice.call(node.parentNode.childNodes).indexOf(node));
      node = node.parentNode;
    }
    return p;
  }
  function nodeAt(path) {
    var n = ed;
    for (var i = 0; i < path.length; i++) {
      if (!n.childNodes[path[i]]) break;
      n = n.childNodes[path[i]];
    }
    return n;
  }
  function selState() {
    var s = doc.getSelection();
    if (!s.rangeCount) return null;
    var r = s.getRangeAt(0);
    if (!ed.contains(r.startContainer) || !ed.contains(r.endContainer)) return null;
    return { sp: pathOf(r.startContainer), so: r.startOffset, ep: pathOf(r.endContainer), eo: r.endOffset };
  }
  function nodeLen(n) { return n.nodeType === 3 ? n.length : n.childNodes.length; }
  function applySel(st) {
    if (!st) return;
    try {
      var sn = nodeAt(st.sp), en = nodeAt(st.ep);
      var r = doc.createRange();
      r.setStart(sn, Math.min(st.so, nodeLen(sn)));
      r.setEnd(en, Math.min(st.eo, nodeLen(en)));
      var s = doc.getSelection();
      s.removeAllRanges();
      s.addRange(r);
      savedRange = r.cloneRange();
    } catch (e) {}
  }
  function record() {
    clearTimeout(hTimer);
    hTimer = null;
    var html = ed.innerHTML, top = hist[hIdx], st = selState();
    if (top && top.html === html) { if (st) top.sel = st; return; }
    hist = hist.slice(0, hIdx + 1);
    hist.push({ html: html, sel: st });
    if (hist.length > 200) hist.shift();
    hIdx = hist.length - 1;
    refreshToolbar();
  }
  function showEntry(entry) {
    ed.innerHTML = entry.html;
    savedRange = null;
    ed.focus();
    applySel(entry.sel);
    src.value = ed.innerHTML;
    sync();
    updateState();
  }
  function undo() { record(); if (hIdx > 0) { hIdx--; showEntry(hist[hIdx]); } }
  function redo() { record(); if (hIdx < hist.length - 1) { hIdx++; showEntry(hist[hIdx]); } }
  undoBtn.addEventListener('click', undo);
  redoBtn.addEventListener('click', redo);

  function push(now) {
    if (ed.innerHTML === '<br>') ed.innerHTML = '';
    src.value = ed.innerHTML;
    sync();
    clearTimeout(hTimer);
    if (now) record(); else { hTimer = setTimeout(record, 400); refreshToolbar(); }
  }

  /* ---------- selection ---------- */
  doc.addEventListener('selectionchange', function () {
    var s = doc.getSelection();
    if (s && s.rangeCount && ed.contains(s.anchorNode)) {
      savedRange = s.getRangeAt(0).cloneRange();
      updateState();
    }
  });
  /* save typing made so far as its own undo step before any toolbar action changes the text */
  function flush() { if (hTimer) record(); }
  function restore() {
    flush();
    ed.focus();
    if (savedRange && ed.contains(savedRange.startContainer) && ed.contains(savedRange.endContainer)) {
      var s = doc.getSelection();
      s.removeAllRanges();
      s.addRange(savedRange);
    }
  }
  function placeStart(r, node) {
    var t = doc.createTreeWalker(node, NodeFilter.SHOW_TEXT, null).nextNode();
    if (t) r.setStart(t, 0); else r.setStart(node, 0);
    r.collapse(true);
  }
  function reselect(sv, fallback) {
    var r = doc.createRange(), ok = true;
    try {
      if (!ed.contains(sv.sc) || !ed.contains(sv.ec)) throw 0;
      r.setStart(sv.sc, sv.so);
      r.setEnd(sv.ec, sv.eo);
    } catch (e) { ok = false; }
    if (!ok) {
      r = doc.createRange();
      if (fallback && ed.contains(fallback)) placeStart(r, fallback);
      else { r.selectNodeContents(ed); r.collapse(true); }
    }
    var s = doc.getSelection();
    s.removeAllRanges();
    s.addRange(r);
    savedRange = r.cloneRange();
  }

  /* ---------- toolbar state ---------- */
  function cellFromNode(n) {
    if (!n) return null;
    var el = elOf(n), c = el && el.closest('td,th');
    return c && ed.contains(c) ? c : null;
  }
  function inCell() { return savedRange ? cellFromNode(savedRange.startContainer) : null; }
  function quoteAt() {
    if (!savedRange) return null;
    var el = elOf(savedRange.startContainer), q = el && el.closest('blockquote');
    return q && ed.contains(q) ? q : null;
  }
  function refreshToolbar() {
    var off = mode === 'html';
    var inQ = !!quoteAt(), inC = !!inCell();
    quoteBtn.setAttribute('aria-pressed', inQ ? 'true' : 'false');
    quoteBtn.disabled = off || inC;
    authorBtn.disabled = off || !inQ;
    tblBtns.forEach(function (b) { b.disabled = off || !inC; });
    undoBtn.disabled = off || !(hIdx > 0 || hTimer);
    redoBtn.disabled = off || hIdx >= hist.length - 1;
  }
  function updateState() {
    doc.querySelectorAll('[data-state]').forEach(function (b) {
      var on = false;
      try { on = doc.queryCommandState(b.getAttribute('data-state')); } catch (e) {}
      b.setAttribute('aria-pressed', on ? 'true' : 'false');
    });
    var v = '';
    try { v = String(doc.queryCommandValue('formatBlock') || '').toLowerCase(); } catch (e) {}
    var map = { h1: 'h1', h2: 'h2', h3: 'h3', pre: 'pre' };
    blockSelect.value = map[v] || 'p';
    refreshToolbar();
  }

  function cmd(name, val) {
    restore();
    doc.execCommand(name, false, val || null);
    push(true);
    updateState();
  }
  $('#toolbar').addEventListener('mousedown', function (e) {
    if (e.target.closest('button')) e.preventDefault();
  });
  doc.querySelectorAll('[data-cmd]').forEach(function (b) {
    b.addEventListener('click', function () { cmd(b.getAttribute('data-cmd')); });
  });
  blockSelect.addEventListener('change', function (e) { cmd('formatBlock', '<' + e.target.value + '>'); });
  $('#clearFmt').addEventListener('click', function () {
    restore();
    doc.execCommand('removeFormat');
    push(true);
    updateState();
  });

  /* ---------- option bars ---------- */
  var docks = { link: $('#dockLink'), color: $('#dockColor'), table: $('#dockTable'), quote: $('#dockQuote') };
  function hideDocks() { Object.keys(docks).forEach(function (k) { docks[k].hidden = true; }); }
  function showDock(name) { hideDocks(); docks[name].hidden = false; }
  function toggleDock(name) {
    var wasOpen = !docks[name].hidden;
    hideDocks();
    if (!wasOpen) docks[name].hidden = false;
    return !wasOpen;
  }
  Object.keys(docks).forEach(function (k) {
    docks[k].addEventListener('mousedown', function (e) { if (e.target.closest('button')) e.preventDefault(); });
  });
  doc.querySelectorAll('[data-close]').forEach(function (b) {
    b.addEventListener('click', function () { hideDocks(); ed.focus(); });
  });

  /* link */
  var linkUrl = $('#linkUrl');
  $('#linkBtn').addEventListener('click', function () {
    if (!toggleDock('link')) return;
    var href = '';
    if (savedRange) {
      var a = elOf(savedRange.commonAncestorContainer).closest('a');
      if (a) href = a.getAttribute('href') || '';
    }
    linkUrl.value = href;
    linkUrl.focus();
    linkUrl.select();
  });
  function applyLink() {
    var url = linkUrl.value.trim();
    if (!url) { hideDocks(); ed.focus(); return; }
    if (!/^[a-z][a-z\d+.-]*:/i.test(url)) url = 'https://' + url;
    if (!/^(https?:|mailto:)/i.test(url)) return;
    restore();
    var sel = doc.getSelection();
    if (sel.isCollapsed) {
      var a = doc.createElement('a');
      a.href = url; a.textContent = url;
      doc.execCommand('insertHTML', false, a.outerHTML);
    } else {
      doc.execCommand('createLink', false, url);
    }
    hideDocks();
    push(true);
  }
  $('#linkApply').addEventListener('click', applyLink);
  $('#linkRemove').addEventListener('click', function () { cmd('unlink'); hideDocks(); });
  linkUrl.addEventListener('keydown', function (e) {
    if (e.key === 'Enter') { e.preventDefault(); applyLink(); }
    if (e.key === 'Escape') { e.preventDefault(); hideDocks(); ed.focus(); }
  });

  /* text color and highlight */
  var swatchesEl = $('#swatches'), colorLabel = $('#colorLabel'), colorCustom = $('#colorCustom');
  var colorKind = null, SENT = '#010203';
  var PALETTES = {
    text: { label: 'Цвет текста', items: [
      ['Цвет по умолчанию', null], ['Чёрный', '#111111'], ['Серый', '#6b7280'], ['Красный', '#d92d20'],
      ['Оранжевый', '#dc6803'], ['Зелёный', '#16803c'], ['Синий', '#2563eb'], ['Фиолетовый', '#7c3aed'], ['Розовый', '#db2777']
    ] },
    mark: { label: 'Выделение цветом', items: [
      ['Без выделения', null], ['Жёлтый', '#fff176'], ['Зелёный', '#b9f6ca'], ['Голубой', '#a5e1ff'],
      ['Розовый', '#ffc1dc'], ['Оранжевый', '#ffd6a0'], ['Сиреневый', '#d9c4ff'], ['Серый', '#d1d5db']
    ] }
  };
  function renderSwatches(kind) {
    swatchesEl.textContent = '';
    PALETTES[kind].items.forEach(function (it) {
      var b = doc.createElement('button');
      b.type = 'button';
      b.className = 'sw' + (it[1] ? '' : ' none');
      b.title = it[0];
      b.setAttribute('aria-label', it[0]);
      if (it[1]) b.style.background = it[1];
      b.addEventListener('click', function () { applyColor(kind, it[1]); });
      swatchesEl.appendChild(b);
    });
  }
  function openColor(kind) {
    if (!docks.color.hidden && colorKind === kind) { hideDocks(); ed.focus(); return; }
    colorKind = kind;
    colorLabel.textContent = PALETTES[kind].label;
    renderSwatches(kind);
    showDock('color');
  }
  function cleanSentinel() {
    var S = 'rgb(1, 2, 3)';
    ed.querySelectorAll('[style]').forEach(function (el) {
      if (el.style.color === S) el.style.removeProperty('color');
      if (el.style.backgroundColor === S) el.style.removeProperty('background-color');
      if (!el.getAttribute('style')) el.removeAttribute('style');
      if (el.tagName === 'SPAN' && !el.attributes.length) {
        while (el.firstChild) el.parentNode.insertBefore(el.firstChild, el);
        el.remove();
      }
    });
  }
  function applyColor(kind, color) {
    restore();
    var c = color || SENT;
    doc.execCommand('styleWithCSS', false, true);
    if (kind === 'text') doc.execCommand('foreColor', false, c);
    else if (!doc.execCommand('hiliteColor', false, c)) doc.execCommand('backColor', false, c);
    doc.execCommand('styleWithCSS', false, false);
    cleanSentinel();
    if (kind === 'text') $('#textBar').style.background = color || '';
    else $('#markIco').style.background = color || '';
    push(true);
    hideDocks();
  }
  $('#textColorBtn').addEventListener('click', function () { openColor('text'); });
  $('#markBtn').addEventListener('click', function () { openColor('mark'); });
  colorCustom.addEventListener('change', function () { if (colorKind) applyColor(colorKind, colorCustom.value); });

  /* ---------- tables ---------- */
  $('#tableBtn').addEventListener('click', function () { toggleDock('table'); });
  $('#tblInsert').addEventListener('click', function () {
    var r = Math.min(30, Math.max(1, parseInt($('#tblRows').value, 10) || 3));
    var c = Math.min(12, Math.max(1, parseInt($('#tblCols').value, 10) || 3));
    var head = $('#tblHead').checked;
    var h = '<table><tbody>';
    for (var i = 0; i < r; i++) {
      h += '<tr>';
      for (var j = 0; j < c; j++) {
        var tag = (head && i === 0) ? 'th' : 'td';
        h += '<' + tag + '><br></' + tag + '>';
      }
      h += '</tr>';
    }
    h += '</tbody></table><p><br></p>';
    restore();
    doc.execCommand('insertHTML', false, h);
    push(true);
    hideDocks();
  });
  function addRowAfter(row) {
    var nr = doc.createElement('tr');
    for (var i = 0; i < row.cells.length; i++) {
      var td = doc.createElement('td');
      td.innerHTML = '<br>';
      nr.appendChild(td);
    }
    row.after(nr);
    return nr;
  }
  tblBtns.forEach(function (b) {
    b.addEventListener('click', function () {
      flush();
      var cell = inCell();
      if (!cell) return;
      var table = cell.closest('table'), row = cell.parentElement, idx = cell.cellIndex;
      switch (b.getAttribute('data-tbl')) {
        case 'addRow': addRowAfter(row); break;
        case 'addCol':
          slice.call(table.rows).forEach(function (tr) {
            var ref = tr.cells[idx] || tr.cells[tr.cells.length - 1];
            if (!ref) return;
            var nc = doc.createElement(ref.tagName.toLowerCase());
            nc.innerHTML = '<br>';
            ref.after(nc);
          });
          break;
        case 'delRow':
          row.remove();
          if (!table.rows.length) table.remove();
          break;
        case 'delCol':
          slice.call(table.rows).forEach(function (tr) { if (tr.cells[idx]) tr.cells[idx].remove(); });
          if (!table.querySelector('td,th')) table.remove();
          break;
        case 'delTable': table.remove(); break;
      }
      savedRange = null;
      if (!ed.lastElementChild || ed.lastElementChild.tagName === 'TABLE') ed.appendChild(emptyP());
      push(true);
      ed.focus();
      refreshToolbar();
    });
  });

  /* ---------- quotes ---------- */
  function emptyP() { var p = doc.createElement('p'); p.innerHTML = '<br>'; return p; }
  function isCite(n) { return n.nodeType === 1 && n.nodeName === 'CITE'; }
  function citeToP(c) { var p = doc.createElement('p'); p.innerHTML = c.innerHTML; return p; }

  /* top-level children of the editor must all be blocks */
  function normalizeTop() {
    var run = null;
    slice.call(ed.childNodes).forEach(function (n) {
      if (n.nodeType === 1 && BLOCK.test(n.nodeName)) { run = null; return; }
      if (n.nodeType === 3 && !n.nodeValue.trim() && !run) return;
      if (n.nodeType === 8) return;
      if (!run) { run = doc.createElement('p'); ed.insertBefore(run, n); }
      run.appendChild(n);
    });
  }
  /* top-level block that holds a range boundary */
  function topOf(node, off, isEnd) {
    var n = node;
    if (n === ed) {
      var kids = ed.childNodes, i = isEnd ? off - 1 : off;
      if (i >= kids.length) i = kids.length - 1;
      if (i < 0) i = 0;
      n = kids[i];
    } else {
      while (n && n.parentNode !== ed) n = n.parentNode;
    }
    while (n && n.nodeType === 3 && !n.nodeValue.trim()) n = isEnd ? n.previousSibling : n.nextSibling;
    return n;
  }
  function unwrapQuote(bq) {
    var par = bq.parentNode;
    while (bq.firstChild) {
      var c = bq.firstChild;
      if (isCite(c)) { var p = citeToP(c); bq.replaceChild(p, c); c = p; }
      par.insertBefore(c, bq);
    }
    par.removeChild(bq);
  }

  function toggleQuote() {
    restore();
    var s = doc.getSelection();
    var truelyEmpty = !ed.textContent.trim() && !ed.querySelector('p,h1,h2,h3,ul,ol,blockquote,pre,table,hr,div,img');
    if (truelyEmpty) {
      ed.innerHTML = '<p><br></p>';
      var r0 = doc.createRange();
      r0.setStart(ed.firstChild, 0);
      r0.collapse(true);
      s.removeAllRanges();
      s.addRange(r0);
    }
    var r = (s.rangeCount && ed.contains(s.anchorNode)) ? s.getRangeAt(0) : null;
    if (!r) { r = doc.createRange(); r.selectNodeContents(ed); r.collapse(false); }

    var sv = { sc: r.startContainer, so: r.startOffset, ec: r.endContainer, eo: r.endOffset };
    if (sv.sc === ed) { var a = topOf(ed, sv.so, false); if (a) { sv.sc = a; sv.so = 0; } }
    if (sv.ec === ed) { var z = topOf(ed, sv.eo, true); if (z) { sv.ec = z; sv.eo = nodeLen(z); } }

    normalizeTop();
    r = doc.createRange();
    r.setStart(sv.sc, sv.so);
    r.setEnd(sv.ec, sv.eo);

    var startB = topOf(r.startContainer, r.startOffset, false);
    var endB = topOf(r.endContainer, r.endOffset, true);
    if (!startB) return;
    if (!endB) endB = startB;
    if (!r.collapsed && endB !== startB) {
      var chk = doc.createRange();
      chk.selectNodeContents(endB);
      chk.setEnd(r.endContainer, r.endOffset);
      if (chk.toString() === '' && endB.previousElementSibling) endB = endB.previousElementSibling;
    }
    var blocks = [];
    for (var n = startB; n; n = n.nextSibling) {
      if (n.nodeType === 1) blocks.push(n);
      if (n === endB) break;
    }
    var fallback = blocks[0];

    if (blocks.every(function (b) { return b.nodeName === 'BLOCKQUOTE'; })) {
      /* remove: lift everything out of the quote(s) */
      var keep = blocks[0].firstElementChild;
      blocks.forEach(unwrapQuote);
      normalizeTop();
      if (!ed.firstChild) ed.appendChild(emptyP());
      fallback = keep && ed.contains(keep) ? keep : ed.firstElementChild;
    } else {
      /* add: wrap the touched blocks into one quote */
      var bq = doc.createElement('blockquote');
      ed.insertBefore(bq, blocks[0]);
      blocks.forEach(function (b) {
        if (b.nodeName === 'BLOCKQUOTE') {
          while (b.firstChild) {
            var c = b.firstChild;
            if (isCite(c)) { var p = citeToP(c); b.replaceChild(p, c); c = p; }
            bq.appendChild(c);
          }
          b.remove();
        } else bq.appendChild(b);
      });
      fallback = bq.firstElementChild || bq;
    }
    reselect(sv, fallback);
    push(true);
    updateState();
  }
  quoteBtn.addEventListener('click', toggleQuote);

  /* move one block out of its quote (splitting the quote if needed) */
  function liftOut(blk) {
    flush();
    var bq = blk.parentNode, par = bq.parentNode;
    var cite = null, beforeN = [], afterN = [], n;
    for (n = bq.firstChild; n && n !== blk; n = n.nextSibling) { if (isCite(n)) cite = n; else beforeN.push(n); }
    for (n = blk.nextSibling; n; n = n.nextSibling) { if (isCite(n)) cite = n; else afterN.push(n); }
    par.insertBefore(blk, bq.nextSibling);
    if (afterN.length) {
      var bq2 = doc.createElement('blockquote');
      afterN.forEach(function (x) { bq2.appendChild(x); });
      if (cite) bq2.appendChild(cite);
      par.insertBefore(bq2, blk.nextSibling);
    } else if (cite && !beforeN.length) {
      par.insertBefore(citeToP(cite), blk.nextSibling);
      cite.remove();
    }
    if (!beforeN.length) bq.remove();
    var r = doc.createRange();
    placeStart(r, blk);
    var s = doc.getSelection();
    s.removeAllRanges();
    s.addRange(r);
    savedRange = r.cloneRange();
    push(true);
    updateState();
  }
  function quoteChild(node) {
    var n = node;
    while (n && n !== ed) {
      if (n.parentNode && n.parentNode.nodeName === 'BLOCKQUOTE' && ed.contains(n.parentNode)) {
        return (n.nodeType === 1 && !isCite(n)) ? n : null;
      }
      n = n.parentNode;
    }
    return null;
  }

  /* quote author */
  authorBtn.addEventListener('click', function () {
    var bq = quoteAt();
    if (!bq || !toggleDock('quote')) return;
    var c = bq.querySelector(':scope > cite');
    $('#quoteSrc').value = c ? c.textContent.replace(/^\s*—\s*/, '') : '';
    $('#quoteSrc').focus();
  });
  function applyAuthor() {
    flush();
    var bq = quoteAt();
    if (!bq) { hideDocks(); return; }
    var t = $('#quoteSrc').value.trim();
    var c = bq.querySelector(':scope > cite');
    if (!t) { if (c) c.remove(); }
    else {
      if (!c) { c = doc.createElement('cite'); bq.appendChild(c); }
      c.textContent = '— ' + t;
    }
    hideDocks();
    push(true);
    ed.focus();
  }
  $('#quoteApply').addEventListener('click', applyAuthor);
  $('#quoteSrc').addEventListener('keydown', function (e) {
    if (e.key === 'Enter') { e.preventDefault(); applyAuthor(); }
    if (e.key === 'Escape') { e.preventDefault(); hideDocks(); ed.focus(); }
  });

  /* ---------- keyboard in the editor ---------- */
  ed.addEventListener('keydown', function (e) {
    var mod = e.ctrlKey || e.metaKey;
    if (mod && !e.altKey) {
      var k = e.key.toLowerCase();
      if (k === 'z') { e.preventDefault(); if (e.shiftKey) redo(); else undo(); return; }
      if (k === 'y') { e.preventDefault(); redo(); return; }
    }
    var s = doc.getSelection();
    if (!s.rangeCount) return;

    if (e.key === 'Tab') {
      var cell = cellFromNode(s.anchorNode);
      if (!cell) return;
      var table = cell.closest('table');
      var cells = slice.call(table.querySelectorAll('td,th'));
      var i = cells.indexOf(cell) + (e.shiftKey ? -1 : 1);
      if (i < 0) return;
      e.preventDefault();
      if (i >= cells.length) {
        addRowAfter(cell.parentElement);
        cells = slice.call(table.querySelectorAll('td,th'));
        push(true);
      }
      var r = doc.createRange();
      r.selectNodeContents(cells[i]);
      r.collapse(true);
      s.removeAllRanges();
      s.addRange(r);
      return;
    }

    /* Enter on an empty line and Backspace at the start of a quote lift the line out of it */
    if ((e.key === 'Enter' && !e.shiftKey && !mod) || e.key === 'Backspace') {
      if (!s.isCollapsed) return;
      var blk = quoteChild(s.anchorNode);
      if (!blk) return;
      if (e.key === 'Enter') {
        if (!/^(P|DIV)$/.test(blk.nodeName) || blk.textContent.trim() || blk.querySelector('img')) return;
        e.preventDefault();
        liftOut(blk);
      } else {
        if (!/^(P|DIV|H[1-6])$/.test(blk.nodeName) || blk.parentNode.firstElementChild !== blk) return;
        var chk = doc.createRange();
        chk.selectNodeContents(blk);
        chk.setEnd(s.anchorNode, s.anchorOffset);
        if (chk.toString() !== '') return;
        e.preventDefault();
        liftOut(blk);
      }
    }
  });
  ed.addEventListener('beforeinput', function (e) {
    if (e.inputType === 'historyUndo') { e.preventDefault(); undo(); }
    else if (e.inputType === 'historyRedo') { e.preventDefault(); redo(); }
  });
  ed.addEventListener('input', function () { push(); });
  ed.addEventListener('paste', function (e) {
    var cd = e.clipboardData;
    if (!cd) return;
    var html = cd.getData('text/html');
    if (html) {
      e.preventDefault();
      doc.execCommand('insertHTML', false, sanitize(html));
      push(true);
    }
  });
  var srcTimer;
  src.addEventListener('input', function () {
    clearTimeout(srcTimer);
    srcTimer = setTimeout(function () {
      if (mode === 'split') {
        ed.innerHTML = sanitize(src.value);
        savedRange = null;
        clearTimeout(hTimer);
        hTimer = setTimeout(record, 400);
      }
      sync();
    }, 250);
  });

  /* ---------- modes ---------- */
  function setMode(m) {
    if (m === mode) return;
    clearTimeout(srcTimer);
    if (mode !== 'visual') {
      ed.innerHTML = sanitize(src.value);
      src.value = ed.innerHTML;
      savedRange = null;
      record();
    }
    mode = m;
    panel.className = 'panel mode-' + m;
    $('#paneVisual').hidden = (m === 'html');
    $('#paneHtml').hidden = (m === 'visual');
    doc.querySelectorAll('.modes button').forEach(function (b) {
      b.setAttribute('aria-pressed', b.getAttribute('data-mode') === m ? 'true' : 'false');
    });
    var off = (m === 'html');
    doc.querySelectorAll('#toolbar button, #toolbar select').forEach(function (c) { c.disabled = off; });
    if (off) hideDocks();
    refreshToolbar();
    sync();
    if (!frame.hidden) (m === 'visual' ? ed : src).focus();
  }
  doc.querySelectorAll('.modes button').forEach(function (b) {
    b.addEventListener('click', function () { setMode(b.getAttribute('data-mode')); });
  });

  /* ---------- resizing ---------- */
  var grip = $('#grip'), MIN_W = 320, MIN_H = 320, MAX_H = 2400;
  function clampSize(w, h) {
    var maxW = panel.parentElement.clientWidth;
    return [Math.max(Math.min(MIN_W, maxW), Math.min(w, maxW)), Math.max(MIN_H, Math.min(h, MAX_H))];
  }
  function setSize(w, h) {
    var s = clampSize(w, h);
    panel.style.width = s[0] + 'px';
    panel.style.height = s[1] + 'px';
  }
  grip.addEventListener('pointerdown', function (e) {
    e.preventDefault();
    try { grip.setPointerCapture(e.pointerId); } catch (err) {}
    var sx = e.clientX, sy = e.clientY, sw = panel.offsetWidth, sh = panel.offsetHeight;
    function move(ev) { setSize(sw + ev.clientX - sx, sh + ev.clientY - sy); }
    function up() {
      grip.removeEventListener('pointermove', move);
      grip.removeEventListener('pointerup', up);
      grip.removeEventListener('pointercancel', up);
    }
    grip.addEventListener('pointermove', move);
    grip.addEventListener('pointerup', up);
    grip.addEventListener('pointercancel', up);
  });
  grip.addEventListener('keydown', function (e) {
    var d = e.shiftKey ? 80 : 24, w = panel.offsetWidth, h = panel.offsetHeight;
    if (e.key === 'ArrowRight') setSize(w + d, h);
    else if (e.key === 'ArrowLeft') setSize(w - d, h);
    else if (e.key === 'ArrowDown') setSize(w, h + d);
    else if (e.key === 'ArrowUp') setSize(w, h - d);
    else return;
    e.preventDefault();
  });
  grip.addEventListener('dblclick', function () {
    panel.style.width = '';
    panel.style.height = '';
  });

  const imageTypes = { "image/png": "png", "image/jpeg": "jpg", "image/webp": "webp" };
  const groups = Array.from(form.querySelectorAll("[data-upload-group]"), group => {
    const input = group.querySelector("input[type=file]");
    const attach = group.querySelector("[data-attach]");
    const list = group.querySelector("[data-attachment-list]");
    const status = group.querySelector("[data-file-status]");
    const help = status.textContent;
    const shapes = new Map();
    const metadata = document.createElement("input");
    metadata.type = "hidden";
    metadata.name = input.name + "_marks";
    group.append(metadata);
    let files = [];
    let previews = [];

    function render() {
      const transfer = new DataTransfer();
      files.forEach(file => transfer.items.add(file));
      input.files = transfer.files;
      metadata.value = files.some(file => shapes.get(file)?.length)
        ? JSON.stringify(files.map(file => shapes.get(file) || [])) : "";
      previews.forEach(url => URL.revokeObjectURL(url));
      previews = [];
      list.hidden = !files.length;
      list.replaceChildren(...files.map((file, index) => {
        const row = document.createElement("li");
        if (Object.hasOwn(imageTypes, file.type)) {
          const image = document.createElement("img");
          image.alt = "";
          image.src = URL.createObjectURL(file);
          previews.push(image.src);
          const marks = shapes.get(file) || [];
          if (marks.length) {
            const marked = document.createElement("marked-image");
            marked.dataset.marks = JSON.stringify(marks);
            marked.append(image);
            row.append(marked);
          } else {
            row.append(image);
          }
          if (input.multiple) {
            const edit = document.createElement("button");
            edit.type = "button";
            edit.className = "btn btn-outline-secondary";
            edit.textContent = "Отметки";
            edit.setAttribute("aria-label", "Отметки на " + file.name);
            edit.onclick = async () => {
              try {
                const {editMarks} = await import("./screenshot-marks.js");
                const edited = await editMarks(file, marks);
                if (!edited || !files.includes(file)) return;
                shapes.set(file, edited);
                render();
                list.children[files.indexOf(file)]?.querySelector("button")?.focus();
              } catch {
                status.textContent = "Не удалось открыть разметку скриншота.";
              }
            };
            row.append(edit);
          }
        }
        const name = document.createElement("span");
        name.textContent = file.name;
        const remove = document.createElement("button");
        remove.type = "button";
        remove.className = "btn btn-outline-secondary";
        remove.textContent = "×";
        remove.setAttribute("aria-label", "Убрать " + file.name);
        remove.onclick = () => {
          files.splice(index, 1);
          shapes.delete(file);
          render();
          status.textContent = help;
          (list.children[Math.min(index, files.length - 1)]?.querySelector("button") || attach).focus();
        };
        row.append(name, remove);
        return row;
      }));
    }
    function add(incoming) {
      const chosen = input.multiple ? [...files, ...incoming] : [...incoming];
      const all = groups.filter(group => group.input !== input && !group.input.disabled)
        .flatMap(group => Array.from(group.input.files)).concat(chosen);
      if (all.length > 5 || all.reduce((sum, file) => sum + file.size, 0) > 50_000_000) {
        status.textContent = "Можно добавить до 5 вложений общим размером до 50 МБ.";
      } else {
        files = chosen;
        status.textContent = help;
      }
      render();
    }
    input.addEventListener("change", () => add(input.files));
    attach.onclick = () => input.click();
    input.hidden = true;
    attach.hidden = false;
    return { input, add, reset() { files = []; shapes.clear(); render(); status.textContent = help; } };
  });
  const images = groups.find(group => group.input.multiple);
  function pasteImages(event) {
    const pasted = Array.from(event.clipboardData?.files || []).filter(file => Object.hasOwn(imageTypes, file.type));
    if (!pasted.length) return;
    event.preventDefault();
    event.stopPropagation();
    images.add(pasted.map(file => /\.(png|jpe?g|webp)$/i.test(file.name) ? file :
      new File([file], "Скриншот." + imageTypes[file.type], {type: file.type})));
  }
  doc.addEventListener("paste", pasteImages, true);
  form.addEventListener("paste", pasteImages);

  ed.addEventListener("drop", event => event.preventDefault());
  form.addEventListener("submit", event => {
    sync();
    const hasImage = Array.from(images.input.files).some(file => /\.(png|jpe?g|webp)$/i.test(file.name));
    const error = taskMessage && !value.value.trim() && !hasImage ? "Напишите сообщение или прикрепите скриншот." :
      value.maxLength >= 0 && value.value.length > value.maxLength ? "Сообщение слишком длинное." : "";
    editor.parentElement.querySelector("[data-editor-error]").textContent = error;
    if (error) {
      event.preventDefault();
      event.stopImmediatePropagation();
      (mode === "html" ? src : ed).focus();
    }
  }, true);
  form.addEventListener("reset", () => {
    clearTimeout(srcTimer);
    clearTimeout(hTimer);
    hTimer = null;
    src.value = "";
    ed.replaceChildren();
    groups.forEach(group => group.reset());
    savedRange = null;
    hist = [];
    hIdx = -1;
    setMode("visual");
    record();
    refreshToolbar();
  });
  value.required = false;
  value.hidden = true;
  frame.hidden = false;
  const resize = () => { frame.style.height = Math.ceil(doc.body.getBoundingClientRect().height) + "px"; };
  new ResizeObserver(resize).observe(doc.body);
  resize();
  src.value = ed.innerHTML;
  record();
  refreshToolbar();
}
