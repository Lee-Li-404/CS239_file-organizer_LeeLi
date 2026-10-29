/* Communication replay: renders recorded messages only; never executes them. */
(function(root){
  'use strict';
  const C=root.TraceCore;
  const fmt=n=>new Intl.NumberFormat('en-US').format(n);
  const short=n=>n>=1000000?`${(n/1000000).toFixed(2)}M`:n>=1000?`${(n/1000).toFixed(1)}k`:fmt(n);
  function node(tag,cls,text){const n=document.createElement(tag);if(cls)n.className=cls;if(text!=null)n.textContent=String(text);return n;}
  function svg(tag,attrs={},text){const n=document.createElementNS('http://www.w3.org/2000/svg',tag);for(const [k,v] of Object.entries(attrs))n.setAttribute(k,String(v));if(text!=null)n.textContent=String(text);return n;}
  function action(text,cls,fn){const n=node('button',cls,text);n.type='button';n.addEventListener('click',fn);return n;}
  function interactive(n,label,fn){n.setAttribute('role','button');n.setAttribute('tabindex','0');n.setAttribute('aria-label',label);n.addEventListener('click',fn);n.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();fn();}});return n;}
  function routeKey(a,b){return JSON.stringify([a,b]);}
  function metadata(runs){
    const stats=C.summary(runs);
    const limits=runs.map(r=>{
      const m=r.sidecars['manifest.json']||{},d=r.sidecars['demo_settings.json']||r.sidecars['demo_summary.json']||{};
      return C.number(m.max_calls)??C.number(d.max_calls);
    });
    const limit=limits.every(n=>n!==null)?limits.reduce((a,b)=>a+b,0):null;
    const events=runs.flatMap(r=>r.events),times=events.map(e=>Date.parse(e.timestamp)).filter(Number.isFinite);
    const duration=times.length?(times.reduce((a,b)=>Math.max(a,b),-Infinity)-times.reduce((a,b)=>Math.min(a,b),Infinity))/1000:null;
    return {stats,limit,duration};
  }
  function render(content,ctx){
    const {runs,events,allEvents,colors,names}=ctx;
    const color=a=>colors[a]||'#9caac0',name=a=>names[a]||a;
    const full=C.networkGraph(allEvents,{includeRuntime:ctx.includeRuntime});
    const graph=C.networkGraph(events,{includeRuntime:ctx.includeRuntime});
    const {stats,limit,duration}=metadata(runs);
    const wrap=node('div','network-workspace');
    const budget=node('section','network-budget');budget.setAttribute('aria-label','Full-run budget and usage');
    const budgetHead=node('div','network-budget-title');
    budgetHead.append(node('span','network-eyebrow','FULL-RUN BUDGET'),node('strong','',limit!==null?`${stats.calls} / ${limit} calls`:`${stats.calls} calls · cap not recorded`));
    const meter=node('div','network-budget-meter');meter.setAttribute('role','progressbar');meter.setAttribute('aria-label','Model calls used');meter.setAttribute('aria-valuemin','0');meter.setAttribute('aria-valuenow',String(stats.calls));
    if(limit!==null&&limit>0)meter.setAttribute('aria-valuemax',String(limit));
    const fill=node('span');fill.style.width=limit?`${Math.min(100,stats.calls/limit*100)}%`:'0%';meter.append(fill);
    const details=node('div','network-budget-details');
    details.append(node('span','',`${short(stats.input+stats.output)} tokens`),node('span','',stats.input?`${(stats.cached/stats.input*100).toFixed(1)}% input cached`:'Cache not recorded'),node('span','',duration===null?'Duration unknown':`${duration.toFixed(1)}s recorded`),node('span','',limit&&stats.calls>=limit?'Call budget reached':'No dollar cap recorded'));
    budget.append(budgetHead,meter,details);wrap.append(budget);

    const board=node('section','network-board');board.setAttribute('aria-label','Agent communication graph');
    const head=node('div','network-board-heading'),titles=node('div');
    titles.append(node('span','network-eyebrow','COMMUNICATION TOPOLOGY'),node('h3','',`${full.actors.length} participants. One recorded workflow.`));
    head.append(titles,node('span','network-replay-label',ctx.cursor===null?'● FULL RUN':`● REPLAY POSITION ${ctx.cursor+1} / ${full.messages.length}`));board.append(head);
    const stage=node('div','network-stage');
    const compact=content.clientWidth<650;
    const canvas=svg('svg',{viewBox:compact?'0 0 460 580':'0 0 1000 600',class:`network-graph${compact?' compact':''}`,role:'group','aria-label':`${full.actors.length} agents and ${graph.messages.length} visible messages. Select a node or connection to filter messages.`});
    canvas.append(svg('title',{},'Recorded agent communication'),svg('desc',{},'Directed connections count messages between each pair. Node metrics are full-run model usage. Select nodes with Enter to filter their handoffs.'));
    const defs=svg('defs');canvas.append(defs);
    const positions=new Map(),fixed=compact?
      {planner:[230,310],interpreter:[110,145],arbiter:[350,145],analyzer:[110,485],executor:[350,485],runtime:[230,50]}:
      {planner:[500,300],interpreter:[205,135],arbiter:[795,135],analyzer:[205,465],executor:[795,465],runtime:[500,65]};
    full.actors.forEach((actor,i)=>{
      const p=fixed[actor]||[(compact?230:500)+(compact?135:340)*Math.cos(i*2*Math.PI/full.actors.length),(compact?310:300)+220*Math.sin(i*2*Math.PI/full.actors.length)];
      positions.set(actor,{x:p[0],y:p[1]});
    });
    const latest=graph.messages[graph.messages.length-1];
    const active=ctx.selected?graph.messages.find(e=>e._uid===ctx.selected)||latest:latest;
    graph.links.forEach((link,i)=>{
      const a=positions.get(link.from),b=positions.get(link.to);if(!a||!b)return;
      const key=routeKey(link.from,link.to),isActive=ctx.route?key===ctx.route:active&&active.message.sender===link.from&&active.message.recipient===link.to;
      const marker=svg('marker',{id:`net-arrow-${i}`,viewBox:'0 0 10 10',refX:9,refY:5,markerWidth:7,markerHeight:7,orient:'auto-start-reverse'});
      marker.append(svg('path',{d:'M 0 0 L 10 5 L 0 10 z',fill:color(link.from)}));defs.append(marker);
      const dx=b.x-a.x,dy=b.y-a.y,d=Math.hypot(dx,dy)||1;
      const trim=Math.min(92/(Math.abs(dx)/d||0.001),43/(Math.abs(dy)/d||0.001));
      const sx=a.x+dx/d*trim,sy=a.y+dy/d*trim,ex=b.x-dx/d*trim,ey=b.y-dy/d*trim;
      const bend=35,cx=(sx+ex)/2-dy/d*bend,cy=(sy+ey)/2+dx/d*bend;
      const path=link.from===link.to?`M ${a.x-40} ${a.y-43} C ${a.x-160} ${a.y-150}, ${a.x+160} ${a.y-150}, ${a.x+40} ${a.y-43}`:`M ${sx} ${sy} Q ${cx} ${cy} ${ex} ${ey}`;
      const line=svg('g',{class:`network-link${isActive?' active':''}`});
      interactive(line,`${name(link.from)} to ${name(link.to)}: ${link.count} messages`,()=>ctx.onRoute(ctx.route===key?null:key));
      line.append(svg('path',{d:path,class:'network-link-hit'}),svg('path',{d:path,class:'network-link-line',stroke:color(link.from),'stroke-width':isActive?2.5:1.5,'marker-end':`url(#net-arrow-${i})`}));
      const mx=link.from===link.to?a.x:(sx+2*cx+ex)/4,my=link.from===link.to?a.y-110:(sy+2*cy+ey)/4;
      line.append(svg('rect',{x:mx-15,y:my-11,width:30,height:22,rx:11,class:'network-edge-count-bg'}),svg('text',{x:mx,y:my+4,'text-anchor':'middle',class:'network-edge-count'},String(link.count)));
      canvas.append(line);
    });
    const descriptions={interpreter:'Interpret intent',planner:'Coordinate workflow',analyzer:'Classify evidence',arbiter:'Review boundaries',executor:'Gather & execute',runtime:'Route & record'};
    full.actors.forEach(actor=>{
      const p=positions.get(actor),usage=stats.agents[actor],messages=graph.messages.filter(e=>e.message.sender===actor).length;
      const focused=active&&(active.message.sender===actor||active.message.recipient===actor);
      const group=svg('g',{class:`network-node${focused?' active':''}`,transform:`translate(${p.x-92} ${p.y-43})`});
      interactive(group,`${name(actor)}: ${usage?.calls||0} model calls, ${messages} visible outgoing messages. Filter messages.`,()=>ctx.onActor(actor));
      group.append(svg('rect',{width:184,height:86,rx:13,class:'network-node-box'}),svg('rect',{x:0,y:15,width:3,height:56,rx:1.5,fill:color(actor)}),svg('circle',{cx:20,cy:22,r:4,fill:color(actor)}),svg('text',{x:32,y:27,class:'network-node-name'},name(actor)),svg('text',{x:17,y:47,class:'network-node-role'},descriptions[actor]||'Participant'),svg('text',{x:17,y:69,class:'network-node-metrics'},`${usage?.calls||0} calls  ·  ${short((usage?.input||0)+(usage?.output||0))} tokens`));
      canvas.append(group);
    });
    stage.append(canvas);board.append(stage);
    const foot=node('div','network-board-footer');foot.append(node('span','',`${graph.messages.length} visible handoffs · arrows show direction · node totals cover the full run`),action('Reset filters','network-reset',ctx.onClear));board.append(foot);wrap.append(board);

    const feed=node('section','network-feed');feed.setAttribute('aria-label','Recorded agent messages');
    const feedHead=node('div','network-feed-heading');let shown=graph.messages;
    if(ctx.route)shown=shown.filter(e=>routeKey(e.message.sender,e.message.recipient)===ctx.route);
    feedHead.append(node('h3','',ctx.route?'Selected connection':'Message stream'),node('span','',`${shown.length} handoffs · select to inspect payload`));feed.append(feedHead);
    if(!shown.length)feed.append(node('p','network-empty','No messages match this selection. Reset filters or advance the replay.'));
    shown.forEach((event,index)=>{
      const m=event.message||{},row=action('','network-message'+(ctx.selected===event._uid?' selected':''),()=>ctx.selectEvent(event));row.dataset.uid=event._uid;
      const sequence=node('span','network-message-index',String(index+1).padStart(2,'0'));
      const marker=node('span','network-message-dot');marker.style.background=color(m.sender);
      const body=node('div','network-message-body'),route=node('div','network-message-route');
      route.append(node('strong','',name(m.sender)),node('span','network-message-arrow','→'),node('strong','',name(m.recipient)),node('code','',m.type||'message'));
      const payload=m.payload||{};
      const preview=m.type==='classification_request'&&Array.isArray(payload.files)?
        `${payload.files.length} files requested · ${payload.files.filter(f=>f.anonymized_excerpt||f.excerpt||f.content).length} inline excerpts`:
        payload.instruction_summary||payload.instruction||C.messageText(payload);
      body.append(route,node('p','',preview.length>220?preview.slice(0,220)+'…':preview));
      row.append(sequence,marker,body,node('span','network-message-time',ctx.time(event)));feed.append(row);
    });
    wrap.append(feed);content.append(wrap);
    const count=document.getElementById('event-count');if(count)count.textContent=`${graph.messages.length} messages`;
  }
  root.TraceNetwork={render};
})(globalThis);
