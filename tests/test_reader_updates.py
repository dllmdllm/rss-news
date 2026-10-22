"""Device-only visit revisions and explicit snapshot-apply boundaries."""
import json
import subprocess
from pathlib import Path

from test_frontend import _require_node
from test_browser_smoke import browser

ROOT = Path(__file__).resolve().parents[1]


def _run(script):
    source = (ROOT / 'docs/js/reader-updates.js').read_text(encoding='utf-8')
    prelude = '''
const vm=require('node:vm'),assert=require('node:assert/strict');
const NOW=Date.UTC(2026,9,22,13,5),HOUR=3600000;
const source=SOURCE;
function article(id,extra={}){return {id,title:'新聞 '+id,source:'測試來源',summary:'現有AI摘要',
 date:new Date(NOW-HOUR).toISOString(),content_hash:'a'.repeat(64),...extra};}
function snapshot(rows,updated='2026-10-22 21:00 HKT'){return {schema_version:2,updated,
 sources:{'測試來源':{fetch_status:'success'}},trending_topics:[],articles:rows};}
function memory(){const data=new Map();return {data,getItem:k=>data.get(k)??null,setItem:(k,v)=>data.set(k,v)};}
function create(storage,options={}){
 const events=[],errors=[];
 const document={hidden:false,addEventListener(){},removeEventListener(){}};
 const context={window:{localStorage:storage},document,AbortController,TextEncoder,TextDecoder,
   setTimeout,clearTimeout,setInterval,clearInterval};
 vm.createContext(context);vm.runInContext(source,context);
 const api=context.window.RssReaderUpdates;
 const state=api.create({now:()=>NOW,onPending:value=>events.push(value),onError:error=>errors.push(error.message),...options});
 return {api,state,events,errors,document};
}
function response(value){return {ok:true,headers:{get:()=>null},text:async()=>JSON.stringify(value)};}
'''.replace('SOURCE', json.dumps(source))
    result = subprocess.run([_require_node(), '-e', prelude + '\n(async()=>{\n' + script + '\n})().catch(error=>{console.error(error);process.exitCode=1;});'],
                            capture_output=True, text=True, timeout=25)
    assert result.returncode == 0, result.stderr


def test_first_visit_is_neutral_revisit_labels_only_source_revisions_and_preserves_reader_state():
    _run('''
 const storage=memory();storage.setItem('rss_reader_state_v1','saved reader state untouched');
 const first=create(storage),initial=snapshot([article('a'),article('b')]);
 assert.equal(first.state.begin(initial).firstVisit,true);
 assert.equal(first.state.label(article('a')),'');
 const changed=article('b',{content_hash:'b'.repeat(64)}),added=article('c');
 const second=create(storage);assert.equal(second.state.begin(snapshot([article('a'),changed,added])).firstVisit,false);
 assert.equal(second.state.label(article('a',{summary:'另一個AI摘要',tags:['新AI標籤']})),'');
 assert.equal(second.state.label(changed),'updated');assert.equal(second.state.label(added),'new');
 assert.equal(second.state.label(article('a',{title:'來源修正標題'})),'updated');
 assert.equal(storage.getItem('rss_reader_state_v1'),'saved reader state untouched');
 const third=create(storage);third.state.begin(snapshot([article('a'),changed,added]));
 assert.equal(third.state.label(changed),'');assert.equal(third.state.label(added),'');
''')


def test_alias_revision_identity_survives_slug_changes_and_twenty_four_hour_selection():
    _run('''
 const storage=memory(),old=article('old-slug-id',{title:'同一篇來源文章'});
 const first=create(storage);first.state.begin(snapshot([old]));
 const stable=article('stable-id',{title:old.title,id_aliases:['old-slug-id']});
 const second=create(storage);second.state.begin(snapshot([stable]));
 assert.equal(second.state.label(stable),'');
 assert.equal(second.state.label({...stable,content_hash:'b'.repeat(64)}),'updated');
 assert.equal(second.state.label(article('outside',{date:new Date(NOW-26*HOUR).toISOString()})),'');
 assert.equal(second.state.label(article('future',{date:new Date(NOW+HOUR).toISOString()})),'');
 const stored=JSON.parse(storage.getItem('rss_reader_updates_v1'));
 assert.equal(stored.records.length,1);assert.equal(stored.records[0].id,'stable-id');
 assert.ok(stored.records[0].aliases.includes('old-slug-id'));
''')


def test_invalid_future_or_denied_storage_stays_safe_and_memory_only():
    _run('''
 for(const raw of ['broken JSON',JSON.stringify({version:1,viewed_at:NOW+HOUR,records:[]}),JSON.stringify({version:99,viewed_at:NOW,records:[]})]){
  const storage=memory();storage.setItem('rss_reader_updates_v1',raw);
  const {state}=create(storage);assert.equal(state.begin(snapshot([article('a')])).firstVisit,true);
  assert.equal(state.label(article('a')),'');
 }
 const storage={getItem(){throw Error('blocked')},setItem(){throw Error('blocked')}};
 const {state}=create(storage);state.begin(snapshot([article('a')]));
 assert.equal(state.isPersistent(),false);assert.equal(state.label(article('a')),'');
 assert.equal(state.label(article('b')),'new');
''')


