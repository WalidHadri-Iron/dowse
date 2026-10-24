// Exercise the real rune-compiled Search controller, not a second implementation of its state machine.
import assert from 'node:assert/strict';
import test from 'node:test';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { build } from 'esbuild';
import ts from 'typescript';
import { compileModule } from 'svelte/compiler';

globalThis.window = globalThis;
const root = fileURLToPath(new URL('..', import.meta.url));
const bundle = await build({
  stdin: { contents: `export {Search} from './src/lib/search.svelte.ts'; export {api} from './src/lib/api';
    export {effect_root,render_effect,flush} from 'svelte/internal/client';`, resolveDir: root },
  bundle: true, write: false, format: 'esm', platform: 'browser', conditions: ['browser'],
  define: { 'process.env.NODE_ENV': '"production"' },
  plugins: [{ name: 'controller-under-test', setup(b) {
    b.onResolve({filter: /(?:^|\/)api$/}, () => ({path:'api',namespace:'stub'}));
    b.onResolve({filter: /(?:^|\/)app\.svelte$/}, () => ({path:'app',namespace:'stub'}));
    b.onLoad({filter:/.*/,namespace:'stub'}, args => ({contents:args.path==='api'
      ? 'export const api={};' : 'export const dowse={state:null,error:null};',loader:'js'}));
    b.onLoad({filter:/search\.svelte\.ts$/},async args => {
      const source = ts.transpileModule(await readFile(args.path,'utf8'), {compilerOptions:{target:ts.ScriptTarget.ESNext,module:ts.ModuleKind.ESNext}}).outputText;
      return {contents:compileModule(source,{filename:args.path,generate:'client',dev:false}).js.code,loader:'js'};
    });
  }}],
});
const {Search,api,effect_root,render_effect,flush} = await import('data:text/javascript;base64,'+Buffer.from(bundle.outputFiles[0].text).toString('base64'));
const pause = ms => new Promise(resolve=>setTimeout(resolve,ms));
const hit = (i,strong=true) => ({path:`C:/Files/${i}.txt`,strong,kind:'doc',score:10-i});
function fixture() {
  Object.assign(api,{history:async()=>({recent:[],queries:[]}),browse:async()=>({hits:[hit(0)],total:1,offset:0,hasMore:false}),
    search:async(q)=>({query:q,hits:[hit(0)],strong:1,terms:[]})});
  let s;
  const dispose = effect_root(()=>{s=new Search();render_effect(()=>{void s.hits;void s.current;});});
  return {s,dispose};
}

test('400-row rendering shares one candidate/result array, including selection and query editing', () => {
  const {s,dispose}=fixture();
  try {
    s.recent=Array.from({length:400},(_,i)=>hit(i));flush();
    const collection=s.hits;
    for(let i=0;i<1000;i++) assert.equal(s.hits,collection);
    s.selected=200;flush();assert.equal(s.hits,collection);assert.equal(s.current.path,hit(200).path);
    s.query='first';s.answer={query:'first',hits:[hit(0)],strong:1};flush();const results=s.hits;
    s.query='firs';flush();assert.equal(s.hits,results);
    s.query='';flush();assert.equal(s.hits.length,400);
  } finally {dispose();}
});

test('identical scheduling does not duplicate collection/history requests',async()=>{
  const {s,dispose}=fixture();let history=0,browse=0;
  api.history=async()=>{history++;return {recent:[],queries:[]}};
  api.browse=async()=>{browse++;return {hits:[hit(0)],total:1,offset:0,hasMore:false}};
  try {s.schedule();s.schedule();await pause(5);assert.equal(history,1);assert.equal(browse,1);}
  finally {dispose();}
});

test('rapid typing debounces both search and history; clearing reuses the loaded collection',async()=>{
  const {s,dispose}=fixture();const queries=[],history=[];let browse=0;
  api.search=async q=>{queries.push(q);return {query:q,hits:[hit(0)],strong:1}};
  api.history=async q=>{history.push(q);return {recent:[],queries:[]}};
  api.browse=async()=>{browse++;return {hits:[hit(0)],total:1,offset:0,hasMore:false}};
  try {
    await s.loadRecent();
    for(const q of ['s','su','sun']) {s.query=q;s.schedule(30);await pause(2);}
    assert.equal(history.length,0);await pause(50);assert.deepEqual(queries,['sun']);assert.deepEqual(history,['sun']);
    s.query='';s.schedule(0);await pause(5);assert.equal(browse,1);assert.equal(s.hits.length,1);
    await s.loadBrowse();assert.equal(browse,2); // explicit index refresh bypasses the cache
  } finally {dispose();}
});

