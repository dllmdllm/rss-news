/* Existing source quotations only. No fetch, generation or fuzzy alignment. */
(function () {
  'use strict';
  const HASH = /^[a-f0-9]{64}$/;
  const ID = /^[A-Za-z0-9_-]{1,128}$/;
  const BLOCKS = 'p,li,blockquote,h1,h2,h3,h4,h5,h6,td,th,figcaption,pre,div';
  const normalise = value => String(value || '').replace(/\s+/g, ' ').trim();
  const quoteText = value => typeof value === 'string' && normalise(value).length >= 8
    && normalise(value).length <= 2000 ? normalise(value) : null;
  function scope(article) {
    const fallback = article?.content_quality?.fallback;
    if (!HASH.test(article?.content_hash || '') || fallback === 'minimal') {
      return {status: 'unavailable', label: '原文段落不可用'};
    }
    const partial = article.evidence_status === 'partial' || article.content_retention
      || (fallback && fallback !== 'none') || article.content_quality?.origin === 'wordpress-public-posts';
    return {status: partial ? 'partial' : 'referenced',
      label: partial ? '僅可核對已取得的正文，完整性未核實' : '原文段落會在載入後核對'};
  }
  function summarySupport(article, point) {
    if (!normalise(point) || scope(article).status === 'unavailable' || !ID.test(article?.id || '')) return null;
    return (Array.isArray(article.summary_evidence) ? article.summary_evidence : []).find(entry => entry
      && entry.verified === true && entry.article_id === article.id
      && normalise(entry.point) === normalise(point) && quoteText(entry.quote)
      && (!entry.content_hash || entry.content_hash === article.content_hash)) || null;
  }
  function keySupport(article, quote) {
    const text = quoteText(quote);
    return scope(article).status !== 'unavailable' && text
      && article.key_sentences_content_hash === article.content_hash
      && (Array.isArray(article.key_sentences) ? article.key_sentences : []).some(value => normalise(value) === text) ? text : null;
  }
  function href(article, quote, kind = 'quote') {
    const text = quoteText(quote);
    if (!text || !ID.test(article?.id || '') || scope(article).status === 'unavailable') return null;
    const params = new URLSearchParams({id: article.id, quote: text, quote_hash: article.content_hash,
      quote_kind: ['summary', 'key', 'quote'].includes(kind) ? kind : 'quote'});
    return 'article.html?' + params.toString();
  }
  function paragraphs(root) {
    // A parent and its descendant are one text occurrence, never two. Direct
    // mixed text around nested blocks stays unavailable rather than inventing
    // a paragraph boundary. Inline markup remains within its original block.
    return [...root.querySelectorAll(BLOCKS)].filter(node => !node.querySelector(BLOCKS)
      && !node.closest('script,style,noscript,template') && normalise(node.textContent));
  }
  function resolveParagraph(root, quote) {
    const text = quoteText(quote);
    if (!root || !text) return {status: 'unavailable'};
    const found = [];
    paragraphs(root).forEach((node, index) => {
      const body = normalise(node.textContent);
      let at = body.indexOf(text);
      while (at >= 0) {
        found.push({node, anchor: 'source-p' + (index + 1), quote: text, offset: at});
        if (found.length > 1) break;
        at = body.indexOf(text, at + 1);
      }
    });
    return found.length === 1 ? {status: 'available', ...found[0]}
      : {status: found.length > 1 ? 'ambiguous' : 'unavailable'};
  }
  const verified = new WeakMap();
  async function verifyBody(article) {
    if (!article || typeof article.content !== 'string' || scope(article).status === 'unavailable') return false;
    const known = verified.get(article);
    if (known?.content === article.content && known.hash === article.content_hash) return known.valid;
    let valid = false;
    try {
      const bytes = new TextEncoder().encode(article.content);
      const hash = [...new Uint8Array(await crypto.subtle.digest('SHA-256', bytes))]
        .map(byte => byte.toString(16).padStart(2, '0')).join('');
      valid = hash === article.content_hash;
    } catch (_) { /* Missing verification is unavailable, never assumed. */ }
    verified.set(article, {content: article.content, hash: article.content_hash, valid});
    return valid;
  }
  window.RssSourceEvidence = {normalise, scope, summarySupport, keySupport, href, resolveParagraph, verifyBody};
}());
