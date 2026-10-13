/* Source evidence: exact text only, safe DOM, no inferred summary/quote pairing. */
(function () {
  'use strict';
  const normalise = value => String(value || '').replace(/\s+/g, ' ').trim();
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
    if (quote.length < 8 || quote.length > 2000) return false;
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
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
  function init(article) {
    const root = document.getElementById('content');
    const summary = document.getElementById('summaryBox');
    if (!root || !summary) return;
    document.getElementById('source-evidence')?.remove();
    summary.querySelectorAll('.summary-source-link,.summary-source-status').forEach(node => node.remove());
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
      style.textContent = '.source-evidence{margin:1rem auto;padding:1rem;width:100%;min-width:0;box-sizing:border-box;overflow-wrap:anywhere;border:1px solid #69778a;border-radius:.5rem}.source-evidence dl{display:grid;grid-template-columns:fit-content(6em) minmax(0,1fr);gap:.3rem 1rem}.source-evidence dt,.source-evidence dd{min-width:0;overflow-wrap:anywhere}.source-evidence dd{margin:0}.summary-source-link,.summary-source-status{margin-inline-start:.6rem;font-size:.85em}.source-evidence-match{background:#ffe08a;color:#17202a;scroll-margin-top:6rem}.source-evidence-match:focus{outline:3px solid #426acb}.summary-source-status{opacity:.75}';
      document.head.append(style);
    }
    const evidence = Array.isArray(article.summary_evidence) ? article.summary_evidence : [];
    for (const item of summary.querySelectorAll('li:not(.summary-pending)')) {
      const point = normalise(item.textContent);
      const support = fallback !== 'minimal' && evidence.find(entry => entry && entry.verified === true && normalise(entry.point) === point && (!entry.article_id || entry.article_id === article.id)
        && normalise(entry.quote).length >= 8 && normalise(entry.quote).length <= 2000 && normalise(root.textContent).includes(normalise(entry.quote)));
      if (!support) { item.append(element('span', '原文依據未提供', 'summary-source-status')); continue; }
      const link = element('a', '查看原文依據', 'summary-source-link');
      link.href = '#source-evidence';
      link.addEventListener('click', event => {
        event.preventDefault();
        const found = highlightQuote(root, support.quote);
        live.textContent = found ? '已標示這點摘要所引用的原文。' : '目前正文未能找到這段引用，依據不可用。';
      });
      item.append(link);
    }
    const requested = new URLSearchParams(window.location.search).get('quote');
    if (requested) {
      const found = fallback !== 'minimal' && requested.length <= 2000 && highlightQuote(root, requested);
      live.textContent = found ? '已標示跨媒體比較所引用的原文。' : '目前正文未能找到這段引用，依據不可用。';
    }
  }
  window.RssArticleEvidence = { init, highlightQuote };
}());
