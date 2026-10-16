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
