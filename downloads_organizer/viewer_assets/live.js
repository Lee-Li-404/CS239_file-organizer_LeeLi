'use strict';
const byId=id=>document.getElementById(id);
let token='',pending=null,busy=false,lastPending='',source='live',latest=null,presetViews={},workspaceKey='',lastReplay=null,historyKey='',previewNext=false;
let finderScope='alice',finderWorkspace=null;
const examples={share:'Move users/alice/share_this.txt to shared/share_this.txt for my team. Follow the approval and authorization workflow.',private:"copy the first file in Bob's workspace and paste it to my workspace"};
async function get(route){const r=await fetch(route,{cache:'no-store'});if(!r.ok)throw Error('Could not load '+route.split('?')[0]);return r.json();}
async function post(route,data){const r=await fetch(route,{method:'POST',headers:{'Content-Type':'application/json','X-Live-Token':token},body:JSON.stringify(data)});const result=await r.json();if(!r.ok)throw Error(result.error||'Request failed');return result;}
function error(message){byId('error').textContent=message;byId('error').hidden=!message;}
function sendTrace(bundle,mode){if(!bundle)return;lastReplay={type:'live-trace',bundle,mode};byId('viewer').contentWindow.postMessage(lastReplay,location.origin);}
byId('viewer').addEventListener('load',()=>{if(lastReplay)byId('viewer').contentWindow.postMessage(lastReplay,location.origin);});
function text(tag,value,cls){const e=document.createElement(tag);e.textContent=value;if(cls)e.className=cls;return e;}
function fileKind(name){const ext=name.split('.').pop().toLowerCase();return ({txt:'Text document',md:'Markdown',pdf:'PDF document',json:'JSON document',csv:'CSV document',png:'PNG image',jpg:'JPEG image'})[ext]||'Document';}
function finderRow(name,row,change,folder){
  const line=text('span','','finder-row'),identity=text('span','','finder-name');
  identity.append(text('span',folder?'›':'','finder-disclosure'),text('span','','finder-icon '+(folder?'folder-icon':'document-icon')),text('span',name,'finder-filename'));
  if(change)identity.append(text('span',change.change==='modified'?'Changed':change.change==='moved in'?'Moved':'New','finder-tag '+change.change));
  line.append(identity,text('span',folder?'—':row?.size!=null?`${row.size} B`:'—','finder-size'),text('span',folder?'Folder':fileKind(name),'finder-kind'));
  return line;
}
function selectFolder(scope){
  finderScope=scope;
  document.querySelectorAll('[data-scope]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.scope===scope)));
  byId('alice-tree').hidden=scope!=='alice';byId('shared-tree').hidden=scope!=='shared';byId('bob-locked').hidden=scope!=='bob';
  byId('finder-title').textContent=scope==='alice'?'Alice':scope==='shared'?'Shared':'Bob';
  byId('finder-path').textContent=scope==='shared'?'workspace › shared':`workspace › users › ${scope}`;
  const prefix=scope==='shared'?'shared/':'users/alice/';
  const count=finderWorkspace?.after.filter(r=>r.kind==='file'&&r.path.startsWith(prefix)).length||0;
  byId('finder-count').textContent=scope==='bob'?'Private workspace':`${count} ${count===1?'file':'files'}`;
}
document.querySelectorAll('[data-scope]').forEach(b=>b.addEventListener('click',()=>selectFolder(b.dataset.scope)));
function tree(rows,prefix,changes,openPaths){
  const root={children:new Map()};
  for(const row of rows){if(!row.path.startsWith(prefix+'/'))continue;const parts=row.path.slice(prefix.length+1).split('/');let node=root;parts.forEach((part,i)=>{if(!node.children.has(part))node.children.set(part,{children:new Map()});node=node.children.get(part);if(i===parts.length-1)node.row=row;});}
  function list(node,path){const ul=document.createElement('ul');ul.className='file-tree';const sorted=[...node.children.entries()].sort((a,b)=>{const ad=a[1].children.size||a[1].row?.kind==='directory',bd=b[1].children.size||b[1].row?.kind==='directory';return Number(!!bd)-Number(!!ad)||a[0].localeCompare(b[0]);});for(const [name,child]of sorted){const full=path+'/'+name,li=document.createElement('li');if(child.children.size||child.row?.kind==='directory'){const d=document.createElement('details');d.dataset.folder=full;d.open=openPaths.has(full)?openPaths.get(full):true;const summary=document.createElement('summary');summary.append(finderRow(name,child.row,null,true));d.append(summary,list(child,full));li.append(d);}else{const line=text('button','','finder-file');line.type='button';line.append(finderRow(name,child.row,changes.find(c=>c.path===full),false));line.addEventListener('click',()=>{byId('workspace-view').querySelectorAll('.finder-file').forEach(b=>b.setAttribute('aria-pressed','false'));line.setAttribute('aria-pressed','true');});li.append(line);}ul.append(li);}return ul;}
  return root.children.size?list(root,prefix):text('p','Empty folder','empty-folder');
}
function renderWorkspace(workspace,label){
  if(!workspace)return;const key=JSON.stringify(workspace)+label;if(key===workspaceKey)return;workspaceKey=key;
  finderWorkspace=workspace;
  const openPaths=new Map([...byId('workspace-view').querySelectorAll('details[data-folder]')].map(d=>[d.dataset.folder,d.open]));
  byId('workspace-state').textContent=label;
  byId('alice-tree').replaceChildren(tree(workspace.after,'users/alice',workspace.changes,openPaths));
  byId('shared-tree').replaceChildren(tree(workspace.after,'shared',workspace.changes,openPaths));
  selectFolder(finderScope);
  byId('changes-panel').hidden=!workspace.changes.length;
  byId('changes-heading').textContent=`Changes from starting workspace (${workspace.changes.length} paths)`;
  byId('changes').replaceChildren(...workspace.changes.map(c=>text('li',`${c.change}: ${c.path}${c.peer?' ↔ '+c.peer:''}`)));
}
function renderLive(){
  if(source!=='live')return;
  const s=latest;
  byId('status').textContent=s?(s.pending?'Waiting for you':s.status):'Ready';
  byId('pending').hidden=!s?.pending;
  renderWorkspace(previewNext?presetViews[byId('preset').value]:s?.workspace||presetViews[byId('preset').value],previewNext?'Preset preview · next run':s?.workspace?(s.active?'Live · refreshes every second':'Final workspace'):'Preset preview');
  byId('saved').textContent=s?.run_directory?`Trace saved locally: ${s.run_directory}`:'';
  if(s?.bundle)sendTrace(s.bundle,'live');
  else if(lastReplay?.type!=='live-clear'){lastReplay={type:'live-clear'};byId('viewer').contentWindow.postMessage(lastReplay,location.origin);}
}
async function historyOptions(){
  const data=await get('/api/history'),key=JSON.stringify(data.runs);if(key===historyKey)return;historyKey=key;
  const select=byId('history-select'),selected=select.value;select.replaceChildren();
  for(const [kind,label]of [['submitted','Published to GitHub'],['local','Local live sessions']]){const group=document.createElement('optgroup');group.label=label;for(const r of data.runs.filter(r=>r.source===kind)){const o=text('option',r.label);o.value=r.id;group.append(o);}if(group.childNodes.length)select.append(group);}
  if([...select.options].some(o=>o.value===selected))select.value=selected;
}
async function loadHistory(){
  const selected=byId('history-select').value;if(!selected)return;
  try{const data=await get('/api/history/run?id='+encodeURIComponent(selected));if(source!=='history'||byId('history-select').value!==selected)return;
    byId('status').textContent='Saved replay · no API calls';byId('pending').hidden=true;
    renderWorkspace(data.workspace,data.workspace.baseline_available===false?'Final workspace · starting snapshot unavailable':'Saved trace · before / after');byId('saved').textContent=data.source==='submitted'?'Published trace from this project’s GitHub submission':'Saved local demo session';sendTrace(data.bundle,'saved');error('');
  }catch(e){error(e.message);}
}
async function setSource(value){source=value;byId('live-tab').setAttribute('aria-pressed',String(value==='live'));byId('history-tab').setAttribute('aria-pressed',String(value==='history'));byId('run-form').hidden=value!=='live';byId('history-controls').hidden=value!=='history';error('');if(value==='history'){byId('pending').hidden=true;await historyOptions();await loadHistory();}else renderLive();}
byId('live-tab').addEventListener('click',()=>setSource('live'));
byId('history-tab').addEventListener('click',()=>setSource('history'));
byId('history-select').addEventListener('change',loadHistory);
byId('preset').addEventListener('change',()=>{if(source==='live'&&!latest?.active){previewNext=true;renderLive();}});
for(const b of document.querySelectorAll('[data-example]'))b.addEventListener('click',()=>{byId('instruction').value=examples[b.dataset.example];if(b.dataset.example==='share'){byId('preset').value='cross_boundary';byId('preset').dispatchEvent(new Event('change'));}});
byId('run-form').addEventListener('submit',async e=>{e.preventDefault();error('');previewNext=false;byId('start').disabled=true;try{await post('/api/live/start',{instruction:byId('instruction').value,preset:byId('preset').value,apply:byId('mode').value==='apply'});await poll();}catch(e){error(e.message);byId('start').disabled=false;}});
async function respond(value){if(!pending)return;try{await post('/api/live/respond',{id:pending.id,value});byId('password').value='';byId('pending').hidden=true;await poll();}catch(e){error(e.message);}}
byId('answer-form').addEventListener('submit',e=>{e.preventDefault();respond(pending?.kind==='confirmation'?true:byId('password').value);});
byId('decline').addEventListener('click',()=>respond(pending?.kind==='confirmation'?false:''));
async function poll(){if(busy)return;busy=true;try{const s=await get('/api/live/status');latest=s;byId('start').disabled=s.active;pending=s.pending;
  byId('live-tab').textContent=s.pending?'Live demo · waiting for you':s.active?'Live demo · running':'Live demo';
  if(pending&&pending.id!==lastPending){lastPending=pending.id;const auth=pending.kind==='authorization';byId('pending-title').textContent=auth?'Runtime requests authorization':'Confirm this plan';byId('pending-detail').textContent=JSON.stringify(pending.payload,null,2);byId('password-label').hidden=!auth;byId('accept').textContent=auth?'Submit password':'Confirm plan';byId('password').value='';}
  renderLive();if(s.error&&source==='live')error(`Run failed (${s.error}). Check local model configuration and the recorded trace.`);
}catch(e){error(e.message);}finally{busy=false;}}
(async()=>{try{const data=await get('/api/live/setup');token=data.token;presetViews=data.preset_workspaces;for(const [value,label]of Object.entries(data.presets)){const o=text('option',label);o.value=value;byId('preset').append(o);}await historyOptions();await poll();setInterval(poll,1000);}catch(e){error(e.message);}})();
