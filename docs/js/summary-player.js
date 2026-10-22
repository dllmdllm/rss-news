/* Existing key-sentence playback: no AI, speech, storage or read-state writes. */
(function () {
  function thumbnailUrl(value) {
    if (typeof value !== 'string' || !value || value !== value.trim() || /[\u0000-\u0020\u007f\\]/.test(value)) return '';
    try {
      const url = new URL(value);
      const host = url.hostname.toLowerCase().replace(/\.$/, '');
      // Existing publisher thumbnails only: no credentials, executable/data
      // schemes, local names or IP-literal targets. Do not discover a fallback.
      if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password ||
          !/^[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?\.[a-z]{2,63}$/.test(host) ||
          /\.(local|localhost|internal|test|invalid)$/.test(host) ||
          (url.port && !['80', '443'].includes(url.port))) return '';
      return url.href;
    } catch (_) { return ''; }
  }

  function create(host, { points, timeLabel, pointLink }) {
    const viewport = host.querySelector('.summary-viewport');
    const track = host.querySelector('.summary-track');
    const toggle = host.querySelector('[data-player-toggle]');
    const speed = host.querySelector('[data-player-speed]');
    const status = host.querySelector('[data-player-status]');
    const count = host.querySelector('[data-player-count]');
    const motion = window.matchMedia('(prefers-reduced-motion: reduce)');
    let active = false, opened = false, paused = true, hovered = false, focused = false;
    let frame = null, previous = null, position = 0, endWait = 0, signature = '', ready = 0, notice = '';
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
      const message = notice || (!ready ? '暫時冇可播放嘅關鍵句' : paused ?
        (motion.matches ? '減少動態：已暫停，可手動捲動或按繼續' : '已暫停，可手動捲動') :
        hovered || focused ? '閱讀中：暫停捲動' : '由下向上播放 · 播完循環');
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
        status.textContent = '關鍵句已全部顯示，可手動閱讀';
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
          time: timeLabel(article), thumbnail: thumbnailUrl(article.thumbnail), summary,
          links: summary.map(text => typeof pointLink === 'function' ? pointLink(article, text) : null),
          members: (Array.isArray(article.group_articles) ? article.group_articles : []).map(member => ({
            id: member.id, title: member.title, source: member.source,
            matched: article.matched_members?.includes(member.id) !== false,
          })),
          partial: window.RssSourceEvidence?.scope(article).status === 'partial' });
      }
      count.textContent = `${rows.length} 篇關鍵句${skipped ? ` · ${skipped} 篇未有可用關鍵句` : ''}`;
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
        const media = document.createElement('div');
        media.className = 'summary-play-media';
        const fallback = document.createElement('span');
        fallback.textContent = '未有圖片';
        media.append(fallback);
        if (row.thumbnail) {
          const image = document.createElement('img');
          image.alt = ''; // Decorative source thumbnail; headline supplies context.
          image.loading = 'lazy';
          image.decoding = 'async';
          image.referrerPolicy = 'no-referrer';
          image.width = 400;
          image.height = 300;
          fallback.hidden = true;
          image.addEventListener('error', () => {
            image.remove();
            fallback.hidden = false;
            fallback.textContent = '圖片未能載入';
          }, { once: true });
          image.src = row.thumbnail;
          media.append(image);
        }
        const body = document.createElement('div');
        body.className = 'summary-play-body';
        const meta = document.createElement('p');
        meta.className = 'summary-play-meta';
        meta.textContent = `${row.source} · ${row.time}`;
        const title = document.createElement('h2');
        const link = document.createElement('a');
        link.href = `article.html?id=${encodeURIComponent(row.id)}`;
        link.textContent = row.title;
        title.append(link);
        const list = document.createElement('ul');
        for (const [index, text] of row.summary.entries()) {
          const item = document.createElement('li');
          item.textContent = text;
          const href = row.links[index];
          if (typeof href === 'string' && href.startsWith('article.html?')) {
            const source = document.createElement('a');
            source.href = href;
            source.textContent = '原文段落';
            source.className = 'summary-source-link';
            item.append(document.createTextNode(' '), source);
          }
          list.append(item);
        }
        body.append(meta, title, list);
        if (row.partial) {
          const note = document.createElement('p');
          note.className = 'reading-provenance';
          note.textContent = '關鍵句只核對已取得的正文；來源完整性未核實。';
          body.append(note);
        }
        if (row.members.length > 1) {
          const details = document.createElement('details');
          details.className = 'story-group';
          const summary = document.createElement('summary');
          summary.textContent = `同組 ${row.members.length} 篇報道`;
          const members = document.createElement('ul');
          for (const member of row.members) {
            const item = document.createElement('li'), link = document.createElement('a');
            link.href = 'article.html?id=' + encodeURIComponent(member.id);
            link.textContent = `${member.source || '來源'} · ${member.title || '報道'}`;
            item.append(link);
            if (!member.matched) item.append(document.createTextNode('（不符合目前搜尋或篩選）'));
            members.append(item);
          }
          details.append(summary, members);
          body.append(details);
        }
        card.append(media, body);
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
    function setNotice(value) { notice = value; refresh(); }
    return { setArticles, setActive, setNotice };
  }
  window.RssSummaryPlayer = { create };
})();
