'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const sourceRoot=fs.existsSync(path.join(__dirname,'../src/downloads_organizer'))
  ? path.join(__dirname,'../src') : path.join(__dirname,'..');
const C=require(path.join(sourceRoot,'downloads_organizer/viewer_assets/trace-core.js'));
const event=(kind,fields={})=>({kind,run_id:'r1',event_id:'e1',...fields});
const bundle=(events,sidecars={})=>C.buildRuns({name:'test',events,sidecars});

test('group chat interleaves messages and compact tool calls without duplicate delivery transport',()=>{
  const [run]=bundle([
    event('tool_request',{event_id:'1',agent:'planner',call_id:'send',name:'send_message',arguments:'{}'}),
    event('agent_message',{event_id:'2',message:{message_id:'m1',sender:'planner',recipient:'executor'}}),
    event('tool_result',{event_id:'3',agent:'planner',call_id:'send',result:{message_id:'m1'}}),
    event('tool_request',{event_id:'4',agent:'executor',call_id:'list',name:'list_files',arguments:'{}'}),
    event('tool_result',{event_id:'5',agent:'executor',call_id:'list',result:{inventory:[]}}),
    event('agent_message',{event_id:'6',message:{sender:'executor',recipient:'planner'}}),
    event('model_response',{event_id:'7',agent:'executor',message:{content:'done'}}),
  ]);
  assert.deepEqual(C.chatActivity(run.events).map(e=>e.event_id),['2','4','6']);
  assert.deepEqual(C.chatActivity(run.events,{tools:false}).map(e=>e.event_id),['2','6']);
  assert.deepEqual(C.chatActivity(run.events,{agent:'planner'}).map(e=>e.event_id),['2','6']);
  assert.deepEqual(C.chatActivity(run.events,{agent:'executor',peer:'planner'}).map(e=>e.event_id),['2','6']);
  assert.equal(run.events.find(e=>e.event_id==='4')._peer,run.events.find(e=>e.event_id==='5')._uid);
});
test('abbreviated tool summaries are bounded and leave exact arguments untouched',()=>{
  const e=event('tool_request',{name:'run_command',arguments:JSON.stringify({argv:['mv','-n','--','a b.txt','folder/a b.txt']})});
  const original=e.arguments;
  assert.equal(C.toolCallSummary(e),'run_command(mv -n -- "a b.txt" "folder/a b.txt")');
  assert.equal(e.arguments,original);
  assert.equal(C.toolCallSummary({name:'list_files',arguments:'{}'}),'list_files()');
  assert.equal(C.toolCallSummary({name:'submit_plan',arguments:{operations:[{},{}]}}),'submit_plan(2 operations)');
  assert.equal(C.toolCallSummary({name:'anonymize_file',arguments:{path:'a'.repeat(500)}}).length,110);
  assert.match(C.toolCallSummary({name:'upload_file',arguments:'not JSON'}),/unparsed arguments/);
});

test('agent chat includes received and sent messages, and peer filtering is bidirectional',()=>{
  const msg=(id,sender,recipient)=>event('agent_message',{event_id:id,message:{sender,recipient,payload:{}}});
  const records=[msg('1','interpreter','planner'),msg('2','planner','executor'),msg('3','executor','planner'),msg('4','analyzer','runtime'),event('tool_request',{agent:'planner'})];
  assert.deepEqual(C.agentEvents(records,{agent:'planner'}).map(e=>e.event_id),['1','2','3']);
  assert.deepEqual(C.agentEvents(records,{agent:'planner',peer:'executor'}).map(e=>e.event_id),['2','3']);
  assert.equal(C.agentEvents(records).length,4);
});
test('agent trace retains model and tool events without confusing embedded contexts with messages',()=>{
  const records=[event('model_request',{agent:'planner',event_id:'1'}),event('model_response',{agent:'planner',event_id:'2'}),
    event('tool_request',{agent:'planner',event_id:'3'}),event('tool_result',{agent:'planner',event_id:'4'}),
    event('model_request',{agent:'executor',event_id:'5',messages:[{role:'user',content:'planner said hello'}]}),
    event('agent_message',{event_id:'6',message:{sender:'executor',recipient:'planner'}})];
  assert.deepEqual(C.agentEvents(records,{agent:'planner',mode:'trace',peer:'analyzer'}).map(e=>e.event_id),['1','2','3','4','6']);
  assert.deepEqual(C.agentEvents(records,{agent:'planner'}).map(e=>e.event_id),['6']);
  assert.equal(C.agentEvents(records,{mode:'trace'}).length,records.length);
});

