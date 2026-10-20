export function createGenerationStudio(ctx){
  const {S,api,esc,toast,run,track,assetOptions,genModeLabel,generationParams,updateGenCost,captureGenDraft,mediaTag}=ctx;
  const $=(s,r=document)=>r.querySelector(s);
  const G={active:null,drafts:new Map(),boards:new Map(),pending:new Map(),scroll:0,idea:null,
    submissions:new Map(JSON.parse(sessionStorage.getItem('video-generation-submissions')||'[]'))};
  const names={'h3-prompt-writing':'自由生成','minimalist-product-ad-generator':'极简产品广告','3d-animation-short-generator':'3D 动画短片','papercraft-stop-motion-explainer':'纸艺定格解说','brand-promo-video-generator':'品牌宣传片','music-video-subtitle-generator':'音乐与字幕','co-op-game-intro-generator':'游戏合作开场','paper-collage-explainer-generator':'纸张拼贴解说','handdrawn-live-video-generator':'手绘与实拍'};
  const title=id=>names[id]||S.skills.find(x=>x.id===id)?.name||'自由生成';
  function saveDraft(){
    if(!G.active)return;
    captureGenDraft();
    G.drafts.set(G.active,{draft:{...S.genDraft},mode:S.genMode,loaded:S.loadedShot,loadedIndex:G.loadedIndex,idea:G.idea});
  }
  function enter(id){
    saveDraft();G.scroll=scrollY;G.active=id;S.skill=id;
    const existing=G.drafts.get(id),skill=S.skills.find(x=>x.id===id);
    S.genDraft=existing?.draft||{duration:6,resolution:'768P',ratio:'9:16'};
    S.genMode=existing?.mode||skill?.mode||'T2VA';
    S.loadedShot=existing?.loaded||false;G.loadedIndex=existing?.loadedIndex;G.idea=existing?.idea||null;
    S.storyboard=G.boards.get(id)||null;
    render();window.scrollTo({top:0,behavior:'instant'});
  }
  function back(){
    saveDraft();G.active=null;render();window.scrollTo({top:G.scroll,behavior:'instant'});
    document.querySelector('[data-gs-action=enter]')?.focus({preventScroll:true});
  }
  function gallery(){
    const ordered=[...S.skills].sort((a,b)=>(a.id==='h3-prompt-writing'?-1:b.id==='h3-prompt-writing'?1:0));
    return `<section class="section generation-gallery"><div class="section-head"><h1>你想创作什么？</h1></div>
      <div class="generation-masonry">${ordered.map((skill,i)=>`<button class="generation-type ${i===0?'generation-free':''}" data-gs-action="enter" data-id="${esc(skill.id)}" aria-label="${esc(title(skill.id))}">
        <div class="type-preview preview-${i%4}" aria-hidden="true">${i===0?'<span class="free-plus">＋</span>':''}</div>
        <div class="type-caption"><h2>${esc(title(skill.id))}</h2><span>${i===0?'从一句想法开始':esc(skill.category)}</span></div>
      </button>`).join('')}</div>
      ${resultsSection()}
    </section>`;
  }
  function resultsSection(){
    const assets=S.assets.filter(a=>a.source==='generation'||a.generation_id);
    if(!assets.length)return '<div id="generation-result-host"></div>';
    return `<section id="generation-result-host" class="generated-section"><div class="strip-heading"><h2>已生成</h2><span class="muted">${assets.length} 个视频</span></div><div class="generation-results">${assets.map(a=>`<article class="result-card" data-generation-asset="${esc(a.id)}">${mediaTag(a)}<div class="result-body"><h3>${esc(a.name)}</h3><div class="actions"><button class="button small" data-action="select" data-id="${esc(a.id)}">${S.selected.has(a.id)?'已选入剪辑':'选入剪辑'}</button><a class="button small" href="${esc(a.url)}" download>下载</a></div></div></article>`).join('')}</div></section>`;
  }
  function references(){
    const d=S.genDraft;
    const order=d.reference_ids||[];
    const assets=[...S.assets].sort((a,b)=>{
      const ai=order.indexOf(a.id),bi=order.indexOf(b.id);
      return (ai<0?Infinity:ai)-(bi<0?Infinity:bi);
    });
    return `${['I2VA','FL2VA'].includes(S.genMode)?`<label>首帧图片<select name="first_frame_id">${assetOptions(['image'],d.first_frame_id)}</select></label>`:''}
    ${['L2VA','FL2VA'].includes(S.genMode)?`<label>尾帧图片<select name="last_frame_id">${assetOptions(['image'],d.last_frame_id)}</select></label>`:''}
    ${S.genMode==='Ref2VA'?`<label>参考素材<select name="reference_ids" multiple size="4">${assets.map(a=>`<option value="${esc(a.id)}" ${d.reference_ids?.includes(a.id)?'selected':''}>${esc(a.name)}</option>`).join('')}</select></label>`:''}`;
  }
  function workspace(){
    const d=S.genDraft,pending=G.pending.get(G.active),skill=S.skills.find(x=>x.id===G.active);
    return `<section class="section generation-workspace"><button class="back-link" data-gs-action="back">‹ 素材生成</button>
      <div class="section-head"><h1>${esc(title(G.active))}</h1><button class="button quiet" data-action="jobs">查看任务</button></div>
      <div class="generation-compose-layout">
        <form id="generation-form" class="generation-idea-form">
          <label class="idea-label">${S.loadedShot?'镜头提示词':'描述你的想法'}<textarea name="prompt" required placeholder="想拍什么、是什么感觉？从一句话开始。">${esc(d.prompt||'')}</textarea></label>
          ${skill?.inputs?.length?`<p class="generation-inputs">参考素材：${esc(skill.inputs.join('、'))}</p>`:''}
          <div class="generation-references">${references()}</div>
          <details class="generation-options"><summary>画面设置 <span>${esc(genModeLabel(S.genMode))}　${d.duration||6} 秒　${esc(d.ratio||'9:16')}</span></summary>
            <div class="mode-tabs">${['T2VA','I2VA','L2VA','FL2VA','Ref2VA'].map(id=>`<button type="button" class="${S.genMode===id?'active':''}" data-gs-action="mode" data-id="${id}" aria-pressed="${S.genMode===id}">${genModeLabel(id)}</button>`).join('')}</div>
            <div class="field-pair"><label>单镜头时长<select name="duration">${Array.from({length:12},(_,i)=>i+4).map(n=>`<option value="${n}" ${Number(d.duration||6)===n?'selected':''}>${n} 秒</option>`).join('')}</select></label><label>清晰度<select name="resolution"><option ${d.resolution!=='2K'?'selected':''}>768P</option><option ${d.resolution==='2K'?'selected':''}>2K</option></select></label></div>
            <label>画面比例<select name="ratio">${['9:16','16:9','1:1','3:4','4:3','21:9'].map(r=>`<option ${(d.ratio||'9:16')===r?'selected':''}>${r}</option>`).join('')}</select></label>
          </details>
          ${S.loadedShot?`<div class="price"><span>预计镜头生成费用</span><strong id="generation-price">—</strong></div><p id="generation-cost-note" class="muted cost-note"></p>
          <div class="actions"><button type="button" class="button" data-gs-action="idea">返回构思</button><button class="button primary" type="submit">生成这个镜头</button></div>`:
          `<div class="actions"><button class="button primary full" type="button" data-gs-action="plan" ${pending?'disabled':''}>${pending?'正在编写脚本…':'编写脚本'}</button></div>`}
        </form>
        <section class="generation-script" aria-label="脚本与分镜"><div id="storyboard-content">${storyboardHTML()}</div></section>
      </div>${resultsSection()}
    </section>`;
  }
  function storyboardHTML(){
    const sb=G.boards.get(G.active);
    if(!sb)return `<div class="script-empty"><span class="script-lines" aria-hidden="true"><i></i><i></i><i></i></span><p>${G.pending.has(G.active)?'正在组织镜头与故事':'脚本与分镜'}</p></div>`;
    return `<div class="script-heading"><h2>${esc(sb.title||'脚本与分镜')}</h2><p class="muted">${esc(sb.summary||'')}</p></div>
      ${sb.missing_inputs?.length?`<p class="notice">还需要：${esc(sb.missing_inputs.join('、'))}</p>`:''}
      ${(sb.shots||[]).map((shot,i)=>`<article class="script-shot" data-shot="${i}"><div class="script-shot-head"><span class="shot-number">${String(i+1).padStart(2,'0')}</span><h3>${esc(shot.title||'镜头')}</h3><span>${shot.duration} 秒</span></div>
        <p class="shot-script">${esc(shot.script||[shot.visual,shot.audio].filter(Boolean).join('\n'))}</p>
        <details class="shot-prompt"><summary>视频提示词</summary><label><span class="sr-only">镜头 ${i+1} 视频提示词</span><textarea data-gs-field="prompt" data-index="${i}">${esc(shot.prompt)}</textarea></label></details>
        <div class="script-shot-footer"><span class="muted">${esc(genModeLabel(shot.mode))}</span><button class="button small" data-gs-action="load-shot" data-index="${i}">使用这个镜头</button></div>
      </article>`).join('')}`;
  }
  function render(){
    $('#app').innerHTML=G.active?workspace():gallery();
    if(S.loadedShot&&G.active){updateGenCost();updateSubmit()}
  }
  function refresh(){
    if(S.route!=='generate')return;
    const old=$('#generation-result-host');
    if(!old)return;
    const existing=[...old.querySelectorAll('[data-generation-asset]')].map(el=>el.dataset.generationAsset);
    const next=S.assets.filter(a=>a.source==='generation'||a.generation_id).map(a=>a.id);
    if(JSON.stringify(existing)===JSON.stringify(next))return;
    const template=document.createElement('template');template.innerHTML=resultsSection();
    for(const item of template.content.querySelectorAll('[data-generation-asset]')){
      const live=old.querySelector(`[data-generation-asset="${item.dataset.generationAsset}"]`);
      if(live)item.replaceWith(live);
    }
    old.replaceWith(template.content);
  }
  async function plan(){
    if(!G.active||G.pending.has(G.active))return;
    const params=generationParams();
    if(!params.prompt.trim())throw new Error('先写下你的想法。');
    if(!S.config.deepseek_configured)throw new Error('脚本服务暂不可用。');
    const id=G.active;
    G.idea={draft:{...S.genDraft},mode:S.genMode};
    G.pending.set(id,'submitting');
    try{
      const response=await api('/generation-plans',params);
      G.pending.set(id,response.job_id);
      await track(response,job=>{
        const board=job.result?.storyboard||job.result;
        G.boards.set(id,board);G.pending.delete(id);
        if(G.active===id){S.storyboard=board;if(S.route==='generate'){$('#storyboard-content').innerHTML=storyboardHTML();const b=$('[data-gs-action=plan]');if(b){b.disabled=false;b.textContent='重新编写脚本'}}}
        toast('脚本已生成');
      });
      const host=$('#storyboard-content');if(host&&!G.boards.has(id))host.innerHTML=storyboardHTML();
    }catch(error){G.pending.delete(id);throw error}
  }
  function onJobs(changed){
    for(const job of changed){
      for(const item of G.submissions.values())if(item.job_id===job.id)item.status=job.status;
    }
    persistSubmissions();updateSubmit();
    for(const job of changed){
      if(!['failed','needs_configuration','unknown'].includes(job.status))continue;
      for(const [id,jobId] of G.pending)if(jobId===job.id){
        G.pending.delete(id);
        if(G.active===id&&S.route==='generate'){
          const b=$('[data-gs-action=plan]');if(b){b.disabled=false;b.textContent='重新编写脚本'}
          $('#storyboard-content').innerHTML=G.boards.has(id)?storyboardHTML():`<div class="data-error">${esc(job.error||'脚本未完成，请重试。')}</div>`;
        }
      }
    }
  }
  function persistSubmissions(){sessionStorage.setItem('video-generation-submissions',JSON.stringify([...G.submissions]))}
  function submissionParams(){
    const {asset_ids,...params}=generationParams();
    return params;
  }
  function updateSubmit(){
    const button=$('#generation-form button[type=submit]');if(!button||!S.loadedShot)return;
    const record=G.submissions.get(JSON.stringify(submissionParams()));
    const labels={submitting:'正在提交…',queued:'正在生成…',running:'正在生成…',succeeded:'已生成',failed:'生成未完成',unknown:'结果待查询',retry:'查询生成结果'};
    button.textContent=record?(labels[record.status]||'查询生成结果'):'生成这个镜头';
    button.disabled=!!record&&record.status!=='retry';
  }
  async function submit(){
    if(!S.loadedShot) return plan();
    const params=submissionParams();
    if(!S.config.minimax_configured)throw new Error('视频生成服务暂不可用。');
    const signature=JSON.stringify(params);
    let record=G.submissions.get(signature);
    if(record&&record.status!=='retry'){toast(record.status==='succeeded'?'这个镜头已生成':'请在任务中查看这次生成');return}
    record=record||{submission_id:crypto.randomUUID(),status:'submitting'};
    record.status='submitting';G.submissions.set(signature,record);persistSubmissions();updateSubmit();
    try{
      const response=await api('/generations',{...params,require_h3_prompt_contract:true,submission_id:record.submission_id});
      record.job_id=response.job_id;record.status='queued';persistSubmissions();
      await track(response,()=>toast('视频已生成并加入素材库'));
      $('#jobs-panel').hidden=false;toast('已提交镜头生成');
    }catch(error){
      record.status='retry';persistSubmissions();throw error;
    }finally{updateSubmit()}
  }
  function invalidateShot(){
    if(!S.loadedShot)return;
    captureGenDraft();
    if(G.idea)S.genDraft.prompt=G.idea.draft.prompt;
    S.loadedShot=false;render();toast('画面设置已改变，请重新编写脚本');
  }
  document.addEventListener('click',event=>{
    const button=event.target.closest('[data-gs-action]');if(!button)return;
    const action=button.dataset.gsAction;
    if(action==='enter')return enter(button.dataset.id);
    if(action==='back')return back();
    if(action==='mode'){
      captureGenDraft();S.genMode=button.dataset.id;
      if(S.loadedShot)invalidateShot();else render();
      $('.generation-options').open=true;return;
    }
    if(action==='idea'){
      if(G.idea){S.genDraft={...G.idea.draft};S.genMode=G.idea.mode}S.loadedShot=false;render();return;
    }
    if(action==='load-shot'){
      const shot=G.boards.get(G.active)?.shots?.[Number(button.dataset.index)];if(!shot)return;
      if(!S.loadedShot){captureGenDraft();G.idea={draft:{...S.genDraft},mode:S.genMode}}
      S.loadedShot=true;G.loadedIndex=Number(button.dataset.index);S.genMode=shot.mode;
      S.genDraft={...S.genDraft,prompt:shot.prompt,duration:shot.duration,first_frame_id:shot.first_frame_id||'',last_frame_id:shot.last_frame_id||'',reference_ids:[...(shot.reference_image_ids||[]),...(shot.reference_video_ids||[]),...(shot.reference_audio_ids||[])]};
      render();$('#generation-form').scrollIntoView({block:'start',behavior:'auto'});return;
    }
    if(action==='plan'){
      if(button.disabled)return;
      button.disabled=true;button.textContent='正在编写脚本…';
      void run(plan).finally(()=>{if(button.isConnected&&!G.pending.has(G.active)){button.disabled=false;button.textContent='编写脚本'}});
    }
  });
  document.addEventListener('input',event=>{
    const field=event.target.dataset.gsField;
    if(field){
      const index=Number(event.target.dataset.index),shot=G.boards.get(G.active)?.shots?.[index];
      if(shot)shot[field]=event.target.value;
      if(field==='prompt'&&S.loadedShot&&G.loadedIndex===index){
        S.genDraft.prompt=event.target.value;$('#generation-form [name=prompt]').value=event.target.value;updateSubmit();
      }
    }
    if(event.target.matches('#generation-form [name=prompt]')&&S.loadedShot){
      const shot=G.boards.get(G.active)?.shots?.[G.loadedIndex];
      if(shot){shot.prompt=event.target.value;const field=$(`[data-gs-field=prompt][data-index="${G.loadedIndex}"]`);if(field)field.value=event.target.value}
      updateSubmit();
    }
  });
  document.addEventListener('change',event=>{
    if(event.target.matches('#generation-form select')&&S.loadedShot)invalidateShot();
  });
  return {render,refresh,onJobs,saveDraft,storyboardHTML,plan,submit,
    showGallery(){saveDraft();G.active=null},active:()=>G.active};
}
