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
    highlight.hidden = !count;
    highlight.addEventListener('click', () => {
      const enabled = root.classList.toggle('hide-key-sentences');
      highlight.setAttribute('aria-pressed', String(!enabled));
    });
    const play = document.getElementById('articleTts');
    const pause = document.getElementById('articleTtsPause');
    const mode = document.getElementById('articleTtsMode');
    const rate = document.getElementById('articleTtsRate');
    const status = document.getElementById('articleTtsStatus');
    const synth = window.speechSynthesis;
    if (!synth || !window.SpeechSynthesisUtterance) {
      play.disabled = true;
      status.textContent = '呢個瀏覽器未支援朗讀';
      return;
    }
    let chunks = [], active = false, paused = false, generation = 0, utterance = null;
    function stop(message = '') {
      generation++;
      active = false;
      paused = false;
      chunks = [];
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
      if (!chunks.length) { stop('朗讀完畢'); return; }
      utterance = new SpeechSynthesisUtterance(chunks.shift());
      const voices = synth.getVoices();
      const voice = voices.find(v => /zh[-_]HK|yue|Cantonese|粵/i.test(v.lang + ' ' + v.name));
      if (voice) utterance.voice = voice;
      utterance.lang = voice?.lang || 'zh-HK';
      utterance.rate = Number(rate.value);
      utterance.onend = () => next(token);
      utterance.onerror = () => { if (token === generation) stop('朗讀失敗，請再試'); };
      synth.speak(utterance);
    }
    play.addEventListener('click', () => {
      if (active) { stop(); return; }
      const text = mode.value === 'summary'
        ? String(article.summary || '').replace(/[・•●]/g, '').replace(/\\n/g, '\n')
        : root.innerText;
      if (!text.trim()) { status.textContent = '暫時未有可朗讀內容'; return; }
      // Short chunks avoid long-utterance stalls on mobile. Start synchronously
      // inside the click handler so iOS preserves the user gesture.
      chunks = ([article.title || '', text].join('。')).match(/[\s\S]{1,180}/g) || [];
      active = true;
      paused = false;
      play.textContent = '⏹ 停止';
      play.setAttribute('aria-pressed', 'true');
      pause.disabled = false;
      status.textContent = '朗讀中';
      next(++generation);
    });
    pause.addEventListener('click', () => {
      if (!active) return;
      paused = !paused;
      if (paused) synth.pause(); else synth.resume();
      pause.textContent = paused ? '繼續' : '暫停';
      status.textContent = paused ? '已暫停' : '朗讀中';
    });
    mode.addEventListener('change', () => stop());
    rate.addEventListener('change', () => stop());
    window.addEventListener('pagehide', () => stop());
  }
  window.RssArticleReading = { init, highlightKeySentences };
}());