test('network graph counts directed messages independently of transport and model records',()=>{
  const msg=(id,from,to)=>event('agent_message',{event_id:id,message:{sender:from,recipient:to,payload:{}}});
  const records=[msg('a','planner','arbiter'),msg('b','arbiter','planner'),msg('c','planner','arbiter'),
    msg('d','interpreter','planner'),msg('e','runtime','interpreter'),event('tool_request',{name:'send_message'})];
  const graph=C.networkGraph(records);
  assert.equal(graph.messages.length,4);
  assert.deepEqual(graph.actors,['planner','arbiter','interpreter']);
  assert.equal(graph.links.find(l=>l.from==='planner'&&l.to==='arbiter').count,2);
  assert.equal(graph.links.find(l=>l.from==='arbiter'&&l.to==='planner').count,1);
  assert.equal(C.networkGraph(records,{includeRuntime:true}).messages.length,5);
});
test('network graph retains unusual actor names, self messages, and empty selections',()=>{
  assert.deepEqual(C.networkGraph([]),{actors:[],links:[],messages:[]});
  const graph=C.networkGraph([event('agent_message',{message:{sender:'__proto__',recipient:'__proto__'}}),event('agent_message',{message:{}})]);
  assert.deepEqual(graph.actors,['__proto__']);
  assert.equal(graph.links[0].count,1);
  assert.equal(graph.links[0].from,graph.links[0].to);
});

