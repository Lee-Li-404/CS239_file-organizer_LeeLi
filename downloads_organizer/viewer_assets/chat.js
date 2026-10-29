/* Agent inbox and conversation history, derived entirely from saved events. */
(function(root){
  'use strict';
  const C=root.TraceCore;
  const roles=Object.assign(Object.create(null),{planner:'Coordinator',interpreter:'Intent interpreter',analyzer:'File analyst',arbiter:'Boundary reviewer',executor:'File operations',runtime:'Runtime',operator:'User / operator'});
  const palette=Object.assign(Object.create(null),{planner:'#58bd7b',interpreter:'#e8a13a',analyzer:'#8d60e7',arbiter:'#58b5a4',executor:'#e57b3f',runtime:'#858585'});
  const shapes=Object.assign(Object.create(null),{planner:'round',interpreter:'cloud',analyzer:'round',arbiter:'triangle',executor:'squircle',runtime:'squircle'});
  const fmt=n=>new Intl.NumberFormat('en-US').format(n);
  const short=n=>n>=1000?`${(n/1000).toFixed(1)}k`:fmt(n);
  function el(tag,cls,text){const n=document.createElement(tag);if(cls)n.className=cls;if(text!=null)n.textContent=String(text);return n;}
  function btn(text,cls,fn){const b=el('button',cls,text);b.type='button';b.addEventListener('click',fn);return b;}
  function avatar(actor,extra=''){const a=el('span',`chat-avatar ${shapes[actor]||'round'} ${extra}`);a.style.setProperty('--avatar-color',palette[actor]||'#7e97b1');a.setAttribute('aria-hidden','true');a.append(el('i','eye'),el('i','eye'));return a;}
  function groupAvatar(){const a=el('span','chat-group-avatar');a.setAttribute('aria-hidden','true');for(const actor of ['planner','analyzer','executor'])a.append(avatar(actor,'tiny-avatar'));return a;}
  function clock(e,seconds=false){const d=new Date(e?.timestamp);return Number.isFinite(+d)?d.toLocaleTimeString([],{hour:'numeric',minute:'2-digit',...(seconds?{second:'2-digit'}:{})}):'—';}
  function stamp(e,sent=false){const n=el('time','',`${sent?'Sent ':''}${clock(e,true)}`);n.title=e.timestamp||'Timestamp not recorded';return n;}
  function date(e){const d=new Date(e?.timestamp);return Number.isFinite(+d)?d.toLocaleDateString([],{month:'short',day:'numeric',year:'numeric'}):'Date not recorded';}
  function text(payload){
    if(!payload||typeof payload!=='object')return String(payload??'');
    const result=[];
    for(const key of ['instruction_summary','instruction','observations','reason','summary','feedback']){
      if(typeof payload[key]==='string'&&payload[key]&&!result.includes(payload[key]))result.push(payload[key]);
    }
    if(result.length)return result.join('\n\n');
    if(payload.plan_id)return `Plan ${payload.plan_id}@${payload.version??'?'}${Array.isArray(payload.pending_operation_ids)?`\nPending operations: ${payload.pending_operation_ids.join(', ')}`:''}`;
    if(Array.isArray(payload.files))return `${payload.files.length} files in this message.`;
    return Object.entries(payload).filter(([,v])=>['string','number','boolean'].includes(typeof v)).map(([k,v])=>`${k.replaceAll('_',' ')}: ${v}`).join('\n')||'Structured message. Open to inspect the complete payload.';
  }
  function render(content,ctx){
    const name=a=>ctx.names[a]||a||'Unknown';
    const events=ctx.runs.flatMap(r=>r.events);
    if(ctx.runs.length>1)events.sort((a,b)=>(Date.parse(a.timestamp)||0)-(Date.parse(b.timestamp)||0));
    const stats=C.summary(ctx.runs),agents=new Set();
    events.forEach(e=>{
      if(['model_request','model_response','tool_request'].includes(e.kind)&&e._actor&&!['runtime','operator','unknown'].includes(e._actor))agents.add(e._actor);
      if(e.kind==='agent_message')for(const a of [e.message?.sender,e.message?.recipient])if(a&&!['runtime','operator'].includes(a))agents.add(a);
    });
    const order=['planner','interpreter','analyzer','arbiter','executor'];
    const roster=[...agents].sort((a,b)=>(order.includes(a)?order.indexOf(a):99)-(order.includes(b)?order.indexOf(b):99));
    const chosen=agents.has(ctx.agent)?ctx.agent:'*';
    const agent=chosen==='*'?'':chosen;
    const allMessages=C.agentEvents(events,{agent,mode:'messages'});
    const trace=C.agentEvents(events,{agent,mode:'trace'});
    const activity=C.chatActivity(events,{agent,peer:ctx.peer,tools:ctx.tools!==false});
    const toolCount=C.chatActivity(events,{agent}).filter(e=>e.kind==='tool_request').length;
    const groupToolCount=C.chatActivity(events).filter(e=>e.kind==='tool_request').length;
    const eventMap=new Map(events.map(e=>[e._uid,e]));
    const layout=el('div',`agent-chat-app${ctx.open?' thread-open':''}`);
    const inbox=el('aside','chat-inbox');inbox.setAttribute('aria-label','Agent conversations');
    const top=el('div','chat-inbox-top');top.append(el('span','chat-wordmark','Agent chats'),btn('Import run','chat-quiet-button',ctx.onImport));inbox.append(top);
    const hero=el('div','chat-team');hero.append(avatar('planner','team-avatar'),el('h1','','File organizer'),el('p','',`${roster.length} agents · saved conversation`));inbox.append(hero);
    const controls=el('div','chat-run-controls');const runSelect=el('select','chat-run-select');runSelect.setAttribute('aria-label','Select chat run');
    if(ctx.allRuns.length>1){const o=el('option','','All loaded runs');o.value='*';runSelect.append(o);}
    for(const run of ctx.allRuns){const status=C.summary([run]).statuses[0],o=el('option','',`${run.id.slice(-12)} · ${status}`);o.value=run.id;runSelect.append(o);}
    runSelect.value=ctx.run;runSelect.addEventListener('change',()=>ctx.onRun(runSelect.value));
    const views=el('select','chat-views-select');views.setAttribute('aria-label','Other workspace views');
    for(const [value,label] of [['chat','Chat inbox'],['network','Network map'],['flow','Tools & messages'],['timeline','Full event log'],['usage','Token usage'],['plans','Plans & files']]){const o=el('option','',label);o.value=value;views.append(o);}views.addEventListener('change',()=>ctx.onView(views.value));controls.append(runSelect,views);inbox.append(controls);
    const searchBox=el('div','chat-inbox-search'),search=el('input');search.type='search';search.placeholder='Search agents or messages';search.setAttribute('aria-label','Search agents or messages');searchBox.append(search);inbox.append(searchBox);
    const list=el('div','chat-agent-list');list.setAttribute('aria-label','Choose an agent');
    const rows=[];
    function addAgent(a){
      const history=C.agentEvents(events,{agent:a==='*'?'':a,mode:'messages'}),last=history[history.length-1];
      const row=btn('',`chat-agent-row${a==='*'?' group-chat-row':''}${chosen===a?' selected':''}`,()=>ctx.onAgent(a));row.setAttribute('aria-label',a==='*'?'Open group chat':`Open ${name(a)} conversation`);row.setAttribute('aria-pressed',String(chosen===a));
      const body=el('div','chat-agent-row-body'),line=el('div','chat-agent-row-title');
      line.append(el('strong','',a==='*'?'Group chat':name(a)),el('span','chat-role',a==='*'?'All agents':roles[a]||'Agent'),el('time','',clock(last)));
      const preview=last?`${last.message.sender===a?'To '+name(last.message.recipient):name(last.message.sender)}: ${text(last.message.payload).replaceAll('\n',' ')}`:'No messages recorded';
      body.append(line,el('p','chat-last-message',a==='*'?`${history.length} messages · ${groupToolCount} tool calls · all agents`:preview));row.append(a==='*'?groupAvatar():avatar(a),body);list.append(row);
      rows.push({row,match:`${a==='*'?'Group chat all agents':name(a)} ${roles[a]||''} ${history.map(e=>e._search||text(e.message.payload)).join(' ')}`.toLowerCase()});
    }
    if(events.length)addAgent('*');roster.forEach(addAgent);inbox.append(list);
    const noResults=el('p','chat-inbox-empty','No matching agents or messages.');noResults.hidden=true;inbox.append(noResults);
    search.addEventListener('input',()=>{let count=0;const q=search.value.toLowerCase().trim();for(const item of rows){item.row.hidden=!!q&&!item.match.includes(q);if(!item.row.hidden)count++;}noResults.hidden=!!count;});
    if(!events.length)inbox.append(btn('Load example conversation','chat-load-demo',ctx.onDemo));
    inbox.append(el('div','chat-inbox-footer',`${fmt(stats.calls)} model calls · ${short(stats.input+stats.output)} tokens · local replay`));
    layout.append(inbox);

    const thread=el('section','chat-thread');thread.setAttribute('aria-label','Selected agent history');
    const header=el('header','chat-thread-header'),back=btn('‹ Agents','chat-back',ctx.onBack);header.append(back,chosen==='*'?groupAvatar():avatar(chosen));
    const heading=el('div','chat-thread-title');heading.append(el('h2','',chosen==='*'?'Group chat':name(chosen)),el('p','',`${chosen==='*'?`${roster.length} agents · all messages`:roles[chosen]||'Agent'} · ${allMessages.length} messages${stats.agents[chosen]?` · ${stats.agents[chosen].calls} model calls`:''}`));
    header.append(heading,btn('View trace ↗','chat-trace-shortcut',()=>ctx.onMode('trace')));thread.append(header);
    const tabs=el('div','chat-history-tabs');tabs.setAttribute('role','tablist');tabs.setAttribute('aria-label','Agent history type');
    for(const [value,label,count] of [['messages','Messages',allMessages.length],['trace','Trace',trace.length]]){
      const b=btn(`${label} ${count}`,`chat-history-tab${ctx.mode===value?' active':''}`,()=>ctx.onMode(value));b.setAttribute('role','tab');b.setAttribute('aria-selected',String(ctx.mode===value));tabs.append(b);
    }
    const historySearch=el('input','chat-history-search');historySearch.type='search';historySearch.placeholder='Search this history';historySearch.setAttribute('aria-label','Search selected agent history');tabs.append(historySearch);thread.append(tabs);
    if(ctx.mode==='messages'){
      const controls=el('div','chat-tool-controls'),label=el('label'),toggle=el('input');toggle.type='checkbox';toggle.checked=ctx.tools!==false;toggle.disabled=!!ctx.peer;toggle.addEventListener('change',()=>ctx.onTools(toggle.checked));
      label.append(toggle,document.createTextNode('Show tool calls'));controls.append(label,el('span','',ctx.peer?'Tool calls appear in All conversations':`${toolCount} calls · abbreviated · click for details`));thread.append(controls);
    }
    if(ctx.mode==='messages'&&chosen!=='*'){
      const peers=[...new Set(allMessages.map(e=>e.message.sender===agent?e.message.recipient:e.message.sender))];
      const chips=el('div','chat-peer-filters');chips.setAttribute('aria-label','Conversation partner');
      chips.append(btn('All conversations',`chat-peer-chip${!ctx.peer?' active':''}`,()=>ctx.onPeer('')));
      peers.forEach(peer=>chips.append(btn(name(peer),`chat-peer-chip${ctx.peer===peer?' active':''}`,()=>ctx.onPeer(peer))));thread.append(chips);
    }
    if(ctx.mode==='messages')thread.append(el('p','chat-order-note',ctx.runs.length>1?'Runs are combined by send time. Select one run for its recorded order.':'Recorded send order. Queued messages can reach the recipient’s model input later.'));
    const stream=el('div','chat-history');stream.setAttribute('role','tabpanel');stream.setAttribute('aria-label',ctx.mode==='messages'?'Agent message history':'Agent trace history');
    const items=ctx.mode==='messages'?activity:trace;let previousDate='';const entries=[];
    for(const event of items.slice(0,ctx.limit)){
      const day=date(event);if(day!==previousDate){stream.append(el('div','chat-date-divider',day));previousDate=day;}
      if(event.kind==='tool_request'){
        const result=eventMap.get(event._peer)?.result;
        const status=!result?'No result':result.timed_out?'Timed out':result.error?'Error':typeof result.exit_code==='number'?`Exit ${result.exit_code}`:'Done';
        const entry=btn('','chat-tool-event',()=>ctx.onInspect(event));entry.dataset.uid=event._uid;entry.setAttribute('aria-label',`Inspect ${name(event._actor)} ${event.name} tool call ${event.event_id}`);
        const meta=el('div','chat-tool-byline');meta.append(avatar(event._actor,'tiny-avatar'),el('strong','',name(event._actor)),el('span','chat-tool-status',status),stamp(event));
        entry.append(meta,el('code','chat-tool-summary',C.toolCallSummary(event)),el('span','chat-tool-open',`Arguments & result ↗ · ${event.event_id}`));stream.append(entry);entries.push({node:entry,event});
      }else if(ctx.mode==='messages'){
        const m=event.message,p=m.payload||{},outgoing=agent&&m.sender===agent;
        const entry=el('div',`chat-entry${outgoing?' outgoing':''}`);entry.dataset.uid=event._uid;
        const byline=el('div','chat-entry-byline');byline.append(avatar(m.sender,'tiny-avatar'),el('strong','',name(m.sender)),el('span','',`to ${name(m.recipient)}`),stamp(event,true));entry.append(byline);
        const bubble=btn('','chat-bubble',()=>ctx.onInspect(event));bubble.setAttribute('aria-label',`Inspect ${name(m.sender)} to ${name(m.recipient)} ${m.type||'message'} ${event.event_id}`);
        bubble.append(el('span','chat-message-type',(m.type||'message').replaceAll('_',' ')),el('p','chat-message-text',text(p)));
        if(Array.isArray(p.files)){
          const files=el('div','chat-file-report');
          for(const value of p.files){const file=value&&typeof value==='object'?value:{path:String(value)},row=el('div','chat-file-row');row.append(el('strong','',file.path||file.file_id||'File'),el('span',file.sensitive?'chat-sensitive':'',file.category||'Evidence requested'));if(file.reason)row.append(el('p','',file.reason));files.append(row);}bubble.append(files);
        }
        const recipientInput=eventMap.get(event._recipientInput);
        bubble.append(el('span','chat-message-delivery',recipientInput?`${name(m.recipient)} input: ${recipientInput.event_id||'record'} · ${clock(recipientInput,true)}`:m.recipient==='runtime'?'Recorded by Runtime; no agent input expected.':'No recipient model input recorded.'));
        bubble.append(el('span','chat-inspect-link',`View message & trace ↗  ·  ${event.event_id||'source record'}`));entry.append(bubble);stream.append(entry);entries.push({node:entry,event});
      }else{
        const entry=btn('','chat-trace-event',()=>ctx.onInspect(event));entry.dataset.uid=event._uid;
        const meta=el('div','chat-trace-meta');meta.append(el('span',`chat-trace-kind kind-${event._group}`,event.kind.replaceAll('_',' ')),el('span','',event.event_id||'record'),stamp(event));
        const title=event.kind==='agent_message'?`${name(event.message?.sender)} → ${name(event.message?.recipient)}`:event._title;
        entry.append(meta,el('strong','chat-trace-event-title',title));
        let preview=event._preview||'';
        if(event.kind==='model_response'&&event.message?.tool_calls?.length)preview=event.message.tool_calls.map(c=>c.function?.name||'tool').join(', ');
        entry.append(el('p','',preview.length>230?preview.slice(0,230)+'…':preview),el('span','chat-inspect-link','Inspect exact source ↗'));stream.append(entry);entries.push({node:entry,event});
      }
    }
    if(items.length>ctx.limit)stream.append(btn(`Show ${Math.min(180,items.length-ctx.limit)} more records`,'chat-load-more',ctx.onMore));
    const empty=el('p','chat-history-empty',items.length?'No matching messages or trace records.':'No records in this conversation.');empty.hidden=!!items.length;stream.append(empty);
    historySearch.addEventListener('input',()=>{const q=historySearch.value.toLowerCase().trim();let count=0;for(const entry of entries){entry.node.hidden=!!q&&!(entry.event._search||entry.node.textContent).toLowerCase().includes(q);if(!entry.node.hidden)count++;}empty.hidden=!!count;stream.querySelectorAll('.chat-date-divider').forEach(d=>{d.hidden=!!q;});});
    thread.append(stream,el('footer','chat-thread-footer',ctx.mode==='messages'?'Sent order · recipient input may occur later · click for source records':'Recorded model inputs, outputs, tool calls, and results · click to inspect'));
    layout.append(thread);content.append(layout);
  }
  root.TraceChat={render};
})(globalThis);
