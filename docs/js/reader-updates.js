/* Device-only visit/revision state and opt-in snapshot refresh.
 *
 * create({onPending, onApply, onError, storage?, fetcher?, isVisible?, now?,
 *         selectArticles?: snapshot => eligibleRows})
 *   .begin(snapshot)       establish the rendered snapshot once; neutral first visit
 *   .label(article)        "new", "updated" or "" since the prior device visit
 *   .check() / .start()    fetch a bounded index while visible; never render it
 *   .pending()             latest candidate/counts, or null
 *   .apply()               call onApply(snapshot), then acknowledge that visit
 *   .stop()                stop checks and discard any in-flight response
 *
 * The owner renders a prompt/badges and preserves its own DOM anchor, input and
 * filters in onApply. No DOM, reader-state, speech, provider or telemetry access.
 */
(function () {
  'use strict';
  const KEY = 'rss_reader_updates_v1';
  const MAX_ROWS = 10000;
  const MAX_RECORDS = 6000;
  const MAX_STORAGE_CHARS = 512 * 1024;
  const MAX_RESPONSE_BYTES = 8 * 1024 * 1024;
  const OVERLAP_MS = 30 * 60 * 60 * 1000;
  const DISPLAY_MS = 24 * 60 * 60 * 1000;
  const SAFE_ID = /^[A-Za-z0-9_-]{1,128}$/;
  const HASH = /^[a-f0-9]{64}$/;
  const clone = value => JSON.parse(JSON.stringify(value));
  const normalTitle = value => String(value || '').normalize('NFKC').replace(/\s+/g, ' ').trim();
  const identityIds = row => [row.id, ...(row.id_aliases || [])];
  // This is a comparison of published bytes, not a replacement integrity hash.
  // New AI summaries/tags, collection clocks and slug changes are not revisions.
  const revision = row => JSON.stringify([normalTitle(row.title), row.content_hash || '']);
  const snapshotTime = value => {
    const match = /^(\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2}) HKT$/.exec(value || '');
    return match ? Date.UTC(+match[1], +match[2] - 1, +match[3], +match[4] - 8, +match[5]) : null;
  };

  function inspect(snapshot, initial = false) {
    if (!snapshot || typeof snapshot !== 'object' || Array.isArray(snapshot) ||
        (snapshot.schema_version !== 2 && !(initial && snapshot.schema_version === undefined)) ||
        typeof snapshot.updated !== 'string' || !snapshot.updated || snapshot.updated.length > 64 ||
        (!initial && snapshotTime(snapshot.updated) === null) ||
        !snapshot.sources || typeof snapshot.sources !== 'object' || Array.isArray(snapshot.sources) ||
        !Array.isArray(snapshot.trending_topics) || snapshot.trending_topics.length > MAX_ROWS ||
        !Array.isArray(snapshot.articles) || !snapshot.articles.length || snapshot.articles.length > MAX_ROWS) {
      throw Error('更新資料格式不完整；保留目前文章。');
    }
    const ids = new Set(), aliases = new Map();
    for (const row of snapshot.articles) {
      if (!row || typeof row !== 'object' || typeof row.id !== 'string' || !SAFE_ID.test(row.id) || ids.has(row.id) ||
          typeof row.title !== 'string' || !normalTitle(row.title) || row.title.length > 4096 ||
          typeof row.source !== 'string' || !row.source || row.source.length > 256 ||
          typeof row.summary !== 'string' || row.summary.length > 100000 ||
          typeof row.date !== 'string' || !Number.isFinite(Date.parse(row.date)) ||
          (!(typeof row.content_hash === 'string' && HASH.test(row.content_hash)) && !(initial && row.content_hash === undefined)) ||
          (row.id_aliases != null && (!Array.isArray(row.id_aliases) || row.id_aliases.length > 64 ||
            row.id_aliases.some(id => typeof id !== 'string' || !SAFE_ID.test(id))))) {
        throw Error('更新文章版本或身分不符；保留目前文章。');
      }
      ids.add(row.id);
      for (const id of identityIds(row)) {
        if (aliases.has(id) && aliases.get(id) !== row.id) throw Error('更新文章身分有衝突；保留目前文章。');
        aliases.set(id, row.id);
      }
    }
    const observed = Object.values(snapshot.sources).map(row => Date.parse(row?.checked_at)).filter(Number.isFinite);
    return {snapshot: clone(snapshot), serial: JSON.stringify(snapshot), time: snapshotTime(snapshot.updated),
      observed: observed.length ? Math.max(...observed) : null};
  }

  function create(options = {}) {
    const now = options.now || (() => Date.now());
    const visible = options.isVisible || (() => typeof document === 'undefined' || !document.hidden);
    const eligible = options.eligible || (row => Date.parse(row.date) >= now() - DISPLAY_MS && Date.parse(row.date) <= now() + 300000);
    const selectArticles = snapshot => {
      if (!options.selectArticles) return snapshot.articles.filter(eligible);
      const selected = options.selectArticles(snapshot);
      if (!Array.isArray(selected)) throw Error('展示文章選擇未完成；保留目前文章。');
      const ids = new Set(selected.map(row => row?.id));
      return snapshot.articles.filter(row => ids.has(row.id));
    };
    const fetcher = options.fetcher || ((...args) => window.fetch(...args));
    const intervalMs = Math.max(60000, Math.min(900000, Number(options.intervalMs) || 120000));
    const timeoutMs = Math.max(1000, Math.min(15000, Number(options.timeoutMs) || 8000));
    let storage = options.storage, persistent = true;
    if (storage === undefined) {
      try { storage = window.localStorage; } catch (_) { persistent = false; }
    }
    let previous = [], hasPreviousVisit = false, previousComplete = true, previousViewedAt = 0;
    try {
      const raw = storage?.getItem(KEY);
      if (raw && raw.length <= MAX_STORAGE_CHARS) {
        const saved = JSON.parse(raw);
        if (saved?.version === 1 && Number.isFinite(saved.viewed_at) && saved.viewed_at > 0 &&
            saved.viewed_at <= now() + 300000 && Array.isArray(saved.records) && saved.records.length <= MAX_RECORDS &&
            saved.records.every(row => row && typeof row.id === 'string' && SAFE_ID.test(row.id) &&
              typeof row.revision === 'string' && row.revision.length <= 8192 &&
              Number.isFinite(row.published) && Array.isArray(row.aliases) && row.aliases.length <= 64 &&
              row.aliases.every(id => typeof id === 'string' && SAFE_ID.test(id)))) {
          hasPreviousVisit = true;
          previousComplete = saved.complete !== false;
          previousViewedAt = saved.viewed_at;
          previous = saved.records.filter(row => row.published >= now() - OVERLAP_MS);
        }
      }
    } catch (_) {
      // A malformed own-key value is replaceable; denied access remains memory-only.
      try { storage?.getItem(KEY); } catch (_) { persistent = false; }
    }
    let current = null, candidate = null, inFlight = null, controller = null;
    let visitBaseline = new Map(), visitComplete = true, visitViewedAt = 0;
    let timer = null, applying = false, generation = 0;

    const indexRecords = records => {
      const result = new Map();
      for (const row of records) for (const id of [row.id, ...row.aliases]) result.set(id, row);
      return result;
    };
    const articleRecord = row => ({id: row.id, aliases: [...new Set(row.id_aliases || [])],
      revision: revision(row), published: Date.parse(row.date)});
    const recordsFor = rows => rows.map(articleRecord);
    const findRecord = (row, records) => {
      for (const id of identityIds(row)) if (records.has(id)) return records.get(id);
      return null;
    };
    function classify(row, records) {
      const prior = findRecord(row, records);
      return !prior ? 'new' : prior.revision !== revision(row) ? 'updated' : '';
    }
    function remember(snapshot) {
      const fresh = recordsFor(selectArticles(snapshot)).filter(row => row.published >= now() - OVERLAP_MS);
      const occupied = new Set(fresh.flatMap(row => [row.id, ...row.aliases]));
      const available = [...fresh, ...previous.filter(row => row.published >= now() - OVERLAP_MS &&
        ![row.id, ...row.aliases].some(id => occupied.has(id)))]
        .sort((a, b) => b.published - a.published);
      previous = available.slice(0, MAX_RECORDS);
      const viewedAt = now();
      let complete = previous.length === available.length;
      let raw = JSON.stringify({version: 1, viewed_at: viewedAt, complete, records: previous});
      while (raw.length > MAX_STORAGE_CHARS && previous.length) {
        complete = false;
        previous = previous.slice(0, Math.max(0, previous.length - Math.max(1, Math.ceil(previous.length / 10))));
        raw = JSON.stringify({version: 1, viewed_at: viewedAt, complete, records: previous});
      }
      try { if (storage?.setItem) storage.setItem(KEY, raw); else persistent = false; }
      catch (_) { persistent = false; }
      hasPreviousVisit = true;
      previousComplete = complete;
      previousViewedAt = viewedAt;
    }
    function notifyPending() {
      try { options.onPending?.(candidate ? {...candidate.counts, snapshot: clone(candidate.value.snapshot)} : null); }
      catch (error) { notifyError(error); }
    }
    function notifyError(error) {
      try { options.onError?.(error); } catch (_) {}
    }
    function begin(snapshot) {
      if (current) return {status: 'already-begun'};
      try {
        current = inspect(snapshot, true);
        visitBaseline = indexRecords(hasPreviousVisit ? previous : recordsFor(selectArticles(current.snapshot)));
        visitComplete = !hasPreviousVisit || previousComplete;
        visitViewedAt = hasPreviousVisit ? previousViewedAt : now();
        const firstVisit = !hasPreviousVisit;
        remember(current.snapshot);
        return {status: 'ready', firstVisit};
      } catch (error) {
        notifyError(error);
        return {status: 'error', reason: error.message};
      }
    }
    function countsFor(snapshot) {
      const prior = indexRecords(recordsFor(selectArticles(current.snapshot)));
      let newCount = 0, updatedCount = 0;
      for (const row of selectArticles(snapshot)) {
        const status = classify(row, prior);
        if (status === 'new') newCount++;
        if (status === 'updated') updatedCount++;
      }
      return {newCount, updatedCount};
    }
    async function readResponse(response) {
      if (!response || !response.ok) throw Error('更新檢查失敗；保留目前文章。');
      if (Number(response.headers?.get('Content-Length')) > MAX_RESPONSE_BYTES) throw Error('更新資料超出大小上限。');
      let raw = '';
      if (response.body?.getReader && typeof TextDecoder !== 'undefined') {
        const reader = response.body.getReader(), decoder = new TextDecoder();
        let bytes = 0;
        try {
          while (true) {
            const part = await reader.read();
            if (part.done) break;
            bytes += part.value.byteLength;
            if (bytes > MAX_RESPONSE_BYTES) { await reader.cancel(); throw Error('更新資料超出大小上限。'); }
            raw += decoder.decode(part.value, {stream: true});
          }
          raw += decoder.decode();
        } finally { reader.releaseLock?.(); }
      } else {
        raw = await response.text();
        const size = typeof TextEncoder === 'undefined' ? raw.length * 3 : new TextEncoder().encode(raw).byteLength;
        if (size > MAX_RESPONSE_BYTES) throw Error('更新資料超出大小上限。');
      }
      return JSON.parse(raw);
    }
    function check() {
      if (!current) return Promise.resolve({status: 'not-ready'});
      if (!visible()) return Promise.resolve({status: 'hidden'});
      if (applying) return Promise.resolve({status: 'applying'});
      if (inFlight) return inFlight;
      const token = generation;
      controller = new AbortController();
      const activeController = controller;
      let deadline;
      const bounded = new Promise((_, reject) => {
        deadline = setTimeout(() => { activeController.abort(); reject(Error('更新檢查逾時；保留目前文章。')); }, timeoutMs);
      });
      inFlight = (async () => {
        try {
          const payload = await Promise.race([
            Promise.resolve(fetcher('data/articles_index.json', {cache: 'no-cache', signal: activeController.signal, redirect: 'error',
              credentials: 'same-origin'})).then(readResponse), bounded,
          ]);
          if (token !== generation || applying) return {status: 'discarded'};
          const value = inspect(payload);
          const olderThan = other => (value.time !== null && other.time !== null && value.time < other.time) ||
            (value.time === other.time && value.observed !== null && other.observed !== null && value.observed < other.observed);
          if (olderThan(current) || candidate && olderThan(candidate.value)) {
            throw Error('更新資料較目前版本舊；保留目前文章。');
          }
          if (value.serial === current.serial) {
            return candidate ? {status: 'pending', ...candidate.counts} : {status: 'unchanged'};
          }
          const counts = countsFor(value.snapshot);
          const changed = !candidate || candidate.value.serial !== value.serial;
          candidate = {value, counts};
          if (changed) notifyPending();
          return {status: 'pending', ...counts};
        } catch (error) {
          if (token !== generation) return {status: 'discarded'};
          notifyError(error);
          return {status: 'error', reason: error.message};
        } finally {
          clearTimeout(deadline);
          if (controller === activeController) controller = null;
          inFlight = null;
        }
      })();
      return inFlight;
    }
    async function apply() {
      if (!candidate || applying || typeof options.onApply !== 'function') return false;
      const selected = candidate;
      applying = true;
      try {
        const accepted = await options.onApply(clone(selected.value.snapshot));
        if (accepted === false) throw Error('更新未套用；保留目前文章。');
        current = selected.value;
        remember(current.snapshot);
        candidate = null;
        notifyPending();
        return true;
      } catch (error) {
        notifyError(error);
        return false;
      } finally { applying = false; }
    }
    const onVisibility = () => { if (visible()) void check(); };
    function start() {
      if (timer !== null) return;
      timer = setInterval(() => { if (visible()) void check(); }, intervalMs);
      if (typeof document !== 'undefined') document.addEventListener?.('visibilitychange', onVisibility);
    }
    function stop() {
      generation++;
      if (timer !== null) clearInterval(timer);
      timer = null;
      if (typeof document !== 'undefined') document.removeEventListener?.('visibilitychange', onVisibility);
      controller?.abort();
    }
    return {begin, check, apply, start, stop,
      label: row => {
        if (!current || !row || typeof row.id !== 'string' || !SAFE_ID.test(row.id) || !eligible(row)) return '';
        if (!visitComplete && !findRecord(row, visitBaseline) && Date.parse(row.date) <= visitViewedAt) return '';
        return classify(row, visitBaseline);
      },
      pending: () => candidate ? {...candidate.counts, snapshot: clone(candidate.value.snapshot)} : null,
      isPersistent: () => persistent};
  }
  window.RssReaderUpdates = {create, revision,
    validateSnapshot: snapshot => { try { inspect(snapshot); return true; } catch (_) { return false; } }};
}());