test('reports malformed lines with line numbers and retains valid and unknown events',()=>{
  const source=[JSON.stringify(event('future_event')), '{not json}', JSON.stringify(event('run_failed',{event_id:'e3'})), '{'].join('\n');
  const parsed=C.parseJSONL(source,'input.jsonl');
  assert.equal(parsed.events.length,2);assert.equal(parsed.errors.length,2);
  assert.match(parsed.errors[0],/input.jsonl, line 2/);assert.match(parsed.errors[1],/line 4/);
  const runs=C.buildRuns({name:'input.jsonl',trace_text:source});
  assert.equal(runs[0].events[0]._group,'other');assert.equal(runs[0].warnings.length,2);
});
test('invalid event structures are reported instead of accepted as records',()=>{
  const result=C.parseJSONL('null\n[]\n{}\n42\n{"kind":"ok"}');
  assert.equal(result.errors.length,4);assert.equal(result.events.length,1);
});
test('correlates identical tool IDs independently across runs',()=>{
  const records=['r1','r2'].flatMap(run_id=>[
    event('tool_request',{run_id,event_id:'e1',agent:'executor',name:'run_command',arguments:'{"argv":["test","-e","a"]}',call_id:'same'}),
    event('tool_result',{run_id,event_id:'e2',agent:'executor',tool:'run_command',call_id:'same',result:{exit_code:1}})]);
  const runs=bundle(records);
  assert.equal(runs.length,2);
  for(const run of runs){assert.equal(run.events[0]._peer,run.events[1]._uid);assert.equal(run.events[1]._peer,run.events[0]._uid);assert.equal(run.warnings.length,0);}
  assert.notEqual(runs[0].events[0]._uid,runs[1].events[0]._uid);
  assert.equal(runs[0].events[1]._group,'tools');
});
test('unmatched requests and results stay inspectable with explicit warnings',()=>{
  const [run]=bundle([event('tool_request',{call_id:'a',name:'x',arguments:'{}'}),event('tool_result',{event_id:'e2',call_id:'b',result:{error:'failed'}})]);
  assert.equal(run.events.length,2);assert.equal(run.warnings.length,2);
  assert.equal(run.events[0]._peer,undefined);
});
test('counts returned usage only once, cached and reasoning remain subsets',()=>{
  const usage={prompt_tokens:100,completion_tokens:20,prompt_tokens_details:{cached_tokens:80},completion_tokens_details:{reasoning_tokens:5}};
  const records=[event('model_request',{agent:'analyzer',messages:[{role:'assistant',usage}]}),event('model_response',{event_id:'e2',agent:'analyzer',message:{},usage})];
  const runs=bundle(records,{'cost_estimate.json':{estimated_usd:0.1234,usage_complete:true}}),s=C.summary(runs);
  assert.equal(s.input,100);assert.equal(s.cached,80);assert.equal(s.output,20);assert.equal(s.reasoning,5);assert.equal(s.cost,0.1234);assert.equal(s.usageComplete,true);
  assert.equal(runs[0].events[0]._peer,runs[0].events[1]._uid);
});
test('missing response or usage is partial and missing cost remains unknown',()=>{
  const s=C.summary(bundle([event('model_request',{agent:'executor'}),event('model_response',{event_id:'e2',agent:'executor',message:{},usage:null}),event('run_failed',{event_id:'e3'})]));
  assert.equal(s.calls,1);assert.equal(s.usageComplete,false);assert.equal(s.cost,null);assert.equal(s.usageResponses,0);assert.deepEqual(s.statuses,['failed']);
});
test('analysis reuse and embedded histories are not extra model calls',()=>{
  const records=[event('analysis_reused',{source_run_id:'older'}),event('agent_message',{event_id:'e2',message:{sender:'analyzer',recipient:'planner',type:'analysis_report',payload:{analysis_provenance:{source_run_id:'older'}}}})];
  const s=C.summary(bundle(records));assert.equal(s.calls,0);assert.equal(s.messages,1);assert.equal(s.replayedMessages,1);
});
test('direct-message filtering excludes runtime dispatch and tools',()=>{
  const records=[event('agent_message',{message:{sender:'runtime',recipient:'analyzer',payload:{}}}),event('agent_message',{event_id:'e2',message:{sender:'analyzer',recipient:'planner',payload:{path:'计算机网络 1.md'}}}),event('tool_result',{event_id:'e3',agent:'analyzer',call_id:'x',result:{}})];
  const [run]=bundle(records);
  assert.equal(C.filter(run.events,{direct:true}).length,1);
  assert.equal(C.filter(run.events,{actor:'planner',direct:true,search:'计算机网络'}).length,1);
  assert.equal(C.filter(run.events,{group:'tools'}).length,1);
});
test('duplicate IDs are retained with separate row identity and warning',()=>{
  const [run]=bundle([event('a'),event('b')]);
  assert.equal(run.events.length,2);assert.notEqual(run.events[0]._uid,run.events[1]._uid);assert.match(run.warnings[0],/Duplicate event/);
});
test('sidecars cannot silently attach to a different run',()=>{
  const [run]=bundle([event('run_started')],{'manifest.json':{run_id:'different',status:'completed'},'cost_estimate.json':{run_id:'different',estimated_usd:999}});
  assert.equal(run.sidecars['manifest.json'],undefined);assert.equal(C.summary([run]).cost,null);assert.equal(run.warnings.length,2);
});
test('raw inspector event preserves source values and hostile strings as inert data',()=>{
  const source=event('agent_message',{message:{sender:'analyzer',recipient:'planner',type:'note',payload:{text:'<img src=x onerror=alert(1)><script>bad()</script>'}}});
  const [run]=bundle([source]);assert.deepEqual(C.raw(run.events[0]),source);assert.match(run.events[0]._preview,/<script>/);
});
test('hostile participant names cannot modify object prototypes',()=>{
  const s=C.summary(bundle([event('model_request',{agent:'__proto__'}),event('model_response',{event_id:'e2',agent:'__proto__',usage:{prompt_tokens:5,completion_tokens:2}})]));
  assert.equal(s.agents['__proto__'].calls,1);assert.equal(s.input,5);assert.equal({}.calls,undefined);
});
test('bundled demo is a real scripted execution with preserved file effects',()=>{
  const data=JSON.parse(fs.readFileSync(path.join(sourceRoot,'downloads_organizer/viewer_assets/demo.json'),'utf8'));
  const runs=data.bundles.flatMap(C.buildRuns),s=C.summary(runs);
  assert.deepEqual(s.statuses,['completed']);assert.equal(s.moves,5);assert.equal(s.cost,null);assert.equal(s.usageComplete,false);
  assert.equal(runs[0].events.find(e=>e.kind==='run_started').real_llm,false);
  assert.equal(runs[0].sidecars['manifest.json'].contents_preserved,true);
  assert.ok(!JSON.stringify(data).includes('/Users/lee/'));assert.match(data.provenance.redaction,/no events omitted/);
});

