"""Offline recovery/source fixtures; all inference and network are forbidden."""
import asyncio
import copy
import hashlib
import json

import pytest
from bs4 import BeautifulSoup
from src import analyse, scrape
from src.analysis_input import input_hash
from src.article_evidence import validate_summary_evidence
from src.source_content import classify_content, text_status, RTHK_DESCRIPTION
from src.summary_recovery import recovery_action, apply_action, complete_metadata
from src.index_projection import project_article
from tools.plan_summary_recovery import load_body, plan
from tools.validate_publication import validate_site
from test_format_diagnostics import complete, article


def rich(status='partial'):
    row = article()
    row['content_hash'] = hashlib.sha256(row['content'].encode('utf-8')).hexdigest()
    generated = analyse._parse_batch(json.dumps([complete()]),1,require_rich=True)[0]
    row.update(generated, analysis_version=analyse.ANALYSIS_VERSION,
               analysis_input_hash=input_hash(row,analyse.ANALYSIS_VERSION), summary_status=status)
    row['summary_evidence'] = validate_summary_evidence(row,row['summary_evidence'])
    return row


def write_snapshot(docs, row):
    data=docs/'data'; (data/'content').mkdir(parents=True,exist_ok=True)
    (data/'articles.json').write_text(json.dumps(dict(schema_version=2,articles=[row])),encoding='utf-8')
    (data/'articles_index.json').write_text(json.dumps(dict(schema_version=2,articles=[project_article(row)])),encoding='utf-8')
    (data/'content'/f"{row['id']}.json").write_text(json.dumps(dict(version=1,content=row['content'],content_hash=row['content_hash'],quality=row['content_quality'])),encoding='utf-8')


def test_complete_label_requires_full_shape_real_input_and_source():
    row=rich(); saved=copy.deepcopy(row)
    corrected=complete_metadata(row,row)
    assert corrected['summary_status']=='complete' and row==saved
    for status in ['limited','complete','legacy']:
        assert complete_metadata(dict(row,summary_status=status),row) is None
    for field,value in [('analysis_input_hash','bad'),('summary_evidence',[]),('summary_points',row['summary_points'][:4])]:
        assert complete_metadata(dict(row,**{field:value}),row) is None


@pytest.mark.parametrize('fault',['identity','input','old-body','current-body','old-quote','old-version'])
def test_historical_reuse_rejects_unbound_or_corrupt_results(fault):
    now=rich(); prior=rich('complete')
    if fault=='identity': prior['id']='another-id'
    if fault=='input': now['title']='User edited title'
    if fault=='old-body': prior['content']+='corrupt'
    if fault=='current-body': now['content']+='corrupt'
    if fault=='old-quote': prior['summary_evidence'][0]['quotes'][0]='完全不在來源原文內的內容。'
    if fault=='old-version': prior['analysis_version']='made-up'
    assert recovery_action(now,prior) is None


def test_reviewed_restore_preserves_media_other_edits_and_reconciles_cache_publication(tmp_path):
    now=rich(); prior=rich('complete')
    now['summary_points']=now['summary_points'][:2]
    now['summary_evidence']=now['summary_evidence'][:2]
    now['summary']='\n'.join('・'+''.join(p) for p in now['summary_points'])
    now['score']=9; now['custom_note']='manual value'; body=now['content']
    action=recovery_action(now,prior)
    assert action and action['kind']=='historical-full'
    cache={now['id']:{'score':9,'custom_cache_note':'keep'}}
    apply_action(now,cache,action)
    assert now['content']==body and now['score']==9 and now['custom_note']=='manual value'
    assert cache[now['id']]['custom_cache_note']=='keep' and cache[now['id']]['summary']==now['summary']
    assert not analyse._needs_full_analysis(cache[now['id']],now)
    write_snapshot(tmp_path/'docs',now); validate_site(tmp_path/'docs',require_current=True)
    assert recovery_action(now,prior) is None