test('query edits retain a pending collection load, but scope changes abort it',async()=>{
  const {s,dispose}=fixture();let signal,finish;
  api.browse=(within,kinds,date,offset,limit,asked)=>{signal=asked;return new Promise(resolve=>{finish=resolve})};
  try {
    const pending=s.loadRecent();
    s.query='sun';s.schedule(5);assert.equal(signal.aborted,false);
    s.query='su';s.schedule(5);assert.equal(signal.aborted,false);
    s.kinds=['doc'];s.schedule(5);assert.equal(signal.aborted,true);
    finish({hits:[hit(9)],total:1,offset:0,hasMore:false});await pending;
    assert.equal(s.recent.length,0);
    await pause(10);
  } finally {dispose();}
});

test('an old search response cannot undo a cleared or newer query',async()=>{
  const {s,dispose}=fixture();let finish;
  api.search=()=>new Promise(resolve=>{finish=resolve});
  try {
    s.query='sun';const pending=s.run();s.query='';s.schedule(0);
    finish({query:'sun',hits:[hit(9)],strong:1});await pending;
    assert.equal(s.answer,null);assert.equal(s.busy,false);await pause(5);
  } finally {dispose();}
});

// Issue #1: while files are being read, the index changes every second or two. The results on screen must not be
// swapped (and the gallery reshuffled) each time: refreshes coalesce, go out as background requests, change nothing
// when the files are the same, and are otherwise offered until the person asks for them.
test('index changes coalesce into one background refresh; the same files change nothing on screen', async () => {
  const {s,dispose}=fixture();const calls=[];
  api.search=async(...args)=>{calls.push(args);return {query:args[0],hits:[hit(0),hit(1)],strong:2,terms:[]};};
  try {
    s.query='photo';s.schedule(0);await pause(5);flush();
    const shown=s.answer;assert.equal(calls.length,1);
    for(let i=0;i<10;i++) s.indexChanged();
    await pause(20);flush();
    assert.equal(calls.length,2,'ten index changes in a burst: one refresh');
    assert.equal(calls[1][6],true,'the refresh says it is a background request');
    assert.equal(s.answer,shown,'same files in the same order: the answer on screen is kept');
    assert.equal(s.pending,null);
    s.indexChanged();await pause(20);
    assert.equal(calls.length,2,'another change within the refresh interval waits');
  } finally {dispose();}
});

test('fresher results are offered, not swapped in; Show applies them and keeps the selection', async () => {
  const {s,dispose}=fixture();let answerHits=[hit(0),hit(1),hit(2)];
  api.search=async(q)=>({query:q,hits:answerHits,strong:answerHits.length,terms:[]});
  try {
    s.query='photo';s.schedule(0);await pause(5);flush();
    s.selected=1;flush();
    const shown=s.answer;
    answerHits=[hit(5),hit(1),hit(0),hit(2)];
    s.indexChanged();await pause(20);flush();
    assert.equal(s.answer,shown,'what is shown stays put');
    assert.ok(s.pending,'the fresher results are offered');
    s.showPending();flush();
    assert.equal(s.pending,null);
    assert.deepEqual(s.answer.hits.map((h)=>h.path),answerHits.map((h)=>h.path));
    assert.equal(s.current.path,hit(1).path,'the selected file stays selected');
  } finally {dispose();}
});

test('a new search drops a stale offer; an empty result takes fresh results straight away', async () => {
  const {s,dispose}=fixture();let answerHits=[];
  api.search=async(q)=>({query:q,hits:answerHits,strong:answerHits.length,terms:[]});
  try {
    s.query='photo';s.schedule(0);await pause(5);flush();
    assert.equal(s.answer.hits.length,0);
    answerHits=[hit(0)];
    s.indexChanged();await pause(20);flush();
    assert.equal(s.pending,null,'nothing was shown: nothing to keep in place');
    assert.equal(s.answer.hits.length,1);
    answerHits=[hit(3),hit(0)];
    await pause(5100);   // the next refresh, after the interval
    s.indexChanged();await pause(20);flush();
    assert.ok(s.pending);
    s.query='photos';s.schedule(0);await pause(5);flush();
    assert.equal(s.pending,null,'typing starts a search of its own: the old offer is gone');
  } finally {dispose();}
});

test('the collection is reloaded after indexing only when its files changed', async () => {
  const {s,dispose}=fixture();let pages=0;const flags=[];
  api.browse=async(...args)=>{pages++;flags.push(args[6]);return {hits:[hit(0),hit(1)],total:2,offset:0,hasMore:false};};
  try {
    s.schedule();await pause(5);flush();
    const shown=s.recent;
    s.indexChanged();await pause(20);flush();
    assert.equal(pages,2);
    assert.equal(flags[1],true,'a background request');
    assert.equal(s.recent,shown,'the same files: the collection on screen is kept');
  } finally {dispose();}
});
