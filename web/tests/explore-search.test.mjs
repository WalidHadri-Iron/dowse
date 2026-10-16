import assert from 'node:assert/strict';
import test from 'node:test';
import { exploreAppearance, rankedStep, fitExplore, filterExplore, filterExploreAisles } from '../src/lib/explore-search.ts';

const collection = [
  {path:'C:\\Photos\\a.jpg', kind:'image', fileDate:'2025-07-12', aisle:3, x:4, y:2},
  {path:'C:\\Photos\\Trips\\b.jpg', kind:'image', fileDate:'2026-07-12', aisle:3, x:5, y:2},
  {path:'C:\\Photos-old\\c.jpg', kind:'image', fileDate:'2025-07-12', aisle:8, x:12, y:2},
  {path:'C:\\Documents\\notes.txt', kind:'doc', fileDate:'2025-07-13', aisle:8, x:13, y:2},
];
const folder = path => ({id:'folder',source:'manual',label:path});
const dateRange = (from, to, source = 'manual') => ({id:'date',source,from,to});

test('Explore hides hard-filter exclusions, unions folder scopes and preserves original coordinates', () => {
  assert.equal(filterExplore(collection, []), collection);
  const within = filterExplore(collection, [folder('c:/PHOTOS/')]);
  assert.deepEqual(within, collection.slice(0,2));
  assert.equal(within[0], collection[0]);
  assert.deepEqual(filterExplore(collection, [folder('C:/Photos'),folder('C:/Photos/Trips'),folder('C:/Documents')]),
    [collection[0],collection[1],collection[3]]);
  assert.deepEqual(filterExplore(collection, [folder('C:/Photos'),dateRange('2025-07-12','2025-07-12')]), [collection[0]]);
  assert.deepEqual(filterExplore(collection, [folder('C:/Empty')]), []);
});

test('Explore uses inclusive/open date bounds and query-inferred date/type restrictions', () => {
  assert.deepEqual(filterExplore(collection, [dateRange('', '2025-07-12')]), [collection[0],collection[2]]);
  assert.deepEqual(filterExplore(collection, [dateRange('2026-07-12', '')]), [collection[1]]);
  assert.deepEqual(filterExplore(collection, [dateRange('2025-01-01', '2025-12-31', 'query'),
    {id:'kind',source:'query',kinds:['doc']}]), [collection[3]]);
  assert.deepEqual(filterExplore([{path:'a',kind:'doc'}], [dateRange('2025-01-01', '')]), []);
});

test('Explore filters the full collection, not top search hits, and hides/recounts empty aisles', () => {
  const many = Array.from({length:125}, (_,i) => ({...collection[0],path:`C:/Photos/${i}.jpg`}));
  assert.equal(filterExplore(many, [folder('C:/Photos')]).length, 125);
  const aisles = [{index:3,count:2,label:'Photos',x:4}, {index:8,count:2,label:'Other',x:12}];
  assert.deepEqual(filterExploreAisles(aisles, [collection[0]]), [{...aisles[0],count:1}]);
  assert.deepEqual(filterExploreAisles(aisles, []), []);
  assert.equal(aisles[0].count, 2);
});

const found = new Map([['a', 0], ['b', 1], ['weak', 2]]);
const matches = new Set(['a', 'b']);

test('Explore sizes strong matches by score without enlarging weak/unclear candidates', () => {
  const a = exploreAppearance('a', found, matches, true, 10, 10, null);
  const b = exploreAppearance('b', found, matches, true, 5, 10, null);
  assert(a.size > b.size);
  assert(Math.abs(b.size ** 2 / a.size ** 2 - .5) < 1e-10);
  assert.equal(a.lift, b.lift); // perspective must not create a rank-based size bonus
  assert.equal(exploreAppearance('weak', found, matches, true, 9, 10, null).state, 'closest');
  assert.equal(exploreAppearance('weak', found, matches, true, 9, 10, null).size, 1);
  assert.equal(exploreAppearance('a', found, matches, false, 10, 10, null).size, 1);
  assert.equal(exploreAppearance('a', found, matches, false, 10, 10, null).state, 'closest');
});

test('nonmatches fade more than unselected matches, with no selection-based size distortion', () => {
  const a = exploreAppearance('a', found, matches, true, 10, 10, 'a');
  const b = exploreAppearance('b', found, matches, true, 5, 10, 'a');
  const other = exploreAppearance('other', found, matches, true, undefined, 10, 'a');
  assert(a.light > b.light && b.light > other.light);
  assert.equal(a.size, exploreAppearance('a', found, matches, true, 10, 10, 'b').size);
  assert(other.outline < b.outline);
  assert.equal(exploreAppearance('a', null, null, false, undefined, 0, null).light, 1);
  assert.equal(exploreAppearance('a', null, null, false, undefined, 0, null).size, 1);
  assert.equal(exploreAppearance('a', new Map(), matches, false, 10, 10, 'a').state, 'other');
});

test('rank navigation follows result order, respects endpoints and handles empty/unknown selections', () => {
  const paths = ['best', 'next', 'last'];
  assert.equal(rankedStep(paths, null, 1), 'best');
  assert.equal(rankedStep(paths, 'best', 1), 'next');
  assert.equal(rankedStep(paths, 'next', -1), 'best');
  assert.equal(rankedStep(paths, 'best', -1), 'best');
  assert.equal(rankedStep(paths, 'last', 1), 'last');
  assert.equal(rankedStep(paths, 'unknown', 1), 'best');
  assert.equal(rankedStep([], null, 1), null);
});

test('camera fits all scattered ranked results and grows appropriately in narrow viewports', () => {
  const points = [{x: -14, y: -6, size: 1.5}, {x: 12, y: 8, size: 1.1}, {x: 30, y: -8, size: .9}];
  const before = structuredClone(points);
  for (const aspect of [.65, 1.2, 2.4]) {
    const view = fitExplore(points, aspect, 34);
    const halfH = view.z * Math.tan(34 * Math.PI / 360);
    for (const p of points) {
      assert(Math.abs(p.x - view.x) + .84 * p.size / 2 < halfH * aspect);
      assert(Math.abs(p.y - view.y) + .62 * p.size / 2 < halfH);
    }
  }
  assert(fitExplore(points, .65, 34).z > fitExplore(points, 2.4, 34).z);
  assert.deepEqual(points, before); // camera moves, aisle coordinates never do
  assert.equal(fitExplore([], 1.2, 34), null);
  assert(Number.isFinite(fitExplore([{x:0,y:0}], 0, 34).z));
});
