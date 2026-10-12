"""No model calls: event comparisons render only grounded provenance."""
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_event_sources_unavailable_conservative_dedupe_and_claims():
    script = r'''
    const assert = require('node:assert/strict');
    require('./docs/js/event-reading.js');
    const api = globalThis.RssEventReading;
    const articles = [{id:'a',source:'HK01 社會',title:'one',cluster_id:'c',content_hash:'a'.repeat(64)},
      {id:'b',source:'明報 本地',title:'two',content_hash:'b'.repeat(64)}, {id:'d',source:'明報 本地',title:'duplicate',duplicate_of:'b'}];
    const topics = [{topic:'same broad topic',article_ids:['a','b','d','a'],cluster_id:'c'}];
    let html = api.render(topics, articles, {c:{digest:{consensus:'UNSUPPORTED',angles:[{detail:'invented'}]}}});
    assert(!html.includes('UNSUPPORTED')); assert(!html.includes('invented'));
    assert(html.includes('暫定')); assert(html.includes('2 篇獨立卡／3 篇原始記錄'));
    assert(html.includes('尚無可核對')); assert(html.includes('查看已標示重複的 1 篇'));
    const good = {text:'source-backed',evidence:[{article_id:'a',quote:'source-backed',verified:true,content_hash:'a'.repeat(64)}, {article_id:'b',quote:'source-backed with detail',verified:true,content_hash:'b'.repeat(64)}]};
    html = api.render(topics, articles, {c:{digest:{shared_facts:[good],new_developments:[{text:'bad',evidence:[{article_id:'unknown',quote:'x'}]}]}}});
    assert(html.includes('source-backed')); assert(html.includes('source-backed with detail')); assert(!html.includes('>bad<'));
    assert(html.includes('article.html?id=a&quote=source-backed#source-evidence'));
    assert(!api.claims([good], [articles[0],{...articles[1],source:'HK01 突發'}],true));
    assert(api.provenance({content_quality:{fallback:'rss'},scrape_error:'503'}).includes('部分正文'));
    assert(api.provenance({}).includes('抓取 未提供'));
    assert.equal(api.outlet('星島頭條'), '星島'); assert.equal(api.outlet('星島 即時國際'), '星島'); assert.equal(api.outlet('星島別家'), '星島別家');
    assert(api.healthObservation({error:'<script>alert(1)</script>'}).includes('&lt;script&gt;'));
    assert(api.healthObservation({}).includes('檢查時間 未提供'));
    assert(!api.healthObservation({fresh_count:0}).includes('原因'));
    assert(!api.healthObservation({checked_at:'2026-10-12T00:00:00Z'}).includes('未提供'));
    assert(!api.claims([{...good,text:'unsupported'}],articles,true));
    assert(!api.claims([{...good,evidence:good.evidence.map(e=>({...e,content_hash:'c'.repeat(64)}))}],articles,true));
    assert(!api.claims([{...good,evidence:good.evidence.map(e=>({...e,verified:undefined}))}],articles,true));
    const escaped = {...good,text:'<script>',evidence:good.evidence.map(e=>({...e,quote:'<script>'}))};
    assert(api.claims([escaped],articles,true).includes('&lt;script&gt;'));
    assert(!api.render(topics,articles,{c:{digest:{contradictions:[{claim_a:'legacy unsupported',claim_b:'other',quote_a:'x',quote_b:'y',article_id_a:'a',article_id_b:'b'}]}}}).includes('legacy unsupported'));
    '''
    subprocess.run(['node', '-e', script], cwd=ROOT, check=True)
