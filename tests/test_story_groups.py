"""Explicit story groups preserve every source record without AI/network calls."""
import copy
import subprocess
from pathlib import Path

import pytest

from test_browser_smoke import browser, news_data, _route_data, _static_server
from test_frontend import _require_node

ROOT = Path(__file__).resolve().parents[1]
GROUPS = ROOT / 'docs/js/story-groups.js'


def _run(script):
    result = subprocess.run(
        [_require_node(), '-e', '''
const assert = require('node:assert/strict');
require('./docs/js/story-groups.js');
const api = globalThis.RssStoryGroups;
const articles = [
 {id:'a', title:'一號原稿',source:'來源甲',category:'新聞',topic:'同一廣泛話題'},
 {id:'b', title:'乙社獨有關鍵字',source:'來源乙',category:'國際',topic:'同一廣泛話題',duplicate_of:'a'},
 {id:'c', title:'丙社第三篇報道',source:'來源丙',category:'新聞',topic:'同一廣泛話題',duplicate_of:'b'},
 {id:'other',title:'另一件事件',source:'來源丁',category:'新聞',topic:'同一廣泛話題'},
];
''' + script],
        cwd=ROOT, capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stderr


def test_explicit_chain_preserves_every_source_and_does_not_group_broad_topic():
    _run('''
const before=JSON.stringify(articles);
const result=api.collapse(articles);
assert.equal(result.length,2);
assert.equal(result[0].id,'a');
assert.deepEqual(result[0].matched_members,['a','b','c']);
assert.deepEqual(result[0].all_members,['a','b','c']);
assert.deepEqual(result[0].matched_sources,['來源甲','來源乙','來源丙']);
assert.equal(result[0].matched_count,3);
assert.equal(result[0].all_count,3);
assert.deepEqual(result[1].all_members,['other']);
assert.equal(JSON.stringify(articles),before);
const html=api.detailsHtml(result[0]);
for(const row of articles.slice(0,3)) {
 assert(html.includes('article.html?id='+row.id)); assert(html.includes(row.title));
}
assert.equal(api.detailsHtml(result[1]),'');
''')


def test_filter_before_representative_keeps_nonroot_match_and_labels_other_members():
    _run('''
const selected={...articles[1],source:'來源乙 國際',metadata_source:'來源乙'};
const result=api.collapse([selected],articles);
assert.equal(result.length,1);
assert.equal(result[0].id,'b');
assert.deepEqual(result[0].matched_members,['b']);
assert.deepEqual(result[0].matched_sources,['來源乙 國際']);
assert.deepEqual(result[0].unmatched_members,['a','c']);
assert.equal(result[0].matched_count,1);
assert.equal(result[0].all_count,3);
const html=api.detailsHtml(result[0]);
assert(html.includes('目前條件 1 篇'));
assert(html.includes('其他 2 篇（不符合目前搜尋或篩選）'));
assert(html.includes('data-story-member="b" data-story-match="true"'));
assert(html.includes('data-story-member="a" data-story-match="false"'));
assert(html.indexOf('乙社獨有關鍵字')<html.indexOf('一號原稿'));
''')


def test_cyclic_dangling_self_and_overlong_chains_fail_open_without_lost_records():
    _run('''
const damaged=[{id:'x',duplicate_of:'y'},{id:'y',duplicate_of:'x'},
 {id:'intoCycle',duplicate_of:'x'}, {id:'self',duplicate_of:'self'},
 {id:'dangling',duplicate_of:'missing'}, {id:'intoDangling',duplicate_of:'dangling'}];
assert.equal(api.collapse(damaged).length,damaged.length);
const chain=Array.from({length:100},(_,i)=>({id:'n'+i,duplicate_of:i<99?'n'+(i+1):null}));
const groups=api.collapse(chain);
const memberIds=groups.flatMap(group=>group.matched_members);
assert.equal(memberIds.length,100); assert.equal(new Set(memberIds).size,100);
assert(groups.some(group=>group.id==='n0' && group.matched_count===1));
''')


def test_filtered_universe_does_not_restore_out_of_window_or_missing_articles():
    _run('''
// Root a is outside the caller's24h eligible universe. Missing provenance
// cannot silently reinsert it or suppress b/c as if a were displayed.
const result=api.collapse([articles[1],articles[2]],[articles[1],articles[2]]);
assert.equal(result.length,2);
assert(!result.some(group=>group.all_members.includes('a')));
const absent=api.collapse([articles[1]],[]);
assert.equal(absent[0].id,'b'); assert.equal(absent[0].all_count,1);
const repeat=api.collapse([articles[0],articles[0],articles[1]],articles);
assert.equal(repeat[0].matched_count,2);
''')


def test_reader_links_and_source_titles_are_escaped_and_have_no_external_recipient():
    _run('''
const malicious=[{id:'a',source:'<img src=x onerror=boom()>',title:'</a><script>boom()</script>'},
 {id:'b&quote="',source:'正常來源',title:'第二篇',duplicate_of:'a'}];
const html=api.detailsHtml(api.collapse(malicious)[0]);
assert(!html.includes('<script>')); assert(!html.includes('<img'));
assert(html.includes('&lt;script&gt;'));
assert(html.includes('article.html?id=b%26quote%3D%22'));
assert(!html.includes('href="http')); assert(!html.includes('href="javascript:'));
assert.equal(api.detailsHtml({group_articles:malicious,matched_members:[]}), '');
''')


def test_shared_search_delegates_to_groups_and_helper_failure_keeps_standalone_rows():
    result = subprocess.run([_require_node(), '-e', '''
const vm=require('node:vm'),fs=require('node:fs'),assert=require('node:assert/strict');
const common=fs.readFileSync('./docs/js/common.js','utf8');
const groups=fs.readFileSync('./docs/js/story-groups.js','utf8');
const data=[{id:'a',title:'普通原稿',source:'來源甲'},
 {id:'b',title:'獨有詞國際版本',source:'來源乙',duplicate_of:'a'}];
const context=vm.createContext({data});
vm.runInContext(common,context);
assert.equal(vm.runInContext('RssSearch.collapse(data).length',context),2);
vm.runInContext(groups,context);
assert.equal(vm.runInContext('RssSearch.collapse(data).length',context),1);
assert.equal(vm.runInContext("RssSearch.collapse(data.filter(a=>RssSearch.matches(a,RssSearch.tokens('獨有詞'))),data)[0].id",context),'b');
'''], cwd=ROOT, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize('width', [390, 1280])
def test_disclosure_keeps_all_reader_links_and_unmatching_members_explicit(browser, width):
    page = browser.new_page(viewport={'width':width,'height':900},service_workers='block')
    page.set_content('<article><a class="card" href="article.html?id=b">代表報道</a><div id="group"></div></article>')
    page.add_script_tag(path=str(GROUPS))
    page.evaluate('''() => {
const all=[{id:'a',source:'來源甲',title:'原稿'},
 {id:'b',source:'來源乙',title:'乙社匹配報道',duplicate_of:'a'},
 {id:'c',source:'來源丙',title:'第三篇',duplicate_of:'a'}];
document.querySelector('#group').innerHTML=RssStoryGroups.detailsHtml(RssStoryGroups.collapse([all[1]],all)[0]);
}''')
    assert page.locator('.story-group').locator('xpath=ancestor::a').count() == 0
    assert page.locator('[data-story-member="b"] a').get_attribute('href') == 'article.html?id=b'
    assert not page.locator('[data-story-member="b"]').is_visible()
    page.locator('.story-group > summary').click()
    assert page.locator('[data-story-member="b"]').is_visible()
    assert not page.locator('[data-story-member="a"]').is_visible()
    assert not page.locator('[data-story-member="c"]').is_visible()
    assert '不符合目前搜尋或篩選' in page.locator('.story-group-other > summary').inner_text()
    page.locator('.story-group-other > summary').click()
    for aid in ('a','b','c'):
        assert page.locator(f'[data-story-member="{aid}"]').is_visible()
        assert page.locator(f'[data-story-member="{aid}"] a').get_attribute('href') == f'article.html?id={aid}'
    page.close()


@pytest.mark.parametrize('width', [390, 1280])
def test_homepage_story_group_search_source_and_expansion(browser, news_data, width):
    template = copy.deepcopy(news_data['articles'][0])
    definitions = [
        ('a0', '甲社原始報道', '來源甲', '新聞', None),
        ('a1', '乙社獨有關鍵字國際版本', '來源乙', '國際', 'a0'),
        ('a2', '丙社第三篇報道', '來源丙', '新聞', 'a0'),
        ('a3', '同話題的另一件完全不同事件', '來源丁', '新聞', None),
    ]
    news_data['articles'] = [dict(copy.deepcopy(template), id=aid, title=title,
                                  source=source, category=category,
                                  topic='共同廣泛話題', duplicate_of=duplicate)
                             for aid, title, source, category, duplicate in definitions]
    news_data['sources'] = {source:{'count':1,'effective_count':1,'category':category}
                            for _, _, source, category, _ in definitions}
    with _static_server() as base:
        page = browser.new_page(viewport={'width':width,'height':900},service_workers='block')
        _route_data(page,news_data)
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(base+'/index.html')

        def results(records, cards):
            page.wait_for_function(
                '(expected)=>document.querySelector("#resultCount").textContent.includes(expected)',
                arg=f'{records} 篇匹配報道 · {cards} 張卡',
            )

        # Shared topic alone must not merge a3 into the explicit a0/a1/a2 group.
        results(4,2)
        assert page.locator('#leadStory > a, #feed a.card').count() == 2
        assert page.locator('#leadStory > a').get_attribute('href') == 'article.html?id=a0'
        assert page.locator('#feed a.card').get_attribute('href') == 'article.html?id=a3'

        if width == 390:
            page.locator('#mobileTabs [data-view="search"]').click()
        page.locator('#search').fill('乙社獨有關鍵字')
        # Hidden group members already contain this title before debounce.
        # Actual result totals, rather than broad textContent, prove filtering.
        results(1,1)
        host = page.locator('#searchResultList' if width == 390 else '#leadStory')
        assert host.locator(':scope > a').get_attribute('href') == 'article.html?id=a1'
        details = host.locator(':scope > details.story-group')
        assert details.count() == 1
        assert details.locator('xpath=ancestor::a').count() == 0
        assert details.locator('[data-story-member]').count() == 3
        details.locator(':scope > summary').click()
        assert details.locator('[data-story-member="a1"]').is_visible()
        assert not details.locator('[data-story-member="a0"]').is_visible()
        assert not details.locator('[data-story-member="a2"]').is_visible()
        details.locator('.story-group-other > summary').click()
        assert details.locator('.story-title').all_text_contents() == [definitions[i][1] for i in (1,0,2)]
        for aid in ('a0','a1','a2'):
            link = details.locator(f'[data-story-member="{aid}"] a')
            assert link.is_visible()
            assert link.get_attribute('href') == f'article.html?id={aid}'
        assert not details.locator('[data-story-member="a3"]').count()

        page.locator('#search').fill('')
        results(4,2)
        if width == 390:
            page.locator('#mobileTabs [data-view="home"]').click()
            page.locator('#mobileSubHome [data-time-mode="category"]').click()
            page.locator('#mobileSourceChips [data-mobile-source="來源乙"]').click()
        else:
            page.locator('#categoryNav [data-tree-category="全部"]').click()
            page.locator('#categoryNav [data-category="全部"][data-source="來源乙"]').click()
        results(1,1)
        lead = page.locator('#leadStory')
        assert lead.locator(':scope > a').get_attribute('href') == 'article.html?id=a1'
        assert lead.locator('h1').inner_text() == definitions[1][1]
        filtered = lead.locator(':scope > details.story-group')
        assert '目前條件 1 篇' in filtered.locator(':scope > summary').inner_text()
        filtered.locator(':scope > summary').click()
        assert '不符合目前搜尋或篩選' in filtered.locator('.story-group-other > summary').inner_text()
        assert filtered.locator('[data-story-match="true"]').count() == 1
        assert filtered.locator('[data-story-match="false"]').count() == 2
        filtered.locator('.story-group-other > summary').click()
        assert all(filtered.locator(f'[data-story-member="{aid}"] a').is_visible()
                   for aid in ('a0','a1','a2'))
        assert page.locator('a details.story-group, details.story-group a a').count() == 0
        assert not errors
        page.close()