@pytest.mark.parametrize('edit',['summary','title','content','score','custom_note'])
def test_recovery_plan_cannot_overwrite_later_manual_changes(edit):
    now=rich(); action=recovery_action(now,rich('complete'))
    now[edit]=str(now.get(edit,''))+' edited'
    with pytest.raises(ValueError,match='changed'): apply_action(now,{},action)


def test_complete_label_runtime_is_persisted_without_an_inference_call(monkeypatch,tmp_path):
    row=rich(); cachefile=tmp_path/'cache.json'
    cached={**row,'input_hash':row['analysis_input_hash'],'version':row['analysis_version']}
    cachefile.write_text(json.dumps({row['id']:cached}),encoding='utf-8')
    monkeypatch.setattr(analyse,'CACHE_PATH',cachefile)
    monkeypatch.setattr(analyse,'MINIMAX_API_KEY','offline-test-only')
    async def forbidden(*args,**kwargs): pytest.fail('paid request forbidden')
    monkeypatch.setattr(analyse,'_analyse_batch',forbidden)
    oldtext=row['summary']; asyncio.run(analyse.analyse_all([row]))
    assert row['summary_status']=='complete' and row['summary']==oldtext
    assert json.loads(cachefile.read_text(encoding='utf-8'))[row['id']]['summary_status']=='complete'


@pytest.mark.parametrize('content,url,fallback,status',[
    ('<img src="https://example.com/a.jpg">','https://example.com/a','none','image-only'),
    ('<p>Only title</p>','https://example.com/a','minimal','title-only'),
    ('<p>Only title</p>','https://example.com/a','none','title-only'),
    ('<p>'+RTHK_DESCRIPTION+'</p>','https://news.rthk.hk/rthk/ch/component/k2/1.htm','none','boilerplate'),
    ('<p>This Press release has been killed</p>','https://www.am730.com.hk/column/1/deleted','none','withdrawn'),
    ('<p>A report quotes This Press release has been killed and discusses its history.</p>','https://www.am730.com.hk/1','none','available'),
    ('<p>'+RTHK_DESCRIPTION+'</p>','https://example.com/a','none','available'),
])
def test_source_states_are_conservative_and_do_not_change_body(content,url,fallback,status):
    row=dict(id='test',title='Only title',url=url,content=content,content_quality={'fallback':fallback})
    assert classify_content(row)==status and row['content']==content
    if status!='available': assert row['content_quality']['completeness']=='insufficient'


def test_newmobilelife_preserves_editorial_blocks_and_captions_without_widgets():
    html='''<div class="entry-content"><div class="wprt-container"><p class="wp-block-paragraph">首段正文。</p>
    <figure><img src="/one.jpg"><noscript><img src="/one.jpg"></noscript><figcaption>原有圖說</figcaption></figure>
    <div class="featured-posts"><p>推薦廣告</p></div><h2>第二節</h2><p>後段正文。</p>
    <div class="related-posts">相關文章</div></div></div>'''
    body,_=scrape._process_html_sync(html,'https://www.newmobilelife.com/story',False)
    assert '推薦廣告' not in body and '相關文章' not in body
    assert body.index('首段正文')<body.index('one.jpg')<body.index('原有圖說')<body.index('後段正文')
    assert len(BeautifulSoup(body,'html.parser').find_all('img'))==1


def test_newmobilelife_noscript_only_figure_is_preserved():
    body,_=scrape._process_html_sync('<div class="entry-content"><p>正文。</p><figure><noscript><img src="/one.jpg"></noscript><figcaption>圖說</figcaption></figure></div>','https://www.newmobilelife.com/story',False)
    assert 'one.jpg' in body and '圖說' in body


