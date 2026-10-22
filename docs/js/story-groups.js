/* Presentation groups use explicit duplicate relationships, never broad topics.
 * The caller supplies the time-eligible universe and filters before collapse.
 * Every recorded article remains available through a local reader link.
 */
(function (scope) {
  'use strict';
  const MAX_CHAIN = 64;
  const validId = value => typeof value === 'string' && value.length > 0 &&
    value.length <= 128 && !/[\u0000-\u001f\u007f]/.test(value);
  const unique = values => [...new Set(values)];
  const esc = value => String(value ?? '').replace(/[&<>"']/g,
    character => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[character]));

  function uniqueRows(rows) {
    const known = new Map();
    for (const row of Array.isArray(rows) ? rows : []) {
      if (row && typeof row === 'object' && validId(row.id) && !known.has(row.id)) known.set(row.id, row);
    }
    return [...known.values()];
  }

  function collapse(rows, allRows = rows) {
    const matched = uniqueRows(rows);
    // A stale/missing universe must not make a matching article disappear.
    const universe = uniqueRows([...uniqueRows(allRows), ...matched]);
    const known = new Map(universe.map(row => [row.id, row]));
    const roots = new Map();
    function rootId(id) {
      if (roots.has(id)) return roots.get(id);
      let current = known.get(id);
      const seen = new Set();
      for (let depth = 0; depth < MAX_CHAIN && current; depth++) {
        if (seen.has(current.id)) break;
        seen.add(current.id);
        if (current.duplicate_of === null || current.duplicate_of === undefined || current.duplicate_of === '') {
          roots.set(id, current.id);
          return current.id;
        }
        if (!validId(current.duplicate_of) || !known.has(current.duplicate_of)) break;
        current = known.get(current.duplicate_of);
      }
      // A dangling, cyclic or excessive chain proves no common root. Keep
      // each affected story separate rather than suppressing its content.
      roots.set(id, id);
      return id;
    }
    const allGroups = new Map();
    for (const article of universe) {
      const root = rootId(article.id);
      if (!allGroups.has(root)) allGroups.set(root, []);
      allGroups.get(root).push(article);
    }
    const matchedGroups = new Map();
    for (const article of matched) {
      const root = rootId(article.id);
      if (!matchedGroups.has(root)) matchedGroups.set(root, []);
      matchedGroups.get(root).push(article);
    }
    return [...matchedGroups].map(([root, members]) => {
      const representative = members.find(article => article.id === root) || members[0];
      const allMembers = allGroups.get(root) || members;
      const matchedIds = new Set(members.map(article => article.id));
      // Preserve the caller's selected source/category view for matching rows;
      // unmatching rows retain their original metadata and explicit label.
      const overrides = new Map(members.map(article => [article.id, article]));
      const allArticles = allMembers.map(article => overrides.get(article.id) || article);
      return {
        ...representative,
        group_id: root,
        matched_members: members.map(article => article.id),
        matched_sources: unique(members.map(article => article.source).filter(Boolean)),
        matched_count: members.length,
        all_members: allArticles.map(article => article.id),
        all_sources: unique(allArticles.map(article => article.source).filter(Boolean)),
        all_count: allArticles.length,
        matched_articles: members,
        group_articles: allArticles,
        unmatched_members: allArticles.filter(article => !matchedIds.has(article.id)).map(article => article.id),
      };
    });
  }

  function memberHtml(article, matches) {
    return `<li data-story-member="${esc(article.id)}" data-story-match="${matches ? 'true' : 'false'}">` +
      `<a href="article.html?id=${esc(encodeURIComponent(article.id))}">` +
      `<span class="story-source">${esc(article.source || '來源未提供')}</span> · ` +
      `<span class="story-title">${esc(article.title || '標題未提供')}</span></a></li>`;
  }

  function detailsHtml(group) {
    const all = uniqueRows(group?.group_articles);
    if (all.length < 2) return '';
    const matchedIds = new Set(Array.isArray(group.matched_members) ? group.matched_members : []);
    const matching = all.filter(article => matchedIds.has(article.id));
    if (!matching.length) return '';
    const other = all.filter(article => !matchedIds.has(article.id));
    const sources = unique(all.map(article => article.source).filter(Boolean));
    const count = other.length ? ` · 目前條件 ${matching.length} 篇` : '';
    const unmatched = other.length
      ? `<details class="story-group-other"><summary>其他 ${other.length} 篇（不符合目前搜尋或篩選）</summary>` +
        `<ul>${other.map(article => memberHtml(article, false)).join('')}</ul></details>`
      : '';
    return `<details class="story-group" data-story-group="${esc(group.group_id || group.id)}">` +
      `<summary>同組 ${all.length} 篇報道 · ${sources.length} 個來源${count}</summary>` +
      `<ul>${matching.map(article => memberHtml(article, true)).join('')}</ul>${unmatched}</details>`;
  }

  scope.RssStoryGroups = {collapse, detailsHtml};
})(globalThis);
