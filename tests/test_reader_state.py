"""Reader state uses device storage only; no backend or live article fixtures."""
import json
import subprocess
from pathlib import Path

from test_frontend import _require_node

ROOT = Path(__file__).resolve().parents[1]


def _run(script):
    source = (ROOT / 'docs/js/reader-state.js').read_text(encoding="utf-8")
    result = subprocess.run([_require_node(), '-e', '''
      const vm=require('node:vm'); const assert=require('node:assert/strict');
      const source=''' + json.dumps(source) + ''';
      function create(storage){
        const events=[];
        const context={localStorage:storage,window:{dispatchEvent:e=>events.push(e)},
          CustomEvent:function(type,options){this.type=type;this.detail=options.detail;}};
        vm.createContext(context);vm.runInContext(source,context);
        return {state:context.window.RssReaderState,events};
      }
    ''' + script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_local_state_persists_status_position_queue_and_filters():
    _run('''
      const data=new Map();const storage={getItem:k=>data.get(k)??null,setItem:(k,v)=>data.set(k,v)};
      const first=create(storage);
      first.state.saveLater('a'); first.state.markRead('b');
      first.state.set('a',{position:.42,tts:{mode:'summary',index:4}});
      assert.equal(first.state.filter([{id:'a'},{id:'b'}],'unread')[0].id,'a');
      assert.equal(first.state.filter([{id:'a'},{id:'b'}],'saved')[0].id,'a');
      assert.equal(first.state.filter([{id:'a'},{id:'b'}],'read')[0].id,'b');
      const second=create(storage).state;
      assert.equal(second.articleState('a').position,.42);
      assert.equal(second.articleState('a').tts.index,4);
      assert.equal(second.articleState('a').tts.mode,'summary');
      assert.equal(first.events.at(-1).detail.statusChanged,false);
      second.saveLater('a',false); assert.equal(second.articleState('a').saved,false);
      first.state.refresh(); assert.equal(first.state.articleState('a').saved,false);
      assert.equal(first.events.at(-1).detail.statusChanged,true);
    ''')


def test_denied_storage_keeps_session_state_and_preferences():
    _run('''
      const storage={getItem(){throw Error('blocked')},setItem(){throw Error('blocked')}};
      const {state}=create(storage);
      state.saveLater('a');state.markRead('a');state.set('a',{position:.7});
      state.setPreference('rss_font_size','small');
      assert.equal(state.preference('rss_font_size'),'small');
      assert.equal(state.articleState('a').saved,true);
      assert.equal(state.articleState('a').read,true);
      assert.equal(state.articleState('a').position,.7);
      assert.equal(state.isPersistent(),false);
      assert.equal(create(storage).state.articleState('a').saved,false);
    ''')


def test_slug_identity_alias_keeps_saved_read_and_position_without_deleting_old_state():
    _run('''
      const data=new Map();const storage={getItem:k=>data.get(k)??null,setItem:(k,v)=>data.set(k,v)};
      const {state}=create(storage);
      state.saveLater('old-url-id');state.markRead('old-url-id');state.set('old-url-id',{position:.64});
      state.saveLater('unrelated');
      const before=data.get('rss_reader_state_v1');
      state.registerAliases([{id:'stable-id',id_aliases:['old-url-id']}]);
      assert.equal(data.get('rss_reader_state_v1'),before);
      assert.equal(state.articleState('stable-id').saved,true);
      assert.equal(state.articleState('stable-id').read,true);
      assert.equal(state.articleState('stable-id').position,.64);
      state.saveLater('stable-id',false);
      assert.equal(state.articleState('old-url-id').saved,false);
      assert.equal(state.articleState('unrelated').saved,true);
      assert.ok(JSON.parse(data.get('rss_reader_state_v1'))['old-url-id']);
    ''')


def test_corrupt_and_unbounded_state_is_normalized_and_capped():
    _run('''
      let raw='invalid json';const storage={getItem:()=>raw,setItem:(k,v)=>raw=v};
      const {state}=create(storage);
      assert.equal(state.articleState('none').position,0);
      state.set('__proto__',{read:true,position:99,tts:{mode:'evil',index:-10}});
      assert.equal(state.articleState('__proto__').position,1);
      assert.equal(state.articleState('__proto__').tts.mode,'full');
      assert.equal(state.articleState('__proto__').tts.index,0);
      for(let i=0;i<505;i++)state.saveLater('item'+i);
      assert.equal(Object.keys(JSON.parse(raw)).length,500);
      assert.equal(state.articleState('item504').saved,true);
    ''')
