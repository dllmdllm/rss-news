/* Source-backed topic reading. Topic clusters are associations, never event identity. */
(function (root) {
  'use strict';
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const href = (id, quote = '') => `article.html?id=${encodeURIComponent(id)}${quote ? '&quote=' + encodeURIComponent(String(quote).slice(0, 500)) : ''}`;
  const outlet = name => /^(星島頭條|星島 即時中國|星島 即時國際)$/.test(String(name || '').trim()) ? '星島' : String(name || '').trim().replace(/^(RTHK|明報|HK01|東網|HuffPost|Now) .*/, '$1');
  const normalise = value => String(value || '').replace(/\s+/g, ' ').trim();
  function claims(items, articles, shared) {
    const byId = new Map(articles.map(a => [a.id, a]));
    return (Array.isArray(items) ? items : []).filter(item => {
      if (!item || !item.text || !Array.isArray(item.evidence) || !item.evidence.length) return false;
      if (!item.evidence.every(e => e && byId.has(e.article_id) && e.verified === true &&
        typeof e.content_hash === 'string' && /^[a-f0-9]{64}$/.test(e.content_hash) &&
        e.content_hash === byId.get(e.article_id).content_hash && typeof e.quote === 'string' && normalise(e.quote))) return false;
      const text = normalise(item.text);
      if (!item.evidence.some(e => text === normalise(e.quote)) || !item.evidence.every(e => normalise(e.quote).includes(text))) return false;
      return !shared || new Set(item.evidence.map(e => outlet(byId.get(e.article_id).source))).size >= 2;
    }).map(item => `<li>${esc(item.text)} <span>${item.evidence.map(e => `<a href="${href(e.article_id)}">${esc(byId.get(e.article_id).source || '來源')}</a>`).join('、')}</span></li>`).join('');
  }
  function healthObservation(info) {
    const value = info.checked_at || info.observed_at;
    const time = value && !Number.isNaN(Date.parse(value)) ? new Date(value).toLocaleString('zh-HK') : '未提供';
    const reason = String(info.error || '').replace(/\s+/g, ' ').trim().slice(0, 140);
    return `<span class="reading-provenance">檢查時間 ${esc(time)}${reason ? ` · 原因：${esc(reason)}` : ''}</span>`;
  }
  function provenance(article) {
    const fallback = article.content_quality?.fallback || article.evidence_status?.fallback;
    const partial = (fallback && !['none', 'unknown'].includes(fallback)) || article.evidence_status === 'partial'
      || ['partial', 'pending-budget'].includes(article.body_status);
    const unknown = fallback === 'unknown' || article.evidence_status === 'unknown'
      || !article.content_hash || article.content_quality?.origin === 'wordpress-public-posts';
    const failed = article.scrape_error || article.content_retention?.latest_error || article.body_status === 'failed';
    const warning = failed ? `最新正文抓取失敗${partial ? '，正文未完整' : ''}；摘要可能未反映更新。`
      : partial ? '正文未完整，摘要可能有遺漏。'
      : article.content_retention ? '使用已保存正文，最新更新未核實。'
      : unknown ? '正文完整性未核實，摘要可能有遺漏。' : '';
    return warning ? `<span class="reading-warning">⚠ ${warning}</span>` : '';
  }
  function render(topics, articles, panels) {
    const byId = new Map(articles.map(a => [a.id, a]));
    return (topics || []).slice(0, 6).map(topic => {
      const members = [...new Set(topic.article_ids || [])].map(id => byId.get(id)).filter(Boolean);
      if (members.length < 2) return '';
      const memberIds = new Set(members.map(a => a.id));
      const canonical = members.filter(a => !a.duplicate_of || !memberIds.has(a.duplicate_of));
      const digest = panels?.[topic.cluster_id || members.find(a => a.cluster_id)?.cluster_id || topic.topic]?.digest || {};
      const shared = claims(digest.shared_facts, members, true);
      const developments = claims(digest.new_developments, members, false);
      const attributed = claims(digest.differing_accounts, members, false);
      // Legacy contradiction rows lack current-body hash attestations.
      // Source-attributed differing_accounts above uses the verified schema.
      const section = (title, html) => `<section><h4>${title}</h4>${html ? `<ul>${html}</ul>` : '<p class="event-unavailable">尚無可核對的來源分析</p>'}</section>`;
      return `<details class="event-reading"><summary>${esc(topic.topic || '相關報道')} · ${members.length} 篇</summary><p>話題關聯（暫定），不代表同一事件；僅隱藏有明確 duplicate_of 指向的重複卡，未作模糊合併。以下整理只按已核對引文，請對照各來源；發布時間較新本身不證明事件有新變化。</p>${section('多來源共同事實', shared)}${section('各來源不同說法', attributed)}${section('報道更新／來源新增資訊', developments)}<h4>來源報道 · ${canonical.length} 篇獨立卡／${members.length} 篇原始記錄</h4><ul>${canonical.map(a => `<li><a href="${href(a.id)}">${esc(a.source)}：${esc(a.title)}</a>${provenance(a)}</li>`).join('')}</ul>${members.length !== canonical.length ? `<details><summary>查看已標示重複的 ${members.length - canonical.length} 篇</summary><ul>${members.filter(a => !canonical.includes(a)).map(a => `<li><a href="${href(a.id)}">${esc(a.source)}：${esc(a.title)}</a> → <a href="${href(a.duplicate_of)}">保留報道</a></li>`).join('')}</ul></details>` : ''}</details>`;
    }).join('') || '<p class="event-unavailable">目前沒有多篇相關報道；單篇報道仍在新聞流。</p>';
  }
  root.RssEventReading = {render, claims, provenance, healthObservation, outlet};
})(typeof window === 'undefined' ? globalThis : window);
