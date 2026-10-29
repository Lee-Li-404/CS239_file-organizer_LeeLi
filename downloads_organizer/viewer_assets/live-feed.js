'use strict';
let liveRun='',liveLength=0;
document.querySelector('.main-footer').textContent='Live agent messages and tool observations. Updates arrive as events are recorded.';
document.querySelector('.local-badge').textContent='● Live demo';
document.querySelector('.sidebar-footer')?.remove();
function labelLiveUI(saved){
  document.querySelector('.chat-load-demo')?.remove();
  const roster=document.querySelector('.chat-team p');
  if(roster)roster.textContent=roster.textContent.replace('saved conversation',saved?'saved conversation':'live conversation');
  const footer=document.querySelector('.chat-inbox-footer');
  if(footer&&!saved)footer.textContent=footer.textContent.replace('local replay','live trace');
}
window.addEventListener('message',event=>{
  if(event.origin!==location.origin||event.source!==parent)return;
  if(event.data?.type==='live-clear'){liveRun='';liveLength=0;S.runs=[];S.run='';S.selected=null;S.cursor=null;S.chatAgent=null;S.chatOpen=false;closeInspector();renderRunOptions();render();labelLiveUI(false);notice('Start a live run above to see agent messages here.');return;}
  if(event.data?.type!=='live-trace')return;
  const bundle=event.data.bundle;
  if(!bundle)return;
  const saved=event.data.mode==='saved';
  document.querySelector('.main-footer').textContent=saved?'Saved trace replay. No model calls or file operations.':'Live agent messages and tool observations. Updates arrive as events are recorded.';
  document.querySelector('.local-badge').textContent=saved?'● Saved trace':'● Live demo';
  const incoming=C.buildRuns(bundle)[0];
  if(!incoming||incoming.id===liveRun&&bundle.trace_text.length===liveLength)return;
  const fresh=incoming.id!==liveRun;
  const history=document.querySelector('.chat-history');
  const oldScroll=history?.scrollTop||0,atEnd=!history||history.scrollHeight-history.scrollTop-history.clientHeight<100;
  liveRun=incoming.id;liveLength=bundle.trace_text.length;
  if(fresh){S.runs=[incoming];S.run=incoming.id;S.cursor=null;S.selected=null;S.chatAgent='*';S.chatPeer='';S.chatOpen=true;S.chatMode='messages';S.limit=10000;closeInspector();notice('');}
  else S.runs=[incoming];
  renderRunOptions();render();
  labelLiveUI(saved);
  const next=document.querySelector('.chat-history');
  if(next)next.scrollTop=atEnd?next.scrollHeight:oldScroll;
});