test('workflow contains only tools and messages, never model traffic or tool-result duplicates',()=>{
  const [run]=bundle([event('model_request',{agent:'analyzer'}),event('model_response',{event_id:'e2',agent:'analyzer',message:{content:'internal explanation'}}),event('tool_request',{event_id:'e3',agent:'analyzer',name:'anonymize_file',call_id:'c',arguments:'{}'}),event('tool_result',{event_id:'e4',agent:'analyzer',call_id:'c',result:{status:'anonymized'}}),event('agent_message',{event_id:'e5',message:{sender:'analyzer',recipient:'planner',payload:{text:'Ready'}}})]);
  const activity=C.activityEvents(run.events);
  assert.deepEqual(activity.map(e=>e.kind),['tool_request','agent_message']);
  assert.equal(C.activityGroups(run.events)[0].events[0].name,'anonymize_file');
});
test('workflow groups consecutive tool calls within a run and agent, preserving message boundaries',()=>{
  const [run]=bundle([event('tool_request',{agent:'analyzer',name:'anonymize_file',call_id:'a',arguments:'{}'}),event('tool_request',{event_id:'e2',agent:'analyzer',name:'upload_file',call_id:'b',arguments:'{}'}),event('agent_message',{event_id:'e3',message:{sender:'analyzer',recipient:'planner',payload:{}}}),event('tool_request',{event_id:'e4',agent:'planner',name:'submit_plan',call_id:'c',arguments:'{}'})]);
  const grouped=C.activityGroups(run.events);
  assert.equal(grouped.length,3);assert.equal(grouped[0].events.length,2);assert.equal(grouped[1].type,'message');assert.equal(grouped[2].actor,'planner');
  assert.equal(C.activityGroups(run.events,{groupTools:false}).length,4);
});
test('successful send_message is represented once by its delivered message; failed sends remain tools',()=>{
  const [run]=bundle([event('tool_request',{agent:'analyzer',name:'send_message',call_id:'c',arguments:'{}'}),event('agent_message',{event_id:'e2',message:{message_id:'m',sender:'analyzer',recipient:'planner',payload:{}}}),event('tool_result',{event_id:'e3',call_id:'c',result:{message_id:'m'}}),event('tool_request',{event_id:'e4',agent:'analyzer',name:'send_message',call_id:'failed',arguments:'{}'}),event('tool_result',{event_id:'e5',call_id:'failed',result:{error:'failed'}})]);
  const activity=C.activityEvents(run.events);
  assert.equal(activity.length,2);assert.equal(activity[0].kind,'agent_message');assert.equal(activity[1].call_id,'failed');
});
test('runtime handoffs are optional, and transport correlation cannot cross runs',()=>{
  const records=[event('agent_message',{message:{message_id:'m',sender:'runtime',recipient:'executor',payload:{}}}),event('tool_request',{run_id:'other',event_id:'e2',agent:'planner',name:'send_message',call_id:'c',arguments:'{}'}),event('tool_result',{run_id:'other',event_id:'e3',call_id:'c',result:{message_id:'m'}})];
  const runs=bundle(records),events=runs.flatMap(r=>r.events);
  assert.equal(C.activityEvents(events).length,1);assert.equal(C.activityEvents(events)[0].kind,'tool_request');assert.equal(C.activityEvents(events,{includeRuntime:true}).length,2);
});
test('readable message text uses recorded content and deterministic file counts',()=>{
  assert.equal(C.messageText({observations:{op_3:'File was reported missing'}}),'op_3: File was reported missing');
  assert.equal(C.messageText({files:[{category:'Papers'},{category:'Papers'},{category:'Code'}]}),'3 files analyzed. Papers: 2 · Code: 1');
  assert.equal(C.messageText({text:'<script>inert()</script>'}),'<script>inert()</script>');
  assert.equal(C.messageText({files:[null,'unknown',{category:'Code'}]}),'3 files analyzed. Uncategorized: 2 · Code: 1');
});

