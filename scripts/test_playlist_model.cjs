const {test}=require('node:test');
const assert=require('node:assert/strict');
const model=require('../openwrt/data/www/iptv/playlist-model.js');
const source='#EXTM3U url-tvg="https://example.com/epg.xml"\n#EXTINF:-1 tvg-id="kept" group-title="News, HD" tvg-logo="/logos/one.png" catchup="append",News, One\n#EXTVLCOPT:http-user-agent=Example\nhttp://192.0.2.1/one\n# comment\n#EXTINF:-1,Two\nrtp://239.255.42.2:8000\n';
const fields={name:'Changed',group:'News',logo:'/logos/two.png',url:'http://192.0.2.1/new'};
test('parse quoted commas and names without rewriting source',()=>{
  const {lines,rows}=model.parse(source);
  assert.equal(lines.join('\n'),source);assert.equal(rows.length,2);
  assert.equal(rows[0].group,'News, HD');assert.equal(rows[0].name,'News, One');
});
test('edit preserves unknown attributes, tags, order and other rows',()=>{
  const result=model.edit(source,0,fields);
  assert.ok(result.includes('tvg-id="kept"'));assert.ok(result.includes('catchup="append"'));
  assert.ok(result.includes('#EXTVLCOPT:http-user-agent=Example\n'+fields.url));
  assert.ok(result.endsWith('# comment\n#EXTINF:-1,Two\nrtp://239.255.42.2:8000\n'));
  assert.deepEqual(model.parse(result).rows.map(r=>r.name),['Changed','Two']);
});
test('append creates one new row without changing existing bytes',()=>{
  const result=model.edit(source,2,fields);assert.ok(result.startsWith(source));
  assert.equal(model.parse(result).rows.length,3);
});
test('clearing known attributes preserves the others',()=>{
  const row=model.parse(model.edit(source,0,{...fields,group:'',logo:''})).rows[0];
  assert.equal(row.group,'');assert.equal(row.logo,'');assert.ok(row.header.includes('tvg-id="kept"'));
});
test('reject stale row indices',()=>assert.throws(()=>model.edit(source,99,fields)));
test('reject malformed fields and unsafe URL schemes',()=>{
  for(const invalid of [{name:''},{name:'Bad\nName'},{url:'javascript:alert(1)'},{url:'http://x/ x'},{group:'Bad"group'},{logo:'data:image/png;base64,x'}]){
    assert.throws(()=>model.edit(source,0,{...fields,...invalid}));
  }
});
test('preserve unicode names and literal dollar substitution characters',()=>{
  const result=model.edit(source,0,{...fields,name:'测试, 高清',group:'$& $1'});
  assert.equal(model.parse(result).rows[0].name,'测试, 高清');assert.equal(model.parse(result).rows[0].group,'$& $1');
});
test('URL comments do not become channels',()=>assert.equal(model.parse('#EXTM3U\n# comment\nhttp://192.0.2.1/x\n').rows.length,0));
