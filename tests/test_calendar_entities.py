"""Offline regressions for calendar grouping and complete entity drilldown."""
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import build

ROOT = Path(__file__).resolve().parents[1]


def js_function(page, name):
    source = (ROOT / 'docs' / page).read_text(encoding="utf-8")
    start = source.index('    function ' + name + '(')
    end = source.find('\n    function ', start + 1)
    if end < 0:
        end = source.index('\n    // ── Load', start)
    return source[start:end]


def node_eval(source):
    result = subprocess.run(['node', '-e', source], capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


def test_graph_retains_all_node_and_edge_references_and_unique_entity_mentions():
    now = datetime(2026, 10, 12, tzinfo=timezone.utc)
    articles = [dict(id=f'a{i:02}', date=now.isoformat(), entities={
        'companies': ['蘋果', '蘋果'], 'people': ['人物']}) for i in range(35)]
    graph = build.build_knowledge_graph(articles, now=now)
    assert len(graph['nodes']) == 2
    assert all(node['count'] == 35 and len(node['articles']) == 35 for node in graph['nodes'])
    assert graph['edges'][0]['weight'] == len(graph['edges'][0]['articles']) == 35


def test_calendar_uses_real_month_and_future_window_caption():
    result = node_eval("const today = new Date('2026-10-12T00:00:00Z');\n" +
                       js_function('upcoming.html', 'bucketLabel') +
                       "console.log(JSON.stringify(['2026-10-30','2026-11-01','2026-11-09'].map(bucketLabel)));")
    assert result == ['本月', '之後', '之後']
    page = (ROOT / 'docs/upcoming.html').read_text(encoding="utf-8")
    assert '個未來活動 · 未來 ' in page
    assert '個未來活動 · 過去 ' not in page


def test_entity_extra_articles_are_keyboard_accessible_without_losing_links():
    prelude = "const TYPE_LABEL={companies:'機構'}; const esc=x=>String(x); const articlesIndex={}; "
    prelude += "for(let i=0;i<30;i++) articlesIndex['a'+i]={title:'Article '+i,source:'Test'};"
    result = node_eval(prelude + js_function('entities.html', 'entityCardHtml') +
                       "console.log(JSON.stringify(entityCardHtml({type:'companies',name:'蘋果',count:30,article_ids:Object.keys(articlesIndex)})));")
    assert result.count('href="article.html?id=') == 30
    assert '<details' in result and '<summary>查看其餘 25 篇相關文章</summary>' in result


def test_graph_panel_counts_and_links_share_current_window():
    result = node_eval("const TYPE_LABEL={companies:'機構'}; const fields={}; "
        "const document={getElementById:id=>fields[id]||(fields[id]={classList:{add(){}}})};"
        "const recentArticleIds=ids=>ids.filter(id=>id==='new');"
        "const renderArticleLinks=ids=>ids.join(','); const requestAnimationFrame=()=>{}; let cy=null;" +
        js_function('graph.html', 'showNodeDetail') +
        "showNodeDetail({data:()=>({etype:'companies',label:'蘋果',count:30,articles:['old','new']})});"
        "console.log(JSON.stringify([fields['sidebar-stat'].textContent,fields['sidebar-articles'].innerHTML]));")
    assert result == ['目前時段出現喺 1 篇文章', 'new']
