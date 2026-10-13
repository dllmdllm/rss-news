/* Device-only summary playback: no fetch, AI, speech, storage or read-state writes. */
(function () {
  function create(host, { points, timeLabel }) {
    const viewport = host.querySelector('.summary-viewport');
    const track = host.querySelector('.summary-track');
    const toggle = host.querySelector('[data-player-toggle]');
    const speed = host.querySelector('[data-player-speed]');
    const status = host.querySelector('[data-player-status]');
    const count = host.querySelector('[data-player-count]');
    const motion = window.matchMedia('(prefers-reduced-motion: reduce)');
    let active = false, opened = false, paused = true, hovered = false, focused = false;
    let frame = null, previous = null, position = 0, endWait = 0, signature = '', ready = 0;
    const speeds = { slow: 12, normal: 24, fast: 40 };

    function stop() {
      if (frame !== null) cancelAnimationFrame(frame);
      frame = null;
      previous = null;
    }
    function running() {
      return active && ready > 0 && !paused && !hovered && !focused && !document.hidden;
    }
    function refresh() {
      stop();
      position = viewport.scrollTop;
      toggle.textContent = paused ? '▶ 繼續' : 'Ⅱ 暫停';
      toggle.setAttribute('aria-pressed', String(!paused));
      toggle.disabled = ready === 0;
      const message = !ready ? '暫時冇可播放嘅 AI 摘要' : paused ?
        (motion.matches ? '減少動態：已暫停，可手動捲動或按繼續' : '已暫停，可手動捲動') :
        hovered || focused ? '閱讀中：暫停捲動' : '由下向上播放 · 播完循環';
      if (status.textContent !== message) status.textContent = message;
      if (running()) frame = requestAnimationFrame(tick);
    }
    function tick(now) {
      frame = null;
      if (!running()) return;
      const delta = previous === null ? 0 : Math.min(Math.max(now - previous, 0), 100) / 1000;
      previous = now;
      const max = viewport.scrollHeight - viewport.clientHeight;
      if (max <= 1) {
        // A short single card is already fully readable; no pointless motion.
        status.textContent = '摘要已全部顯示，可手動閱讀';
        previous = null;
        return;
      }
      if (position >= max) {
        endWait += delta;
        if (endWait >= 2) {
          viewport.scrollTop = 0;
          position = 0;
          endWait = 0;
        }
      } else {
        endWait = 0;
        position = Math.min(max, position + delta * speeds[speed.value]);
        viewport.scrollTop = position;
      }
      frame = requestAnimationFrame(tick);
    }
    toggle.addEventListener('click', () => { paused = !paused; refresh(); });
    speed.addEventListener('change', refresh);
    host.querySelector('[data-player-restart]').addEventListener('click', () => {
      viewport.scrollTop = 0;
      endWait = 0;
      refresh(); // Restart preserves the user's pause choice.
    });
    viewport.addEventListener('pointerenter', () => { hovered = true; refresh(); });
    viewport.addEventListener('pointerleave', () => { hovered = false; refresh(); });
    viewport.addEventListener('focusin', () => { focused = true; refresh(); });
    viewport.addEventListener('focusout', event => {
      focused = viewport.contains(event.relatedTarget);
      refresh();
    });
    viewport.addEventListener('wheel', () => { paused = true; refresh(); }, { passive: true });
    viewport.addEventListener('keydown', event => {
      if (['ArrowUp', 'ArrowDown', 'PageUp', 'PageDown', 'Home', 'End', ' '].includes(event.key)) {
        paused = true;
        refresh(); // Keep native keyboard scrolling; don't capture search input.
      }
    });
    document.addEventListener('visibilitychange', () => {
      if (document.hidden) paused = true;
      refresh(); // Foreground returns paused, without a time/distance jump.
    });
    window.addEventListener('pagehide', () => { paused = true; stop(); });
    motion.addEventListener('change', () => { if (motion.matches) paused = true; refresh(); });
    new ResizeObserver(refresh).observe(viewport);

    function setArticles(articles) {
      const seen = new Set();
      let skipped = 0;
      const rows = [];
      for (const article of articles) {
        if (!article.id || seen.has(article.id)) continue;
        seen.add(article.id);
        const summary = points(article, Infinity);
        if (!summary.length) { skipped++; continue; }
        rows.push({ id: article.id, title: article.title || '', source: article.source || '', date: article.date || '',
          time: timeLabel(article), summary });
      }
      count.textContent = `${rows.length} 篇摘要${skipped ? ` · ${skipped} 篇等候 AI` : ''}`;
      const next = JSON.stringify(rows.map(({ time, ...row }) => row));
      if (next === signature) {
        // Relative clock labels may change without changing the reading queue.
        track.querySelectorAll('.summary-play-meta').forEach((meta, i) => {
          meta.textContent = `${rows[i].source} · ${rows[i].time}`;
        });
        return;
      }
      signature = next;
      ready = rows.length;
      const fragment = document.createDocumentFragment();
      for (const row of rows) {
        const card = document.createElement('article');
        card.className = 'summary-play-card';
        const meta = document.createElement('p');
        meta.className = 'summary-play-meta';
        meta.textContent = `${row.source} · ${row.time}`;
        const title = document.createElement('h2');
        const link = document.createElement('a');
        link.href = `article.html?id=${encodeURIComponent(row.id)}`;
        link.textContent = row.title;
        title.append(link);
        const list = document.createElement('ul');
        for (const text of row.summary) {
          const item = document.createElement('li');
          item.textContent = text;
          list.append(item);
        }
        card.append(meta, title, list);
        fragment.append(card);
      }
      track.replaceChildren(fragment);
      viewport.scrollTop = 0;
      endWait = 0;
      // A changed filter/order/content starts a fresh queue, not a surprise jump.
      if (active) paused = true;
      refresh();
    }
    function setActive(value) {
      if (value === active) return;
      active = value;
      if (active && !opened) { paused = motion.matches; opened = true; }
      else paused = true;
      hovered = viewport.matches(':hover');
      focused = viewport.contains(document.activeElement);
      refresh();
    }
    return { setArticles, setActive };
  }
  window.RssSummaryPlayer = { create };
})();
