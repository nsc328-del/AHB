const clock=value=>{const tenths=Math.round(Number(value)*10);return `${String(Math.floor(tenths/600)).padStart(2,'0')}:${String(Math.floor(tenths%600/10)).padStart(2,'0')}.${tenths%10}`};
const secs=value=>`${Number(Number(value).toFixed(2))} 秒`;

export function createCutInspector({api,esc}){
  const cache=new Map(),selected=new Map();
  let root=null,host=null,renderId=null,receipt=null,mode='source',open=false,request=0;
  const $=selector=>host?.querySelector(selector);
  const current=()=>receipt?.nodes.find(node=>node.id===selected.get(renderId))||receipt?.nodes[0];
  function stop(){root?.querySelectorAll('video').forEach(video=>video.pause())}
  function animate(element){
    if(!element||matchMedia('(prefers-reduced-motion: reduce)').matches)return;
    element.getAnimations().forEach(animation=>animation.cancel());
    element.animate([{opacity:.4},{opacity:1}],{duration:180,easing:'ease-out'});
  }
  function setVisibility(){
    root?.querySelectorAll('.workspace-film-player,.workspace-film-meta,.workspace-film-summary').forEach(element=>element.hidden=open);
    root?.querySelectorAll('[data-cut-view]').forEach(button=>button.setAttribute('aria-pressed',String((button.dataset.cutView==='inspect')===open)));
    if(host)host.hidden=!open;
  }
  function renderPlayer(){
    const node=current(),player=$('.ci-player');if(!node||!player)return;
    player.querySelector('video')?.pause();
    const image=mode==='source'&&node.kind==='image',url=mode==='source'?node.source_url:node.result_url;
    player.innerHTML=image?`<img src="${esc(url)}" alt="${esc(node.asset_name)}">`:`<video controls playsinline preload="auto" ${mode==='source'?`poster="${esc(node.thumbnail_url)}"`:''} aria-label="${mode==='source'?'原片片段':'剪后片段'} ${node.order}"></video><span class="ci-video-loading" role="status">准备片段…</span>`;
    const video=player.querySelector('video');
    if(video){
      const message=player.querySelector('.ci-video-loading');
      const isCurrent=()=>video.isConnected&&player.querySelector('video')===video;
      // Safari may defer the first decoded frame until play. Metadata is enough
      // to reveal its controls; the loading status must never cover the player.
      const ready=()=>{if(isCurrent())message.remove()};
      for(const event of ['loadedmetadata','loadeddata','canplay','playing'])video.addEventListener(event,ready,{once:true});
      video.addEventListener('error',()=>{
        if(!isCurrent())return;
        message.classList.add('is-error');
        message.innerHTML='片段加载失败 <button type="button" data-cut-player-retry>重试</button>';
        player.append(message);
      },{once:true});
      video.src=url;
      video.load();
    }
    $$('.ci-player-modes button').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.cutMode===mode)));
  }
  const $$=selector=>[...(host?.querySelectorAll(selector)||[])];
  function renderNode(){
    const node=current();if(!node)return;
    const decision=node.decision,exec=receipt.execution,model=receipt.actor==='model'?'DS 方案':'方案记录';
    $$('.ci-node').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.cutNode===node.id)));
    const treatment=[`截取 ${clock(node.source_start)}—${clock(node.source_end)}`,`排在成片 ${clock(node.output_start)}—${clock(node.output_end)}`];
    if(exec.schema_version===2||exec.renderer_matches_current){
      treatment.push(node.transition==='fade'?`画面与原声从黑场淡入 ${secs(Math.min(exec.fade_seconds,node.duration))}`:node.order===1?'直接起片':'直接切入');
      treatment.push(node.source_has_audio?(node.original_volume===0?'原声静音':`原声音量 ${Math.round(node.original_volume*100)}%`):'此素材无原音轨');
      if(exec.schema_version===2){treatment.push(`播放速度 ${Number(node.speed.toFixed(2))}×${Math.abs(node.speed-1)>.001?' · 保持音高':''}`);if(exec.music_added)treatment.push(`本段配乐 ${Math.round(exec.music_volume*node.music_gain*100)}%`);if(node.beat_alignment)treatment.push(node.beat_alignment.matched?`出点贴音乐${node.beat_alignment.kind==='onset'?'起音':'节拍'} · 偏差 ${Math.abs(node.beat_alignment.error_ms)} 毫秒`:'出点未贴音乐节拍')} 
      if(node.kind==='image')treatment.push('图片缓慢推近 4%');
    }else treatment.push(node.transition==='fade'?'淡入':'直接切入');
    $('.ci-node-detail').innerHTML=`<div class="ci-evidence"><div class="ci-player-modes" role="group" aria-label="片段对照"><button type="button" data-cut-mode="source" aria-pressed="${mode==='source'}">原片片段</button><button type="button" data-cut-mode="result" aria-pressed="${mode==='result'}">剪后片段</button></div><div class="ci-player"></div><p class="ci-source-name">${esc(node.asset_name)}</p><div class="ci-time-pair"><span>原片 <b>${clock(node.source_start)}—${clock(node.source_end)}</b></span><span>成片 <b>${clock(node.output_start)}—${clock(node.output_end)}</b></span></div></div>
      <div class="ci-decision"><h3>${model}<span>片段 ${node.order} · ${secs(node.duration)}</span></h3><dl><div><dt>选段用途</dt><dd>${esc(decision.role||'当时未记录用途')}</dd></div><div><dt>画面判断</dt><dd>${esc(decision.label||'当时未记录画面描述')}</dd></div>${decision.cut_reason?`<div><dt>动作切点</dt><dd>${esc(decision.cut_reason)}</dd></div>`:''}${decision.speed_reason?`<div><dt>快慢节奏</dt><dd>${esc(decision.speed_reason)}</dd></div>`:''}${decision.sound_reason?`<div><dt>声音取舍</dt><dd>${esc(decision.sound_reason)}</dd></div>`:''}<div><dt>字幕</dt><dd class="ci-caption">${decision.caption?esc(decision.caption):'不加字幕'}</dd></div></dl><details class="ci-execution" open><summary>实际处理</summary><ul>${treatment.map(item=>`<li>${esc(item)}</li>`).join('')}</ul>${node.changed_fields.length?`<p class="ci-difference">方案与执行记录有差异：${esc(node.changed_fields.map(key=>({start:'入点',end:'出点',caption:'字幕',transition:'转场'}[key]||key)).join('、'))}</p>`:''}</details></div>`;
    renderPlayer();animate($('.ci-node-detail'));
  }
  function renderReceipt(){
    const stats=receipt.stats,exec=receipt.execution;
    host.innerHTML=`<div class="ci-overview"><div><h2>剪辑拆解</h2><span>${stats.clips} 个片段</span><span>${stats.captions} 条字幕</span>${stats.speed_changes?`<span>${stats.speed_changes} 段变速</span>`:''}${stats.beat_joins?`<span>${stats.beat_joins} 处卡点</span>`:''}${stats.fades?`<span>${stats.fades} 次淡入</span>`:''}<span>${secs(stats.duration)}</span></div><div class="ci-overview-facts">${stats.sources===1&&stats.source_duration?`原片 ${secs(stats.source_duration)}，取用 ${secs(stats.unique_source_duration)}${stats.reordered?' · 调整顺序':''}`:`取用 ${stats.sources} 份素材`}<span>${exec.width} × ${exec.height} · ${exec.fps} fps${exec.music_added?' · 另加配乐':' · 未另加配乐'}</span></div></div>
      <ol class="ci-nodes" aria-label="成片片段顺序">${receipt.nodes.map(node=>`<li><button type="button" class="ci-node" data-cut-node="${esc(node.id)}" aria-label="片段 ${node.order}，原片 ${clock(node.source_start)} 至 ${clock(node.source_end)}" aria-pressed="false"><span class="ci-node-cover"><img src="${esc(node.thumbnail_url)}" alt="" loading="lazy"><span>${String(node.order).padStart(2,'0')}</span></span><span class="ci-node-bottom"><b>${secs(node.duration)}</b><span>${clock(node.output_start)}</span></span><span class="ci-node-label">${esc(node.decision.role||node.decision.label||node.asset_name)}</span></button></li>`).join('')}</ol>
      <div class="ci-node-detail"></div><details class="ci-plan-summary"><summary>整条方案${receipt.strategy?' · '+esc(receipt.strategy):''}</summary><p>${esc(receipt.summary||'未记录整体说明')}</p>${receipt.input_kind==='analysis'?'<span>编排依据：已完成的素材分析</span>':''}</details>`;
    renderNode();
  }
  async function show(){
    open=true;stop();setVisibility();
    if(receipt)return;
    const token=++request,id=renderId,target=host;
    host.innerHTML='<div class="ci-loading" role="status">读取剪辑记录…</div>';
    try{
      let result=cache.get(id);
      if(!result){result=await api(`/renders/${encodeURIComponent(id)}/breakdown`);cache.set(id,result)}
      if(token!==request||!target.isConnected||host!==target||!open)return;
      receipt=result;renderReceipt();
    }catch(error){if(token===request&&target.isConnected&&open)host.innerHTML=`<div class="ci-error">${esc(error.message||'剪辑记录暂不可用')}<button type="button" class="button small" data-cut-retry>重试</button></div>`;}
  }
  function mount(element,id){
    const target=element?.querySelector('.workspace-cut-inspector');
    if(root===element&&renderId===id&&host===target){setVisibility();return}
    const wasOpen=open;stop();request++;root=element;host=target;renderId=id;receipt=null;mode='source';
    if(!host){open=false;return}
    open=wasOpen;setVisibility();if(open)void show();
  }
  document.addEventListener('click',event=>{
    if(!root?.isConnected||!root.contains(event.target))return;
    const view=event.target.closest('[data-cut-view]');
    if(view){if(view.dataset.cutView==='inspect')void show();else{open=false;request++;stop();setVisibility()}return}
    if(event.target.closest('[data-cut-retry]')){void show();return}
    if(event.target.closest('[data-cut-player-retry]')){renderPlayer();return}
    const node=event.target.closest('[data-cut-node]');
    if(node&&receipt?.nodes.some(item=>item.id===node.dataset.cutNode)){
      if(current()?.id===node.dataset.cutNode)return;
      stop();selected.set(renderId,node.dataset.cutNode);renderNode();return;
    }
    const toggle=event.target.closest('[data-cut-mode]');
    if(toggle&&mode!==toggle.dataset.cutMode){mode=toggle.dataset.cutMode;renderPlayer()}
  });
  return {mount};
}
