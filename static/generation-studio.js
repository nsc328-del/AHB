import {createGenerationBatches} from './generation-batches.js?v=20261020-batch2';
import {getGenerationPreview} from './generation-previews.js?v=20261020-compose2';
import {isReferenceAsset} from './media-gallery.js?v=20261020-referencecuts3';

export function createGenerationStudio(ctx){
  const {S,api,esc,toast,run,track,genModeLabel,generationParams,updateGenCost,captureGenDraft,mediaTag}=ctx;
  const $=(s,r=document)=>r.querySelector(s);
  const DEFAULT='papercraft-stop-motion-explainer';
  const G={active:null,defaultOpen:true,drafts:new Map(),records:new Map(),pending:new Map(),rewriting:new Map(),loading:new Set(),touched:new Set(),readFailures:new Map(),errors:new Map(),dirty:new Map(),saving:new Set(),finished:new Set(),opener:null,idea:null,loadedId:null,sequence:0,
    historicalPlans:new Map(),historyOpen:new Set(),attachments:new Map(),attachmentVersions:new Map(),pickers:new Set(),uploads:new Map(),attachmentErrors:new Map(),
    submissions:new Map(JSON.parse(sessionStorage.getItem('video-generation-submissions')||'[]'))};
  const names={'h3-prompt-writing':'自由生成','minimalist-product-ad-generator':'极简产品广告','3d-animation-short-generator':'3D 动画短片','papercraft-stop-motion-explainer':'纸艺定格解说','brand-promo-video-generator':'品牌宣传片','music-video-subtitle-generator':'音乐与字幕','co-op-game-intro-generator':'游戏合作开场','paper-collage-explainer-generator':'纸张拼贴解说','handdrawn-live-video-generator':'手绘与实拍'};
  const glyphs={
    'h3-prompt-writing':'<path d="M12 4v16M4 12h16"/>',
    'minimalist-product-ad-generator':'<path d="m12 3 8 4.5v9L12 21l-8-4.5v-9L12 3Z M4 7.5l8 4.5 8-4.5M12 12v9M8 5.2l8 4.6"/>',
    '3d-animation-short-generator':'<path d="m12 3 9 5-9 5-9-5 9-5ZM3 8v8l9 5 9-5V8M12 13v8"/><path d="m9 7 5 1-5 2V7Z"/>',
    'papercraft-stop-motion-explainer':'<path d="m3 7 18-4-6 18-4-9-8-5ZM11 12 21 3M11 12l-2 6 4-2"/>',
    'brand-promo-video-generator':'<path d="m4 11 13-6v14L4 14v-3ZM7 15l2 6h3l-2-5M20 8l2-1M20 12h2M20 16l2 1"/>',
    'music-video-subtitle-generator':'<path d="M10 17V5l10-2v12M10 8l10-2"/><ellipse cx="7" cy="17.5" rx="3" ry="2.5"/><ellipse cx="17" cy="15.5" rx="3" ry="2.5"/>',
    'co-op-game-intro-generator':'<path d="M7 6h10c2 0 3 2 3.5 4l1 7c.3 2-2 3-3.5 1l-2-3H8l-2 3c-1.5 2-3.8 1-3.5-1l1-7C4 8 5 6 7 6Z M7 9v5M4.5 11.5h5"/><circle cx="15.5" cy="10" r=".75"/><circle cx="18" cy="12.5" r=".75"/>',
    'paper-collage-explainer-generator':'<path d="M4 3h13v15H4zM8 7h13v14H8"/><path d="m5 14 4-4 3 3 2-2 3 3"/>',
    'handdrawn-live-video-generator':'<path d="m14 4 6 6M4 20l4-1 13-13a2.8 2.8 0 0 0-4-4L4 15v5Z M3 23c4-2 6 1 10-1 3-1 4-1 8-1"/>',
    image:'<rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="8" cy="9" r="1.5"/><path d="m3 17 5-5 4 4 4-5 5 6"/>',
    video:'<rect x="3" y="4" width="18" height="16" rx="2"/><path d="m10 8 6 4-6 4V8Z"/>',
    audio:'<path d="M5 9v6M9 5v14M13 8v8M17 3v18M21 9v6"/>',
    close:'<path d="M6 6L18 18M6 18L18 6"/>',
    library:'<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/>',
    check:'<path d="m5 12 4 4L19 6"/>',
  };
  const icon=id=>`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${glyphs[id]||glyphs.video}</svg>`;
  const title=id=>names[id]||S.skills.find(x=>x.id===id)?.name||'自由生成';
  const record=()=>G.records.get(G.active);
  const key=(skill,id)=>`${skill}:${id}`;
  const fields=shot=>({script:shot.script||'',prompt:shot.prompt||'',duration:Number(shot.duration)});
  const signature=shot=>JSON.stringify(fields(shot));
  const batches=createGenerationBatches({S,api,esc,toast,run,usageSlot:ctx.usageSlot,
    getRecord:record,hasDirty:id=>G.dirty.has(key(G.active,id)),isBusy:()=>G.saving.has(G.active)||G.pending.has(G.active)||G.rewriting.has(G.active),
    onContentChange:ctx.onContentChange,onStarted:ctx.onBatchStarted,onMutationState(){syncShotStates();setComposerBusy();if(G.loadedId&&batches.hasBatch())returnToIdea()},
    onReview(jobId,fresh){const entry=[...G.records].find(([,view])=>view.job_id===jobId);if(!entry)return;const [skill,current]=entry;G.records.set(skill,mergeReview(fresh,current,{skill}));if(G.active===skill){syncShotStates();updateSubmit()}}
  });
  function rememberHistory(){
    const history=$('.gs-history');if(!history||history.dataset.historyJob!==G.historicalPlans.get(G.active))return;
    if(history.open)G.historyOpen.add(history.dataset.historyJob);else G.historyOpen.delete(history.dataset.historyJob);
  }
  function saveDraft(){
    if(!G.active)return;rememberHistory();
    captureGenDraft();
    if(!G.loadedId)rememberAttachments(G.active,S.genMode,S.genDraft);
    G.drafts.set(G.active,{draft:structuredClone(S.genDraft),mode:S.genMode,loadedId:G.loadedId,idea:G.idea?structuredClone(G.idea):null});
  }
  function applyDraft(id){
    const draft=G.drafts.get(id),skill=S.skills.find(x=>x.id===id);
    S.skill=id;S.genDraft=draft?.draft||{duration:6,resolution:'768P',ratio:'9:16',prompt:''};
    S.genMode=draft?.mode||skill?.mode||'T2VA';G.loadedId=draft?.loadedId||null;S.loadedShot=!!G.loadedId;G.idea=draft?.idea||null;
    if(!G.loadedId){rememberAttachments(id,S.genMode,S.genDraft,false);applyAttachments(id)}
  }
  function gallery(){
    const priority=id=>id==='h3-prompt-writing'?0:id===DEFAULT?1:2;const ordered=[...S.skills].sort((a,b)=>priority(a.id)-priority(b.id));
    return `<section class="section gs-gallery"><div class="section-head"><h1>素材生成</h1><a class="button" href="#generate/animation">动画短片</a></div><div class="gs-template-grid">${ordered.map(skill=>`<button type="button" class="gs-template" data-gs-action="enter" data-id="${esc(skill.id)}" aria-label="${esc(title(skill.id))}" aria-expanded="false" aria-controls="generation-workspace"><span class="gs-template-mark" aria-hidden="true">${icon(skill.id)}</span><span><strong>${esc(title(skill.id))}</strong><small>${skill.id==='h3-prompt-writing'?'从你的想法开始':esc(skill.category)}</small></span></button>`).join('')}<div id="generation-workspace" class="gs-expansion" aria-hidden="true" inert><div class="gs-expansion-clip"><section class="gs-workspace" aria-labelledby="gs-workspace-title"></section></div></div></div>${resultsSection()}</section>`;
  }
  function resultsSection(){
    const assets=S.assets.filter(a=>(['generation','generation_batch'].includes(a.source)||a.generation_id)&&!isReferenceAsset(a));
    if(!assets.length)return '<div id="generation-result-host"></div>';
    return `<section id="generation-result-host" class="generated-section"><div class="strip-heading"><h2>已生成</h2><span class="muted">${assets.length} 个视频</span></div><div class="generation-results">${assets.map(a=>`<article class="result-card" data-generation-asset="${esc(a.id)}">${mediaTag(a)}<div class="result-body"><h3>${esc(a.name)}</h3>${a.source==='generation_batch'?'':ctx.usageSlot('asset',a.id,'视频用量')}<div class="actions"><button class="button small" data-action="select" data-id="${esc(a.id)}">${S.selected.has(a.id)?'已选入剪辑':'选入剪辑'}</button><a class="button small" href="${esc(a.url)}" download>下载</a></div></div></article>`).join('')}</div></section>`;
  }
  function previewHTML(id){
    const preview=getGenerationPreview(id);
    return `<aside class="gs-preview" aria-label="模板预览"><div class="gs-preview-screen">${preview?`<img src="${esc(preview.src)}" width="${preview.width}" height="${preview.height}" alt="${esc(preview.title)}动态样片" decoding="async" referrerpolicy="no-referrer"><div class="gs-preview-error" hidden>样片暂时无法加载</div>`:'<div class="gs-preview-empty"><span aria-hidden="true">＋</span></div>'}</div></aside>`;
  }
  const refSlots=mode=>[...(['I2VA','FL2VA'].includes(mode)?['first_frame_id']:[]),...(['L2VA','FL2VA'].includes(mode)?['last_frame_id']:[]),...(mode==='Ref2VA'?['reference_ids']:[])];
  const slotTitle=slot=>({first_frame_id:'首帧图片',last_frame_id:'尾帧图片',reference_ids:'参考素材'}[slot]);
  const attachmentKey=(skill,slot)=>`${skill}:${slot}`;
  const assetsById=ids=>ids.map(id=>S.assets.find(asset=>asset.id===id)).filter(Boolean);
  const slotIds=(draft,slot)=>slot==='reference_ids'?[...(draft.reference_ids||[])]:draft[slot]?[draft[slot]]:[];
  const seconds=value=>`${Number(Number(value||0).toFixed(1))} 秒`;
  const kindTitle=kind=>({image:'图片',video:'视频',audio:'音频'}[kind]||'素材');
  function rememberAttachments(skill,mode,draft,overwrite=true){
    const saved=G.attachments.get(skill)||{};
    for(const slot of refSlots(mode))if(overwrite||!(slot in saved))saved[slot]=slot==='reference_ids'?[...(draft[slot]||[])]:draft[slot]||'';
    G.attachments.set(skill,saved);
  }
  function applyAttachments(skill){
    const saved=G.attachments.get(skill)||{};
    delete S.genDraft.first_frame_id;delete S.genDraft.last_frame_id;S.genDraft.reference_ids=[];
    for(const slot of refSlots(S.genMode))S.genDraft[slot]=slot==='reference_ids'?[...(saved[slot]||[])]:saved[slot]||'';
  }
  function attachmentReason(asset,slot){
    if(!asset||isReferenceAsset(asset))return '样片不能用作生成素材';
    if(!['image','video','audio'].includes(asset.kind))return '不支持的素材类型';
    if(slot!=='reference_ids'&&asset.kind!=='image')return '请选择图片';
    if(['video','audio'].includes(asset.kind)&&!(asset.duration>=2&&asset.duration<=15))return '请先裁成 2–15 秒的片段';
    if(['image','video'].includes(asset.kind)&&asset.width&&asset.height&&(!(asset.width>=256&&asset.width<=5760&&asset.height>=256&&asset.height<=5760)||asset.width/asset.height<.4||asset.width/asset.height>2.5))return '宽高需为 256–5760 像素，比例 0.4–2.5';
    return '';
  }
  function validateAttachments(ids,slot){
    if(slot!=='reference_ids'&&ids.length>1)throw new Error('首帧和尾帧每次只能选择一张图片。');
    const assets=assetsById(ids);if(assets.length!==ids.length)throw new Error('素材已不存在，请重新选择。');
    for(const asset of assets){const reason=attachmentReason(asset,slot);if(reason)throw new Error(`${asset.name}：${reason}`)}
    if(slot!=='reference_ids')return;
    const count=kind=>assets.filter(a=>a.kind===kind).length;
    if(ids.length>12||count('image')>9||count('video')>3||count('audio')>3)throw new Error('最多选择 9 张图片、3 段视频、3 段音频，合计 12 份。');
    for(const kind of ['video','audio'])if(assets.filter(a=>a.kind===kind).reduce((sum,a)=>sum+Number(a.duration||0),0)>15.001)throw new Error(`参考${kindTitle(kind)}总时长不能超过 15 秒。`);
  }
  function thumbnail(asset){
    const src=asset.thumbnail_url||(asset.kind==='image'?asset.url:'');
    return src?`<img src="${esc(src)}" alt="" loading="lazy" decoding="async">`:`<span class="gs-media-symbol">${icon(asset.kind)}</span>`;
  }
  function selectedAttachments(slot){
    return slotIds(S.genDraft,slot).map(id=>{
      const asset=S.assets.find(row=>row.id===id);
      return `<div class="gs-ref-card" data-ref-asset="${esc(id)}"><button type="button" class="gs-ref-preview" data-action="preview" data-id="${esc(id)}" aria-label="预览 ${esc(asset?.name||'素材已不存在')}" ${asset?'':'disabled'}>${asset?thumbnail(asset):icon('image')}${asset&&asset.kind!=='image'?`<span class="gs-ref-duration">${seconds(asset.duration)}</span>`:''}</button><button type="button" class="gs-ref-remove" data-gs-action="ref-remove" data-slot="${slot}" data-id="${esc(id)}" aria-label="移除 ${esc(asset?.name||'素材')}">${icon('close')}</button><span class="gs-ref-name" title="${esc(asset?.name||'素材已不存在')}">${esc(asset?.name||'素材已不存在')}</span></div>`;
    }).join('');
  }
  function pickerHTML(slot){
    const selected=new Set(slotIds(S.genDraft,slot)),assets=S.assets.filter(a=>!isReferenceAsset(a)&&(slot==='reference_ids'?['image','video','audio'].includes(a.kind):a.kind==='image'));
    return assets.length?`<div class="gs-library-grid">${assets.map(asset=>{const reason=attachmentReason(asset,slot),chosen=selected.has(asset.id);return `<button type="button" class="gs-library-asset ${chosen?'is-selected':''}" data-gs-action="ref-pick" data-slot="${slot}" data-id="${esc(asset.id)}" aria-label="${esc(asset.name)}${reason?'，'+esc(reason):''}" aria-pressed="${chosen}" ${reason&&!chosen?'disabled':''} title="${esc(reason||asset.name)}"><span class="gs-library-thumb">${thumbnail(asset)}<span class="gs-library-check">${icon('check')}</span></span><strong>${esc(asset.name)}</strong><small>${esc(reason||(asset.kind==='image'?`${asset.width||'—'} × ${asset.height||'—'}`:seconds(asset.duration)))}</small></button>`}).join('')}</div>`:'<p class="gs-library-empty">还没有可选素材</p>';
  }
  function references(){
    return refSlots(S.genMode).map(slot=>{
      const stateKey=attachmentKey(G.active,slot),uploading=G.uploads.has(stateKey),open=G.pickers.has(stateKey),error=G.attachmentErrors.get(stateKey);
      const hidden=slot==='reference_ids'?`<select name="reference_ids" multiple hidden aria-hidden="true" tabindex="-1">${slotIds(S.genDraft,slot).map(id=>`<option selected value="${esc(id)}"></option>`).join('')}</select>`:`<input type="hidden" name="${slot}" value="${esc(S.genDraft[slot]||'')}">`;
      return `<section class="gs-ref-field" data-ref-slot="${slot}"><div class="gs-ref-heading"><span>${slotTitle(slot)}</span><button type="button" class="gs-library-toggle" data-gs-action="ref-library" data-slot="${slot}" aria-expanded="${open}" aria-controls="gs-picker-${slot}">${icon('library')}从素材库选取</button></div>${hidden}<div class="gs-ref-tray" data-ref-drop="${slot}" ${uploading?'aria-busy="true"':''}>${selectedAttachments(slot)}<button type="button" class="gs-ref-upload" data-gs-action="ref-upload" data-slot="${slot}" ${uploading?'disabled':''}>${icon('h3-prompt-writing')}<span>${uploading?'正在上传…':'上传 / 拖拽'}</span></button><input type="file" class="gs-ref-file" data-ref-upload="${slot}" ${slot==='reference_ids'?'multiple accept="image/*,video/*,audio/*"':'accept="image/*"'} hidden></div>${error?`<p class="gs-attachment-error" role="alert">${esc(error)}</p>`:''}<div class="gs-library-panel ${open?'is-open':''}" id="gs-picker-${slot}" ${open?'':'inert'} aria-hidden="${!open}"><div class="gs-library-clip">${pickerHTML(slot)}</div></div></section>`;
    }).join('');
  }
  function paintReferences(){
    const host=$('.gs-references');if(!host||!G.active||G.loadedId||S.route!=='generate')return;
    const scroll=new Map([...host.querySelectorAll('.gs-library-grid')].map(el=>[el.closest('[data-ref-slot]').dataset.refSlot,el.scrollTop]));
    const focus=document.activeElement?.closest('[data-gs-action]'),action=focus?.dataset.gsAction,id=focus?.dataset.id,slot=focus?.dataset.slot;
    host.innerHTML=references();for(const el of host.querySelectorAll('.gs-library-grid'))el.scrollTop=scroll.get(el.closest('[data-ref-slot]').dataset.refSlot)||0;
    if(action){const next=[...host.querySelectorAll('[data-gs-action]')].find(el=>el.dataset.gsAction===action&&el.dataset.id===id&&el.dataset.slot===slot);next?.focus({preventScroll:true})}
    setComposerBusy();
  }
  function storeSelection(skill,slot,ids){
    validateAttachments(ids,slot);
    const versionKey=attachmentKey(skill,slot);G.attachmentVersions.set(versionKey,(G.attachmentVersions.get(versionKey)||0)+1);
    const saved=G.attachments.get(skill)||{};saved[slot]=slot==='reference_ids'?[...ids]:ids[0]||'';G.attachments.set(skill,saved);G.touched.add(skill);
    if(G.active===skill&&!G.loadedId&&refSlots(S.genMode).includes(slot)){captureGenDraft();S.genDraft[slot]=slot==='reference_ids'?[...ids]:ids[0]||'';paintReferences()}
  }
  function changeSelection(slot,id,remove=false){
    const saved=G.attachments.get(G.active)||{},current=slotIds(saved,slot),ids=remove?current.filter(value=>value!==id):slot==='reference_ids'?(current.includes(id)?current.filter(value=>value!==id):[...current,id]):[id];
    G.attachmentErrors.delete(attachmentKey(G.active,slot));
    storeSelection(G.active,slot,ids);
    if(slot!=='reference_ids'&&!remove){G.pickers.delete(attachmentKey(G.active,slot));paintReferences();$(`[data-gs-action="ref-library"][data-slot="${slot}"]`)?.focus({preventScroll:true})}
  }
  async function uploadReferences(files,skill,slot){
    const stateKey=attachmentKey(skill,slot);if(!files.length||G.uploads.has(stateKey))return;
    G.attachmentErrors.delete(stateKey);const originalVersion=G.attachmentVersions.get(stateKey)||0;let uploaded=false;
    try{
      if(slot!=='reference_ids'&&files.length!==1)throw new Error('请一次上传一张图片。');
      if(files.length>12)throw new Error('一次最多上传 12 份参考素材。');
      for(const file of files){
        const image=/\.(jpe?g|png|webp|heic|heif)$/i.test(file.name),video=/\.(mp4|mov)$/i.test(file.name),audio=/\.(mp3|wav)$/i.test(file.name);
        if(!(image||slot==='reference_ids'&&(video||audio)))throw new Error(slot==='reference_ids'?'请上传 JPG、PNG、WebP、HEIC、MP4、MOV、MP3 或 WAV。':'请上传 JPG、PNG、WebP 或 HEIC 图片。');
        if(file.size>(image?30:video?50:15)*1024*1024)throw new Error(`${file.name} 超过${image?30:video?50:15} MB，请缩小后上传。`);
      }
      G.uploads.set(stateKey,true);if(G.active===skill)paintReferences();
      const assets=await ctx.uploadGenerationAssets(files);uploaded=true;
      if(slot!=='reference_ids'&&(G.attachmentVersions.get(stateKey)||0)!==originalVersion){toast('图片已上传到素材库，保留当前选择');return}
      const current=slotIds(G.attachments.get(skill)||{},slot),ids=slot==='reference_ids'?[...new Set([...current,...assets.map(a=>a.id)])]:assets.map(a=>a.id);
      storeSelection(skill,slot,ids);toast(G.active===skill?'已添加参考素材':`已添加到「${title(skill)}」`);
    }catch(error){const message=uploaded?`已上传到素材库，未加入参考：${error.message}`:error.message;G.attachmentErrors.set(stateKey,message);if(G.active!==skill||!refSlots(S.genMode).includes(slot)||G.loadedId)toast(`「${title(skill)}」${message}`,true)}
    finally{G.uploads.delete(stateKey);if(G.active===skill)paintReferences()}
  }
  function settings(){
    const d=S.genDraft;
    return `<details class="gs-settings"><summary>画面设置 <span>${esc(genModeLabel(S.genMode))} · ${d.duration||6} 秒/段 · ${esc(d.ratio||'9:16')}</span></summary><div class="mode-tabs">${['T2VA','I2VA','L2VA','FL2VA','Ref2VA'].map(id=>`<button type="button" class="${S.genMode===id?'active':''}" data-gs-action="mode" data-id="${id}" aria-pressed="${S.genMode===id}">${genModeLabel(id)}</button>`).join('')}</div><div class="field-pair"><label>默认片段时长<select name="duration">${Array.from({length:12},(_,i)=>i+4).map(n=>`<option value="${n}" ${Number(d.duration||6)===n?'selected':''}>${n} 秒</option>`).join('')}</select></label><label>画面比例<select name="ratio">${['9:16','16:9','1:1','3:4','4:3','21:9'].map(r=>`<option ${(d.ratio||'9:16')===r?'selected':''}>${r}</option>`).join('')}</select></label></div><label>清晰度<select name="resolution"><option ${d.resolution!=='2K'?'selected':''}>768P</option><option ${d.resolution==='2K'?'selected':''}>2K</option></select></label></details>`;
  }
  function lockedSettings(){
    const d=S.genDraft;
    return `<div class="gs-loaded-settings">${esc(genModeLabel(S.genMode))} · ${d.duration} 秒 · ${esc(d.ratio||'9:16')} · ${esc(d.resolution||'768P')}${['duration','ratio','resolution','first_frame_id','last_frame_id'].map(name=>`<input type="hidden" name="${name}" value="${esc(d[name]||'')}">`).join('')}<select name="reference_ids" multiple hidden>${(d.reference_ids||[]).map(id=>`<option selected value="${esc(id)}"></option>`).join('')}</select></div>`;
  }
  function composerHTML(){
    const d=S.genDraft,pending=G.pending.has(G.active)||G.rewriting.has(G.active)||[...G.uploads.keys()].some(value=>value.startsWith(`${G.active}:`)),loaded=G.loadedId&&record()?.storyboard.shots.find(x=>x.id===G.loadedId);
    return `<form id="generation-form" class="gs-composer">${loaded?`<div class="gs-loaded-heading"><button type="button" class="back-link" data-gs-action="idea">‹ 返回脚本</button><h3>${esc(loaded.title||loaded.label||'片段')}</h3></div>`:''}<label class="gs-description-label">${loaded?'已确认的提示词':'视频描述'}<textarea name="prompt" ${loaded?'readonly':''} required placeholder="${esc(S.skills.find(skill=>skill.id===G.active)?.prompt_placeholder||'描述主体、动作、场景和想要的声音。')}">${esc(d.prompt||'')}</textarea></label>${loaded?lockedSettings():`<div class="gs-references">${references()}</div>${settings()}`}${loaded?`<div class="price"><span>预计生成费用</span><strong id="generation-price">—</strong></div><p id="generation-cost-note" class="muted cost-note"></p><button type="submit" class="button primary full">提交视频生成</button><div id="generation-job-usage"></div>`:`<button type="submit" class="button primary full" ${pending?'disabled':''}>${G.pending.has(G.active)?'正在拆分镜头…':G.rewriting.has(G.active)?'正在更新提示词…':'生成方案'}</button>`}</form>`;
  }
  function storyboardHTML(){
    const current=record(),board=current?.storyboard;
    const loading=G.loading.has(G.active),pending=G.pending.has(G.active),error=G.errors.get(G.active);
    const state=error?`<div class="gs-error" role="alert">${esc(error)} ${G.readFailures.has(G.active)?'<button type="button" data-gs-action="retry-read">重新读取</button>':''}</div>`:loading&&!board?'<div class="gs-pending" role="status">读取上次脚本…</div>':pending?'<div class="gs-pending" role="status"><span class="gs-progress-dot"></span>正在编写脚本与提示词</div>':'';
    if(!board)return state;
    const content=`<div class="gs-board-heading"><h3>${esc(board.title||'分段脚本')}</h3><span>${board.shots.length} 个片段</span></div>${board.missing_inputs?.length?`<p class="notice">还需要：${esc(board.missing_inputs.join('、'))}</p>`:''}<div class="gs-shot-list">${board.shots.map((shot,i)=>shotHTML(shot,i)).join('')}</div><div id="gs-batch-controls"></div><div id="gs-batch-output"></div>`;
    const historical=G.historicalPlans.get(G.active)===current.job_id;
    return historical?`${state}<details class="gs-history" data-history-job="${esc(current.job_id)}" ${G.historyOpen.has(current.job_id)?'open':''}><summary><span>上次方案</span><strong title="${esc(board.title||'分段脚本')}">${esc(board.title||'分段脚本')}</strong><svg viewBox="0 0 24 24" aria-hidden="true"><path d="m8 5 7 7-7 7"/></svg></summary><div class="gs-history-content">${content}</div></details>`:`${state}${content}`;
  }
  function shotHTML(shot,index){
    const dirty=G.dirty.has(key(G.active,shot.id));
    return `<article class="gs-shot" data-shot-id="${esc(shot.id)}"><div class="gs-shot-heading"><span class="gs-shot-order">${String(index+1).padStart(2,'0')}</span><h4>${esc(shot.title||shot.label||'片段')}</h4><span class="gs-shot-state">${dirty?'未保存':shot.confirmed?'已确认':'待确认'}</span></div><label>片段脚本<textarea data-gs-field="script" data-shot-id="${esc(shot.id)}">${esc(shot.script||[shot.visual,shot.audio].filter(Boolean).join('\n'))}</textarea></label><details class="gs-shot-prompt"><summary>视频提示词</summary><label><span class="sr-only">片段 ${index+1} 视频提示词</span><textarea data-gs-field="prompt" data-shot-id="${esc(shot.id)}">${esc(shot.prompt)}</textarea></label></details><div class="gs-shot-usage" data-shot-usage="${esc(shot.id)}">${ctx.usageSlot('job',shot.prompt_job_id,'提示词用量')}</div><div class="gs-shot-footer"><label class="gs-shot-duration">时长<input type="number" min="4" max="15" step="1" value="${shot.duration}" data-gs-field="duration" data-shot-id="${esc(shot.id)}" aria-label="片段 ${index+1} 时长">秒</label><div class="actions"><button type="button" class="button small quiet" data-gs-action="save-shot" data-shot-id="${esc(shot.id)}">保存修改</button><button type="button" class="button small" data-gs-action="rewrite-shot" data-shot-id="${esc(shot.id)}">更新提示词</button><button type="button" class="button small" data-gs-action="confirm-shot" data-shot-id="${esc(shot.id)}">确认片段</button></div></div><div class="gs-shot-result" data-shot-result="${esc(shot.id)}"></div></article>`;
  }
  function workHTML(){
    return `<div class="gs-workspace-head"><h2 id="gs-workspace-title">${esc(title(G.active))}</h2><button type="button" class="gs-close" data-gs-action="back" aria-label="收起模板">⌃</button></div><div class="gs-workspace-body"><section class="gs-work" aria-label="脚本工作区"><div id="gs-compose-host">${composerHTML()}</div><section class="gs-script" aria-label="分段脚本"><div id="script-job-usage"></div><div id="storyboard-content">${storyboardHTML()}</div></section></section>${previewHTML(G.active)}</div>`;
  }
  function placeWorkspace(){
    const grid=$('.gs-template-grid'),expansion=$('#generation-workspace');if(!grid||!expansion)return;
    // The full catalogue remains above the same expanding work surface.
    if(grid.lastElementChild!==expansion)grid.append(expansion);
  }
  function render(){
    if(!$('.gs-gallery'))$('#app').innerHTML=gallery();
    if(G.defaultOpen){G.defaultOpen=false;enter(DEFAULT)}
    ctx.onContentChange?.();
  }
  function mountWorkspace(){
    const expansion=$('#generation-workspace'),workspace=$('.gs-workspace');if(!workspace)return;
    workspace.querySelectorAll('video,audio').forEach(el=>el.pause());workspace.innerHTML=workHTML();
    const image=$('.gs-preview img',workspace);
    image?.addEventListener('error',()=>{image.hidden=true;$('.gs-preview-error',workspace).hidden=false},{once:true});
    placeWorkspace();
    expansion.inert=false;expansion.setAttribute('aria-hidden','false');
    document.querySelectorAll('.gs-template').forEach(card=>{const active=card.dataset.id===G.active;card.classList.toggle('active',active);card.setAttribute('aria-expanded',String(active))});
    requestAnimationFrame(()=>{if(G.active)expansion.classList.add('is-open')});
    updatePlanUsage();syncShotStates();updateSubmit();setComposerBusy();batches.mount();
    if(S.loadedShot)updateGenCost();
    ctx.onContentChange?.();
  }
  async function loadLatest(id){
    if(G.records.has(id)||G.loading.has(id))return;
    G.loading.add(id);renderBoard();
    try{
      const data=await api(`/generation-plans?skill_id=${encodeURIComponent(id)}`),latest=data.plans?.[0];
      if(latest&&!G.records.has(id)){
        G.records.set(id,latest);G.historicalPlans.set(id,latest.job_id);
      }
      G.errors.delete(id);
    }catch(e){G.errors.set(id,e.message);G.readFailures.set(id,null)}
    finally{G.loading.delete(id);if(G.active===id){renderBoard();updatePlanUsage()}}
  }
  function enter(id){
    if(!S.skills.some(x=>x.id===id))return;
    saveDraft();G.opener=document.activeElement?.closest('[data-gs-action=enter]')||$(`[data-gs-action=enter][data-id="${id}"]`);G.active=id;G.sequence++;applyDraft(id);
    const pending=S.jobs.find(job=>job.kind==='generation_plan'&&job.skill_id===id&&['queued','running'].includes(job.status));
    if(pending)G.pending.set(id,pending.id);
    const rewriting=S.jobs.find(job=>job.kind==='generation_prompt'&&job.skill_id===id&&['queued','running'].includes(job.status));
    if(rewriting)G.rewriting.set(id,{job_id:rewriting.id,shot_id:rewriting.shot_id,plan_job_id:rewriting.plan_job_id});
    mountWorkspace();void loadLatest(id);
  }
  function leave(){
    batches.leave();saveDraft();const expansion=$('#generation-workspace');expansion?.querySelectorAll('video,audio').forEach(el=>el.pause());
    if(expansion){expansion.inert=true;expansion.setAttribute('aria-hidden','true');expansion.classList.remove('is-open')}
    document.querySelectorAll('.gs-template').forEach(card=>{card.classList.remove('active');card.setAttribute('aria-expanded','false')});
    G.active=null;G.loadedId=null;S.loadedShot=false;G.sequence++;
  }
  function back(){const opener=G.opener;leave();opener?.isConnected&&opener.focus({preventScroll:true});ctx.onContentChange?.()}
  function refreshComposer(){const host=$('#gs-compose-host');if(!host||S.route!=='generate'||!G.active)return;host.innerHTML=composerHTML();if(S.loadedShot)updateGenCost();updateSubmit();ctx.onContentChange?.()}
  function renderBoard(){const host=$('#storyboard-content');if(!host||!G.active||S.route!=='generate')return;rememberHistory();host.innerHTML=storyboardHTML();syncShotStates();batches.mount();ctx.onContentChange?.()}
  function updateJobUsage(selector,jobId,label){const host=$(selector);if(!host)return;if(host.dataset.jobId!==(jobId||'')){host.dataset.jobId=jobId||'';host.innerHTML=ctx.usageSlot('job',jobId,label)}ctx.onContentChange?.()}
  function updatePlanUsage(){const pending=G.pending.get(G.active),jobId=record()?.job_id,collapsed=G.historicalPlans.get(G.active)===jobId&&!G.historyOpen.has(jobId);updateJobUsage('#script-job-usage',pending&&pending!=='submitting'?pending:collapsed?null:jobId,'脚本用量')}
  function syncShotStates(){
    const current=record();if(!current)return;
    const rewrite=G.rewriting.get(G.active),busy=G.saving.has(G.active)||G.pending.has(G.active)||!!rewrite||batches.busy(),missing=!!current.storyboard.missing_inputs?.length;
    for(const row of document.querySelectorAll('.gs-shot')){
      const shot=current.storyboard.shots.find(x=>x.id===row.dataset.shotId);if(!shot)continue;
      row.querySelectorAll('textarea,input').forEach(field=>field.disabled=batches.busy());
      const dirty=G.dirty.has(key(G.active,shot.id)),confirmed=shot.confirmed&&!dirty&&!shot.prompt_stale;
      $('.gs-shot-state',row).textContent=rewrite?.shot_id===shot.id?'更新提示词中…':dirty?'未保存':shot.prompt_stale?'提示词待更新':confirmed?'已确认':'待确认';row.classList.toggle('is-confirmed',confirmed);
      $('[data-gs-action=save-shot]',row).disabled=busy||!dirty;
      const rewriteButton=$('[data-gs-action=rewrite-shot]',row);rewriteButton.hidden=!shot.prompt_stale;rewriteButton.disabled=busy;
      const confirm=$('[data-gs-action=confirm-shot]',row);confirm.hidden=confirmed||!!shot.prompt_stale;confirm.disabled=busy||missing;
    }
  }
  async function saveShot(id,confirmed){
    const skill=G.active,current=record(),shot=current?.storyboard.shots.find(x=>x.id===id);if(!shot||G.saving.has(skill)||G.pending.has(skill)||G.rewriting.has(skill)||batches.busy())return;
    const sent=signature(shot);G.saving.add(skill);syncShotStates();
    try{
      const fresh=await api(`/generation-plans/${current.job_id}/shots/${encodeURIComponent(id)}`,{...fields(shot),confirmed,expected_revision:current.revision},'PUT');
      const previous=G.records.get(skill);
      if(previous?.job_id!==current.job_id)return;
      fresh.storyboard.shots=fresh.storyboard.shots.map(item=>{
        const local=previous.storyboard.shots.find(x=>x.id===item.id),dirty=G.dirty.has(key(skill,item.id));
        if(item.id===id&&local&&signature(local)===sent){G.dirty.delete(key(skill,id));return item}
        return dirty&&local?{...item,...fields(local),confirmed:false,prompt_stale:local.prompt_stale}:item;
      });
      G.records.set(skill,fresh);toast(confirmed?'片段已确认':'修改已保存');
    }finally{G.saving.delete(skill);if(G.active===skill){syncShotStates();batches.mount()}}
  }
  async function plan(){
    if(!G.active||G.pending.has(G.active)||G.rewriting.has(G.active)||batches.busy())return;
    if([...G.uploads.keys()].some(value=>value.startsWith(`${G.active}:`)))throw new Error('参考素材正在上传，请稍等。');
    const params=generationParams();if(!params.prompt.trim())throw new Error('先粘贴或写下视频描述。');
    if(!S.config.deepseek_configured)throw new Error('脚本服务暂不可用。');
    const id=G.active;G.errors.delete(id);G.pending.set(id,'submitting');refreshComposer();renderBoard();
    try{
      const response=await api('/generation-plans',params);G.pending.set(id,response.job_id);if(G.active===id)updatePlanUsage();
      await track(response,job=>finishPlan(id,job));
    }catch(error){G.pending.delete(id);G.errors.set(id,error.message);if(G.active===id){refreshComposer();renderBoard()}throw error}
  }
  async function finishPlan(id,job){
    if(G.finished.has(job.id)||G.pending.get(id)!==job.id)return;
    G.finished.add(job.id);
    try{
      if(job.status!=='succeeded')throw new Error(job.error||'脚本未完成，请重试。');
      const fresh=await api(`/generation-plans/${job.id}`);
      if(G.active===id&&G.loadedId)returnToIdea();
      G.records.set(id,fresh);G.historicalPlans.delete(id);G.errors.delete(id);G.readFailures.delete(id);
      const savedDraft=G.drafts.get(id);if(savedDraft?.loadedId)G.drafts.set(id,{...(savedDraft.idea||savedDraft),loadedId:null,idea:null});
      for(const entry of G.dirty.keys())if(entry.startsWith(`${id}:`))G.dirty.delete(entry);
      toast('脚本已拆成片段，请逐段确认');
    }catch(error){G.errors.set(id,error.message);if(job.status==='succeeded')G.readFailures.set(id,{job_id:job.id})}
    finally{G.pending.delete(id);if(G.active===id&&S.route==='generate'){refreshComposer();renderBoard();updatePlanUsage()}}
  }
  function mergeReview(fresh,current,task){
    if(current?.job_id!==fresh.job_id)return fresh;
    fresh.storyboard.shots=fresh.storyboard.shots.map(shot=>{
      const local=current.storyboard.shots.find(row=>row.id===shot.id);
      if(!local||!G.dirty.has(key(task.skill,shot.id)))return shot;
      const edits=fields(local);if(shot.id===task.shot_id&&task.base&&local.prompt===task.base.prompt)edits.prompt=shot.prompt;
      return {...shot,...edits,confirmed:false,prompt_stale:local.prompt_stale};
    });return fresh;
  }
  async function retryRead(){
    const id=G.active,failure=G.readFailures.get(id);if(!failure?.job_id){G.errors.delete(id);return loadLatest(id)}
    G.loading.add(id);renderBoard();
    try{
      const fresh=await api(`/generation-plans/${failure.job_id}`),current=G.records.get(id);
      if(current?.job_id!==fresh.job_id){G.historicalPlans.delete(id);if(G.active===id&&G.loadedId)returnToIdea();for(const entry of G.dirty.keys())if(entry.startsWith(`${id}:`))G.dirty.delete(entry)}
      G.records.set(id,mergeReview(fresh,current,{...failure.rewrite,skill:id}));G.errors.delete(id);G.readFailures.delete(id);
    }catch(error){G.errors.set(id,error.message)}
    finally{G.loading.delete(id);if(G.active===id){renderBoard();updatePlanUsage()}}
  }
  function setComposerBusy(){const button=$('#generation-form [type=submit]');if(button&&!G.loadedId){button.disabled=G.pending.has(G.active)||G.rewriting.has(G.active)||batches.busy()||[...G.uploads.keys()].some(value=>value.startsWith(`${G.active}:`));button.textContent=G.pending.has(G.active)?'正在拆分镜头…':G.rewriting.has(G.active)?'正在更新提示词…':'生成方案'}}
  async function rewriteShot(shotId){
    const skill=G.active;if(G.pending.has(skill)||G.rewriting.has(skill)||G.saving.has(skill)||batches.busy())return;
    if(G.dirty.has(key(skill,shotId)))await saveShot(shotId,false);
    if(G.dirty.has(key(skill,shotId)))throw new Error('保存期间又有修改，请再次更新提示词。');
    const current=G.records.get(skill);if(!current)return;
    const request={shot_id:shotId,plan_job_id:current.job_id,job_id:null,base:fields(current.storyboard.shots.find(shot=>shot.id===shotId))};G.rewriting.set(skill,request);syncShotStates();setComposerBusy();
    try{
      const response=await api(`/generation-plans/${current.job_id}/shots/${shotId}/rewrite-prompt`,{expected_revision:current.revision});
      request.job_id=response.job_id;await track(response,job=>finishRewrite(skill,job));
    }catch(error){G.rewriting.delete(skill);if(G.active===skill){syncShotStates();setComposerBusy()}throw error}
  }
  async function finishRewrite(skill,job){
    const task=G.rewriting.get(skill);if(!task||task.job_id!==job.id||G.finished.has(job.id))return;
    G.finished.add(job.id);
    try{
      if(job.status!=='succeeded')throw new Error(job.error||'提示词未更新，请重试。');
      const fresh=await api(`/generation-plans/${task.plan_job_id}`),current=G.records.get(skill);
      if(current&&current.job_id!==fresh.job_id)return;
      G.records.set(skill,mergeReview(fresh,current,{...task,skill}));G.errors.delete(skill);toast('提示词已更新，请确认片段');
    }catch(error){G.errors.set(skill,error.message);if(job.status==='succeeded')G.readFailures.set(skill,{job_id:task.plan_job_id,rewrite:task})}
    finally{G.rewriting.delete(skill);if(G.active===skill){renderBoard();updateSubmit();setComposerBusy()}}
  }
  function onJobs(jobs){
    for(const job of jobs){
      for(const item of G.submissions.values())if(item.job_id===job.id)item.status=job.status;
      if(['succeeded','failed','needs_configuration','unknown'].includes(job.status))for(const [skill,task] of G.rewriting)if(task.job_id===job.id)void finishRewrite(skill,job);
      if(['succeeded','failed','needs_configuration','unknown'].includes(job.status))for(const [id,jobId] of G.pending)if(jobId===job.id)void finishPlan(id,job);
    }
    persistSubmissions();if(S.route==='generate'){updateSubmit();batches.onJobs()}
  }
  function refresh(){
    if(S.route!=='generate')return;const old=$('#generation-result-host');if(!old)return;
    const existing=[...old.querySelectorAll('[data-generation-asset]')].map(x=>x.dataset.generationAsset),next=S.assets.filter(a=>(['generation','generation_batch'].includes(a.source)||a.generation_id)&&!isReferenceAsset(a)).map(a=>a.id);
    if(JSON.stringify(existing)===JSON.stringify(next))return;
    const template=document.createElement('template');template.innerHTML=resultsSection();
    for(const item of template.content.querySelectorAll('[data-generation-asset]')){const live=old.querySelector(`[data-generation-asset="${item.dataset.generationAsset}"]`);if(live)item.replaceWith(live)}
    old.replaceWith(template.content);ctx.onContentChange?.();
  }
  function persistSubmissions(){sessionStorage.setItem('video-generation-submissions',JSON.stringify([...G.submissions]))}
  function submissionParams(){const {asset_ids,...params}=generationParams();return{...params,storyboard_job_id:record()?.job_id,storyboard_shot_id:G.loadedId,review_revision:record()?.revision}}
  function submissionKey(params){const {review_revision,...stable}=params;return JSON.stringify(stable)}
  function updateSubmit(){
    if(!G.loadedId||S.route!=='generate')return;
    const button=$('#generation-form button[type=submit]');if(!button)return;
    const item=G.submissions.get(submissionKey(submissionParams()));updateJobUsage('#generation-job-usage',item?.job_id,'片段生成用量');
    const labels={submitting:'正在提交…',queued:'正在生成…',running:'正在生成…',succeeded:'已生成',failed:'生成未完成',unknown:'结果待查询',retry:'查询生成结果'};
    button.textContent=item?(labels[item.status]||'查询生成结果'):'提交视频生成';button.disabled=!!item&&item.status!=='retry';
  }
  async function submit(){
    if(!G.loadedId)return plan();
    const shot=record()?.storyboard.shots.find(x=>x.id===G.loadedId);
    if(!shot?.confirmed||shot.prompt_stale||G.dirty.has(key(G.active,G.loadedId)))throw new Error('请先确认这个片段。');
    const params=submissionParams();if(!S.config.minimax_configured)throw new Error('视频生成服务暂不可用。');
    const hash=submissionKey(params);let item=G.submissions.get(hash);
    if(item&&item.status!=='retry'){toast(item.status==='succeeded'?'这个片段已生成':'请在任务中查看这次生成');return}
    item=item||{submission_id:crypto.randomUUID(),status:'submitting',params:structuredClone(params)};item.status='submitting';G.submissions.set(hash,item);persistSubmissions();updateSubmit();
    try{const response=await api('/generations',{...(item.params||params),require_h3_prompt_contract:true,submission_id:item.submission_id});item.job_id=response.job_id;item.status='queued';persistSubmissions();await track(response,()=>toast('视频已生成并加入素材'));toast('已提交片段生成')}
    catch(error){item.status='retry';persistSubmissions();throw error}finally{updateSubmit()}
  }
  function loadShot(id){
    const shot=record()?.storyboard.shots.find(x=>x.id===id);if(!shot?.confirmed||shot.prompt_stale||G.dirty.has(key(G.active,id))||G.pending.has(G.active)||G.rewriting.has(G.active)||batches.busy()||batches.hasBatch())return;
    if(!G.loadedId){captureGenDraft();G.idea={draft:structuredClone(S.genDraft),mode:S.genMode}}
    G.loadedId=id;S.loadedShot=true;S.genMode=shot.mode;S.genDraft={...S.genDraft,prompt:shot.prompt,duration:shot.duration,first_frame_id:shot.first_frame_id||shot.first_asset_id||'',last_frame_id:shot.last_frame_id||shot.last_asset_id||'',reference_ids:shot.reference_asset_ids||[...(shot.reference_image_ids||[]),...(shot.reference_video_ids||[]),...(shot.reference_audio_ids||[])]};
    refreshComposer();$('#gs-compose-host')?.scrollIntoView({block:'start',behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'instant':'smooth'});
  }
  function returnToIdea(){if(G.idea){S.genDraft=structuredClone(G.idea.draft);S.genMode=G.idea.mode}G.loadedId=null;S.loadedShot=false;applyAttachments(G.active);refreshComposer()}
  document.addEventListener('click',event=>{
    const b=event.target.closest('[data-gs-action]');if(!b)return;
    const action=b.dataset.gsAction;
    if(action==='enter')return G.active===b.dataset.id?back():enter(b.dataset.id);
    if(action==='back')return back();
    if(action==='idea')return returnToIdea();
    if(action==='mode'){if(G.loadedId)return;G.touched.add(G.active);captureGenDraft();rememberAttachments(G.active,S.genMode,S.genDraft);S.genMode=b.dataset.id;applyAttachments(G.active);refreshComposer();$('.gs-settings').open=true;return}
    if(action==='ref-upload'){const input=$(`[data-ref-upload="${b.dataset.slot}"]`);if(input){input.value='';input.dataset.owner=G.active;input.click()}return}
    if(action==='ref-library'){
      const stateKey=attachmentKey(G.active,b.dataset.slot),panel=document.getElementById(`gs-picker-${b.dataset.slot}`),open=!G.pickers.has(stateKey);
      if(open)G.pickers.add(stateKey);else G.pickers.delete(stateKey);
      panel?.classList.toggle('is-open',open);if(panel){panel.inert=!open;panel.setAttribute('aria-hidden',String(!open))}b.setAttribute('aria-expanded',String(open));return;
    }
    if(action==='ref-pick'||action==='ref-remove')return void run(()=>changeSelection(b.dataset.slot,b.dataset.id,action==='ref-remove'));
    if(action==='load-shot')return loadShot(b.dataset.shotId);
    if(action==='rewrite-shot')return void run(()=>rewriteShot(b.dataset.shotId));
    if(action==='save-shot'||action==='confirm-shot')return void run(()=>saveShot(b.dataset.shotId,action==='confirm-shot'));
    if(action==='retry-read')void retryRead();
  });
  document.addEventListener('toggle',event=>{
    if(!event.target.matches('.gs-history'))return;
    const id=event.target.dataset.historyJob;if(event.target.open)G.historyOpen.add(id);else G.historyOpen.delete(id);
    updatePlanUsage();
  },true);
  document.addEventListener('input',event=>{
    const field=event.target.dataset.gsField;
    if(field){const shot=record()?.storyboard.shots.find(x=>x.id===event.target.dataset.shotId);if(!shot)return;shot[field]=field==='duration'?Number(event.target.value):event.target.value;shot.confirmed=false;if(field==='script'||field==='duration')shot.prompt_stale=true;else if(field==='prompt')shot.prompt_stale=false;G.dirty.set(key(G.active,shot.id),true);if(G.loadedId===shot.id)returnToIdea();syncShotStates();batches.paint()}
    if(event.target.matches('#generation-form [name=prompt]')&&!G.loadedId){G.touched.add(G.active);captureGenDraft()}
  });
  document.addEventListener('change',event=>{
    if(event.target.matches('[data-ref-upload]')){const input=event.target;void uploadReferences([...input.files],input.dataset.owner||G.active,input.dataset.refUpload);return}
    if(!event.target.matches('#generation-form select'))return;G.touched.add(G.active);if(G.loadedId)returnToIdea();else{captureGenDraft();const summary=$('.gs-settings>summary span');if(summary)summary.textContent=`${genModeLabel(S.genMode)} · ${S.genDraft.duration||6} 秒/段 · ${S.genDraft.ratio||'9:16'}`}});
  document.addEventListener('dragover',event=>{const tray=event.target.closest('[data-ref-drop]');if(!tray||!event.dataTransfer?.types.includes('Files'))return;event.preventDefault();event.dataTransfer.dropEffect='copy';tray.classList.add('is-dragging')});
  document.addEventListener('dragleave',event=>{const tray=event.target.closest('[data-ref-drop]');if(tray&&!tray.contains(event.relatedTarget))tray.classList.remove('is-dragging')});
  document.addEventListener('drop',event=>{const tray=event.target.closest('[data-ref-drop]');if(!tray||!event.dataTransfer?.types.includes('Files'))return;event.preventDefault();tray.classList.remove('is-dragging');void uploadReferences([...event.dataTransfer.files],G.active,tray.dataset.refDrop)});
  return{render,refresh,onJobs,saveDraft,storyboardHTML,plan,submit,leave,showGallery(){leave();G.defaultOpen=true},active:()=>G.active};
}