def test_image_only_old_body_gets_bounded_scrape_but_withdrawal_does_not(monkeypatch):
    calls=[]
    async def fetch(*args): calls.append(1); return '<div class="entry-content"><p>真正擷取到的原站正文。</p></div>'
    monkeypatch.setattr(scrape,'_fetch_html',fetch)
    row=dict(id='image',title='Title',source='New MobileLife',url='https://www.newmobilelife.com/story',content='<img src="https://example.com/a.jpg">',content_quality={'fallback':'none'})
    row['content_hash']=hashlib.sha256(row['content'].encode()).hexdigest()
    result=asyncio.run(scrape._scrape_one(None,row,asyncio.Semaphore(1)))
    assert calls==[1] and text_status(result)=='available' and '真正擷取' in result['content']
    row.update(url='https://www.am730.com.hk/deleted',content='<p>This Press release has been killed</p>')
    row['content_hash']=hashlib.sha256(row['content'].encode()).hexdigest()
    calls.clear(); asyncio.run(scrape._scrape_one(None,row,asyncio.Semaphore(1)))
    assert not calls and text_status(row)=='withdrawn'


def test_boilerplate_and_withdrawal_never_admit_paid_analysis(monkeypatch,tmp_path):
    row=rich('limited'); row.update(content='<p>'+RTHK_DESCRIPTION+'</p>',url='https://news.rthk.hk/story')
    for key in ['summary','summary_points','summary_evidence']: row[key]='' if key=='summary' else []
    row['content_hash']=hashlib.sha256(row['content'].encode()).hexdigest()
    row['analysis_input_hash']=input_hash(row,row['analysis_version'])
    cachefile=tmp_path/'cache.json'
    cached={**row,'input_hash':row['analysis_input_hash'],'version':row['analysis_version']}
    cachefile.write_text(json.dumps({row['id']:cached}),encoding='utf-8')
    monkeypatch.setattr(analyse,'CACHE_PATH',cachefile); monkeypatch.setattr(analyse,'MINIMAX_API_KEY','offline-test-only')
    async def forbidden(*args,**kwargs): pytest.fail('paid analysis forbidden')
    monkeypatch.setattr(analyse,'_analyse_batch',forbidden)
    asyncio.run(analyse.analyse_all([row]))
    assert row['content_quality']['text_status']=='boilerplate' and row['summary_status']=='limited'
    assert json.loads(cachefile.read_text(encoding='utf-8'))[row['id']]['summary_status']=='limited'


def test_offline_planner_rechecks_sidecar_hash_and_does_not_write_inputs(tmp_path):
    row=rich(); row['id']='a'*12
    for item in row['summary_evidence']: item['article_id']=row['id']
    docs=tmp_path/'snapshot/docs'; write_snapshot(docs,row)
    before=(docs/'data/articles.json').read_bytes()
    report=plan(docs,[],set())
    assert len(report['actions'])==1 and report['actions'][0]['kind']=='completeness-label'
    assert (docs/'data/articles.json').read_bytes()==before
    sidecar=docs/'data/content'/f"{row['id']}.json"
    data=json.loads(sidecar.read_text(encoding='utf-8')); data['content_hash']='0'*64
    sidecar.write_text(json.dumps(data),encoding='utf-8')
    with pytest.raises(ValueError,match='provenance'): load_body(docs,row)


def test_source_status_update_keeps_body_and_original_observation_time(monkeypatch,tmp_path):
    import build
    monkeypatch.setattr(build,'DATA_DIR',tmp_path)
    monkeypatch.setattr(build,'CONTENT_DIR',tmp_path)
    row=dict(id='image-only',title='Original title',source='Synthetic',url='https://example.com/a',
             thumbnail='https://example.com/hero.jpg',content='<img src="https://example.com/a.jpg">',content_quality={'fallback':'none'})
    old={'version':1,'content':row['content'],'quality':row['content_quality'],
         'scraped_at':'2026-10-23T12:00:00Z'}
    path=tmp_path/'image-only.json';path.write_text(json.dumps(old),encoding='utf-8')
    build._write_content_sidecars([row])
    result=json.loads(path.read_text(encoding='utf-8'))
    assert result['quality']['text_status']=='image-only'
    assert result['content']==old['content'] and result['scraped_at']==old['scraped_at']
    assert result['content_updated_at'] is None
