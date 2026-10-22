/* Article-only reading controls. No API credentials or paid network requests. */
(function () {
  function highlightKeySentences(root, quotes) {
    root.querySelectorAll('mark.key-sentence').forEach(mark => mark.replaceWith(...mark.childNodes));
    root.normalize();
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    const nodes = [];
    let text = '', node;
    while ((node = walker.nextNode())) {
      nodes.push({ node, start: text.length, end: text.length + node.data.length });
      text += node.data;
    }
    const ranges = [];
    for (const quote of [...new Set((quotes || []).filter(q => typeof q === 'string' && q.trim()))].slice(0, 5)) {
      let from = 0, at;
      while ((at = text.indexOf(quote, from)) !== -1) {
        ranges.push([at, at + quote.length]);
        from = at + quote.length;
      }
    }
    ranges.sort((a, b) => a[0] - b[0]);
    const merged = [];
    for (const range of ranges) {
      const last = merged[merged.length - 1];
      if (last && range[0] <= last[1]) last[1] = Math.max(last[1], range[1]);
      else merged.push([...range]);
    }
    for (const { node, start, end } of nodes) {
      const hits = merged.filter(r => r[0] < end && r[1] > start);
      if (!hits.length) continue;
      const fragment = document.createDocumentFragment();
      let offset = 0;
      for (const range of hits) {
        const left = Math.max(start, range[0]) - start;
        const right = Math.min(end, range[1]) - start;
        fragment.append(document.createTextNode(node.data.slice(offset, left)));
        const mark = document.createElement('mark');
        mark.className = 'key-sentence';
        mark.textContent = node.data.slice(left, right);
        fragment.append(mark);
        offset = right;
      }
      fragment.append(document.createTextNode(node.data.slice(offset)));
      node.replaceWith(fragment);
    }
    return merged.length;
  }

  function init(article) {
    const root = document.getElementById('content');
    const highlight = document.getElementById('highlightToggle');
    const count = highlightKeySentences(root, article.key_sentences);
    const reader = window.RssReaderState;
    const saved = reader.articleState(article.id);
    const read = document.getElementById('readerRead');
    const resume = document.getElementById('readerResume');
    const localStatus = document.getElementById('readerLocalStatus');
    function localUi() {
      const state = reader.articleState(article.id);
      read.setAttribute('aria-pressed', String(state.read));
      read.textContent = state.read ? '標記未讀' : '標記已讀';
      localStatus.textContent = reader.isPersistent() ? '記錄只保留在此裝置' : '儲存被封鎖，記錄只保留在本次頁面';
    }
    read.addEventListener('click', () => { reader.markRead(article.id, !reader.articleState(article.id).read); localUi(); });
    // Storage/pageshow refreshes emit without an id; update labels only, leaving
    // this tab's scroll position and speech queue under the user's control.
    window.addEventListener('rss-reader-state', event => {
      if (!event.detail?.id || event.detail.id === String(article.id)) localUi();
    });
    resume.hidden = saved.position <= 0.01;
    resume.textContent = `繼續上次位置（${Math.round(saved.position * 100)}%）`;
    let moved = false, scrollTimer;
    function savePosition() {
      if (!moved) return;
      const top = root.getBoundingClientRect().top + window.scrollY;
      const span = Math.max(1, root.scrollHeight - window.innerHeight);
      reader.set(article.id, {position: (window.scrollY - top) / span});
      localUi();
    }
    resume.addEventListener('click', () => {
      moved = true;
      const top = root.getBoundingClientRect().top + window.scrollY;
      window.scrollTo({top: top + saved.position * Math.max(1, root.scrollHeight - window.innerHeight), behavior: 'auto'});
      resume.hidden = true;
    });
    window.addEventListener('scroll', () => {
      moved = true;
      clearTimeout(scrollTimer);
      scrollTimer = setTimeout(savePosition, 250);
    }, {passive: true});
    window.addEventListener('pagehide', savePosition);
    localUi();
    root.classList.toggle('hide-key-sentences', reader.preference('rss_highlight', '1') === '0');
    highlight.setAttribute('aria-pressed', String(!root.classList.contains('hide-key-sentences')));
    highlight.hidden = !count;
    highlight.addEventListener('click', () => {
      const enabled = root.classList.toggle('hide-key-sentences');
      highlight.setAttribute('aria-pressed', String(!enabled));
      reader.setPreference('rss_highlight', enabled ? '0' : '1');
    });
    const play = document.getElementById('articleTts');
    const pause = document.getElementById('articleTtsPause');
    const mode = document.getElementById('articleTtsMode');
    const rate = document.getElementById('articleTtsRate');
    const status = document.getElementById('articleTtsStatus');
    const previous = document.getElementById('articleTtsPrevious');
    const following = document.getElementById('articleTtsNext');
    const seek = document.getElementById('articleTtsSeek');
    const progress = document.getElementById('articleTtsProgress');
    mode.value = reader.preference('rss_tts_mode', saved.tts.mode);
    rate.value = reader.preference('rss_tts_rate', '1');
    if (!['full', 'summary'].includes(mode.value)) mode.value = 'full';
    if (!['0.85', '1', '1.2', '1.5'].includes(rate.value)) rate.value = '1';
    const synth = window.speechSynthesis;
    if (!synth || !window.SpeechSynthesisUtterance) {
      play.disabled = true;
      previous.disabled = following.disabled = seek.disabled = true;
      status.textContent = '呢個瀏覽器未支援朗讀';
      return;
    }
    let chunks = [], index = saved.tts.mode === mode.value ? saved.tts.index : 0, active = false, paused = false, generation = 0, utterance = null, browserDefault = false, completed = false, startTimer, started = false;
    function watchStart(token, current) {
      clearTimeout(startTimer);
      startTimer = setTimeout(() => {
        if (active && !paused && !started && token === generation && utterance === current) {
          stop('語音未能開始，請檢查裝置語音支援後再試');
        }
      }, 5000);
    }
    function stop(message = '') {
      clearTimeout(startTimer);
      generation++;
      active = false;
      paused = false;
      utterance = null;
      synth.cancel();
      play.textContent = '🔊 朗讀';
      play.setAttribute('aria-pressed', 'false');
      pause.disabled = true;
      pause.textContent = '暫停';
      status.textContent = message;
    }
    function next(token) {
      if (!active || token !== generation) return;
      if (index >= chunks.length) { completed = true; index = chunks.length - 1; updateQueue(); stop('朗讀完畢'); return; }
      const text = chunks[index];
      const voices = synth.getVoices();
      const voice = voices.find(v => /zh[-_]HK|yue|Cantonese|粵/i.test(v.lang + ' ' + v.name))
        || voices.find(v => /^zh/i.test(v.lang));
      function speak(useDefault = false) {
        if (!active || token !== generation) return;
        utterance = new SpeechSynthesisUtterance(text);
        if (!useDefault) {
          if (voice) utterance.voice = voice;
          utterance.lang = voice?.lang || 'zh-HK';
        }
        utterance.rate = Number(rate.value);
        status.dataset.voice = useDefault ? 'browser-default' : (voice?.name || 'zh-HK (browser selection)');
        const current = utterance;
        started = false;
        status.textContent = '正在啟動朗讀';
        utterance.onstart = () => {
          if (utterance === current && token === generation && active) {
            clearTimeout(startTimer); started = true;
            status.textContent = paused ? '已暫停' : '朗讀中';
          }
        };
        utterance.onend = () => { if (utterance === current && token === generation) { clearTimeout(startTimer); index++; if (index < chunks.length) updateQueue(); next(token); } };
        utterance.onerror = event => {
          if (token !== generation || utterance !== current) return;
          clearTimeout(startTimer);
          const error = event?.error || '';
          status.dataset.error = error;
          // Only retry unavailable language/voice, once, with browser default.
          // Permission/audio/network failures need a new user action or device fix.
          if (!useDefault && ['language-unavailable', 'voice-unavailable'].includes(error)) {
            browserDefault = true;
            status.textContent = '所選語音不可用，嘗試瀏覽器預設語音';
            speak(true);
            return;
          }
          const selected = status.dataset.voice;
          stop(error ? `朗讀失敗（${error}；語音：${selected}），請檢查瀏覽器語音支援後再試` : '朗讀失敗，請再試');
        };
        watchStart(token, current);
        try { synth.speak(utterance); }
        catch (_) { utterance.onerror({error: 'synthesis-failed'}); }
      }
      speak(browserDefault);
    }
    function updateQueue() {
      index = Math.max(0, Math.min(index, Math.max(0, chunks.length - 1)));
      seek.max = String(Math.max(0, chunks.length - 1));
      seek.value = String(index);
      seek.disabled = !chunks.length;
      previous.disabled = !chunks.length || index === 0;
      following.disabled = !chunks.length || index >= chunks.length - 1;
      progress.textContent = `${chunks.length ? index + 1 : 0} / ${chunks.length} 段`;
      reader.set(article.id, {tts: {mode: mode.value, index: completed ? 0 : index}});
    }
    function prepareQueue() {
      const text = mode.value === 'summary'
        ? String(article.summary || '').replace(/\\n/g, '\n').replace(/(?:^|\n)\s*[・•●]\s*/g, '')
        : root.innerText;
      chunks = text.trim() ? ([article.title || '', text].join('。')).match(/[\s\S]{1,180}/g) || [] : [];
      updateQueue();
    }
    function start() {
      if (!chunks.length) { status.textContent = '暫時未有可朗讀內容'; return; }
      if (completed) { index = 0; completed = false; }
      updateQueue();
      active = true;
      paused = false;
      play.textContent = '⏹ 停止';
      play.setAttribute('aria-pressed', 'true');
      pause.disabled = false;
      status.textContent = '正在啟動朗讀';
      browserDefault = false;
      delete status.dataset.error;
      // cancel()/speak() preserve the global engine's paused state. Resume
      // only inside this explicit Play gesture, before scheduling a new queue.
      if (synth.paused) synth.resume();
      next(++generation);
    }
    function move(nextIndex) {
      const playing = active && !paused;
      stop();
      completed = false;
      index = nextIndex;
      updateQueue();
      // A click/change is a new user gesture. Paused/stopped queues remain silent.
      if (playing) start();
    }
    previous.addEventListener('click', () => move(index - 1));
    following.addEventListener('click', () => move(index + 1));
    seek.addEventListener('change', () => move(Number(seek.value)));
    prepareQueue();
    play.addEventListener('click', () => {
      if (active) { stop(); return; }
      start();
    });
    pause.addEventListener('click', () => {
      if (!active) return;
      paused = !paused;
      if (paused) synth.pause(); else synth.resume();
      if (paused) clearTimeout(startTimer);
      else if (!started) watchStart(generation, utterance);
      pause.textContent = paused ? '繼續' : '暫停';
      status.textContent = paused ? '已暫停' : (started ? '朗讀中' : '正在啟動朗讀');
    });
    mode.addEventListener('change', () => { stop(); completed = false; index = 0; reader.setPreference('rss_tts_mode', mode.value); prepareQueue(); });
    rate.addEventListener('change', () => { stop(); reader.setPreference('rss_tts_rate', rate.value); });
    window.addEventListener('pagehide', () => stop());
  }
  window.RssArticleReading = { init, highlightKeySentences };
}());
