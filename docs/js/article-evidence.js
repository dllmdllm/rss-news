/* Source evidence: exact text only, safe DOM, no inferred summary/quote pairing. */
(function () {
  'use strict';
  const normalise = value => String(value || '').replace(/\s+/g, ' ').trim();
  let generation = 0;
  function element(tag, text, className) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
  }
  function clearMarks(root) {
    root.querySelectorAll('mark.source-evidence-match').forEach(mark => mark.replaceWith(...mark.childNodes));
    root.normalize();
  }
  function highlightQuote(root, quote) {
    clearMarks(root);
    quote = normalise(quote);
    const resolved = window.RssSourceEvidence?.resolveParagraph(root, quote);
    if (resolved?.status !== 'available') return false;
    const paragraph = resolved.node;
    paragraph.id = resolved.anchor;
    const walker = document.createTreeWalker(paragraph, NodeFilter.SHOW_TEXT, {
      acceptNode(node) {
        return node.parentElement.closest('script,style,noscript,template')
          ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT;
      }
    });
    const nodes = [];
    let raw = '', node;
    while ((node = walker.nextNode())) {
      nodes.push({ node, start: raw.length, end: raw.length + node.data.length });
      raw += node.data;
    }
    let text = '';
    const starts = [], ends = [];
    for (let i = 0; i < raw.length; i++) {
      if (/\s/.test(raw[i])) {
        if (!text.length) continue;
        if (text.endsWith(' ')) { ends[ends.length - 1] = i + 1; continue; }
        text += ' ';
      } else text += raw[i];
      starts.push(i); ends.push(i + 1);
    }
    const at = text.indexOf(quote);
    if (at < 0) return false;
    const left = starts[at], right = ends[at + quote.length - 1];
    let first = null;
    for (const item of nodes) {
      if (item.end <= left || item.start >= right) continue;
      const a = Math.max(left, item.start) - item.start;
      const b = Math.min(right, item.end) - item.start;
      const fragment = document.createDocumentFragment();
      fragment.append(document.createTextNode(item.node.data.slice(0, a)));
      const mark = element('mark', item.node.data.slice(a, b), 'source-evidence-match');
      mark.tabIndex = -1;
      fragment.append(mark, document.createTextNode(item.node.data.slice(b)));
      item.node.replaceWith(fragment);
      first ||= mark;
    }
    if (first) {
      try {
        const target = new URL(window.location.href);
        target.hash = resolved.anchor;
        window.history.replaceState(null, '', target);
      } catch (_) { /* The verified local paragraph remains usable. */ }
      first.scrollIntoView({ block: 'center', behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' });
      first.focus({ preventScroll: true });
    }
    return Boolean(first);
  }
  async function init(article) {
    const token = ++generation;
    const root = document.getElementById('content');
    const summary = document.getElementById('summaryBox');
    if (!root || !summary) return;
    summary.dataset.bodyCheck = 'pending';
    document.getElementById('source-evidence')?.remove();
    summary.querySelectorAll('.summary-source-link,.summary-source-status,.summary-integrity-warning').forEach(node => node.remove());
    document.getElementById('facts')?.querySelectorAll('.key-source-link,.key-source-status').forEach(node => node.remove());
    clearMarks(root);
    const source = window.RssSourceEvidence;
    const validBody = source && await source.verifyBody(article);
    if (token !== generation || !root.isConnected) return;
    const original = document.createElement('template');
    if (validBody) {
      original.innerHTML = article.content;
      original.content.querySelectorAll('script,style,noscript,template').forEach(node => node.remove());
    }
    function resolve(quote) {
      const supplied = validBody && source.resolveParagraph(original.content, quote);
      return supplied?.status === 'available' ? source.resolveParagraph(root, quote)
        : {status: supplied?.status || 'unavailable'};
    }
    for (const item of summary.querySelectorAll('li:not(.summary-pending)')) {
      const support = validBody && source.summarySupport(article, normalise(item.textContent));
      const quotes = support && (support.quotes || [support.quote]);
      if (!quotes || !quotes.every(quote => resolve(quote).status === 'available')) {
        item.append(element('span', '未核實', 'summary-source-status'));
      }
    }
    for (const item of document.querySelectorAll('#facts > li')) {
      const quote = validBody && source.keySupport(article, normalise(item.textContent));
      if (!quote || resolve(quote).status !== 'available') item.append(element('span', '未核實', 'key-source-status'));
    }
    // Existing deep links remain hash/paragraph checked; no evidence controls
    // or panels are generated. A stale link never highlights another version.
    const params = new URLSearchParams(window.location.search);
    const requested = params.get('quote');
    const expectedHash = params.get('quote_hash');
    let stale = false;
    if (requested) {
      const current = validBody && (!expectedHash || expectedHash === article.content_hash);
      const resolved = current && requested.length <= 2000 && resolve(requested);
      stale = !(resolved?.status === 'available' && highlightQuote(root, requested));
    }
    if (!validBody || stale) {
      summary.append(element('p', !validBody ? '正文版本未能核對，摘要未核實。' : '引用版本未能核對，請以目前正文及原文為準。', 'summary-integrity-warning'));
    }
    if (!document.getElementById('source-evidence-styles')) {
      const style = element('style'); style.id = 'source-evidence-styles';
      style.textContent = '.summary-source-status,.key-source-status{margin-inline-start:.4rem;font-size:.8em;border:1px solid currentColor;border-radius:.2rem;padding-inline:.2rem}.summary-integrity-warning,.summary-limits{font-size:.85rem;overflow-wrap:anywhere}.source-evidence-match{background:#ffe08a;color:#17202a;scroll-margin-top:6rem}.source-evidence-match:focus{outline:3px solid #426acb}';
      document.head.append(style);
    }
    summary.dataset.bodyCheck = validBody ? 'matched' : 'unavailable';
  }
  window.RssArticleEvidence = { init, highlightQuote };
}());