def test_storage_is_bounded_and_pruned_records_are_not_falsely_new_on_revisit():
    _run('''
 const storage=memory(),rows=Array.from({length:6100},(_,i)=>article('a'+i));
 const first=create(storage);first.state.begin(snapshot(rows));
 const raw=storage.getItem('rss_reader_updates_v1'),saved=JSON.parse(raw);
 assert.ok(raw.length<=512*1024);assert.ok(saved.records.length<=6000);assert.equal(saved.complete,false);
 const missing=rows.find(row=>!saved.records.some(record=>record.id===row.id));assert.ok(missing);
 const second=create(storage);second.state.begin(snapshot(rows));
 assert.equal(second.state.label(missing),'');
 assert.equal(second.state.label(article('just-published',{date:new Date(NOW+1000).toISOString()})),'new');
''')


def test_pending_snapshot_never_applies_automatically_and_only_successful_apply_advances_visit():
    _run('''
 const storage=memory();let applyCount=0,rendered='old';
 const next=snapshot([article('a',{content_hash:'b'.repeat(64)}),article('b')],'2026-10-22 21:01 HKT');
 const {state,events}=create(storage,{fetcher:async()=>response(next),onApply:async data=>{applyCount++;rendered=data.updated;}});
 state.begin(snapshot([article('a')]));const before=storage.getItem('rss_reader_updates_v1');
 const result=await state.check();assert.equal(result.status,'pending');assert.equal(result.newCount,1);assert.equal(result.updatedCount,1);
 assert.equal(applyCount,0);assert.equal(rendered,'old');assert.equal(storage.getItem('rss_reader_updates_v1'),before);
 const unsafeCopy=state.pending();unsafeCopy.snapshot.articles[0].title='caller mutation';
 assert.equal(state.pending().snapshot.articles[0].title,'新聞 a');
 assert.equal(await state.apply(),true);assert.equal(applyCount,1);assert.equal(rendered,next.updated);
 assert.equal(state.pending(),null);assert.equal(events.at(-1),null);
 assert.equal(state.label(article('a',{content_hash:'b'.repeat(64)})),'updated');
 assert.equal(state.label(article('b')),'new');
 const revisit=create(storage);revisit.state.begin(next);assert.equal(revisit.state.label(article('b')),'');
''')


def test_visible_single_flight_checks_keep_one_candidate_and_preserve_it_on_errors_or_stale_data():
    _run('''
 let resolve,requests=0,visible=false;
 const {state,events,errors}=create(memory(),{isVisible:()=>visible,fetcher:()=>{requests++;return new Promise(r=>resolve=r);}});
 const initial=snapshot([article('a')]);state.begin(initial);
 assert.equal((await state.check()).status,'hidden');assert.equal(requests,0);
 visible=true;const one=state.check(),two=state.check();assert.equal(one,two);assert.equal(requests,1);
 const next=snapshot([article('a'),article('b')],'2026-10-22 21:01 HKT');resolve(response(next));await one;
 assert.equal(events.length,1);assert.equal(state.pending().newCount,1);
 let request=state.check();resolve(response(next));await request;assert.equal(events.length,1);
 request=state.check();resolve(response({...next,articles:[{...article('b'),content_hash:'wrong'}]}));
 assert.equal((await request).status,'error');assert.equal(state.pending().newCount,1);assert.equal(errors.length,1);
 request=state.check();resolve(response(initial));assert.equal((await request).status,'error');assert.equal(state.pending().newCount,1);
 const latest=snapshot([article('a'),article('b'),article('c')],'2026-10-22 21:02 HKT');
 request=state.check();resolve(response(latest));await request;assert.equal(state.pending().newCount,2);assert.equal(events.length,2);
''')


def test_bad_generation_alias_conflicts_empty_or_oversized_payloads_cannot_replace_current():
    _run('''
 const initial=snapshot([article('a',{id_aliases:null})]);let fetched;
 const {state,api}=create(memory(),{fetcher:async()=>response(fetched)});state.begin(initial);
 for(const bad of [snapshot([]),snapshot([article('a'),article('a')]),snapshot([article('a'),article('b',{id_aliases:['a']})]),
  {...initial,schema_version:1},{...initial,updated:'unknown generation'},snapshot([article('a',{id:['a']})]),snapshot([article('a',{content_hash:['a'.repeat(64)]})])]){
  assert.equal(api.validateSnapshot(bad),false);fetched=bad;assert.equal((await state.check()).status,'error');assert.equal(state.pending(),null);
 }
 const module=create(memory(),{fetcher:async()=>({ok:true,headers:{get:()=>String(9*1024*1024)},text:async()=>{throw Error('body must not be read')}})});
 module.state.begin(initial);assert.equal((await module.state.check()).status,'error');assert.equal(module.state.pending(),null);
''')


