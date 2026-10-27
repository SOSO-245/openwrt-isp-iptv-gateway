const test = require('node:test');
const assert = require('node:assert/strict');
const model = require('../openwrt/data/www/iptv/discovery-model.js');
test('silent candidates are grouped without hiding existing failures or previous media', () => {
  const silent={change:'unverified',validation:'no_response'};
  assert.equal(model.matches(silent,'active'),false);
  assert.equal(model.matches(silent,'silent'),true);
  assert.equal(model.matches({...silent,change:'existing'},'active'),true);
  assert.equal(model.matches({...silent,last_success:123},'active'),true);
  assert.equal(model.matches({...silent,validation:'media',last_check:'no_response'},'active'),true);
  assert.equal(model.matches({...silent,validation:'media'},'active'),true);
  assert.equal(model.matches(silent,'all'),true);
});
test('latest failed check is not presented as valid media', () => {
  const row={change:'existing',validation:'media',last_check:'no_response'};
  assert.equal(model.matches(row,'media'),false);
  assert.equal(model.matches(row,'existing_failed'),true);
  assert.equal(model.matches(row,'failed'),true);
});
test('resume and restart are explicit and capability gated', () => {
  const data={capabilities:{audit_modes:['new','resume']},audit:{state:'paused',done:4,total:8}};
  assert.equal(model.scanState(data,'audit-new').disabled,false);
  assert.equal(model.scanState(data,'audit-resume').disabled,false);
  assert.equal(model.scanState({...data,audit:{state:'complete',done:8,total:8}},'audit-resume').disabled,true);
  assert.equal(model.scanState({...data,capabilities:{}},'audit-new').disabled,true);
});
test('busy, offline and storage states block scan entry points', () => {
  for(const status of [{job:{state:'running'}},{capture:{state:'sampling'}},{service_error:'offline'},{storage_error:'full'}]) {
    for(const mode of ['sample','verify','retry','audit-new','audit-resume']) assert.equal(model.scanState(status,mode).disabled,true);
  }
});
test('empty retry and unconfigured scan are disabled', () => {
  assert.equal(model.scanState({rows:[]},'retry').disabled,true);
  assert.equal(model.scanState({rows:[{validation:'no_response'}]},'retry').disabled,false);
  assert.equal(model.scanState({settings:{ranges:[]}},'scan').disabled,true);
});
test('workflow stages separate signal, name, repeat checks and final update review', () => {
  const row={change:'new_media',validation:'media',name:'Example',name_source:'stream',verified_count:2};
  for(const [fields,stage] of [[{},'await_update'],[{verified_count:1},'needs_verify'],
    [{name_source:'unknown'},'needs_name'],[{name:''},'needs_name'],[{name_source:'catalog'},'needs_name'],
    [{last_check:'no_response'},'needs_verify'],[{change:'possible_move'},'await_update'],
    [{change:'existing',last_check:'no_response'},'existing_failed'],[{change:'existing'},'existing']]) {
    assert.equal(model.workflowStage({...row,...fields}),stage);
    assert.equal(model.matches({...row,...fields},stage),true);
  }
  assert.equal(model.workflowStage({change:'unverified',validation:'no_response'}),'silent');
  assert.equal(model.workflowStage({change:'unverified',validation:'pending'}),'needs_verify');
  assert.equal(model.matches({...row,last_check:'inconclusive'},'await_update'),false);
});
test('unidentified filter excludes silent candidates and existing channels', () => {
  assert.equal(model.matches({change:'unverified',validation:'no_response',name_source:'unknown'},'unnamed'),false);
  assert.equal(model.matches({change:'new_media',validation:'media',name_source:'unknown'},'unnamed'),true);
  assert.equal(model.matches({change:'existing',validation:'media',name_source:'unknown'},'unnamed'),false);
});
test('successful additions remain visible after an unchanged update', () => {
  const changes=[{kind:'add',name:'Example B'},{kind:'replace',name:'Example A'}];
  const data={update:{state:'unchanged'},recent_updates:[{at:123,changes}]};
  assert.deepEqual(model.appliedUpdates(data),changes.map(c=>({at:123,...c})));
  assert.deepEqual(model.appliedUpdates({update:{last_applied:{at:123,changes}}}),model.appliedUpdates(data));
  assert.deepEqual(model.appliedUpdates({}),[]);
});
