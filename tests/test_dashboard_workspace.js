// Run with: node --test tests/test_dashboard_workspace.js
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');

test('concurrent reads share one request and release it after completion',async()=>{
  const source=fs.readFileSync('static/js/core.js','utf8');
  const wrapper=source.slice(source.indexOf('const pendingReads='),source.indexOf('async function requestJSON'));
  let calls=0,resolve;
  const context=vm.createContext({requestJSON:()=>{calls++;return new Promise(r=>{resolve=r;});}});
  vm.runInContext(wrapper,context);
  const first=context.fetchJSON('/api/status');
  assert.equal(context.fetchJSON('/api/status'),first);
  assert.equal(calls,1);
  resolve({cpu_percent:20});await first;
  const next=context.fetchJSON('/api/status');
  assert.notEqual(next,first);assert.equal(calls,2);resolve(null);await next;
});

test('resource insights handle pressure, failure, missing data and recovery',()=>{
  const elements={};
  const $=id=>elements[id]??=( {dataset:{},textContent:'',innerHTML:'',hidden:true} );
  const context=vm.createContext({$,Date,Number,S:{th:{cpuWarn:80,ramWarn:85,diskWarn:85}},lastApiError:'Request timed out',esc:s=>String(s)});
  const source=fs.readFileSync('static/js/overview.js','utf8');
  vm.runInContext(source.slice(source.indexOf('let lastMetricSample=')),context);
  context.renderMetricFreshness(null);
  assert.equal($('#dataNotice').hidden,false);
  assert.match($('#dataNoticeText').textContent,/No successful sample/);
  context.renderMetricFreshness({cpu_percent:91,ram_percent:20,disk_percent:40});
  assert.equal($('#dataNotice').hidden,true);
  assert.match($('#insightStrip').innerHTML,/Inspect processes/);
  assert.equal($('#insightStrip').dataset.level,'warn');
  context.renderMetricFreshness(null);
  assert.match($('#sampleStamp').textContent,/Stale/);
  context.renderMetricFreshness({cpu_percent:10,ram_percent:20,disk_percent:40});
  assert.equal($('#insightStrip').dataset.level,'ok');
  assert.equal($('#dataNotice').hidden,true);
  context.renderMetricFreshness({});
  assert.match($('#insightStrip').innerHTML,/metrics are missing/);
});
