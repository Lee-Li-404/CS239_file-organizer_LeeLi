/* Local trace explorer. No trace content is inserted as HTML or executed. */
'use strict';
const C = TraceCore;
const $ = id => document.getElementById(id);
const colors = Object.assign(Object.create(null),{interpreter:'#6bb9ff',analyzer:'#c595ff',planner:'#71e2bd',arbiter:'#f0c36d',executor:'#fc967f',runtime:'#96a4ba',operator:'#96a4ba'});
const names = Object.assign(Object.create(null),{interpreter:'Interpreter',analyzer:'Analyzer',planner:'Planner',arbiter:'Arbiter',executor:'Executor',runtime:'Runtime',operator:'User / operator'});
const S = {runs:[],run:'',view:'chat',mode:'key',direct:true,showRuntime:false,groupTools:true,selected:null,cursor:null,limit:180,timer:null,chains:[],networkRoute:null,chatAgent:null,chatMode:'messages',chatPeer:'',chatOpen:false,chatTools:true};
const fmt = n => new Intl.NumberFormat('en-US').format(n);
const short = n => n >= 1000000 ? `${(n/1000000).toFixed(2)}M` : n >= 1000 ? `${(n/1000).toFixed(1)}k` : fmt(n);
const money = n => n == null ? '—' : `$${n.toFixed(4)}`;
function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}
function button(text, cls, fn) { const b=el('button',cls,text); b.type='button'; b.addEventListener('click',fn); return b; }
function dot(actor) { const d=el('span','agent-dot'); d.style.background=colors[actor] || '#8390a2'; return d; }
function badge(text, cls='') { return el('span',`badge ${cls}`,text); }
function allEvents() { return S.runs.flatMap(r=>r.events); }
function linkedRunIds(root) {
  const ids=new Set(),visit=id=>{if(ids.has(id)||!S.runs.some(r=>r.id===id))return;ids.add(id);
    const run=S.runs.find(r=>r.id===id);run.events.filter(e=>e.kind==='analysis_reused').forEach(e=>visit(e.source_run_id));
    S.chains.filter(c=>c.execution?.run_id===id).forEach(c=>visit(c.analysis?.run_id));};
  visit(root);return ids;
}
function selectedRuns() {
  if(S.run==='*')return S.runs;
  if(S.run.startsWith('chain:')){const ids=linkedRunIds(S.run.slice(6));return S.runs.filter(r=>ids.has(r.id));}
  return S.runs.filter(r=>r.id===S.run);
}
function sequence() {
  const events=selectedRuns().flatMap(r=>r.events);
  if(selectedRuns().length>1)events.sort((a,b)=>(Date.parse(a.timestamp)||0)-(Date.parse(b.timestamp)||0));
  if(S.view==='network') return C.networkGraph(events,{includeRuntime:S.showRuntime}).messages;
  return S.view==='flow'?C.activityEvents(events,{includeRuntime:S.showRuntime}):events;
}
function visibleEvents() { const events=sequence(); return S.cursor===null ? events : events.slice(0,S.cursor+1); }
function filtered(mode='all') {
  return C.filter(visibleEvents(),{actor:$('actor-filter').value,group:$('kind-filter').value,search:$('search').value,mode});
}
function findEvent(uid) { return allEvents().find(e=>e._uid===uid); }
function notice(text,error=false) { $('notice').textContent=text; $('notice').className=`notice${error?' error':''}`; $('notice').hidden=!text; }
function toast(text) { $('toast').textContent=text; $('toast').hidden=false; clearTimeout(toast.timer); toast.timer=setTimeout(()=>$('toast').hidden=true,4500); }
function empty(title,text) { const d=el('div','empty-state'); d.append(el('h3','',title),el('p','',text)); return d; }
function closeInspector() {$('inspector').classList.remove('open');document.querySelector('.workspace-body').classList.remove('inspecting');}
function addRuns(bundles, replace=false) {
  pause();
  const incoming=bundles.flatMap(C.buildRuns);
  if (!incoming.length) { notice('No valid trace events found. '+bundles.flatMap(b=>C.parseJSONL(b.trace_text||'',b.name).errors).join('\n'),true); return; }
  const errors=bundles.flatMap(b=>b.trace_text ? C.parseJSONL(b.trace_text,b.name).errors : []);
  const byId=new Map((replace?[]:S.runs).map(r=>[r.id,r]));
  incoming.forEach(r=>byId.set(r.id,r));
  S.runs=[...byId.values()]; S.run=incoming[0].id; S.cursor=null; S.selected=null; S.limit=180; S.networkRoute=null;
  S.chatAgent=null;S.chatPeer='';S.chatOpen=false;S.chatMode='messages';
  if(linkedRunIds(S.run).size>1)S.run='chain:'+S.run;
  closeInspector();
  notice(errors.length ? `${errors.length} malformed line(s) were reported. Valid events remain inspectable; see import warnings.` : '',!!errors.length);
  renderRunOptions(); render();
}
function renderRunOptions() {
  const select=$('run-select'); select.replaceChildren();
  if (!S.runs.length) { const o=el('option','','No run loaded'); o.value=''; select.append(o); return; }
  if (S.runs.length>1) { const o=el('option','',`All loaded runs · ${S.runs.length}`); o.value='*'; select.append(o); }
  const runLabel=r=>{const count=r.sidecars['manifest.json']?.before?.length;return count!=null?`${count} files`:r.name;};
  S.runs.filter(r=>linkedRunIds(r.id).size>1).forEach(r=>{const o=el('option','',`${runLabel(r)} · recovered workflow`);o.value='chain:'+r.id;o.title=r.id;select.append(o);});
  S.runs.forEach(r=>{ const status=C.summary([r]).statuses[0]; const o=el('option','',`${runLabel(r)} · ${status} · ${r.id.slice(-6)}`);o.value=r.id;o.title=r.id;select.append(o); });
  select.value=S.run;
  const actors=new Set();
  allEvents().forEach(e=>{actors.add(e._actor);if(e.kind==='agent_message') actors.add(e.message?.recipient || 'unknown');});
  const af=$('actor-filter'),previous=af.value; af.replaceChildren(); const opt=el('option','','All participants');opt.value='';af.append(opt);
  [...actors].forEach(actor=>{const o=el('option','',names[actor]||actor);o.value=actor;af.append(o);}); af.value=actors.has(previous)?previous:'';
}
function renderSummary() {
  const runs=selectedRuns(),s=C.summary(runs),stats=$('summary'); stats.replaceChildren();
  for(const [label,value,note] of [
    ['Model calls',fmt(s.calls),`${fmt(s.responses)} recorded responses`],
    ['Agent messages',fmt(s.messages),`${s.runtimeMessages} runtime · ${s.replayedMessages} replayed`],
    ['Moves completed',fmt(s.moves),'From move_completed records'],
    ['Recorded estimate',money(s.cost),s.costRuns===runs.length && runs.length ? 'From saved cost reports' : s.costRuns ? `Partial · ${s.costRuns}/${runs.length} reports` : 'Cost report not supplied']]) {
      const d=el('div','stat'); d.append(el('div','stat-label',label),el('div','stat-value',value),el('div','stat-sub',note)); stats.append(d);
  }
  const strip=$('token-strip');strip.replaceChildren();
  if(S.cursor!==null)strip.append(el('span','','Full-run totals:'));
  for(const [n,t] of [[s.usageResponses?short(s.input):'—','input tokens'],[s.usageResponses?short(s.output):'—','output tokens'],[s.input?`${(100*s.cached/s.input).toFixed(1)}%`:'—','input cached'],[s.usageComplete?'Complete':'Partial / unavailable','usage coverage']]) {
    const d=el('span'); d.append(el('strong','',n),document.createTextNode(` ${t}`));strip.append(d);
  }
  const badges=$('run-badges');badges.replaceChildren();
  const statuses=[...new Set(s.statuses)],status=statuses.length===1?statuses[0]:'mixed outcomes';
  if(runs.length) badges.append(badge(status,status==='completed'?'good':status==='failed'?'bad':'warn'));
  const starts=runs.map(r=>r.events.find(e=>e.kind==='run_started')).filter(Boolean);
  if(starts.length) {
    const real=starts.every(e=>e.real_llm===true),scripted=starts.every(e=>e.real_llm===false);
    if(scripted||['timeline','plans','usage'].includes(S.view))badges.append(badge(real?'Real model':scripted?'Scripted example':'Mixed sources','source-label-badge'));
    if(['timeline','plans','usage'].includes(S.view)&&starts.some(e=>e.policy_enforcement===false)) badges.append(badge('Phase 1 · no policy enforcement','warn'));
  }
  if(runs.some(r=>r.events.some(e=>e.kind==='confirmation' && /operator/.test(e.source||'')))) badges.append(badge('Operator-assisted'));
  const warnings=runs.flatMap(r=>r.warnings.map(w=>`${r.id}: ${w}`));
  $('warning-box').hidden=!warnings.length;$('warning-summary').textContent=`${warnings.length} import / trace warning${warnings.length===1?'':'s'}`;
  $('warning-box').classList.toggle('has-import-errors',warnings.some(w=>/line \d+:/.test(w)));
  $('warnings').replaceChildren(...warnings.map(w=>el('p','',w)));
  $('nav-message-count').textContent=fmt(s.messages);
  const participants=$('participants');participants.replaceChildren();
  const present=new Set(runs.flatMap(r=>r.events).map(e=>e._actor));
  for(const actor of ['interpreter','planner','analyzer','arbiter','executor'].filter(a=>present.has(a))) {
    const tools=runs.flatMap(r=>r.events).filter(e=>e.kind==='tool_request'&&e._actor===actor).length;
    const row=el('div','participant');row.title=`${tools} tool calls, including message delivery`;row.append(dot(actor),el('span','',names[actor]),el('small','',`${tools} calls`));participants.append(row);
  }
}
function renderPlayback() {
  const seq=sequence(),max=Math.max(0,seq.length-1),value=S.cursor===null?max:Math.min(S.cursor,max);
  $('seek').max=max;$('seek').value=value;$('seek').disabled=!seq.length;
  $('position').textContent=`${seq.length?value+1:0} / ${seq.length}`;
  $('previous').disabled=!seq.length || value<=0;$('next').disabled=!seq.length || value>=max;$('play').disabled=!seq.length;
}
function setView(view) { pause();S.view=view;S.cursor=null;S.limit=180;S.networkRoute=null;closeInspector();document.querySelectorAll('.nav-item').forEach(b=>b.classList.toggle('active',b.dataset.view===view));renderSummary();renderPlayback();renderView(); }
function render() { renderSummary();renderPlayback();renderView(); if(S.selected && findEvent(S.selected)) renderInspector(findEvent(S.selected));else renderInspector(null); }
function renderView() {
  document.body.classList.toggle('chat-view',S.view==='chat');
  document.body.classList.toggle('network-view',S.view==='network');
  document.body.classList.toggle('technical-view',['timeline','plans','usage'].includes(S.view));
  if(S.view==='chat'){
    const content=$('view-content');content.replaceChildren();
    TraceChat.render(content,{
      runs:selectedRuns(),allRuns:S.runs,run:S.run,agent:S.chatAgent,mode:S.chatMode,peer:S.chatPeer,open:S.chatOpen,limit:S.limit,tools:S.chatTools,
      names,selected:S.selected,
      onAgent:agent=>{S.chatAgent=agent;S.chatPeer='';S.chatMode='messages';S.chatOpen=true;S.selected=null;S.limit=180;closeInspector();renderView();},
      onBack:()=>{S.chatOpen=false;closeInspector();renderView();},
      onPeer:peer=>{S.chatPeer=peer;closeInspector();renderView();},
      onMode:mode=>{S.chatMode=mode;S.limit=180;closeInspector();renderView();},
      onMore:()=>{S.limit+=180;renderView();},
      onTools:tools=>{S.chatTools=tools;renderView();},
      onInspect:e=>{pause();selectEvent(e);},onView:setView,onRun:switchRun,
      onImport:()=>$('folder-input').click(),onDemo:loadDemo,
    });
    return;
  }
  const simple=['network','flow','messages'].includes(S.view),kind=$('kind-filter');
  const choices=simple?[['','Tools & messages'],['tools','Tool calls'],['messages','Messages']]:[['','All categories'],['messages','Messages'],['tools','Tools'],['model','Model'],['plans','Plans'],['files','Files'],['run','Run & recovery'],['policy','Policy decisions'],['other','Other']];
  if(kind.dataset.mode!==(simple?'simple':'full')){const previous=kind.value;kind.replaceChildren(...choices.map(([value,text])=>{const o=el('option','',text);o.value=value;return o;}));kind.value=choices.some(([v])=>v===previous)?previous:'';kind.dataset.mode=simple?'simple':'full';}
  kind.hidden=['network','messages'].includes(S.view);if(kind.hidden)kind.value='';
  const titles={network:['Communication map','Explore the actual handoffs between agents.'],flow:['Tools & messages','Follow what each agent does and says.'],timeline:['Full event log','All recorded events, including model inputs and outputs.'],messages:['Agent messages','The conversation between agents.'],plans:['Plans & files','Proposals, trusted review events, and recorded filesystem effects.'],usage:['Token usage','Returned API usage, counted once per response.']};
  $('view-title').textContent=titles[S.view][0];$('view-description').textContent=titles[S.view][1];
  const opts=$('view-options');opts.replaceChildren();
  if(S.view==='network') {
    const label=el('label','checkbox-label'),box=el('input');box.type='checkbox';box.checked=S.showRuntime;
    box.addEventListener('change',()=>{pause();S.showRuntime=box.checked;S.cursor=null;S.networkRoute=null;renderPlayback();renderView();});
    label.append(box,document.createTextNode('Include Runtime'));opts.append(label,el('span','','Select an agent or connection to follow its messages.'));
  } else if(S.view==='flow') {
    const seg=el('div','segmented');[[true,'Grouped tools'],[false,'Every call']].forEach(([value,text])=>seg.append(button(text,S.groupTools===value?'selected':'',()=>{S.groupTools=value;renderView();})));
    const label=el('label','checkbox-label'),box=el('input');box.type='checkbox';box.checked=S.showRuntime;box.addEventListener('change',()=>{pause();S.showRuntime=box.checked;S.cursor=null;renderPlayback();renderView();});label.append(box,document.createTextNode('Show system handoffs'));opts.append(seg,label);
  } else if(S.view==='timeline') {
    const seg=el('div','segmented');[['key','Key events'],['all','All events']].forEach(([value,text])=>seg.append(button(text,value===S.mode?'selected':'',()=>{S.mode=value;S.limit=180;renderView();})));
    opts.append(seg,el('span','',S.mode==='key'?'Tool steps and full contexts are available in All events.':'Every source event is retained.'));
  } else if(S.view==='messages') {
    const label=el('label','checkbox-label');const box=el('input');box.type='checkbox';box.checked=S.direct;box.addEventListener('change',()=>{S.direct=box.checked;renderView();});label.append(box,document.createTextNode('Direct agent messages only'));
    opts.append(label,el('span','','Click a handoff to inspect its exact payload.'));
  } else opts.append(el('span','',S.view==='plans'?'Approvals apply to exact plan versions.':'Cached input is included in input, never added twice.'));
  const content=$('view-content');content.replaceChildren();
  if(!selectedRuns().length) {content.append(empty('Start with a saved run','Open a run folder, import trace.jsonl, or load the synthetic demo. No API key is needed.'));$('event-count').textContent='No trace';return;}
  if(S.view==='flow') renderFlow(content);
  if(S.view==='network') TraceNetwork.render(content,{
    runs:selectedRuns(),events:filtered(),allEvents:selectedRuns().flatMap(r=>r.events),
    includeRuntime:S.showRuntime,route:S.networkRoute,selected:S.selected,cursor:S.cursor,
    colors,names,selectEvent:event=>{pause();selectEvent(event);renderView();},time,
    onActor:actor=>{pause();$('actor-filter').value=$('actor-filter').value===actor?'':actor;S.networkRoute=null;renderView();},
    onRoute:route=>{pause();S.networkRoute=route;renderView();},
    onClear:()=>{S.networkRoute=null;$('actor-filter').value='';$('search').value='';renderView();},
  });
  if(S.view==='timeline') renderTimeline(content);
  if(S.view==='messages') renderMessages(content);
  if(S.view==='plans') renderPlans(content);
  if(S.view==='usage') renderUsage(content);
}
function time(e) {
  const times=selectedRuns().map(r=>Date.parse(r.events[0]?.timestamp)).filter(Number.isFinite),start=Math.min(...times),stamp=Date.parse(e.timestamp);
  if(!Number.isFinite(start)||!Number.isFinite(stamp))return 'time unknown';
  const seconds=Math.max(0,(stamp-start)/1000);return `+${seconds.toFixed(1)}s`;
}
function selectEvent(e,open=true) {S.selected=e._uid;renderInspector(e);document.querySelectorAll('[data-uid]').forEach(n=>n.classList.toggle('selected',n.dataset.uid===e._uid));if(open){$('inspector').classList.add('open');document.querySelector('.workspace-body').classList.add('inspecting');}}
function evidence(e) {if(e.kind==='model_response')return ['Model output','claim'];if(e.kind==='tool_result')return ['Tool observation','observation'];if(e.kind==='move_completed')return ['Recorded effect','evidence'];if(e.kind==='confirmation')return ['Review event','evidence'];if(e.kind==='policy_decision')return ['Policy record',''];return [e._group,''];}
function renderTimeline(content) {
  const events=filtered(S.mode);$('event-count').textContent=`${fmt(events.length)} events`;
  if(!events.length){content.append(empty('No matching events','Change the filters or show the full run.'));return;}
  const icons={messages:'⇄',tools:'⌘',model:'◇',plans:'▤',files:'↳',run:'◷',policy:'⚑',other:'·'};
  for(const e of events.slice(0,S.limit)) {
    const row=button('','event-row'+(e._uid===S.selected?' selected':''),()=>selectEvent(e));row.dataset.uid=e._uid;
    const main=el('div','event-main'),meta=el('div','event-meta'),[tag,kind]=evidence(e);
    meta.append(dot(e._actor),el('span','actor-label',names[e._actor]||e._actor),el('span','event-id',e.event_id||`line ${e._line??'?'}`),el('span',`event-tag ${kind}`,tag));
    if(S.run==='*')meta.append(el('span','event-id',e._run));
    main.append(meta,el('div','event-title',e._title),el('div','event-preview',e._preview.length>240?e._preview.slice(0,240)+'…':e._preview));
    row.append(el('div','event-icon',icons[e._group]||'·'),main,el('span','event-time',time(e)));content.append(row);
  }
  if(events.length>S.limit)content.append(button(`Show next ${Math.min(180,events.length-S.limit)} events`,'load-more',()=>{S.limit+=180;renderView();}));
}
function svg(tag,attrs={}) {const n=document.createElementNS('http://www.w3.org/2000/svg',tag);Object.entries(attrs).forEach(([k,v])=>n.setAttribute(k,String(v)));return n;}
function flowActors(events,includeRuntime) {
  const actors=includeRuntime?['runtime','analyzer','planner','executor']:['analyzer','planner','executor'];
  events.forEach(e=>{for(const a of e.kind==='agent_message'?[e.message?.sender,e.message?.recipient]:[e._actor])if(a&&!actors.includes(a))actors.push(a);});return actors;
}
function flowHeader(actors) {
  const head=el('div','flow-columns');head.style.gridTemplateColumns=`repeat(${actors.length},minmax(0,1fr))`;
  const roles={analyzer:'Analyzes files',planner:'Plans changes',executor:'Runs tools',runtime:'System handoffs'};
  actors.forEach(a=>{const col=el('div','flow-agent'),title=el('div');title.append(dot(a),document.createTextNode(names[a]||a));col.append(title,el('small','',roles[a]||'Participant'));head.append(col);});return head;
}
function messageCard(e) {
  const m=e.message||{},card=el('article','conversation-card');card.dataset.uid=e._uid;
  const header=el('div','conversation-header');header.append(el('strong','',`${names[m.sender]||m.sender||'?'} → ${names[m.recipient]||m.recipient||'?'}`));
  const transport=allEvents().some(t=>t._run===e._run&&t.kind==='tool_result'&&t.result?.message_id&&t.result.message_id===m.message_id);
  if(transport)header.append(el('code','message-tool','send_message'));
  if(m.payload?.analysis_provenance)header.append(el('span','replay-tag','Reused report'));
  const type=el('div','conversation-type',(m.type||'message').replaceAll('_',' ')),text=C.messageText(m.payload),body=el('p','conversation-body',text.length>340?text.slice(0,340)+'…':text);
  const full=el('details','conversation-full');full.append(el('summary','','Full message'),jsonBlock(m.payload));
  const footer=el('div','conversation-footer');footer.append(full,button('Inspect ↗','text-button',()=>selectEvent(e)));
  card.append(header,type,body,footer);return card;
}
function flowMessageRow(e,actors,index) {
  const m=e.message||{},from=actors.indexOf(m.sender),to=actors.indexOf(m.recipient),row=el('div','flow-message-row');
  const arrow=svg('svg',{viewBox:'0 0 1000 28',preserveAspectRatio:'none','aria-hidden':'true'});arrow.classList.add('flow-arrow');
  const color=colors[m.sender]||'#8390a2',x1=(from+.5)*1000/actors.length,x2=(to+.5)*1000/actors.length,defs=svg('defs'),marker=svg('marker',{id:`flow-arrow-${index}`,viewBox:'0 0 10 10',refX:9,refY:5,markerWidth:5,markerHeight:5,orient:'auto'});
  marker.append(svg('path',{d:'M 0 0 L 10 5 L 0 10 z',fill:color}));defs.append(marker);arrow.append(defs);
  if(from===to)arrow.append(svg('path',{d:`M ${x1} 18 C ${x1+90} 0, ${x1+90} 28, ${x1} 22`,fill:'none',stroke:color,'stroke-width':1.5,'marker-end':`url(#flow-arrow-${index})`}));
  else arrow.append(svg('line',{x1,x2,y1:15,y2:15,stroke:color,'stroke-width':1.5,'marker-end':`url(#flow-arrow-${index})`}));
  arrow.append(svg('circle',{cx:x1,cy:15,r:3,fill:color}));
  const track=el('div','flow-message-track');track.style.gridTemplateColumns=`repeat(${actors.length},minmax(0,1fr))`;
  const card=messageCard(e);card.style.gridColumn=`${Math.min(from,to)+1} / ${Math.max(from,to)+2}`;track.append(card);row.append(arrow,track);return row;
}
function renderFlow(content) {
  const events=filtered(),nodes=C.activityGroups(events,{groupTools:S.groupTools,includeRuntime:S.showRuntime});
  const tools=nodes.filter(n=>n.type==='tools').reduce((n,item)=>n+item.events.length,0),messages=nodes.filter(n=>n.type==='message').length;
  $('event-count').textContent=`${tools} tool calls · ${messages} messages`;
  if(!nodes.length){content.append(empty('No activity in this selection','Choose another participant or clear the search.'));return;}
  const actors=flowActors(events,S.showRuntime),canvas=el('div','workflow-canvas');canvas.style.setProperty('--columns',actors.length);canvas.append(flowHeader(actors));
  nodes.slice(0,S.limit).forEach((node,index)=>{
    if(node.type==='message'){canvas.append(flowMessageRow(node.event,actors,index));return;}
    const row=el('div','flow-tool-row');row.style.gridTemplateColumns=`repeat(${actors.length},minmax(0,1fr))`;
    const turn=el('div','tool-turn');turn.style.gridColumn=actors.indexOf(node.actor)+1;turn.style.borderLeftColor=colors[node.actor]||'#8390a2';
    const heading=el('div','tool-turn-heading');heading.append(el('span','','Tools'),el('span','',`${node.events.length} call${node.events.length===1?'':'s'}`));turn.append(heading);
    const byName=new Map();node.events.forEach(e=>{if(!byName.has(e.name))byName.set(e.name,[]);byName.get(e.name).push(e);});
    for(const [name,calls]of byName){
      if(name==='run_command'){
        calls.forEach(e=>{
          const command=C.commandDetails(e),card=button('','command-card',()=>selectEvent(e));
          card.setAttribute('aria-label',`Inspect run_command: ${command.description||'No description recorded'}`);
          card.append(el('code','command-tool','run_command'),
            el('span','command-description',command.description||'No description recorded in this trace.'),
            el('code','command-argv',command.command));
          turn.append(card);
        });
        continue;
      }
      const chip=button('','tool-chip',()=>{selectEvent(calls[0]);if(calls.length>1){const list=el('div','group-call-list');calls.forEach((e,i)=>{let label=name;try{const a=JSON.parse(e.arguments);if(a.argv?.length)label+=' · '+a.argv[0];else if(a.path)label+=' · '+a.path;}catch(_){}list.append(button(`${i+1}. ${label}`,'group-call',()=>selectEvent(e)));});$('inspector-content').append(detailSection(`${calls.length} calls in this group`,list));}});
      chip.setAttribute('aria-label',`Inspect ${name}${calls.length>1?' ('+calls.length+' calls)':''}`);chip.append(el('code','',name||'unknown tool'));
      if(calls.length>1)chip.append(el('span','tool-count','×'+calls.length));turn.append(chip);
    }
    row.append(turn);canvas.append(row);
  });content.append(canvas);
  if(nodes.length>S.limit)content.append(button('Show more activity','load-more',()=>{S.limit+=180;renderView();}));
}
function renderMessages(content) {
  let events=filtered().filter(e=>e.kind==='agent_message');
  if(S.direct)events=events.filter(e=>e.message?.sender!=='runtime'&&e.message?.recipient!=='runtime');
  $('event-count').textContent=`${events.length} messages`;
  if(!events.length){content.append(empty('No messages in this selection','Disable the direct-only filter to include Runtime dispatches, or adjust replay position.'));return;}
  const conversationActors=flowActors(events,!S.direct),conversation=el('div','workflow-canvas');conversation.style.setProperty('--columns',conversationActors.length);conversation.append(flowHeader(conversationActors));
  events.slice(0,S.limit).forEach((e,i)=>conversation.append(flowMessageRow(e,conversationActors,i)));content.append(conversation);
  if(events.length>S.limit)content.append(button('Show more messages','load-more',()=>{S.limit+=180;renderView();}));
}

