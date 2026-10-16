import assert from 'node:assert/strict';
import test from 'node:test';
import { preparationBusy, preparationLabel } from '../src/lib/model-preparation.ts';

test('the processor completion event cannot re-lock controls, even without downloads', () => {
  const stages = ['validate','activate','done'];
  assert.deepEqual(stages.map(stage => preparationBusy({state:stage==='done'?'done':'running',stage,done:0,total:0})),
    [true,true,false]);
  assert.equal(preparationBusy({state:'done',done:100,total:100}),false);
});

test('all terminal states permit retry, and HTTP requests lock only while pending', () => {
  assert.equal(preparationBusy(null),false);
  assert.equal(preparationBusy(null,true),true);
  for (const state of ['done','failed','cancelled']) {
    assert.equal(preparationBusy({state}),false);
    assert.equal(preparationBusy({state},true),true);
  }
  assert.equal(preparationBusy({state:'running'}),true);
});

test('device switches have their own progress/success/failure/cancel labels, not model changes', () => {
  for (const stage of ['model','validate','activate','done','failed','cancelled','unknown']) {
    assert(preparationLabel({kind:'processor',stage}).toLowerCase().includes('reading'));
  }
  assert.equal(preparationLabel({kind:'processor',stage:'done'}),'Reading device ready');
  assert.equal(preparationLabel({kind:'model',stage:'done'}),'Model ready');
  assert.equal(preparationLabel({kind:'setup',stage:'cancelled'}),'Preparation cancelled');
});
