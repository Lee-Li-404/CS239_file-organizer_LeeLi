/* Shared browser/Node parser. Input strings remain data, never markup or code. */
(function (root) {
  'use strict';
  const known = new Set(['run_started','run_finished','run_failed','agent_message','model_request','model_response',
    'tool_request','tool_result','plan_proposed','confirmation','plan_review_feedback','move_intent','move_completed',
    'analysis_reused','execution_resumed','execution_recovered','plan_validation_failed','pending_work_limit','agent_reminder',
    'tool_call_compatibility','tool_protocol_stopped']);
  const number = v => typeof v === 'number' && Number.isFinite(v) && v >= 0 ? v : null;
  const object = v => v && typeof v === 'object' && !Array.isArray(v);
  const compact = v => typeof v === 'string' ? v : JSON.stringify(v ?? '');
  function parseJSONL(text, name = 'trace.jsonl') {
    const events = [], errors = [];
    String(text).replace(/^\uFEFF/, '').split(/\r?\n/).forEach((line, i) => {
      if (!line.trim()) return;
      try {
        const e = JSON.parse(line);
        if (!object(e) || typeof e.kind !== 'string') throw new Error('Event must be an object with a string kind.');
        events.push({...e, _line: i + 1});
      } catch (err) { errors.push(`${name}, line ${i + 1}: ${err.message}`); }
    });
    return {events, errors};
  }
  function actor(e) {
    if (e.kind === 'agent_message') return e.message?.sender || 'unknown';
    if (e.agent || e.actor) return e.agent || e.actor;
    if (['confirmation','plan_review_feedback'].includes(e.kind)) return 'operator';
    return 'runtime';
  }
  function group(e) {
    if (e.kind === 'agent_message') return 'messages';
    if (e.kind.startsWith('tool_')) return 'tools';
    if (e.kind.startsWith('model_')) return 'model';
    if (['plan_proposed','confirmation','plan_review_feedback','plan_validation_failed'].includes(e.kind)) return 'plans';
    if (e.kind.startsWith('move_')) return 'files';
    if (e.kind === 'policy_decision') return 'policy';
    return known.has(e.kind) ? 'run' : 'other';
  }
  function title(e) {
    const m = e.message || {};
    switch (e.kind) {
      case 'agent_message': return `${m.sender || '?'} → ${m.recipient || '?'} · ${m.type || 'message'}`;
      case 'tool_request': return e.name || 'Tool request';
      case 'tool_result': return `${e.tool || 'Tool'} · result`;
      case 'tool_call_compatibility': return 'Tool-call format normalized';
      case 'tool_protocol_stopped': return 'Stopped after repeated tool-call errors';
      case 'model_request': return 'Model input context';
      case 'model_response': return m.tool_calls?.length ? `Model requested ${m.tool_calls.map(c => c.function?.name || 'tool').join(', ')}` : 'Visible model response';
      case 'plan_proposed': return `Plan v${e.plan?.version ?? '?'} · ${e.plan?.operations?.length ?? 0} operations`;
      case 'confirmation': return `Plan v${e.version ?? '?'} · ${e.accepted === true ? 'approved' : e.accepted === false ? 'declined' : 'decision unknown'}`;
      case 'plan_review_feedback': return 'Operator requested changes';
      case 'move_completed': return 'File move completed';
      case 'move_intent': return 'File move intended';
      case 'analysis_reused': return 'Analysis reused from an earlier run';
      case 'execution_resumed': return `Execution resumed · ${e.pending_operation_ids?.length ?? '?'} pending`;
      case 'run_finished': return `Run ${e.status || 'finished'}`;
      case 'run_failed': return `Run failed · ${e.error_type || 'unspecified'}`;
      default: return e.kind.replaceAll('_', ' ');
    }
  }
  function preview(e) {
    if (e.kind === 'agent_message') return compact(e.message?.payload);
    if (e.kind === 'tool_request') {
      const command = commandDetails(e);
      if(command) return `${command.description || 'No description recorded'} · ${command.command}`;
      try { const a = JSON.parse(e.arguments); return a.argv ? a.argv.map(v => JSON.stringify(v)).join(' ') : compact(a); }
      catch (_) { return compact(e.arguments); }
    }
    if (e.kind === 'tool_result') return compact(e.result);
    if (e.kind === 'tool_call_compatibility') return e.repairs?.map(r => r.reason).join(', ') || '';
    if (e.kind === 'tool_protocol_stopped') return e.note || e.error || '';
    if (e.kind === 'model_response') return e.message?.content || 'Tool selection; no visible explanation returned.';
    if (e.kind === 'model_request') return `${e.messages?.length ?? 0} context messages · ${e.tools?.length ?? 0} tools`;
    if (e.source && e.destination) return `${e.source} → ${e.destination}`;
    if (e.kind === 'plan_proposed') return e.plan?.summary || '';
    if (e.kind === 'confirmation') return `Source: ${e.source || 'unknown'} · ${e.plan_id || ''}`;
    if (e.kind === 'analysis_reused') return `Source ${e.source_run_id || 'unknown'} · not a new Analyzer response`;
    if (e.feedback) return e.feedback;
    return e.note || e.status || '';
  }
  function keyEvent(e) {
    if (['model_request','tool_request','tool_result','move_intent'].includes(e.kind)) return false;
    if (e.kind === 'model_response') return !!e.message?.content;
    return true;
  }
  function raw(e) { return Object.fromEntries(Object.entries(e).filter(([k]) => !k.startsWith('_'))); }
  function buildRuns(bundle) {
    const parsed = bundle.events ? {events: bundle.events, errors: []} : parseJSONL(bundle.trace_text || '', bundle.name);
    const buckets = new Map();
    for (const e of parsed.events) {
      const id = typeof e.run_id === 'string' && e.run_id ? e.run_id : `unidentified:${bundle.name || 'trace'}`;
      if (!buckets.has(id)) buckets.set(id, []);
      buckets.get(id).push(e);
    }
    return [...buckets].map(([id, input]) => {
      const warnings = [...parsed.errors, ...(bundle.warnings || [])], seen = new Set();
      const run = {id, name: bundle.name || id, warnings, events: [], sidecars: {}};
      const side = bundle.sidecars || {};
      if (buckets.size === 1) {
        for (const [name, data] of Object.entries(side)) {
          if (!object(data) || (data.run_id && data.run_id !== id)) warnings.push(`Ignored mismatched ${name}.`);
          else run.sidecars[name] = data;
        }
      } else if (Object.keys(side).length) warnings.push('Sidecars not attached: this file contains multiple runs.');
      const pendingTools = new Map(), pendingModels = new Map();
      input.forEach((value, i) => {
        const e = {...value, _run: id, _index: i, _uid: JSON.stringify([id, value.event_id || 'missing', i])};
        if (value.event_id && seen.has(value.event_id)) warnings.push(`Duplicate event ID ${value.event_id}; rows retained separately.`);
        seen.add(value.event_id);
        e._actor = actor(e); e._group = group(e); e._title = title(e); e._preview = preview(e);
        e._search = JSON.stringify(value).toLocaleLowerCase();
        run.events.push(e);
        if (e.kind === 'tool_request') {
          if (!e.call_id) warnings.push(`Tool request ${e.event_id || i} has no call ID.`);
          else {
            if (pendingTools.has(e.call_id)) warnings.push(`Repeated outstanding tool call ${e.call_id}.`);
            pendingTools.set(e.call_id, e);
          }
        }
        if (e.kind === 'tool_result') {
          const request = e.call_id ? pendingTools.get(e.call_id) : null;
          if (request) { e._peer = request._uid; request._peer = e._uid; pendingTools.delete(e.call_id); }
          else warnings.push(`Unmatched tool result ${e.call_id || e.event_id || i}.`);
        }
        if (e.kind === 'model_request') {
          if (pendingModels.has(e._actor)) warnings.push(`Unanswered model input before ${e.event_id || i}; sequence pairing may be incomplete.`);
          pendingModels.set(e._actor, e);
        }
        if (e.kind === 'model_response') {
          const request = pendingModels.get(e._actor);
          if (request) { e._peer = request._uid; request._peer = e._uid; pendingModels.delete(e._actor); }
          else warnings.push(`Unmatched model response ${e.event_id || i}.`);
        }
      });
      if (pendingTools.size) warnings.push(`${pendingTools.size} tool request(s) have no recorded result.`);
      if (pendingModels.size) warnings.push(`${pendingModels.size} model input(s) have no recorded response.`);
      return run;
    });
  }
  function summary(runs) {
    const agents = Object.create(null), result = {events:0, calls:0, responses:0, usageResponses:0, input:0, cached:0, output:0,
      reasoning:0, messages:0, runtimeMessages:0, replayedMessages:0, tools:0, moves:0, cost:null, costRuns:0,
      usageComplete:true, statuses:[], agents, warnings:0};
    for (const run of runs) {
      result.events += run.events.length; result.warnings += run.warnings.length;
      const finished = [...run.events].reverse().find(e => ['run_finished','run_failed'].includes(e.kind));
      const manifest = run.sidecars['manifest.json'];
      result.statuses.push(finished ? (finished.kind === 'run_failed' ? 'failed' : finished.status || 'finished') : manifest?.status || 'unfinished');
      const cost = number(run.sidecars['cost_estimate.json']?.estimated_usd);
      if (cost !== null) { result.cost = (result.cost || 0) + cost; result.costRuns++; }
      let requests = 0, responses = 0, complete = 0;
      for (const e of run.events) {
        if (e.kind === 'agent_message') {
          if (['runtime'].includes(e.message?.sender) || ['runtime'].includes(e.message?.recipient)) result.runtimeMessages++;
          else { result.messages++; if (e.message?.payload?.analysis_provenance) result.replayedMessages++; }
        }
        if (e.kind === 'tool_request') result.tools++;
        if (e.kind === 'move_completed') result.moves++;
        if (!['model_request','model_response'].includes(e.kind)) continue;
        const a = agents[e._actor] ||= {calls:0,responses:0,usageResponses:0,input:0,cached:0,output:0,reasoning:0};
        if (e.kind === 'model_request') { requests++; result.calls++; a.calls++; continue; }
        responses++; result.responses++; a.responses++;
        const u = e.usage;
        if (!object(u) || number(u.prompt_tokens) === null || number(u.completion_tokens) === null) continue;
        complete++; result.usageResponses++; a.usageResponses++;
        const values = {input:u.prompt_tokens, output:u.completion_tokens,
          cached:number(u.prompt_tokens_details?.cached_tokens) ?? 0,
          reasoning:number(u.completion_tokens_details?.reasoning_tokens) ?? 0};
        for (const [k,v] of Object.entries(values)) { result[k] += v; a[k] += v; }
      }
      if (requests !== responses || complete !== responses) result.usageComplete = false;
    }
    if (!result.responses) result.usageComplete = false;
    return result;
  }
  function filter(events, {actor: who='', group: category='', search='', mode='all', direct=false} = {}) {
    const query = search.trim().toLocaleLowerCase();
    return events.filter(e => (!who || e._actor === who || (e.kind === 'agent_message' && e.message?.recipient === who))
      && (!category || e._group === category) && (!query || e._search.includes(query))
      && (mode !== 'key' || keyEvent(e))
      && (!direct || (e.kind === 'agent_message' && e.message?.sender !== 'runtime' && e.message?.recipient !== 'runtime')));
  }
  function activityEvents(events, {includeRuntime=false} = {}) {
    const delivered = new Set(events.filter(e=>e.kind==='agent_message').map(e=>JSON.stringify([e._run||e.run_id,e.message?.message_id])).filter(Boolean));
    const transports = new Set(events.filter(e=>e.kind==='tool_result' && e.result?.message_id
      && delivered.has(JSON.stringify([e._run||e.run_id,e.result.message_id])))
      .map(e=>JSON.stringify([e._run||e.run_id,e.call_id])));
    return events.filter(e=>{
      if(e.kind==='agent_message') return includeRuntime || (e.message?.sender!=='runtime' && e.message?.recipient!=='runtime');
      if(e.kind!=='tool_request') return false;
      // A successfully delivered send_message is represented by its message,
      // while failed/unmatched sends remain visible as actual tool calls.
      return e.name!=='send_message' || !transports.has(JSON.stringify([e._run||e.run_id,e.call_id]));
    });
  }
  function activityGroups(events, {groupTools=true,includeRuntime=false} = {}) {
    const nodes=[];
    for(const e of activityEvents(events,{includeRuntime})) {
      if(e.kind==='agent_message'){nodes.push({type:'message',event:e});continue;}
      const last=nodes[nodes.length-1],who=actor(e),run=e._run||e.run_id;
      if(groupTools && last?.type==='tools' && last.actor===who && last.run===run)last.events.push(e);
      else nodes.push({type:'tools',actor:who,run,events:[e]});
    }
    return nodes;
  }
  function messageText(payload) {
    if(typeof payload==='string')return payload;
    if(!object(payload))return compact(payload);
    for(const key of ['text','content','message','feedback','reason'])if(typeof payload[key]==='string')return payload[key];
    if(payload.observations) {
      const observations=typeof payload.observations==='string'?payload.observations:Object.entries(payload.observations).map(([key,value])=>`${key}: ${compact(value)}`).join('\n');
      return observations;
    }
    if(payload.kind==='inventory')return Array.isArray(payload.files)
      ? `Inventory evidence · ${payload.files.length} files · request ${payload.request_id??'?'}`
      : `Request inventory · ${Array.isArray(payload.fields)?payload.fields.join(', '):'file metadata'}`;
    if(payload.kind==='content' && Array.isArray(payload.files)) {
      const observed=payload.files.some(f=>object(f)&&('status' in f || 'anonymized_excerpt' in f));
      const ready=payload.files.filter(f=>typeof f?.anonymized_excerpt==='string').length;
      return `${observed?'Content evidence':'Request content'} · ${payload.files.length} files${observed?` · ${ready} anonymized excerpts`:''} · request ${payload.request_id??'?'}\n${payload.files.map(f=>typeof f?.path==='string'?f.path:'Unknown file').join('\n')}`;
    }
    if(Array.isArray(payload.completed_operation_ids))return `${payload.completed_operation_ids.length} operations completed · ${Array.isArray(payload.conflicting_operations)?payload.conflicting_operations.length:0} conflicts${payload.blocked?' · blocked':''}`;
    if(payload.plan_id && Array.isArray(payload.pending_operation_ids))return `Plan ${payload.plan_id}@${payload.version??'?'} · ${payload.pending_operation_ids.length} pending operations`;
    if(Array.isArray(payload.files)) {
      const counts=new Map();payload.files.forEach(f=>{const category=typeof f?.category==='string'&&f.category?f.category:'Uncategorized';counts.set(category,(counts.get(category)||0)+1);});
      return `${payload.files.length} files analyzed. ${[...counts].map(([category,count])=>`${category}: ${count}`).join(' · ')}`;
    }
    if(payload.plan)return `Plan v${payload.plan.version??'?'} · ${payload.plan.operations?.length??0} file operations.${payload.instruction?' '+payload.instruction:''}`;
    return compact(payload);
  }
  function commandDetails(e) {
    if(e.kind!=='tool_request' || e.name!=='run_command')return null;
    let args=e.arguments;
    try{if(typeof args==='string')args=JSON.parse(args);}catch(_){return {description:null,command:'Arguments could not be parsed'};}
    if(!object(args))return {description:null,command:'Arguments could not be parsed'};
    const description=typeof args.description==='string' && args.description.trim()?args.description:null;
    const command=Array.isArray(args.argv)?args.argv.map(v=>JSON.stringify(v)).join(' '):'No argv recorded';
    return {description,command};
  }
  function networkGraph(events, {includeRuntime=false} = {}) {
    const actors = new Set(), routes = new Map(), messages = [];
    for(const event of events) {
      if(event.kind !== 'agent_message') continue;
      const m = event.message || {}, from=m.sender, to=m.recipient;
      if(!from || !to || (!includeRuntime && (from==='runtime' || to==='runtime'))) continue;
      actors.add(from); actors.add(to); messages.push(event);
      const key=JSON.stringify([from,to]);
      if(!routes.has(key)) routes.set(key,{from,to,events:[],count:0});
      const route=routes.get(key); route.events.push(event); route.count++;
    }
    return {actors:[...actors],links:[...routes.values()],messages};
  }
  function agentEvents(events, {agent='',peer='',mode='messages'} = {}) {
    return events.filter(e=>{
      const m=e.kind==='agent_message'?e.message:null;
      if(mode==='messages'&&!m)return false;
      if(agent && !(m?(m.sender===agent||m.recipient===agent):actor(e)===agent))return false;
      if(mode==='messages'&&peer&&(!m||!((m.sender===agent&&m.recipient===peer)||(m.sender===peer&&m.recipient===agent))))return false;
      return true;
    });
  }
  function chatActivity(events, {agent='',peer='',tools=true} = {}) {
    return activityEvents(events,{includeRuntime:true}).filter(e=>{
      if(e.kind==='agent_message')return agentEvents([e],{agent,peer}).length>0;
      return tools&&!peer&&(!agent||actor(e)===agent);
    });
  }
  function toolCallSummary(event, limit=110) {
    let args=event.arguments;
    try{if(typeof args==='string')args=JSON.parse(args);}catch(_){return `${event.name||'tool'}(unparsed arguments)`;}
    if(!object(args))args={};
    let detail='';
    if(Array.isArray(args.argv))detail=args.argv.map(v=>typeof v==='string'&&!/\s/.test(v)?v:JSON.stringify(v)).join(' ');
    else if(typeof args.path==='string'||typeof args.file==='string')detail=JSON.stringify(args.path??args.file);
    else if(Array.isArray(args.operations))detail=`${args.operations.length} operations`;
    else if(Array.isArray(args.operation_ids))detail=args.operation_ids.join(', ');
    else if(args.plan_id)detail=`${args.plan_id}@${args.version??'?'}`;
    else detail=Object.entries(args).slice(0,3).map(([k,v])=>`${k}=${compact(v)}`).join(', ');
    const label=`${event.name||'tool'}(${detail})`;
    return label.length>limit?label.slice(0,limit-2)+'…)':label;
  }
  const api = {parseJSONL,buildRuns,summary,filter,actor,group,title,preview,keyEvent,raw,number,activityEvents,activityGroups,messageText,commandDetails,networkGraph,agentEvents,chatActivity,toolCallSummary};
  root.TraceCore = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(globalThis);
