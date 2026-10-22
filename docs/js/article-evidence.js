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
  function knownTime(value) {
    if (typeof value !== 'string' || !/(?:Z|[+-]\d\d:\d\d)$/.test(value)) return '時間未提供';
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? '時間未提供' : date.toLocaleString('zh-HK', { timeZone: 'Asia/Hong_Kong', hour12: false }) + ' HKT';
  }
  async function init(article) {
    const token = ++generation;
    const root = document.getElementById('content');
    const summary = document.getElementById('summaryBox');
    if (!root || !summary) return;
    document.getElementById('source-evidence')?.remove();
    summary.querySelectorAll('.summary-source-link,.summary-source-status').forEach(node => node.remove());
    document.getElementById('facts')?.querySelectorAll('.key-source-link,.key-source-status').forEach(node => node.remove());
    clearMarks(root);
    const section = element('section', undefined, 'source-evidence');
    section.id = 'source-evidence';
    section.setAttribute('aria-label', '文章來源與證據');
    section.append(element('h2', '來源與時間'));
    const times = element('dl');
    for (const [label, value] of [['刊登', article.published_at || article.date], ['最近抓取', article.fetched_at], ['內容變更', article.content_updated_at]]) {
      times.append(element('dt', label), element('dd', knownTime(value)));
    }
    section.append(times);
    const fallback = article.content_quality?.fallback;
    const partial = article.evidence_status === 'partial' || (fallback && fallback !== 'none');
    const status = element('p', partial ? '目前正文可能只包含 RSS 摘要或保留內容，證據範圍有限。' : '引用只核對目前顯示的正文；AI 摘要仍需讀者判斷。');
    if (article.content_quality?.origin === 'wordpress-public-posts') status.textContent = '正文來自來源公開 WordPress API；完整性未獨立核實。引用只核對目前顯示的正文。';
    if (article.content_retention) status.textContent = '目前顯示之前保留的正文；最近抓取未能取得完整更新。';
    section.append(status);
    if (article.scrape_error || article.content_retention?.latest_error) section.append(element('p', '最近抓取狀況：' + String(article.scrape_error || article.content_retention.latest_error).slice(0, 500)));
    const uncertain = (article.upcoming_events || []).filter(event => event && (event.precision === 'uncertain' || !event.date)).slice(0, 3);
    if (uncertain.length) section.append(element('p', '事件日期可能未確定；請以來源原文日期字眼為準。'));
    const live = element('p', '', 'source-evidence-status');
    live.setAttribute('role', 'status');
    section.append(live);
    summary.after(section);
    if (!document.getElementById('source-evidence-styles')) {
      const style = element('style'); style.id = 'source-evidence-styles';
      style.textContent = '.source-evidence{margin:1rem auto;padding:1rem;width:100%;min-width:0;box-sizing:border-box;overflow-wrap:anywhere;border:1px solid #69778a;border-radius:.5rem}.source-evidence dl{display:grid;grid-template-columns:fit-content(6em) minmax(0,1fr);gap:.3rem 1rem}.source-evidence dt,.source-evidence dd{min-width:0;overflow-wrap:anywhere}.source-evidence dd{margin:0}.summary-source-link,.summary-source-status,.key-source-link,.key-source-status{margin-inline-start:.6rem;font-size:.85em}.source-evidence-match{background:#ffe08a;color:#17202a;scroll-margin-top:6rem}.source-evidence-match:focus{outline:3px solid #426acb}.summary-source-status,.key-source-status{opacity:.75}';
      document.head.append(style);
    }
    const source = window.RssSourceEvidence;
    const validBody = source && await source.verifyBody(article);
    if (token !== generation || !section.isConnected) return;
    if (!validBody) status.textContent = '正文未能通過版本及 hash 核對，原文段落依據不可用。AI 摘要不是原文引句。';
    // Check the exact hashed source body too, not only whatever DOM happens to
    // be on screen. A template is inert: parsing quotations never loads media.
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
    const unavailable = reason => reason === 'ambiguous' ? '原文有多個相同段落，未能唯一定位' : '原文依據未提供';
    function addLink(item, quote, kind, className) {
      const resolved = resolve(quote);
      if (!resolved || resolved.status !== 'available') {
        item.append(element('span', unavailable(resolved?.status), className + '-status'));
        return;
      }
      const link = element('a', '查看原文段落', className + '-link');
      link.href = '#' + resolved.anchor;
      resolved.node.id = resolved.anchor;
      link.addEventListener('click', event => {
        event.preventDefault();
        const found = highlightQuote(root, quote);
        live.textContent = found ? (kind === 'summary' ? '已標示這點 AI 摘要所引用的原文段落。' : '已標示這句原文關鍵句所在的段落。')
          : '目前正文未能唯一找到這段引用，依據不可用。';
      });
      item.append(link);
    }
    for (const item of summary.querySelectorAll('li:not(.summary-pending)')) {
      const point = normalise(item.textContent);
      const support = validBody && source.summarySupport(article, point);
      if (!support) { item.append(element('span', '原文依據未提供', 'summary-source-status')); continue; }
      addLink(item, support.quote, 'summary', 'summary-source');
    }
    for (const item of document.querySelectorAll('#facts > li')) {
      const quote = validBody && source.keySupport(article, normalise(item.textContent));
      if (!quote) { item.append(element('span', '原文段落不可用', 'key-source-status')); continue; }
      addLink(item, quote, 'key', 'key-source');
    }
    const params = new URLSearchParams(window.location.search);
    const requested = params.get('quote');
    if (requested) {
      const expectedHash = params.get('quote_hash');
      const current = validBody && (!expectedHash || expectedHash === article.content_hash);
      const resolved = current && requested.length <= 2000 && resolve(requested);
      const found = resolved?.status === 'available' && highlightQuote(root, requested);
      const kind = params.get('quote_kind');
      live.textContent = found ? (kind === 'summary' ? '已標示 AI 摘要所引用的原文段落。'
        : kind === 'key' ? '已標示原文關鍵句所在的段落。' : '已標示跨媒體比較所引用的原文段落。')
        : resolved?.status === 'ambiguous' ? '原文有多個相同段落，未能唯一定位；依據不可用。'
        : expectedHash && expectedHash !== article.content_hash ? '正文版本已變更，這條引用的段落依據不可用。'
        : '目前正文未能找到這段引用，依據不可用。';
    }
  }
  window.RssArticleEvidence = { init, highlightQuote };
}());
