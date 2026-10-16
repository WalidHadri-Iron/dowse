import assert from 'node:assert/strict';
import test from 'node:test';
import { folderFloor, DOWSED_FILES } from '../src/lib/explore-folders.ts';
import { filterExplore, filterExploreAisles } from '../src/lib/explore-search.ts';

const piece = path => ({path,kind:'doc',name:path.replaceAll('\\','/').split('/').at(-1),folder:'Original folder',
  visual:false,fileDate:'2025-07-12',aisle:7,x:23,y:42});
const files = [piece('C:\\Photos\\a.txt'),piece('C:\\Photos\\Trips\\b.txt'),piece('C:/Photos/Trips/c.txt'),
  piece('C:/Documents/d.txt'),piece('D:/Other/e.txt'),piece('C:/Photos-old/f.txt')];
const roots = ['C:/Photos', 'C:/Documents'];

test('folder mode groups by actual parent, with one Dowsed files group outside watched roots', () => {
  const map = folderFloor(files, roots);
  assert.equal(map.pieces.length, files.length);
  assert.deepEqual(map.aisles.map(a=>[a.label,a.count]), [[DOWSED_FILES,2],['Documents',1],['Photos',1],['Trips',2]]);
  const loose = map.aisles.find(a=>a.id==='dowsed-files');
  assert.deepEqual(map.pieces.filter(p=>p.aisle===loose.index).map(p=>p.path), [files[5].path,files[4].path]);
  assert(map.aisles.find(a=>a.label==='Trips').about.endsWith('Photos/Trips'));
});

test('grouping is deterministic, read-only, Windows-aware and independent of search rank', () => {
  const before = structuredClone(files);
  const map = folderFloor(files, roots);
  assert.deepEqual(folderFloor([...files].reverse(), ['c:\\PHOTOS\\','c:\\documents']), map);
  assert.deepEqual(files,before);
  for (const p of map.pieces) {
    const original = files.find(f=>f.path===p.path);
    assert.equal(p.name,original.name);
    assert.equal(p.folder,original.folder);
    assert.equal(p.fileDate,original.fileDate);
    assert.notEqual(p,original);
  }
});

test('Dowsed files includes every individually added file, even inside a chosen folder, without duplicates', () => {
  const named = ['c:/PHOTOS/a.txt', files[4].path, files[5].path];
  const map = folderFloor(files, roots, named);
  const dowsed = map.aisles.find(a=>a.id==='dowsed-files');
  assert.equal(dowsed.count,3);
  assert.deepEqual(new Set(map.pieces.filter(p=>p.aisle===dowsed.index).map(p=>p.path)),
    new Set([files[0].path,files[4].path,files[5].path]));
  assert.equal(new Set(map.pieces.map(p=>p.path)).size,files.length);
  assert.equal(map.pieces.length,files.length);
  assert(!map.aisles.some(a=>a.label==='Photos'));
  // Physical folder filtering still includes named files in that directory.
  const filtered = filterExplore(map.pieces,[{id:'folder',source:'manual',label:'C:/Photos'}]);
  assert.equal(filtered.length,3);
  assert(filtered.some(p=>p.path===files[0].path && p.aisle===dowsed.index));
  assert(dowsed.about.includes('added one by one'));
});

test('separate identically named folders remain distinguishable, including identical ancestor names', () => {
  const map = folderFloor([piece('C:/Work/Notes/a.txt'),piece('D:/Work/Notes/b.txt'),
    piece('C:/Dowsed files/c.txt'),piece('E:/Loose/d.txt')],['C:/Work','D:/Work','C:/Dowsed files']);
  assert.equal(new Set(map.aisles.map(a=>a.id)).size,4);
  assert.equal(new Set(map.aisles.map(a=>a.label)).size,4);
  assert(map.aisles.some(a=>a.label==='C: / Work / Notes'));
  assert(map.aisles.some(a=>a.label==='D: / Work / Notes'));
});

test('folder and date filters hide exclusions without moving remaining folder cases', () => {
  const map = folderFloor(files, roots);
  const filtered = filterExplore(map.pieces,[{id:'folder',source:'manual',label:'C:/Photos/Trips'},
    {id:'date',source:'manual',from:'2025-07-12',to:'2025-07-12'}]);
  assert.equal(filtered.length,2);
  assert(filtered.every(p=>map.pieces.includes(p)));
  assert.deepEqual(filterExploreAisles(map.aisles,filtered).map(a=>a.label),['Trips']);
  assert.deepEqual(folderFloor(files, roots),map);
});

test('folder layout accommodates score-sized cases and packs many groups in two dimensions', () => {
  const many = Array.from({length:160},(_,i)=>piece(`C:/Collection/Folder ${i%20}/${i}.txt`));
  const map = folderFloor(many,['C:/Collection']);
  assert.equal(new Set(map.pieces.map(p=>p.path)).size,160);
  for (const p of map.pieces) {
    assert(Number.isFinite(p.x) && Number.isFinite(p.y));
    for (const q of map.pieces) {
      if (p===q) continue;
      assert(Math.abs(p.x-q.x)>=.84*1.5 || Math.abs(p.y-q.y)>=.62*1.5,'largest cases overlap');
    }
  }
  assert(new Set(map.aisles.map(a=>a.x)).size>1);
  assert(new Set(map.aisles.map(a=>a.y)).size>1);
});

test('empty, root-level, UNC and only-individual collections remain usable', () => {
  assert.deepEqual(folderFloor([],[]),{pieces:[],aisles:[]});
  assert.equal(folderFloor(files,[]).aisles[0].label,DOWSED_FILES);
  const map = folderFloor([piece('C:\\a.txt'),piece('\\\\server\\share\\b.txt'),piece('/c.txt')],['C:/','//server/share','/']);
  assert.equal(map.aisles.length,3);
  assert(map.aisles.some(a=>a.label==='C:'));
  assert(map.aisles.some(a=>a.label==='share'));
  assert(map.aisles.some(a=>a.label==='/'));
});