function table(headers) {const wrap=el('div','table-wrap'),t=el('table','data-table'),head=el('thead'),tr=el('tr'),body=el('tbody');headers.forEach(h=>tr.append(el('th','',h)));head.append(tr);t.append(head,body);wrap.append(t);return {wrap,body};}
function section(title){const n=el('section','content-section');n.append(el('h3','section-title',title));return n;}
function renderPlans(content) {
  const events=filtered(),plans=events.filter(e=>e.kind==='plan_proposed'),moves=events.filter(e=>e.kind==='move_completed');
  $('event-count').textContent=`${plans.length} plans · ${moves.length} moves`;
  const ps=section('Plan history'),pt=table(['Version','Operations','Decision','Review source']);
  plans.forEach(e=>{
    const confirms=visibleEvents().filter(c=>c._run===e._run&&c.kind==='confirmation'&&c.plan_id===e.plan?.plan_id&&c.version===e.plan?.version);
    const c=confirms[confirms.length-1],tr=el('tr');tr.dataset.uid=e._uid;tr.addEventListener('click',()=>selectEvent(e));
    const version=el('td','mono',`v${e.plan?.version??'?'}`);version.append(el('div','source-label',e.plan?.plan_id||''));
    tr.append(version,el('td','',e.plan?.operations?.length??'—'));
    const d=el('td');d.append(badge(c?c.accepted===true?'Approved':'Declined':'Not confirmed',c?c.accepted===true?'good':'warn':''));tr.append(d,el('td','',c?.source||'—'));pt.body.append(tr);
  });ps.append(plans.length?pt.wrap:el('p','usage-note','No proposed plans in this selection.'));content.append(ps);
  const ms=section('Completed moves'),mt=table(['From','To','Evidence']);
  moves.slice(0,S.limit).forEach(e=>{const tr=el('tr');tr.dataset.uid=e._uid;tr.addEventListener('click',()=>selectEvent(e));tr.append(el('td','path',e.source),el('td','path',e.destination),el('td','mono',e.event_id));mt.body.append(tr);});
  ms.append(moves.length?mt.wrap:el('p','usage-note','No move_completed records. A proposed move is not a completed move.'));content.append(ms);
  if(moves.length>S.limit)content.append(button('Show more moves','load-more',()=>{S.limit+=180;renderView();}));
  for(const run of selectedRuns()) {
    const m=run.sidecars['manifest.json'];if(!m)continue;
    const ss=section('Recorded file inventory');ss.append(el('p','sidecar-note',`${run.id} · manifest snapshot${S.cursor!==null?' · final state, not replay position':''}`));
    const grid=el('div','file-grid');for(const [field,label] of [['before','Before'],['after','After']]){
      const box=el('div','file-box'),rows=Array.isArray(m[field])?m[field]:[];box.append(el('h3','',`${label} · ${rows.length} files`));
      rows.slice(0,100).forEach(r=>box.append(el('div',`file-path${field==='after'?' done':''}`,r.path)));
      if(rows.length>100)box.append(el('p','usage-note',`${rows.length-100} additional paths in the manifest.`));if(!Array.isArray(m[field]))box.append(el('p','usage-note','Snapshot not recorded.'));grid.append(box);
    }ss.append(grid);if(typeof m.contents_preserved==='boolean')ss.append(el('p','usage-note',`Content preservation: ${m.contents_preserved?'confirmed':'not confirmed'} by the runtime's recorded comparison.`));content.append(ss);
    const verification=run.sidecars['verification.json'];
    if(verification){const audit=section('Separate verification report');audit.append(el('p','sidecar-note','Supplied post-run audit; separate from model claims and runtime events.'));
      const details=el('details');details.append(el('summary','usage-note',`Inspect verification.json · ${verification.status||'status not specified'}`),jsonBlock(verification));audit.append(details);content.append(audit);}
  }
}
function renderUsage(content) {
  // Usage is for the complete selected source runs, independent of replay cutoff.
  const runs=selectedRuns(),s=C.summary(runs);$('event-count').textContent=`${s.responses} responses`;
  const us=section('Input, output, and repeated context');
  const cards=el('div','usage-cards');for(const [label,value] of [['Total input',s.usageResponses?fmt(s.input):'Unavailable'],['Cached input (subset)',s.usageResponses?fmt(s.cached):'Unavailable'],['Generated output',s.usageResponses?fmt(s.output):'Unavailable']]){const d=el('div','usage-card');d.append(el('small','',label),el('strong','',value));cards.append(d);}us.append(cards);
  const t=table(['Agent','Calls','Input','Cached','Output','Input share']);
  Object.entries(s.agents).sort((a,b)=>b[1].input-a[1].input).forEach(([a,u])=>{
    const tr=el('tr'),name=el('td');name.append(dot(a),document.createTextNode(` ${names[a]||a}`));tr.append(name,el('td','numeric',fmt(u.calls)),el('td','numeric',u.usageResponses?fmt(u.input):'—'),el('td','numeric',u.usageResponses?fmt(u.cached):'—'),el('td','numeric',u.usageResponses?fmt(u.output):'—'));
    const share=el('td','numeric',s.input?`${(100*u.input/s.input).toFixed(1)}%`:'—'),bar=el('div','usage-bar'),fill=el('span');fill.style.width=`${s.input?100*u.input/s.input:0}%`;fill.style.background=colors[a]||'#8190a0';bar.append(fill);share.append(bar);tr.append(share);t.body.append(tr);
  });us.append(t.wrap,el('p','usage-note',`Usage coverage: ${s.usageResponses}/${s.responses} responses; ${s.calls} requests. ${s.usageComplete?'Returned usage is complete.':'Missing response usage is unknown, not zero.'} Cached input is already part of total input. Reasoning tokens (${fmt(s.reasoning)}) are already part of output. Full-run totals do not change during replay.`));content.append(us);
  const cs=section('Saved cost estimates'),ct=table(['Run','Estimate','Coverage']);
  for(const run of runs){const report=run.sidecars['cost_estimate.json'],tr=el('tr');tr.append(el('td','mono',run.id),el('td','numeric',money(C.number(report?.estimated_usd))),el('td','',report?report.usage_complete===true?'Recorded complete':report.usage_complete===false?'Partial':'Unknown':'No report'));ct.body.append(tr);}
  cs.append(ct.wrap,el('p','usage-note','These are saved estimates, not invoices. The viewer does not invent prices or charge for replay. Aggregate costs include each loaded run once; importing an analysis reference alone does not import or charge its source run.'));
  for(const run of runs){const report=run.sidecars['cost_estimate.json'];if(report){const d=el('details');d.append(el('summary','usage-note',`${run.id} · pricing assumptions and recorded report`),jsonBlock(report));cs.append(d);}}content.append(cs);
}
function jsonBlock(data) {
  const text=JSON.stringify(data,null,2)??'null',wrap=el('div'),pre=el('pre','json-block',text.slice(0,35000));wrap.append(pre);
  if(text.length>35000)wrap.append(button(`Show full JSON (${fmt(text.length)} characters)`,'text-button',e=>{pre.textContent=text;e.currentTarget.remove();}));return wrap;
}
function detailSection(title,node){const s=el('section','inspector-section');s.append(el('h3','',title),node);return s;}
function renderInspector(e) {
  const content=$('inspector-content');content.replaceChildren();
  if(!e){const d=el('div','empty-inspector');d.append(el('span','','⌕'),el('h3','','Follow the evidence'),el('p','','Select an event or message to see its exact inputs, outputs, and related records.'));content.append(d);return;}
  if(['flow','messages'].includes(S.view)){
    const isTool=e.kind==='tool_request';content.append(badge(isTool?'Tool call':'Message'),el('h3','inspector-title',isTool?e.name:`${names[e.message?.sender]||e.message?.sender} → ${names[e.message?.recipient]||e.message?.recipient}`));
    if(isTool){let args=e.arguments;try{args=JSON.parse(args);}catch(_){}const command=C.commandDetails(e);if(command)content.append(detailSection('Executor explanation',el('p','detail-copy readable-text',command.description||'No description recorded in this trace.')),el('p','detail-note','Agent-provided intent; check the actual command and result.'));content.append(detailSection('Arguments',jsonBlock(args)));const result=findEvent(e._peer);if(result)content.append(detailSection('Result',jsonBlock(result.result)));else content.append(el('p','detail-note','No result was recorded.'));}
    else content.append(detailSection(e.message?.type?.replaceAll('_',' ')||'Message',jsonBlock(e.message?.payload)));
    const raw=el('details');raw.append(el('summary','','Source record'),jsonBlock(C.raw(e)));content.append(raw);return;
  }
  const [tag]=evidence(e);content.append(badge(tag),el('h3','inspector-title',e._title),el('div','detail-meta',`${e._run}\n${e.event_id||'?'} · ${e.timestamp||'Time unknown'}`));
  const kv=el('dl','detail-kv');for(const [label,value] of [['Participant',names[e._actor]||e._actor],['Event type',e.kind],['Source line',e._line??'Bundled event']])kv.append(el('dt','',label),el('dd','',value));content.append(kv);
  if(e.kind==='agent_message') {
    const m=e.message||{};content.append(detailSection('Message envelope',jsonBlock({sender:m.sender,recipient:m.recipient,type:m.type,message_id:m.message_id})),detailSection('Exact payload',jsonBlock(m.payload)));
    content.append(el('p','detail-note',`Sent/queued at ${e.timestamp||'unknown time'}. A recipient model input can occur later.`));
    const recipientInput=findEvent(e._recipientInput);
    if(recipientInput){content.append(el('p','detail-note',`First recorded recipient input: ${recipientInput.event_id||'record'} · ${recipientInput.timestamp||'unknown time'}.`),button('Inspect recipient model input ↗','peer-button',()=>selectEvent(recipientInput)));}
    else content.append(el('p','detail-note',m.recipient==='runtime'?'Runtime records this message without an agent activation.':'No matching recipient model input is recorded.'));
    if(m.sender!=='runtime')content.append(el('p','detail-note','An agent message reports information or requests an action. Verify factual claims against the referenced tool observations.'));
    if(m.payload?.analysis_provenance)content.append(el('p','detail-note','Replayed analysis from another run. This message is not a new Analyzer response.'));
  } else if(e.kind==='tool_request') {
    let a=e.arguments;try{a=JSON.parse(a);}catch(_){}
    const command=C.commandDetails(e);if(command)content.append(detailSection('Executor explanation',el('p','detail-copy readable-text',command.description||'No description recorded in this trace.')),el('p','detail-note','Agent-provided intent; check the actual command and result.'));
    content.append(detailSection('Tool arguments',jsonBlock(a)));
    const peer=e._peer?findEvent(e._peer):null;
    if(peer)content.append(detailSection('Recorded tool result',jsonBlock(peer.result)));
    else content.append(el('p','detail-note','No matching result is recorded.'));
    content.append(el('p','detail-note','A command result is an observation. Exit codes alone do not imply a policy-framework interception.'));
  } else if(e.kind==='tool_result')content.append(detailSection('Tool observation',jsonBlock(e.result)));
  else if(e.kind==='model_response') {
    content.append(el('p','detail-note','Visible model output is a claim or requested action, not independently verified evidence. Hidden reasoning is not available.'));
    if(e.message?.content)content.append(detailSection('Visible response',el('div','detail-copy readable-text',e.message.content)));
    if(e.message?.tool_calls)content.append(detailSection('Requested tools',jsonBlock(e.message.tool_calls)));
    content.append(detailSection('Returned usage',jsonBlock(e.usage??{note:'Usage not provided.'})));
  } else if(e.kind==='model_request') {
    content.append(el('p','detail-note',`${e.messages?.length??0} context messages and ${e.tools?.length??0} tool definitions were sent. Expand raw context below to inspect repeated history.`));
    const counts=Object.create(null);(e.messages||[]).forEach(m=>counts[m.role]=(counts[m.role]||0)+1);content.append(detailSection('Context structure',jsonBlock(counts)));
  } else if(e.kind==='plan_proposed')content.append(detailSection('Proposed plan',jsonBlock(e.plan)));
  else if(e.kind==='analysis_reused') {
    content.append(el('p','detail-note','Recorded recovery provenance. No new Analyzer model response is implied, and source paths are not opened automatically.'));
    if(S.runs.some(r=>r.id===e.source_run_id))content.append(button(`Inspect source run ${e.source_run_id}`,'peer-button',()=>switchRun(e.source_run_id)));
    else content.append(el('p','detail-copy','Import the source trace explicitly to inspect its evidence.'));
  } else if(e.kind==='confirmation')content.append(el('p','detail-note',`Recorded ${e.accepted===true?'approval':'decline'} by ${e.source||'unknown source'}. This applies to the recorded plan ID, version and digest.`));
  else if(e.kind==='policy_decision')content.append(el('p','detail-note','Explicit policy-decision record. Check its checkpoint, policy ID and provenance in the raw event.'));
  else if(e.kind==='move_completed')content.append(el('p','detail-note','The runtime journaled this move after observing source absence and a matching destination hash.'));
  if(e._peer)content.append(button(`Inspect related ${findEvent(e._peer)?.kind.replaceAll('_',' ')||'event'} ↗`,'peer-button',()=>{const peer=findEvent(e._peer);if(peer)selectEvent(peer);}));
  const raw=el('details');raw.open=!['model_request','model_response','agent_message','tool_request','tool_result','plan_proposed'].includes(e.kind);
  raw.append(el('summary','','Exact source event'),jsonBlock(C.raw(e)));content.append(raw);
}
function switchRun(id) {pause();S.run=id;S.cursor=null;S.selected=null;S.limit=180;S.networkRoute=null;S.chatAgent=null;S.chatPeer='';S.chatOpen=false;S.chatMode='messages';notice('');closeInspector();$('run-select').value=id;render();}
function pause(){if(S.timer)clearInterval(S.timer);S.timer=null;$('play').textContent='▶ Replay';}
function step(delta){pause();const seq=sequence();S.cursor=Math.max(0,Math.min(seq.length-1,(S.cursor===null?seq.length-1:S.cursor)+delta));renderPlayback();renderView();if(seq[S.cursor])selectEvent(seq[S.cursor],false);}
function startReplay(){
  if(S.timer){pause();return;}const seq=sequence();if(!seq.length)return;
  if(S.cursor===null||S.cursor>=seq.length-1)S.cursor=0;$('play').textContent='Ⅱ Pause';closeInspector();renderPlayback();renderView();
  S.timer=setInterval(()=>{if(S.cursor>=seq.length-1){pause();return;}S.cursor++;renderPlayback();renderView();selectEvent(seq[S.cursor],false);},Number($('speed').value));
}
async function importFiles(files) {
  const grouped=new Map(),sidecars=[];let importedChain=false;
  try {
    for(const file of files){
      if(file.size>100000000){toast(`${file.name}: exceeds 100 MB limit.`);continue;}
      const path=file.webkitRelativePath||file._relativePath||file.name,folder=path.includes('/')?path.slice(0,path.lastIndexOf('/')):'',name=path.split('/').pop();
      if(!/\.(jsonl|json)$/i.test(name))continue;
      if(/\.jsonl$/i.test(name)) {
        const b={name:path,trace_text:await file.text(),sidecars:{},warnings:[]};if(!grouped.has(folder))grouped.set(folder,[]);grouped.get(folder).push(b);
      }else if(['manifest.json','cost_estimate.json','verification.json','trace_chain.json','demo_settings.json','demo_summary.json'].includes(name)) {
        let data;try{data=JSON.parse(await file.text());}catch(err){throw new Error(`${path}: invalid JSON (${err.message})`);}
        if(name==='trace_chain.json'){S.chains.push(data);importedChain=true;}else sidecars.push({folder,name,data});
      }else if(name==='demo.json'){
        const data=JSON.parse(await file.text());if(Array.isArray(data.bundles))data.bundles.forEach(b=>{const k=`demo:${b.name}`;grouped.set(k,[b]);});
      }
    }
    for(const {folder,name,data} of sidecars) {
      let candidates=grouped.get(folder)||[];
      if(candidates.length!==1 && data?.run_id)candidates=[...grouped.values()].flat().filter(b=>C.parseJSONL(b.trace_text,b.name).events.some(e=>e.run_id===data.run_id));
      if(candidates.length===1)candidates[0].sidecars[name]=data;
      else if(grouped.size) [...grouped.values()][0][0].warnings.push(`${name} not attached: ambiguous or missing corresponding trace. Import a run folder to preserve associations.`);
    }
    const bundles=[...grouped.values()].flat();
    if(bundles.length)addRuns(bundles);
    else if(sidecars.length){
      let attached=0;
      sidecars.forEach(({name,data})=>{const candidates=data?.run_id?S.runs.filter(r=>r.id===data.run_id):selectedRuns();if(candidates.length===1){candidates[0].sidecars[name]=data;attached++;}});
      if(attached){render();toast(`${attached} sidecar(s) attached to the selected run.`);}else toast('Import the matching trace folder to attach these sidecars unambiguously.');
    }else if(!importedChain)toast('Select trace.jsonl or a folder containing a trace.');
    if(importedChain){renderRunOptions();notice('Trace chain recorded. Import its associated trace files explicitly; paths inside the chain are never opened automatically.');}
  }catch(err){notice(`Import stopped: ${err.message}`,true);}
}
async function loadDemo(){try{const data=await(await fetch('demo.json',{cache:'no-store'})).json();addRuns(data.bundles);notice('Synthetic scripted demo · actual offline execution, no LLM calls. Local workspace paths were replaced for distribution.');}catch(err){notice(`Could not load the demo. Start the local viewer server, then try again. ${err.message}`,true);}}
document.querySelectorAll('[data-view]').forEach(b=>b.addEventListener('click',()=>setView(b.dataset.view)));
$('run-select').addEventListener('change',()=>switchRun($('run-select').value));
for(const id of ['actor-filter','kind-filter'])$(id).addEventListener('change',()=>{S.limit=180;renderView();});
let searchTimer;$('search').addEventListener('input',()=>{clearTimeout(searchTimer);searchTimer=setTimeout(()=>{S.limit=180;renderView();},100);});
$('previous').addEventListener('click',()=>step(-1));$('next').addEventListener('click',()=>step(1));$('play').addEventListener('click',startReplay);
$('seek').addEventListener('input',()=>{pause();S.cursor=Number($('seek').value);renderPlayback();renderView();const e=sequence()[S.cursor];if(e)selectEvent(e,false);});
$('show-all').addEventListener('click',()=>{pause();S.cursor=null;renderPlayback();renderView();});$('speed').addEventListener('change',()=>{if(S.timer){pause();startReplay();}});
$('close-inspector').addEventListener('click',closeInspector);
$('import-button').addEventListener('click',()=>$('file-input').click());$('folder-button').addEventListener('click',()=>$('folder-input').click());
for(const id of ['file-input','folder-input'])$(id).addEventListener('change',async e=>{await importFiles([...e.target.files]);e.target.value='';});
$('demo-button').addEventListener('click',loadDemo);
$('paste-button').addEventListener('click',()=>$('paste-dialog').showModal());
$('paste-load').addEventListener('click',()=>{
  const text=$('paste-text').value;if(!text.trim()){toast('Paste at least one JSONL event.');return;}
  if(new Blob([text]).size>100000000){toast('Pasted trace exceeds the 100 MB limit.');return;}
  addRuns([{name:$('paste-name').value.trim()||'pasted-trace.jsonl',trace_text:text,sidecars:{}}]);$('paste-dialog').close();
});
let dragDepth=0;document.addEventListener('dragenter',e=>{if([...e.dataTransfer.types].includes('Files')){e.preventDefault();dragDepth++;$('drop-overlay').hidden=false;}});
document.addEventListener('dragleave',()=>{dragDepth=Math.max(0,dragDepth-1);if(!dragDepth)$('drop-overlay').hidden=true;});document.addEventListener('dragover',e=>e.preventDefault());
async function entryFiles(entry,prefix='') {
  if(entry.isFile){const f=await new Promise((resolve,reject)=>entry.file(resolve,reject));f._relativePath=prefix+f.name;return [f];}
  if(entry.isDirectory){const reader=entry.createReader(),out=[];let children;do{children=await new Promise((resolve,reject)=>reader.readEntries(resolve,reject));for(const child of children)out.push(...await entryFiles(child,prefix+entry.name+'/'));}while(children.length);return out;}return [];
}
document.addEventListener('drop',async e=>{e.preventDefault();dragDepth=0;$('drop-overlay').hidden=true;try{const items=[...e.dataTransfer.items],entries=items.map(i=>i.webkitGetAsEntry?.()).filter(Boolean);const files=entries.length?(await Promise.all(entries.map(x=>entryFiles(x)))).flat():[...e.dataTransfer.files];await importFiles(files);}catch(err){notice(`Could not read dropped files: ${err.message}`,true);}});
document.addEventListener('keydown',e=>{if(e.key==='Escape')closeInspector();});
(async function init(){
  renderRunOptions();render();
  try{const data=await(await fetch('api/bootstrap',{cache:'no-store'})).json();if(data.bundles?.length)addRuns(data.bundles);else if(data.live)notice('Start a live run above to see agent messages here.');else await loadDemo();}
  catch(_){notice('Open this viewer through python -m downloads_organizer view, or import local trace files. No model calls are needed.');}
})();
