export function createGenerationBatches(ctx){
  const {S,api,esc,toast,run,usageSlot}=ctx,$=(q,r=document)=>r.querySelector(q);
  const state={plans:new Map(),quotes:new Map(),errors:new Map(),busy:new Set(),fetching:new Set(),viewed:new Map(),timer:null,
    requests:new Map(JSON.parse(localStorage.getItem('video-generation-batch-requests')||'[]'))};
  const active=()=>ctx.getRecord(),activeId=()=>active()?.job_id;
  const inProgress=batch=>batch&&['queued','running','assembling'].includes(batch.status);
  const statusLabel={queued:'等待生成',running:'生成片段中',assembling:'正在拼接',succeeded:'已完成',failed:'生成未完成',unknown:'结果待查询'};
  const latest=id=>(state.plans.get(id)||[])[0];
  const viewed=id=>(state.plans.get(id)||[]).find(b=>b.id===state.viewed.get(id))||latest(id);
  const money=q=>q&&q.amount!=null&&Number.isFinite(Number(q.amount))?`${q.currency==='USD'?'US$':'¥'}${Number(q.amount).toFixed(2)}`:'—';
  const options=view=>({ratio:view.request?.ratio||'9:16',resolution:view.request?.resolution||'768P'});
  const quoteKey=(view,shotId='')=>`${view.job_id}:${view.revision}:${JSON.stringify(options(view))}:${shotId}`;
  const unresolved=id=>(state.plans.get(id)||[]).some(batch=>inProgress(batch)||batch.status==='unknown');
  const changed=(shot,part)=>!!part?.snapshot&&['script','prompt','duration','mode'].some(key=>String(shot[key]??'')!==String(part.snapshot[key]??''));
  const shotQuote=(view,id)=>state.quotes.get(quoteKey(view,id))||state.quotes.get(quoteKey(view));
  function saveRequests(){localStorage.setItem('video-generation-batch-requests',JSON.stringify([...state.requests]))}
  function patch(host,html){
    if(!host||host._batchHTML===html)return;
    const template=document.createElement('template');template.innerHTML=html;
    for(const fresh of template.content.querySelectorAll('video')){const old=[...host.querySelectorAll('video')].find(el=>el.getAttribute('src')===fresh.getAttribute('src'));if(old)fresh.replaceWith(old)}
    const focused=host.contains(document.activeElement)?document.activeElement?.dataset.batchAction:null;
    host.replaceChildren(template.content);host._batchHTML=html;
    if(focused)host.querySelector(`[data-batch-action="${focused}"]`)?.focus({preventScroll:true});
  }
  function controlsHTML(view){
    const batch=latest(view.job_id),quote=state.quotes.get(quoteKey(view)),busy=state.busy.has(view.job_id)||ctx.isBusy(),
      dirty=view.storyboard.shots.some(s=>ctx.hasDirty(s.id)),stale=view.storyboard.shots.some(s=>s.prompt_stale),missing=!!view.storyboard.missing_inputs?.length;
    const existing=!!batch,working=inProgress(batch),unknown=batch?.status==='unknown';
    const note=dirty?'有修改尚未保存':stale?'有片段提示词待更新':missing?'请先补齐所需素材':'';
    const unavailable=busy||dirty||stale||missing||!quote||!state.plans.has(view.job_id)||working||unknown||unresolved(view.job_id);
    return `<div class="gs-batch-controls"><div class="gs-batch-price"><span>${view.storyboard.shots.length} 个片段 · ${view.storyboard.shots.reduce((n,s)=>n+Number(s.duration||0),0)} 秒</span><strong>预计 ${money(quote)}</strong></div>${note?`<p class="gs-batch-note">${note}</p>`:''}<button type="button" class="button primary" data-batch-action="create" ${unavailable?'disabled':''}>${busy?'正在提交…':working?(statusLabel[batch.status]||'生成中'):existing?'确认全部并生成新版本':'确认全部并生成'}</button>${state.errors.get(view.job_id)?`<p class="gs-batch-error" role="alert">${esc(state.errors.get(view.job_id))}</p><button type="button" class="button small" data-batch-action="refresh">刷新状态</button>`:''}</div>`;
  }
  function outputHTML(view){
    const batches=state.plans.get(view.job_id)||[],batch=viewed(view.job_id);if(!batch)return '';
    const complete=batch.shots.filter(s=>s.status==='succeeded').length,updates=batch.shots.filter(part=>{const shot=view.storyboard.shots.find(s=>s.id===part.shot_id);return shot&&changed(shot,part)}).length;
    const retryAmount=batch.shots.filter(s=>s.status!=='succeeded').reduce((sum,s)=>sum+Number(batch.quote?.per_shot?.find(p=>p.shot_id===s.shot_id)?.amount||0),0);
    const locked=state.busy.has(view.job_id)||ctx.isBusy()||unresolved(view.job_id);
    return `<div class="gs-batch-status"><span>${esc(statusLabel[batch.status]||batch.status)} · ${complete}/${batch.shots.length} 段</span><select aria-label="生成版本" data-batch-action="version">${batches.map(item=>`<option value="${item.id}" ${item.id===batch.id?'selected':''}>版本 ${item.version} · ${esc(statusLabel[item.status]||item.status)}</option>`).join('')}</select></div><div class="gs-batch-progress" aria-hidden="true">${batch.shots.map(shot=>`<span class="${shot.status==='succeeded'?'done':['running','queued'].includes(shot.status)?'working':''}"></span>`).join('')}</div>${batch.output?`<div class="gs-batch-output"><video src="${esc(batch.output.url)}" controls playsinline preload="metadata" ${batch.output.thumbnail_url?`poster="${esc(batch.output.thumbnail_url)}"`:''} aria-label="完整视频版本 ${batch.version}"></video><div class="actions"><span>完整视频 · 版本 ${batch.version}</span><a class="button small" href="${esc(batch.output.url)}" download>下载完整视频</a></div></div>`:''}${updates?`<p class="gs-batch-warning">${updates} 段脚本已修改，视频待更新</p>`:''}${batch.error?`<p class="gs-batch-error">${esc(batch.error)}</p>`:''}<div class="actions">${batch.can_resume?'<button type="button" class="button small" data-batch-action="resume">继续查询原任务</button>':''}${batch.can_retry?`<button type="button" class="button small" data-batch-action="retry" ${locked?'disabled':''}>${batch.shots.every(s=>s.status==='succeeded')?'重新拼接':`重试版本 ${batch.version} 的失败片段`}</button><span class="gs-shot-cost">预计 ${money({...batch.quote,amount:retryAmount})}</span>`:''}</div>`;
  }
  function shotHTML(view,shot){
    const batch=viewed(view.job_id),part=batch?.shots.find(s=>s.shot_id===shot.id);if(!part)return '';
    const quote=shotQuote(view,shot.id),cost=quote?.per_shot?.find(s=>s.shot_id===shot.id),amount=cost?money({...quote,amount:cost.amount}):'—';
    const canRegenerate=batch.shots.every(s=>s.shot_id===shot.id||s.status==='succeeded')&&!inProgress(batch)&&batch.status!=='unknown';
    const disabled=state.busy.has(view.job_id)||ctx.isBusy()||ctx.hasDirty(shot.id)||shot.prompt_stale||!!view.storyboard.missing_inputs?.length||!quote||unresolved(view.job_id);
    return `${part.url?`<video src="${esc(part.url)}" controls playsinline preload="metadata" ${part.thumbnail_url?`poster="${esc(part.thumbnail_url)}"`:''} aria-label="片段 ${part.index+1} 生成结果"></video>`:''}<div class="gs-shot-result-row"><span>${esc(statusLabel[part.status]||part.status)}${part.source_batch_id&&part.source_batch_id!==batch.id?' · 沿用':''}${changed(shot,part)?' · 脚本已修改，视频待更新':''}</span>${part.job_id?usageSlot('job',part.job_id,'片段用量'):''}${canRegenerate?`<span class="gs-shot-cost">预计 ${amount}</span><button type="button" class="button small" data-batch-action="regenerate" data-shot-id="${esc(shot.id)}" ${disabled?'disabled':''}>确认并重新生成</button>`:''}${part.error?`<span class="gs-batch-error">${esc(part.error)}</span>`:''}</div>`;
  }
  function paint(){
    const view=active();if(S.route!=='generate'||!view)return;
    patch($('#gs-batch-controls'),controlsHTML(view));patch($('#gs-batch-output'),outputHTML(view));
    for(const shot of view.storyboard.shots)patch(document.querySelector(`[data-shot-result="${shot.id}"]`),shotHTML(view,shot));
    ctx.onContentChange?.();
  }
  async function loadQuote(view,shotId=''){
    const key=quoteKey(view,shotId);if(state.quotes.has(key)||state.fetching.has(key))return;
    state.fetching.add(key);
    try{const query=new URLSearchParams({...options(view),...(shotId?{shot_id:shotId}:{})}),data=await api(`/generation-plans/${view.job_id}/batch-quote?${query}`);state.quotes.set(key,data.quote);if(!shotId)state.errors.delete(view.job_id)}
    catch(error){if(!shotId)state.errors.set(view.job_id,error.message)}
    finally{state.fetching.delete(key);if(activeId()===view.job_id)paint()}
  }
  function schedule(){
    clearTimeout(state.timer);const view=active();if(!view||S.route!=='generate'||!inProgress(latest(view.job_id)))return;
    state.timer=setTimeout(()=>refresh(view.job_id),1800);
  }
  async function refresh(id=activeId()){
    if(!id||state.fetching.has(id))return;state.fetching.add(id);
    try{const response=await api(`/generation-plans/${id}/batches`);state.plans.set(id,response.batches||[]);state.errors.delete(id)}
    catch(error){state.errors.set(id,error.message)}
    finally{state.fetching.delete(id);if(activeId()===id){ctx.onMutationState?.();paint();schedule()}}
  }
  function mount(){const view=active();if(!view)return;paint();if(!view.storyboard.shots.some(s=>s.prompt_stale))void loadQuote(view);for(const shot of view.storyboard.shots)if(!shot.prompt_stale)void loadQuote(view,shot.id);if(!state.plans.has(view.job_id))void refresh(view.job_id);schedule()}
  async function mutate(kind,shotId){
    const view=active();if(!view||state.busy.has(view.job_id))return;
    const batch=viewed(view.job_id);let path,payload;
    if(kind!=='resume'&&unresolved(view.job_id))throw new Error('请先等当前批次完成，或查询未返回的生成结果。');
    if(kind==='create'){
      if(ctx.isBusy()||view.storyboard.shots.some(s=>ctx.hasDirty(s.id)||s.prompt_stale))throw new Error('先保存修改并更新提示词。');
      path=`/generation-plans/${view.job_id}/batches`;payload={expected_revision:view.revision,...options(view)};
    }else{
      if(!batch)return;path=`/generation-batches/${batch.id}/${kind}`;
      payload=kind==='regenerate'?{shot_id:shotId,expected_revision:view.revision}:{};
      if(kind==='regenerate'&&(ctx.hasDirty(shotId)||view.storyboard.shots.find(s=>s.id===shotId)?.prompt_stale))throw new Error('先保存这段修改并更新提示词。');
    }
    const key=JSON.stringify({path,payload});let request=state.requests.get(key);
    if(!request){request={...payload,...(kind==='resume'?{}:{submission_id:crypto.randomUUID()})};state.requests.set(key,request);saveRequests()}
    state.busy.add(view.job_id);ctx.onMutationState?.();paint();
    try{
      const response=await api(path,request),fresh=response.batch;
      state.plans.set(view.job_id,[fresh,...(state.plans.get(view.job_id)||[]).filter(item=>item.id!==fresh.id)]);
      state.viewed.set(view.job_id,fresh.id);state.errors.delete(view.job_id);ctx.onStarted?.();
      const reviewed=await api(`/generation-plans/${view.job_id}`);ctx.onReview(view.job_id,reviewed);state.requests.delete(key);saveRequests();
      toast(kind==='resume'?'继续查询原任务':kind==='regenerate'?'已提交此片段，完成后自动重拼':kind==='retry'?'已开始重试':'已提交整批生成');
    }catch(error){state.errors.set(view.job_id,error.message);void refresh(view.job_id);throw error}
    finally{state.busy.delete(view.job_id);ctx.onMutationState?.();if(activeId()===view.job_id)mount()}
  }
  document.addEventListener('click',event=>{
    const button=event.target.closest('button[data-batch-action]');if(!button||button.disabled)return;
    const action=button.dataset.batchAction;
    if(action==='refresh'){const view=active();if(view){void refresh();void loadQuote(view)}return}
    void run(()=>mutate(action,button.dataset.shotId));
  });
  document.addEventListener('change',event=>{if(event.target.dataset.batchAction!=='version')return;state.viewed.set(activeId(),event.target.value);paint()});
  return{mount,paint,refresh,hasBatch:()=>!!latest(activeId()),leave(){clearTimeout(state.timer)},onJobs(){if(activeId()&&inProgress(latest(activeId())))void refresh()},busy:()=>state.busy.has(activeId())};
}