def test_custom_display_selection_preserves_raw_overlap_snapshot_for_explicit_apply():
    _run('''
 const older=article('overlap',{date:new Date(NOW-26*HOUR).toISOString()}),newer=article('newer');
 const next=snapshot([article('a'),older,newer],'2026-10-22 21:01 HKT');let applied;
 const {state}=create(memory(),{selectArticles:s=>s.articles.filter(a=>Date.parse(a.date)>=NOW-24*HOUR),
   fetcher:async()=>response(next),onApply:s=>{applied=s;}});
 state.begin(snapshot([article('a')]));const result=await state.check();assert.equal(result.newCount,1);
 await state.apply();assert.equal(applied.articles.length,3);assert.equal(state.label(older),'');
''')


def test_failed_apply_and_timeout_do_not_advance_or_clear_last_good_snapshot():
    _run('''
 const storage=memory(),next=snapshot([article('a'),article('b')],'2026-10-22 21:01 HKT');
 const {state}=create(storage,{fetcher:async()=>response(next),onApply:()=>{throw Error('render failed');}});
 state.begin(snapshot([article('a')]));const before=storage.getItem('rss_reader_updates_v1');await state.check();
 assert.equal(await state.apply(),false);assert.equal(storage.getItem('rss_reader_updates_v1'),before);assert.equal(state.pending().newCount,1);
 let signal;const stalled=create(memory(),{timeoutMs:1000,fetcher:(url,options)=>{assert.equal(url,'data/articles_index.json');signal=options.signal;return new Promise(()=>{});}});
 stalled.state.begin(snapshot([article('a')]));assert.equal((await stalled.state.check()).status,'error');assert.equal(signal.aborted,true);assert.equal(stalled.state.pending(),null);
''')


def test_browser_pending_snapshot_preserves_typing_scroll_and_filter_until_user_click(browser):
    page = browser.new_page(viewport={'width': 1280, 'height': 800}, service_workers='block')
    page.set_content('''<input id="query" value="香港"><select id="filter"><option value="saved">稍後閱讀</option></select>
      <button id="apply" style="position:fixed;right:15px;top:15px" hidden>套用更新</button>
      <div id="articles" style="height:3000px">原有新聞</div>''')
    page.add_script_tag(path=str(ROOT / 'docs/js/reader-updates.js'))
    page.evaluate('''() => {
      const data=new Map(),now=Date.now();window.requests=[];window.applied=0;
      const row=id=>({id,title:'來源新聞 '+id,summary:'已有AI摘要',source:'來源',date:new Date(now-3600000).toISOString(),content_hash:'a'.repeat(64)});
      const snapshot=(articles,updated)=>({schema_version:2,updated,sources:{來源:{}},trending_topics:[],articles});
      window.updates=RssReaderUpdates.create({storage:{getItem:k=>data.get(k)||null,setItem:(k,v)=>data.set(k,v)},
        fetcher:async (url,options)=>{requests.push({url,cache:options.cache});return {ok:true,text:async()=>JSON.stringify(snapshot([row('a'),row('b')],'2026-10-22 21:01 HKT'))};},
        onPending:pending=>{document.querySelector('#apply').hidden=!pending;},
        onApply:incoming=>{applied++;document.querySelector('#articles').textContent=incoming.articles.map(a=>a.title).join(' / ');}});
      updates.begin(snapshot([row('a')],'2026-10-22 21:00 HKT'));
      document.querySelector('#apply').addEventListener('click',()=>void updates.apply());
    }''')
    page.locator('#query').fill('香港 蘋果')
    page.evaluate('window.scrollTo(0,500)')
    before = page.evaluate('scrollY')
    result = page.evaluate('updates.check()')
    assert result['status'] == 'pending'
    assert result['newCount'] == 1
    assert page.locator('#articles').inner_text() == '原有新聞'
    assert page.evaluate('document.activeElement.id') == 'query'
    assert page.evaluate('scrollY') == before
    assert page.locator('#query').input_value() == '香港 蘋果'
    assert page.locator('#filter').input_value() == 'saved'
    assert page.evaluate('applied') == 0
    page.locator('#apply').click()
    page.wait_for_function('applied===1', timeout=3000)
    assert '來源新聞 b' in page.locator('#articles').inner_text()
    assert page.locator('#query').input_value() == '香港 蘋果'
    assert page.locator('#filter').input_value() == 'saved'
    assert page.evaluate('scrollY') == before
    assert page.evaluate('requests') == [{'url': 'data/articles_index.json', 'cache': 'no-cache'}]
    page.close()
