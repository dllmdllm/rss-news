/* Device-local reader state. No telemetry, account, network or automatic speech. */
(function () {
  const KEY = 'rss_reader_state_v1';
  const MAX_ARTICLES = 500;
  const memoryPreferences = new Map();
  let records = Object.create(null), persistent = true;
  const aliases = new Map(), aliasGroups = new Map();
  function registerAliases(articles) {
    aliases.clear(); aliasGroups.clear();
    for (const article of articles || []) {
      const ids = [article.id, ...(article.id_aliases || [])].filter(id => typeof id === 'string' && id);
      aliasGroups.set(article.id, ids);
      ids.forEach(id => aliases.set(id, article.id));
    }
  }
  try {
    const value = JSON.parse(localStorage.getItem(KEY) || '{}');
    if (value && typeof value === 'object' && !Array.isArray(value)) {
      for (const [id, row] of Object.entries(value).slice(-MAX_ARTICLES)) {
        if (row && typeof row === 'object') records[id] = clean(row);
      }
    }
  } catch (_) { persistent = false; }
  function clean(row) {
    return {
      read: row.read === true, saved: row.saved === true,
      position: Math.max(0, Math.min(1, Number(row.position) || 0)),
      updated: Number(row.updated) || 0,
      tts: { mode: row.tts?.mode === 'summary' ? 'summary' : 'full',
        index: Math.max(0, Math.min(10000, Math.floor(Number(row.tts?.index) || 0))) },
    };
  }
  function articleState(id) {
    const key = aliases.get(String(id)) || String(id);
    const rows = (aliasGroups.get(key) || [key]).map(alias => records[alias]).filter(Boolean);
    const latest = [...rows].sort((a, b) => b.updated - a.updated)[0] || {};
    return clean({...latest, read: rows.some(row => row.read), saved: rows.some(row => row.saved)});
  }
  function set(id, patch) {
    if (!id || typeof patch !== 'object' || !patch) return articleState(id);
    const key = aliases.get(String(id)) || String(id);
    const previous = articleState(key);
    records[key] = clean({ ...previous, ...patch, updated: Date.now() });
    // Explicit state changes apply to aliases of this same publisher article;
    // no unrelated duplicate-source membership is marked read or unsaved.
    for (const alias of aliasGroups.get(key) || []) {
      if (records[alias]) records[alias] = records[key];
    }
    const entries = Object.entries(records).sort((a, b) => a[1].updated - b[1].updated).slice(-MAX_ARTICLES);
    records = Object.assign(Object.create(null), Object.fromEntries(entries));
    try { localStorage.setItem(KEY, JSON.stringify(records)); } catch (_) { persistent = false; }
    const current = articleState(key);
    window.dispatchEvent(new CustomEvent('rss-reader-state', { detail: {id: key, state: current,
      statusChanged: previous.read !== current.read || previous.saved !== current.saved} }));
    return current;
  }
  function saveLater(id, value = true) { return set(id, {saved: value}); }
  function markRead(id, value = true) { return set(id, {read: value}); }
  function filter(articles, status = 'all') {
    return (articles || []).filter(article => {
      const row = articleState(article.id);
      return status === 'unread' ? !row.read : status === 'read' ? row.read : status === 'saved' ? row.saved : true;
    });
  }
  function preference(key, fallback = null) {
    if (memoryPreferences.has(key)) return memoryPreferences.get(key);
    let value = fallback;
    try { value = localStorage.getItem(key) ?? fallback; } catch (_) {}
    memoryPreferences.set(key, value);
    return value;
  }
  function setPreference(key, value) {
    memoryPreferences.set(key, String(value));
    try { localStorage.setItem(key, String(value)); } catch (_) { persistent = false; }
  }
  function refresh() {
    // Back/forward cache and other tabs can otherwise display stale saved/read tags.
    if (!persistent) return;
    try {
      const value = JSON.parse(localStorage.getItem(KEY) || '{}');
      if (!value || typeof value !== 'object' || Array.isArray(value)) return;
      const next = Object.create(null);
      for (const [id, row] of Object.entries(value).slice(-MAX_ARTICLES)) {
        if (row && typeof row === 'object') next[id] = clean(row);
      }
      if (JSON.stringify(next) === JSON.stringify(records)) return;
      records = next;
      window.dispatchEvent(new CustomEvent('rss-reader-state', {detail: {statusChanged: true}}));
    } catch (_) {}
  }
  window.addEventListener?.('pageshow', refresh);
  window.addEventListener?.('storage', event => { if (event.key === KEY || event.key === null) refresh(); });
  window.RssReaderState = { registerAliases, refresh, isPersistent: () => persistent, articleState, get: articleState, set, saveLater, markRead, filter, preference, setPreference };
}());