test('evidence requests and observations are not mislabeled as classifications',()=>{
  assert.equal(C.messageText({kind:'inventory',request_id:'i1',files:[{path:'a.pdf'}]}),'Inventory evidence · 1 files · request i1');
  assert.equal(C.messageText({kind:'content',request_id:'c1',files:[{path:'a.pdf',reason:'Ambiguous filename'}]}),'Request content · 1 files · request c1\na.pdf');
  assert.equal(C.messageText({kind:'content',request_id:'c1',files:[{path:'a.pdf',status:'anonymized',anonymized_excerpt:'PRIVATE_TEXT_MUST_NOT_APPEAR_IN_PREVIEW'}]}),'Content evidence · 1 files · 1 anonymized excerpts · request c1\na.pdf');
  assert.equal(C.messageText({plan_id:'p1',version:2,pending_operation_ids:['op1']}),'Plan p1@2 · 1 pending operations');
});

test('compatibility audit remains visible without counting normalization as an extra action',()=>{
  const [run]=bundle([
    event('tool_call_compatibility',{agent:'analyzer',repairs:[{reason:'assistant_json_tool_fallback'}],effective_message:{role:'assistant',tool_calls:[]}}),
    event('tool_request',{event_id:'e2',agent:'analyzer',name:'send_message',call_id:'compat_1',arguments:'{}'}),
    event('tool_result',{event_id:'e3',agent:'analyzer',call_id:'compat_1',result:{error:'missing_tool_arguments'}}),
    event('tool_protocol_stopped',{event_id:'e4',agent:'analyzer',error:'missing_tool_arguments',note:'Three consecutive identical protocol errors'})
  ]);
  assert.equal(run.events[0]._title,'Tool-call format normalized');
  assert.match(run.events[0]._preview,/assistant_json_tool_fallback/);
  assert.equal(run.events[3]._title,'Stopped after repeated tool-call errors');
  assert.equal(C.activityEvents(run.events).length,1);
  assert.equal(C.summary([run]).tools,1);
});

test('command explanations preserve recorded text and distinguish older traces',()=>{
  const e=event('tool_request',{name:'run_command',arguments:JSON.stringify({argv:['mv','-n','a $(literal).txt','b.txt'],description:'Move the approved file without replacing an occupied target.'})});
  const d=C.commandDetails(e);
  assert.equal(d.description,'Move the approved file without replacing an occupied target.');
  assert.match(d.command,/a \$\(literal\)\.txt/);
  assert.match(C.preview(e),/without replacing/);
  assert.equal(C.commandDetails(event('tool_request',{name:'run_command',arguments:'{"argv":["test","-e","a"]}'})).description,null);
  assert.equal(C.commandDetails(event('tool_request',{name:'run_command',arguments:'broken'})).description,null);
  assert.equal(C.commandDetails(event('tool_request',{name:'run_command',arguments:{argv:['test'],description:'   '}})).description,null);
  assert.equal(C.commandDetails(event('tool_request',{name:'list_files',arguments:'{}'})),null);
  assert.equal(C.commandDetails(event('tool_request',{name:'run_command',arguments:JSON.stringify({argv:['test'],description:'<img src=x onerror=alert(1)>'})})).description,'<img src=x onerror=alert(1)>');
});
