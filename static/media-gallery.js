const galleryStatus={
  not_analyzed:'待分析',queued:'等待分析',running:'分析中',succeeded:'已分析',
  failed:'分析失败',needs_configuration:'服务暂不可用',unknown:'状态待确认',
};
const kindNames={image:'图片',video:'视频',audio:'音频'};

export const isReferenceAsset=asset=>!!asset&&(asset.purpose==='reference'||asset.analysis?.reference_analysis===true||Array.isArray(asset.analysis?.structure)&&asset.analysis.structure.length>0);

export function createMediaGallery(ctx){
  const {S,esc,fmt,mediaTag,thumb,assetById,selectedAssets,persistSelection}=ctx;
  const $=ctx.$||((selector,root=document)=>root.querySelector(selector));
  const $$=ctx.$$||((selector,root=document)=>[...root.querySelectorAll(selector)]);
  const state={
    analysis:{month:null,day:null,summaries:new Map(),loadedAt:0,loading:false,detailId:null,scroll:0,anchorId:null,anchorOffset:null,openDates:new Set(),restore:0},
    reference:{detailId:null,scroll:0,anchorId:null,anchorOffset:null,search:'',renaming:null,restore:0,rhythmPages:new Map(),presentationLinks:new Map(),presentationPending:new Set(),chapters:new Map()},
  };
  const titleFor=kind=>kind==='reference'?'样片分析':'素材&分析';
  const uploadFor=kind=>kind==='reference'?'上传样片':'添加素材';
  const routeIs=kind=>S.route===kind;
  const selectedSegment=(id,index)=>ctx.segmentSelected?ctx.segmentSelected(id,index):S.segments.some(item=>item.asset_id===id&&item.index===index);
  const isReference=isReferenceAsset;
  const assetsFor=kind=>S.assets.filter(asset=>kind==='reference'?asset.kind==='video'&&isReference(asset):asset.source!=='candidate_preview'&&!isReference(asset)&&['image','video','audio'].includes(asset.kind));

  function visibleAssets(kind){
    if(kind==='reference'){
      const query=state.reference.search.trim().toLocaleLowerCase();
      return assetsFor(kind).filter(asset=>!query||String(asset.name||'').toLocaleLowerCase().includes(query)).sort((a,b)=>String(a.name||'').localeCompare(String(b.name||''),'zh-CN',{numeric:true}));
    }
    const query=String(S.search||'').trim().toLowerCase();
    return assetsFor(kind).filter(asset=>(!S.filter||S.filter==='all'||asset.kind===S.filter)&&(!query||[asset.name,asset.analysis?.summary,...(asset.analysis?.tags||[])].filter(Boolean).join(' ').toLowerCase().includes(query)));
  }
  function status(asset,kind){
    if(asset.kind==='audio')return {value:'audio',label:asset.audio_status==='succeeded'?'已转写':'可试听'};
    const value=asset.analysis_status||'not_analyzed';
    if(['failed','needs_configuration','unknown','queued','running'].includes(value))return {value,label:galleryStatus[value]};
    const complete=kind==='reference'?isReference(asset)&&!!asset.analysis?.reference_analysis||!!asset.analysis?.structure?.length:!!asset.analysis;
    return complete?{value:'succeeded',label:'已分析'}:{value:'not_analyzed',label:'待分析'};
  }
  function dateInfo(asset){
    const date=new Date(asset.created_at||asset.uploaded_at||'');
    if(Number.isNaN(date.getTime()))return {key:'unknown',label:'日期未记录',time:0};
    const key=new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit'}).format(date);
    return {key,label:date.toLocaleDateString('zh-CN',{year:'numeric',month:'long',day:'numeric'}),time:date.getTime()};
  }
  function grouped(kind){
    const groups=new Map();
    for(const asset of visibleAssets(kind)){
      const date=dateInfo(asset);
      if(!groups.has(date.key))groups.set(date.key,{...date,assets:[]});
      groups.get(date.key).assets.push(asset);
    }
    return [...groups.values()].sort((a,b)=>b.time-a.time).map(group=>({...group,assets:group.assets.sort((a,b)=>dateInfo(b).time-dateInfo(a).time)}));
  }
  function badge(asset,kind){
    const value=status(asset,kind);
    return `<span class="mg-status mg-status-${value.value}">${esc(value.label)}</span>`;
  }
  function mediaInfo(asset){
    return [kindNames[asset.kind]||'素材',asset.duration?fmt(asset.duration):null,asset.width&&asset.height?`${asset.width} × ${asset.height}`:null].filter(Boolean).join('　');
  }
  function uploadButton(kind,primary=false){
    return `<button type="button" class="button ${primary?'primary':''}" data-action="upload" data-purpose="${kind}">${ctx.icons?.plus||'＋'} ${uploadFor(kind)}</button>`;
  }
  function cardContent(asset,kind){
    const analysis=asset.analysis,summary=String(analysis?.summary||'').trim();
    const tags=[...new Set((analysis?.tags||[]).filter(tag=>typeof tag==='string'&&tag.trim()))].slice(0,3);
    const selected=S.selected.has(asset.id),duration=Number(asset.duration)>0?fmt(asset.duration):null;
    const detail=[kindNames[asset.kind]||'素材',duration].filter(Boolean).join(' · ');
    const segments=analysis?.segments?.length||0;
    return `<button type="button" class="mg-card-preview" data-mg-action="open" data-kind="${kind}" data-id="${esc(asset.id)}" aria-label="查看${esc(asset.name)}">${thumb(asset)?`<img src="${esc(thumb(asset))}" alt="" loading="lazy">`:`<span class="mg-no-thumb ${asset.kind==='audio'?'mg-audio-thumb':''}">${asset.kind==='audio'?'♫':'暂无预览'}</span>`}${duration?`<span class="mg-card-type">${esc(duration)}</span>`:''}</button>${kind==='analysis'?`<button type="button" class="mg-card-select ${selected?'selected':''}" data-mg-action="select" data-id="${esc(asset.id)}" aria-label="${selected?'取消选择':'选入剪辑'} ${esc(asset.name)}" title="${selected?'取消选择':'选入剪辑'}" aria-pressed="${selected}">${selected?'✓':'＋'}</button>`:''}<div class="mg-card-body"><button type="button" class="mg-card-name" data-mg-action="open" data-kind="${kind}" data-id="${esc(asset.id)}" title="${esc(asset.name)}">${esc(asset.name)}</button><div class="mg-card-meta"><span>${esc(detail)}</span>${badge(asset,kind)}</div>${summary?`<p class="mg-card-summary">${esc(summary)}</p>`:''}${asset.analysis_error?`<p class="mg-card-error">${esc(asset.analysis_error)}</p>`:''}${tags.length?`<div class="mg-card-tags">${tags.map(tag=>`<span title="${esc(tag)}">${esc(tag)}</span>`).join('')}</div>`:''}${segments?`<div class="mg-card-facts"><span>${segments} 个可用片段</span></div>`:''}</div>`;
  }
  function groupSummary(group){
    return `<span class="mg-window-stack" aria-hidden="true">${group.assets.slice(0,3).reverse().map((asset,index)=>`<span class="mg-stack-card" style="--stack-index:${index}">${thumb(asset)?`<img src="${esc(thumb(asset))}" alt="" loading="lazy">`:`<span class="mg-stack-empty">${asset.kind==='audio'?'♫':'▧'}</span>`}</span>`).join('')}</span><span class="mg-date-caption"><span class="mg-date-title">${esc(group.label)}</span><span class="mg-date-count">${group.assets.length} 份</span></span><span class="mg-date-toggle"><span class="mg-date-toggle-label">展开</span><span class="mg-date-chevron" aria-hidden="true"></span></span>`;
  }
  function filtersHTML(){
    return `<div class="mg-toolbar"><div class="mg-kind-tabs" role="group" aria-label="素材类型">${[['all','全部'],['video','视频'],['image','图片'],['audio','音频']].map(([id,label])=>`<button type="button" data-mg-action="filter" data-id="${id}" class="${(S.filter||'all')===id?'active':''}" aria-pressed="${(S.filter||'all')===id}">${label}</button>`).join('')}</div><label class="mg-search">${ctx.icons?.search||''}<input type="search" id="mg-search" value="${esc(S.search||'')}" placeholder="搜索名称、标签或内容" aria-label="搜索素材"></label><button type="button" class="button small quiet" data-mg-action="select-visible">选择当前素材</button><span class="mg-selected-count"></span></div>`;
  }
  function referenceFiltersHTML(){
    return `<div class="mg-toolbar mg-reference-toolbar"><label class="mg-search">${ctx.icons?.search||''}<input type="search" id="mg-reference-search" value="${esc(state.reference.search)}" placeholder="搜索文件名" aria-label="搜索样片文件名"></label></div>`;
  }
  function referenceTitleHTML(asset){
    const edit=state.reference.renaming;
    if(edit?.id===asset.id)return `<form class="mg-rename-form" data-asset-id="${esc(asset.id)}"><input name="name" value="${esc(edit.value)}" maxlength="240" aria-label="样片名称" required ${edit.saving?'disabled':''}><button type="submit" class="button small" ${edit.saving?'disabled':''}>${edit.saving?'保存中':'保存'}</button><button type="button" class="button small quiet" data-mg-action="rename-cancel" ${edit.saving?'disabled':''}>取消</button></form>`;
    return `<button type="button" class="mg-reference-name" data-mg-action="open" data-kind="reference" data-id="${esc(asset.id)}" title="${esc(asset.name)}">${esc(asset.name)}</button><button type="button" class="mg-rename-button" data-mg-action="rename" data-id="${esc(asset.id)}" aria-label="重命名 ${esc(asset.name)}" title="重命名"><svg viewBox="0 0 20 20" aria-hidden="true"><path d="m12.7 3.1 4.2 4.2M3 17l4.2-.8L17 6.4a1.6 1.6 0 0 0 0-2.3l-1.1-1.1a1.6 1.6 0 0 0-2.3 0L3.8 12.8 3 17Z"/></svg></button>`;
  }
  function referencePreviewHTML(asset){
    return `<button type="button" class="mg-reference-preview" data-mg-action="open" data-kind="reference" data-id="${esc(asset.id)}" aria-label="查看 ${esc(asset.name)} 的分析">${thumb(asset)?`<img src="${esc(thumb(asset))}" alt="" loading="lazy">`:'<span class="mg-no-thumb">暂无预览</span>'}<span class="mg-reference-play" aria-hidden="true">▶</span><span class="mg-card-type">${fmt(asset.duration)}</span></button>`;
  }
  function referenceDurationHTML(node){
    const valid=value=>(typeof value==='number'||typeof value==='string'&&value.trim()!=='')&&Number.isFinite(Number(value));
    if(!valid(node.start)||!valid(node.end))return '';
    const start=Number(node.start),duration=Number(node.end)-start;
    if(start<0||duration<=0)return '';
    const label=duration<.1?'&lt;0.1':String(Number(duration.toFixed(1)));
    return `<strong class="mg-reference-duration"><span>${label}</span><small>秒</small></strong>`;
  }
  const presentationKinds={narrative:'叙事结构',motion_transition:'运镜衔接',rhythm_montage:'节奏混剪',scene_collection:'场景组接',uncertain:'镜头概览'};
  const linkKinds={motion_match:'运镜衔接',rhythm_link:'节奏衔接',content_progression:'内容推进',juxtaposition:'并列组接',uncertain:'衔接待核对',sequence:'时间顺序'};
  function semanticFor(asset){
    const result=asset?.analysis?.semantic_structure;
    if(!result||asset.analysis?.semantic_invalidated||result.version!==1||!Array.isArray(result.chapters)||!result.chapters.length||!Array.isArray(result.units)||!result.units.length)return null;
    return result;
  }
  function chapterFor(asset){
    const result=semanticFor(asset),id=state.reference.chapters.get(asset.id);
    return result?.chapters.find(chapter=>chapter.id===id)||null;
  }
  function unitsFor(asset){
    const result=semanticFor(asset);if(!result)return [];
    const chapter=chapterFor(asset),ids=chapter&&new Set(chapter.unit_ids||[]);
    return ids?result.units.filter(unit=>ids.has(unit.id)):result.units;
  }
  function shotsFor(asset){
    const shots=asset.analysis?.shots||[];
    if(!chapterFor(asset))return shots;
    const ids=new Set(unitsFor(asset).flatMap(unit=>unit.shot_ids||[]));
    return shots.filter(shot=>ids.has(shot.id));
  }
  function isNarrative(asset){return !!semanticFor(asset)||presentationFor(asset)?.kind==='narrative'}
  function refreshesSemantics(asset){return !!asset?.analysis?.semantic_structure||isNarrative(asset)}
  function presentationFor(asset){
    const result=asset?.analysis?.presentation;
    if(!result||![1,2,3].includes(result.version)||!Object.hasOwn(presentationKinds,result.kind))return null;
    // source_analysis_id names the input evidence; the published analysis has a new id.
    // The backend removes presentation when that evidence changes.
    const nodes=result.nodes,links=result.links;
    if(!Array.isArray(nodes)||!nodes.length||!Array.isArray(links))return null;
    const ids=new Set(nodes.map(node=>node.id));
    if(ids.size!==nodes.length||nodes.some(node=>!node.id||!Number.isFinite(node.start)||!Number.isFinite(node.end)||node.start<0||node.end<=node.start))return null;
    if(links.some(link=>!ids.has(link.from)||!ids.has(link.to)))return null;
    return result;
  }
  function presentationNodes(asset){
    const semantic=semanticFor(asset);
    if(semantic)return semantic.chapters;
    const result=presentationFor(asset);
    if(result?.nodes?.length)return result.nodes;
    return (asset.analysis?.shots||[]).map((shot,index)=>({id:shot.id||`shot-${index+1}`,label:`镜头 ${index+1}`,start:shot.start,end:shot.end,shot_ids:shot.id?[shot.id]:[],description:shot.description||''}));
  }
  function presentationBusy(asset){
    return state.reference.presentationPending.has(asset.id)||['queued','running'].includes(refreshesSemantics(asset)?asset.semantic_status:asset.presentation_status)||(S.jobs||[]).some(job=>['reference_presentation','reference_semantics'].includes(job.kind)&&job.payload?.asset_id===asset.id&&['queued','running'].includes(job.status));
  }
  function presentationButton(asset){
    if(!asset.analysis)return '';
    const busy=presentationBusy(asset),result=semanticFor(asset)||presentationFor(asset);
    const error=refreshesSemantics(asset)?asset.semantic_error:asset.presentation_error;
    return `${error&&!busy?`<span class="mg-refresh-error" title="${esc(error)}">更新未完成</span>`:''}<button type="button" class="button small quiet mg-presentation-refresh" data-mg-action="presentation" data-id="${esc(asset.id)}" ${busy?'disabled':''}>${busy?'分析剪辑方式中':result?'更新剪辑方式':'分析剪辑方式'}</button>`;
  }
  function nodeThumbnail(asset,node){
    const shots=asset.analysis?.shots||[],ids=new Set(node.shot_ids||[]);
    if(semanticFor(asset)){
      // Chapter boundaries follow speech, so their first frame can belong to the
      // preceding shot. Reuse an interior thumbnail instead of a boundary frame.
      const middle=(Number(node.start)+Number(node.end))/2;
      const inside=shots.filter(shot=>shot.thumbnail_url&&Number(shot.thumbnail_time)>=Number(node.start)&&Number(shot.thumbnail_time)<Number(node.end));
      inside.sort((a,b)=>Math.abs(Number(a.thumbnail_time)-middle)-Math.abs(Number(b.thumbnail_time)-middle));
      if(inside.length)return inside[0].thumbnail_url;
    }
    const matched=shots.find(shot=>ids.has(shot.id));
    const byTime=shots.find(shot=>Number(node.start)>=Number(shot.start)&&Number(node.start)<Number(shot.end));
    return (matched||byTime)?.thumbnail_url||'';
  }
  function presentationButtonAttrs(asset,node,compact){
    return `data-mg-action="${compact?'open-seek':'presentation-seek'}" data-kind="reference" data-id="${esc(asset.id)}" data-time="${Number(node.start)||0}" data-end="${Number(node.end)||0}" data-node-id="${esc(node.id||'')}"`;
  }
  function presentationTrackHTML(asset,compact=false){
    const result=presentationFor(asset),nodes=presentationNodes(asset),semantic=semanticFor(asset),kind=semantic?'narrative':result?.kind||'uncertain';
    if(!nodes.length)return '';
    const visible=compact?nodes.slice(0,4):nodes;
    const links=result?.links||[];
    const linked=kind==='motion_transition'||kind==='rhythm_montage';
    const rows=visible.map((node,index)=>{
      const image=nodeThumbnail(asset,node),next=visible[index+1],link=next&&links.find(item=>item.from===node.id&&item.to===next.id);
      const text=node.label||`镜头 ${index+1}`;
      const card=`<button type="button" class="mg-presentation-node" ${semantic?'aria-pressed="false"':''} style="--mg-duration-width:${Math.max(4,Math.min(100,(Number(node.end)-Number(node.start))/Math.max(...nodes.map(item=>Number(item.end)-Number(item.start)),.1)*100)).toFixed(1)}%" ${presentationButtonAttrs(asset,node,compact)} aria-label="${esc(text)}，${timecode(node.start)} 至 ${timecode(node.end)}"><span class="mg-presentation-thumb">${image?`<img src="${esc(image)}" alt="" loading="lazy">`:'<span class="mg-shot-placeholder" aria-hidden="true">▧</span>'}<span class="mg-presentation-number">${index+1}</span></span><span class="mg-presentation-node-title">${esc(text)}</span><span class="mg-presentation-node-time">${timecode(node.start)}–${timecode(node.end)}</span>${referenceDurationHTML(node)}${!compact&&node.description?`<span class="mg-presentation-node-description">${esc(node.description)}</span>`:''}</button>`;
      const connection=linked&&next?`<button type="button" class="mg-presentation-joint ${link?.kind==='uncertain'?'is-uncertain':''}" data-mg-action="presentation-link" data-kind="reference" data-id="${esc(asset.id)}" data-from="${esc(node.id||'')}" data-to="${esc(next.id||'')}" aria-label="查看 ${esc(text)} 与 ${esc(next.label||'下一镜头')} 的衔接依据" ${hasLinkEvidence(link)?'':'disabled'}><svg viewBox="0 0 32 14" aria-hidden="true"><path d="M5 7h22"/><circle cx="3" cy="7" r="2"/><circle cx="29" cy="7" r="2"/></svg>${!compact?`<span>${esc(linkKinds[link?.kind]||'待核对')}</span>`:''}</button>`:'';
      return `<li>${card}${connection}</li>`;
    }).join('');
    return `<ol class="mg-presentation-track is-${kind} ${compact?'is-compact':''}" aria-label="${esc(presentationKinds[kind])}">${rows}${compact&&nodes.length>4?`<li class="mg-presentation-more"><button type="button" data-mg-action="open" data-kind="reference" data-id="${esc(asset.id)}" aria-label="查看全部 ${nodes.length} 个${semantic?'章节':'镜头'}">+${nodes.length-4}</button></li>`:''}</ol>`;
  }
  const hasLinkEvidence=link=>!!(link&&(String(link.description||'').trim()||link.evidence_times?.length));
  function presentationLinkHTML(asset){
    const result=presentationFor(asset),key=state.reference.presentationLinks.get(asset.id);
    const link=result?.links?.find(item=>`${item.from}|${item.to}`===key);
    if(!hasLinkEvidence(link))return '';
    const from=result.nodes?.find(node=>node.id===link.from),to=result.nodes?.find(node=>node.id===link.to);
    return `<div class="mg-presentation-link-detail"><div class="mg-presentation-link-heading"><h3>${esc(linkKinds[link.kind]||'衔接依据')}</h3><button type="button" data-mg-action="presentation-link-close" data-id="${esc(asset.id)}" aria-label="收起衔接依据">×</button></div><div class="mg-presentation-pair"><span>${esc(from?.label||'前一镜头')}</span><span aria-hidden="true">→</span><span>${esc(to?.label||'后一镜头')}</span></div><p>${esc(link.description||'暂无衔接判断')}</p>${link.evidence_times?.length?`<div class="mg-presentation-times">${[...new Set(link.evidence_times)].map(time=>`<button type="button" data-mg-action="presentation-evidence" data-time="${Number(time)||0}" aria-label="查看依据画面 ${timecode(time)}">${timecode(time)}</button>`).join('')}</div>`:''}</div>`;
  }
  function referenceCopyHTML(asset){
    const analysis=asset.analysis,semantic=semanticFor(asset),segments=contentFor(asset),shots=analysis?.shots||[],tags=[...new Set(analysis?.tags||[])].slice(0,8),presentation=presentationFor(asset);
    const metadata=[semantic?`${semantic.chapters.length} 个章节`:null,segments.length?`${segments.length} 个内容段`:null,shots.length?`自动识别 ${shots.length} 个镜头`:analysis?'镜头待分析':null,asset.width&&asset.height?`${asset.width} × ${asset.height}`:null].filter(Boolean);
    const summary=semantic?.thesis||presentation?.summary||analysis?.summary||(['queued','running'].includes(status(asset,'reference').value)?'正在分析画面与剪辑结构':asset.analysis_error||'暂无分析结果');
    return `<div class="mg-reference-meta">${badge(asset,'reference')}${presentation?`<span class="mg-presentation-type">${esc(presentation.label||presentationKinds[presentation.kind])}</span>`:''}${metadata.map(item=>`<span>${esc(item)}</span>`).join('')}<div data-usage-scope="asset" data-usage-asset-id="${esc(asset.id)}"></div></div><p class="mg-reference-summary ${asset.analysis_error&&!analysis?'has-error':''}">${esc(summary)}</p>${tags.length?`<div class="mg-tags mg-reference-tags">${tags.map(tag=>`<span>${esc(tag)}</span>`).join('')}</div>`:''}${presentationTrackHTML(asset,true)}<div class="mg-reference-footer"><button type="button" class="mg-reference-open" data-mg-action="open" data-kind="reference" data-id="${esc(asset.id)}">查看完整分析 <span aria-hidden="true">↗</span></button>${analysis?presentationButton(asset):analyzeButton(asset,'reference')}</div>`;
  }
  function refreshReferenceCards(root){
    const assets=visibleAssets('reference'),gallery=$('.mg-gallery',root);
    gallery.classList.add('mg-reference-gallery');
    $('.mg-gallery-count',root).textContent=state.reference.search?`${assets.length} / ${assetsFor('reference').length} 条样片`:`${assets.length} 条样片`;
    if(!assets.length){
      gallery.innerHTML=`<div class="mg-empty"><p>${assetsFor('reference').length?'没有匹配的样片':'还没有样片'}</p>${assetsFor('reference').length?'<button type="button" class="button" data-mg-action="reference-reset-search">清除搜索</button>':uploadButton('reference',true)}</div>`;
      return;
    }
    $('.mg-empty',gallery)?.remove();
    const ids=new Set(assets.map(asset=>asset.id));
    $$('.mg-reference-card',gallery).forEach(card=>{if(!ids.has(card.dataset.assetId))card.remove()});
    assets.forEach((asset,index)=>{
      let card=$$('.mg-reference-card',gallery).find(item=>item.dataset.assetId===asset.id);
      if(!card){card=document.createElement('article');card.className='mg-reference-card';card.dataset.assetId=asset.id;card.innerHTML='<div class="mg-reference-preview-slot"></div><div class="mg-reference-body"><div class="mg-reference-title-slot"></div><div class="mg-reference-copy"></div></div>'}
      patchHTML('.mg-reference-preview-slot',referencePreviewHTML(asset),card);
      if(state.reference.renaming?.id!==asset.id||!$('.mg-rename-form',card))patchHTML('.mg-reference-title-slot',referenceTitleHTML(asset),card);
      patchHTML('.mg-reference-copy',referenceCopyHTML(asset),card);
      if(gallery.children[index]!==card)gallery.insertBefore(card,gallery.children[index]||null);
    });
  }
  function setDateOpen(section,open,kind){
    const current=state[kind],button=$('.mg-date-trigger',section),panel=$('.mg-album-reveal',section);
    if(open)current.openDates.add(section.dataset.date);else current.openDates.delete(section.dataset.date);
    section.classList.toggle('is-open',open);
    button.setAttribute('aria-expanded',String(open));
    $('.mg-date-toggle-label',button).textContent=open?'收起':'展开';
    panel.inert=!open;panel.setAttribute('aria-hidden',String(!open));
  }
  function rootFor(kind){
    const root=$('.mg-root');
    return root?.dataset.kind===kind?root:null;
  }
  function render(kind){
    if(!state[kind])return;
    let root=rootFor(kind);
    if(!root){
      $('#app').innerHTML=`<section class="mg-root drop-area" data-kind="${kind}"><div class="mg-page-heading"><div><h1>${titleFor(kind)}</h1><p class="mg-gallery-count"></p></div><div class="mg-heading-actions">${uploadButton(kind,true)}</div></div>${kind==='analysis'?filtersHTML():referenceFiltersHTML()}<div class="mg-gallery"></div><div class="mg-detail" hidden></div></section>`;
      root=rootFor(kind);
      ctx.bindDrop?.();
    }
    refreshGallery(kind);
    const asset=assetById(state[kind].detailId),hasDetail=asset&&assetsFor(kind).some(item=>item.id===asset.id);
    root.classList.toggle('mg-detail-open',!!hasDetail);
    if(hasDetail){
      $('.mg-gallery',root).hidden=true;
      const detail=$('.mg-detail',root);detail.hidden=false;
      if(detail.dataset.assetId!==asset.id)mountDetail(kind,asset);else refreshDetail(kind,asset);
    }else{
      state[kind].detailId=null;
      $('.mg-gallery',root).hidden=false;$('.mg-detail',root).hidden=true;
    }
    syncSelection();
    ctx.onContentChange?.();
  }
  function refreshGallery(kind){
    const root=rootFor(kind);if(!root)return;
    if(kind==='reference')return refreshReferenceCards(root);
    refreshCalendar(root);
  }
  const shanghaiDay=()=>new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date());
  function refreshCalendar(root){
    const current=state.analysis,groups=grouped('analysis'),byDay=new Map(groups.map(group=>[group.key,group]));
    const gallery=$('.mg-gallery',root);gallery.classList.add('mg-calendar');
    $('.mg-gallery-count',root).textContent=`${assetsFor('analysis').length} 份素材`;
    current.month ||= shanghaiDay().slice(0,7);
    if(!current.loading&&Date.now()-current.loadedAt>15000){
      current.loading=true;current.loadedAt=Date.now();
      ctx.api('/material-calendar').then(data=>{current.summaries=new Map(data.days.map(row=>[row.day,row]));if(root.isConnected)refreshCalendar(root)})
        .catch(()=>{}).finally(()=>{current.loading=false});
    }
    const [year,month]=current.month.split('-').map(Number),first=new Date(Date.UTC(year,month-1,1)),offset=(first.getUTCDay()+6)%7;
    const count=new Date(Date.UTC(year,month,0)).getUTCDate(),cells=Math.ceil((offset+count)/7)*7;
    let body='';
    for(let index=0;index<cells;index++){
      const dayNumber=index-offset+1,valid=dayNumber>0&&dayNumber<=count;
      const key=valid?`${current.month}-${String(dayNumber).padStart(2,'0')}`:'',group=byDay.get(key),digest=current.summaries.get(key);
      const summary=digest?.result?.summary||digest?.previous_result?.summary;
      const counts=group?['video','image','audio'].map(kind=>{const n=group.assets.filter(a=>a.kind===kind).length;return n?`${n} ${kindNames[kind]}`:''}).filter(Boolean).join(' · '):'';
      const waiting=key>=shanghaiDay()?'明日汇总':digest?.status==='waiting_analysis'?'等待素材分析':['queued','running'].includes(digest?.status)?'汇总中':digest?.error?'汇总未完成':'待汇总';
      const stack=group?groupSummary(group).split('<span class="mg-date-caption">')[0]:'';
      body+=`<div data-calendar-key="${key||'outside-'+index}" class="mg-calendar-cell ${!valid?'is-outside':''} ${key===shanghaiDay()?'is-today':''} ${current.day===key?'is-selected':''}">${valid?`<button type="button" class="mg-calendar-day" data-mg-action="calendar-day" data-day="${key}" aria-expanded="${current.day===key}" aria-label="${key}，${group?group.assets.length+'份素材':'无素材'}" ${!group?'disabled':''}><span class="mg-calendar-date">${dayNumber}</span>${group?`${stack}<span class="mg-calendar-count">${esc(counts)}</span><span class="mg-calendar-summary">${esc(summary||waiting)}</span>${summary&&digest?.status==='outdated'?'<span class="mg-calendar-pending">有新分析待汇总</span>':''}`:''}</button>`:''}</div>`;
      if(index%7===6&&current.day){
        const selected=Number(current.day.slice(-2)),position=offset+selected-1;
        if(current.day.startsWith(current.month)&&position>=index-6&&position<=index&&byDay.has(current.day)){
          const selectedGroup=byDay.get(current.day),digest=current.summaries.get(current.day),summary=digest?.result||digest?.previous_result;
          body+=`<section data-calendar-key="expanded-${current.day}" class="mg-calendar-expanded" data-date="${current.day}"><div class="mg-calendar-expanded-heading"><div><h2>${Number(current.day.slice(5,7))}月${selected}日</h2><span>${selectedGroup.assets.length} 份素材</span></div><div>${digest?.job_id?`<div data-usage-scope="job" data-usage-job-id="${esc(digest.job_id)}" data-usage-label="本次汇总用量"></div>`:''}${current.day<shanghaiDay()?`<button type="button" class="button small quiet" data-mg-action="calendar-summary" data-day="${current.day}" ${['queued','running'].includes(digest?.status)?'disabled':''}>${['queued','running'].includes(digest?.status)?'汇总中':summary?'更新汇总':'汇总素材'}</button>`:''}<button type="button" class="mg-calendar-close" data-mg-action="calendar-close" aria-label="收起日期">×</button></div></div>${summary&&['queued','running','outdated'].includes(digest?.status)?'<p class="mg-calendar-pending">新汇总准备中，当前为上次结果</p>':''}${summary?`<p class="mg-calendar-day-summary">${esc(summary.summary)}</p><div class="mg-tags">${(summary.themes||[]).map(tag=>`<span>${esc(tag)}</span>`).join('')}</div>${summary.missing_count?`<p class="mg-calendar-pending">${summary.missing_count} 份尚无分析，未计入总结</p>`:''}`:''}${digest?.error?`<p class="mg-card-error">${esc(digest.error)}</p>`:''}<div class="mg-album">${selectedGroup.assets.map(asset=>`<article class="mg-card ${S.selected.has(asset.id)?'selected':''}" data-asset-id="${esc(asset.id)}">${cardContent(asset,'analysis')}</article>`).join('')}</div></section>`;
        }
      }
    }
    const html=`<div class="mg-calendar-heading"><h2>${year}<span>年</span> ${month}<span>月</span></h2><div><button type="button" data-mg-action="calendar-prev" aria-label="上个月">‹</button><button type="button" data-mg-action="calendar-today">本月</button><button type="button" data-mg-action="calendar-next" aria-label="下个月">›</button></div></div><div class="mg-calendar-scroll"><div class="mg-calendar-grid">${['一','二','三','四','五','六','日'].map(day=>`<div data-calendar-key="weekday-${day}" class="mg-calendar-weekday">${day}</div>`).join('')}${body}</div></div>`;
    if(gallery._mgMarkup!==html){
      if(gallery.dataset.month!==current.month){gallery.innerHTML=html;gallery.dataset.month=current.month}
      else patchCalendar(gallery,html);
      gallery._mgMarkup=html;ctx.onContentChange?.();
    }
  }
  function patchCalendar(gallery,html){
    const template=document.createElement('template');template.innerHTML=html;
    const grid=$('.mg-calendar-grid',gallery),incoming=$('.mg-calendar-grid',template.content);
    const existing=new Map([...grid.children].map(node=>[node.dataset.calendarKey,node]));
    const wanted=new Set([...incoming.children].map(node=>node.dataset.calendarKey));
    let cursor=grid.firstElementChild;
    for(const fresh of incoming.children){
      const key=fresh.dataset.calendarKey;let node=existing.get(key);existing.delete(key);
      if(!node){node=fresh.cloneNode(true)}
      else {
        if(node._exitAnimation){node._exitAnimation.cancel();node._exitAnimation=null;node.inert=false}
        node.className=fresh.className;
        const button=$('.mg-calendar-day',node),next=$('.mg-calendar-day',fresh);
        if(button&&next){
          for(const attr of ['aria-expanded','aria-label','disabled'])next.hasAttribute(attr)?button.setAttribute(attr,next.getAttribute(attr)):button.removeAttribute(attr);
          if(button.innerHTML!==next.innerHTML)button.innerHTML=next.innerHTML;
        }else if(node.innerHTML!==fresh.innerHTML)node.innerHTML=fresh.innerHTML;
      }
      while(cursor&&!wanted.has(cursor.dataset.calendarKey))cursor=cursor.nextElementSibling;
      if(node!==cursor)grid.insertBefore(node,cursor);
      cursor=node.nextElementSibling;
    }
    for(const node of existing.values()){
      if(node.classList.contains('mg-calendar-expanded')&&!matchMedia('(prefers-reduced-motion: reduce)').matches){
        if(node._exitAnimation)continue;
        node.inert=true;
        const animation=node.animate([{opacity:1},{opacity:0}],{duration:140,easing:'ease-out'});
        node._exitAnimation=animation;
        animation.finished.then(()=>{if(node._exitAnimation===animation)node.remove()}).catch(()=>{});
      }else node.remove();
    }
  }
  function analyzeButton(asset,kind){
    if(asset.kind==='audio')return '';
    const value=status(asset,kind);
    return `<button type="button" class="button small" data-mg-action="analyze" data-kind="${kind}" data-id="${esc(asset.id)}" ${['queued','running'].includes(value.value)?'disabled':''}>${['queued','running'].includes(value.value)?value.label:asset.analysis?'重新分析':'开始分析'}</button>`;
  }
  function detailHeading(asset,kind){
    const animation=kind==='analysis'&&asset.animation_project_id?new URLSearchParams({animation_id:asset.animation_project_id,...(asset.animation_revision_id?{revision_id:asset.animation_revision_id}:{})}):null;
    return `<div class="mg-detail-heading"><div><h2>${esc(asset.name)}</h2><p>${esc(mediaInfo(asset))}</p></div><div class="mg-detail-status">${badge(asset,kind)}<div data-usage-scope="asset" data-usage-asset-id="${esc(asset.id)}"></div>${animation?`<a class="button small" href="${esc(`#generate/animation?${animation}`)}">编辑动画</a>`:''}${analyzeButton(asset,kind)}</div></div>${asset.analysis_error?`<p class="mg-error">${esc(asset.analysis_error)}</p>`:''}`;
  }
  function summaryHTML(asset){
    const analysis=asset.analysis;
    if(!analysis)return '';
    const semantic=semanticFor(asset);
    if(semantic){
      const chapter=chapterFor(asset);
      return chapter?`<div class="mg-semantic-summary"><h3>${esc(chapter.label)}</h3>${chapter.summary?`<p>${esc(chapter.summary)}</p>`:''}${chapter.purpose?`<p class="mg-chapter-purpose">${esc(chapter.purpose)}</p>`:''}</div>`:'';
    }
    return `<div class="mg-summary">${analysis.summary?`<p>${esc(analysis.summary)}</p>`:''}<div class="mg-tags">${(analysis.tags||[]).map(tag=>`<span>${esc(tag)}</span>`).join('')}</div></div>`;
  }
  function timecode(seconds){
    const value=Number(seconds),tenths=Math.round(Math.max(0,Number.isFinite(value)?value:0)*10);
    const minutes=Math.floor(tenths/600),wholeSeconds=Math.floor(tenths%600/10);
    return `${String(minutes).padStart(2,'0')}:${String(wholeSeconds).padStart(2,'0')}.${tenths%10}`;
  }
  function timeRangeHTML(start,end){
    return `<span class="mg-time-start">${timecode(start)}</span><span class="mg-time-separator" aria-hidden="true">–</span><span class="mg-time-end">${timecode(end)}</span>`;
  }
  function contentFor(asset){
    return semanticFor(asset)?.units||asset.analysis?.content_segments||asset.analysis?.segments||[];
  }
  function speechEvidenceHTML(segment){
    const evidence=segment.speech_evidence||[],status=segment.speech_evidence_status||(segment.speech_ids?(evidence.length?'aligned':'no_speech'):null);
    if(!status)return '';
    const label=evidence.length?(status==='partial'?'对应语音 · 部分待核对':`对应语音 · ${evidence.length} 段`):status==='no_speech'?'此段无可靠对白':'语音对照暂不可用';
    if(!evidence.length)return `<p class="mg-speech-empty">${label}</p>`;
    return `<details class="mg-speech-evidence"><summary>${label}</summary><ol>${evidence.map(item=>`<li><button type="button" class="mg-time" data-mg-action="seek" data-time="${Number(item.start)||0}" aria-label="播放语音 ${timecode(item.start)} 至 ${timecode(item.end)}">${timeRangeHTML(item.start,item.end)}</button><p>${esc(item.text||'')}</p></li>`).join('')}</ol></details>`;
  }
  function contentEvidenceHTML(segment){
    const overlap=segment.overlaps||[];
    const intervals=overlap.length?`<div class="mg-content-overlap">${overlap.map(item=>`<button type="button" data-mg-action="seek" data-time="${Number(item.start)||0}" title="${timecode(item.start)}–${timecode(item.end)}">与第 ${Number(item.segment_index)+1} 段时间重叠</button>`).join('')}</div>`:'';
    return `${speechEvidenceHTML(segment)}${intervals}`;
  }
  function semanticUnitsHTML(asset){
    const semantic=semanticFor(asset),selected=chapterFor(asset),units=unitsFor(asset),allShots=asset.analysis?.shots||[];
    if(!semantic)return '';
    const unitHTML=unit=>{
      const ids=new Set(unit.shot_ids||[]),shots=allShots.filter(shot=>ids.has(shot.id));
      return `<article class="mg-segment mg-semantic-unit" data-unit-id="${esc(unit.id)}" data-start="${Number(unit.start)||0}" data-end="${Number(unit.end)||0}"><div class="mg-unit-heading"><h4>${esc(unit.label)}</h4><button type="button" class="mg-time" data-mg-action="seek" data-time="${Number(unit.start)||0}" aria-label="播放 ${esc(unit.label)}，${timecode(unit.start)} 至 ${timecode(unit.end)}">${timeRangeHTML(unit.start,unit.end)}</button></div><div>${unit.summary?`<p>${esc(unit.summary)}</p>`:''}${speechEvidenceHTML(unit)}${shots.length?`<div class="mg-unit-shot-heading"><span>对应镜头</span><span>${shots.length} 个</span></div><div class="mg-unit-shots">${shots.map(shot=>`<button type="button" data-mg-action="shot-seek" data-time="${Number(shot.start)||0}" data-start="${Number(shot.start)||0}" data-end="${Number(shot.end)||0}" aria-label="镜头 ${allShots.indexOf(shot)+1}，${timecode(shot.start)} 至 ${timecode(shot.end)}">${shot.thumbnail_url?`<img src="${esc(shot.thumbnail_url)}" alt="" loading="lazy">`:'<span class="mg-shot-placeholder" aria-hidden="true">▧</span>'}<span>镜头 ${allShots.indexOf(shot)+1}</span></button>`).join('')}</div>`:''}${unit.visual_support?`<p class="mg-visual-support">${esc(unit.visual_support)}</p>`:''}</div></article>`;
    };
    const chapters=selected?[selected]:semantic.chapters;
    return `<div class="mg-block-title"><h3>内容段</h3><span>${selected?`${units.length} / ${semantic.units.length}`:units.length} 段</span></div><div class="mg-semantic-units">${chapters.map(chapter=>{const ids=new Set(chapter.unit_ids||[]),rows=units.filter(unit=>ids.has(unit.id));return `<section class="mg-chapter-units" data-chapter-id="${esc(chapter.id)}">${!selected?`<button type="button" class="mg-chapter-group" data-mg-action="presentation-seek" data-kind="reference" data-id="${esc(asset.id)}" data-node-id="${esc(chapter.id)}" data-time="${Number(chapter.start)||0}"><strong>${esc(chapter.label)}</strong><span>${rows.length} 段</span></button>`:''}${rows.map(unitHTML).join('')}</section>`}).join('')}</div>`;
  }
  function segmentsHTML(asset,kind){
    if(kind==='reference'&&semanticFor(asset))return semanticUnitsHTML(asset);
    const segments=kind==='reference'?contentFor(asset):asset.analysis?.segments||[];
    if(!segments.length)return '';
    const digits=Math.max(7,...segments.flatMap(segment=>[timecode(segment.start).length,timecode(segment.end).length]));
    return `<div class="mg-block-title"><h3>${kind==='reference'?'内容段':'可用片段'}</h3><span>${segments.length} 段</span></div><div class="mg-segments" style="--mg-time-endpoint-width:${digits}ch">${segments.map((segment,index)=>`<article class="mg-segment ${selectedSegment(asset.id,index)&&kind==='analysis'?'selected':''}" data-segment-index="${index}" data-start="${Number(segment.start)||0}" data-end="${Number(segment.end)||0}"><button type="button" class="mg-time" data-mg-action="seek" data-time="${Number(segment.start)||0}" aria-label="${timecode(segment.start)} 至 ${timecode(segment.end)}">${timeRangeHTML(segment.start,segment.end)}</button><div>${kind==='reference'?'<span class="mg-interpretation-label">内容解读</span>':''}<p>${esc(segment.visual_description||segment.description||segment.content||segment.summary||segment.label||'')}</p><div class="mg-tags">${[kind!=='reference'||presentationFor(asset)?.kind==='narrative'?segment.visual_role||segment.role:null,segment.shot_type,segment.motion,...(segment.tags||[])].filter(Boolean).slice(0,6).map(tag=>`<span>${esc(tag)}</span>`).join('')}</div>${kind==='reference'?contentEvidenceHTML(segment):''}</div>${kind==='analysis'?`<button type="button" class="button small" data-mg-action="segment-select" data-id="${esc(asset.id)}" data-index="${index}" aria-pressed="${selectedSegment(asset.id,index)}">${selectedSegment(asset.id,index)?'已选中':'选入剪辑'}</button>`:''}</article>`).join('')}</div>`;
  }
  function evidenceHTML(asset){
    const frames=asset.analysis?.evidence_frames||[];
    if(!frames.length)return '';
    return `<div class="mg-block-title"><h3>分析画面</h3><span>${frames.length} 帧</span></div><div class="mg-evidence">${frames.map(frame=>`<button type="button" data-mg-action="seek" data-time="${Number(frame.time)||0}"><img src="${esc(frame.url)}" alt="${timecode(frame.time)}画面" loading="lazy"><span>${timecode(frame.time)}</span></button>`).join('')}</div>`;
  }
  function shotsHTML(asset){
    const allShots=asset.analysis?.shots||[],shots=shotsFor(asset),chapter=chapterFor(asset);
    if(!shots.length)return '<p class="mg-panel-empty">镜头待分析</p>';
    const duration=shot=>`${(Math.max(0,Number(shot.end)-Number(shot.start))).toFixed(2)} 秒`;
    const gradual=(asset.analysis?.shot_detection?.gradual_transitions||[]).filter(item=>!chapter||Number(item.end)>Number(chapter.start)&&Number(item.start)<Number(chapter.end));
    const review=gradual.length?`<details class="mg-transcript-review"><summary>${gradual.length} 处渐变待核对</summary><div class="mg-gradual-list">${gradual.map(item=>`<button type="button" class="mg-time" data-mg-action="seek" data-time="${Number(item.start)||0}">${timeRangeHTML(item.start,item.end)}</button>`).join('')}</div></details>`:'';
    return `<div class="mg-block-title"><h3>${chapter?'本章镜头':'镜头切分'}</h3><span>${chapter?`${shots.length} / ${allShots.length} 个镜头`:`自动识别 ${shots.length} 段`}</span></div>${review}<div class="mg-shot-list">${shots.map(shot=>{const index=allShots.indexOf(shot);return `<button type="button" class="mg-shot-row" data-mg-action="shot-seek" data-time="${Number(shot.start)||0}" data-start="${Number(shot.start)||0}" data-end="${Number(shot.end)||0}" aria-label="镜头 ${index+1}，${timecode(shot.start)} 至 ${timecode(shot.end)}">${shot.thumbnail_url?`<img src="${esc(shot.thumbnail_url)}" alt="" loading="lazy">`:'<span class="mg-shot-placeholder" aria-hidden="true">▧</span>'}<span class="mg-shot-copy"><span class="mg-shot-heading"><strong>镜头 ${index+1}</strong><small>${duration(shot)}</small></span><span class="mg-time">${timeRangeHTML(shot.start,shot.end)}</span>${shot.description?`<span class="mg-shot-description">${esc(shot.description)}</span>`:''}</span></button>`}).join('')}</div>`;
  }
  function referenceFramesHTML(asset){
    if(!isNarrative(asset))return '';
    const allShots=asset.analysis?.shots||[],shots=shotsFor(asset),chapter=chapterFor(asset);
    if(!shots.length)return chapter?'':evidenceHTML(asset);
    return `<div class="mg-block-title mg-shot-strip-heading"><h3>${chapter?esc(chapter.label):'镜头切分'}</h3><span>${chapter?`${shots.length} / ${allShots.length}`:shots.length} 个镜头</span></div><div class="mg-evidence mg-shot-strip">${shots.map(shot=>{const index=allShots.indexOf(shot);return `<button type="button" data-mg-action="shot-seek" data-time="${Number(shot.start)||0}" data-start="${Number(shot.start)||0}" data-end="${Number(shot.end)||0}" aria-label="镜头 ${index+1}，${timecode(shot.start)} 至 ${timecode(shot.end)}">${shot.thumbnail_url?`<img src="${esc(shot.thumbnail_url)}" alt="镜头 ${index+1}" loading="lazy">`:'<span class="mg-shot-placeholder" aria-hidden="true">▧</span>'}<span><b>${index+1}</b>${timecode(shot.start)}</span></button>`}).join('')}</div>`;
  }
  function transcriptHTML(asset){
    if(!ctx.transcriptPanel)return '';
    return ctx.transcriptPanel(asset).replaceAll('data-action="seek-asset"','data-mg-action="seek"');
  }
  const rhythmWindowSeconds=20;
  const rhythmNumber=value=>value!==null&&value!==undefined&&value!==''&&Number.isFinite(Number(value))?Number(value):null;
  function rhythmTimecode(seconds){
    const milliseconds=Math.round(Math.max(0,rhythmNumber(seconds)||0)*1000);
    return `${String(Math.floor(milliseconds/60000)).padStart(2,'0')}:${String(Math.floor(milliseconds%60000/1000)).padStart(2,'0')}.${String(milliseconds%1000).padStart(3,'0')}`;
  }
  function rhythmView(asset){
    const analysis=asset.analysis?.rhythm_analysis,evidence=analysis?.evidence||{};
    const duration=Math.max(0,rhythmNumber(evidence.duration)||rhythmNumber(asset.duration)||0);
    const pages=Math.max(1,Math.ceil(duration/rhythmWindowSeconds));
    const page=Math.max(0,Math.min(pages-1,state.reference.rhythmPages.get(asset.id)||0));
    const start=page*rhythmWindowSeconds,end=Math.min(duration,start+rhythmWindowSeconds);
    const validTime=value=>rhythmNumber(value)!==null&&Number(value)>=0&&Number(value)<=duration;
    const within=time=>validTime(time)&&Number(time)>=start&&(Number(time)<end||page===pages-1&&Number(time)===end);
    const onsets=(evidence.onsets||[]).filter(item=>within(item.time));
    const beats=(evidence.beat_candidates||[]).filter(item=>within(item.time));
    const rawMatches=(evidence.cut_matches||[]).filter(item=>validTime(item.cut_time));
    const modelMatches=(analysis?.matches||[]).filter(item=>validTime(item.cut_time));
    const matches=modelMatches.length?modelMatches:rawMatches.map(item=>({
      cut_time:item.cut_time,onset_time:item.onset?.time,beat_time:item.beat_candidate?.time,
      onset_offset_ms:item.onset?.offset_ms,beat_offset_ms:item.beat_candidate?.offset_ms,
      classification:item.beat_candidate?.within_tolerance?'beat_aligned':item.onset?.within_tolerance?'onset_aligned':'unaligned',
    }));
    // The visual cut track comes from measured cuts, even when the model discusses only a subset.
    const cuts=(rawMatches.length?rawMatches:matches).filter(item=>within(item.cut_time));
    return {analysis,evidence,duration,pages,page,start,end,onsets,beats,cuts,matches:matches.filter(item=>within(item.cut_time)).sort((a,b)=>a.cut_time-b.cut_time)};
  }
  function rhythmTimestamp(time,label,extra=''){
    if(rhythmNumber(time)===null||Number(time)<0)return '<span class="mg-rhythm-no-time">—</span>';
    return `<button type="button" class="mg-rhythm-time" data-mg-action="rhythm-seek" data-time="${Number(time)}" aria-label="${esc(label)} ${rhythmTimecode(time)}"${extra}>${rhythmTimecode(time)}</button>`;
  }
  function rhythmReadable(value){
    const labels={beat_candidates:'周期性拍点候选',beat_candidate:'周期性拍点候选',beat_aligned:'邻近周期性拍点候选',onset_aligned:'邻近起音候选',unaligned:'未见邻近节奏点',uncertain:'待核对',not_established:'未形成稳定规律',beat_driven:'周期性拍点邻近较多',accent_driven:'起音邻近较多',mixed:'两类邻近均有',editing_pattern:'时序关系',alignment_summary:'邻近统计',uniform_time_coverage_fraction:'均匀时间覆盖率',shifted_cut_baseline:'平移切点对照',within_tolerance:'容差内',confidence:'证据充分度',onsets:'起音候选',onset:'起音候选',beat:'周期性拍点候选',cut_id:'切点编号',cuts:'切点',cut:'切点',high:'高',medium:'中',low:'低'};
    return String(value||'').replace(/\bcut\s+(\d+(?:\s*[、,，–-]\s*\d+)*)/gi,(_,indexes)=>`切点 ${indexes.replace(/\d+/g,n=>String(Number(n)+1))}`)
      .replace(new RegExp(`\\b(${Object.keys(labels).sort((a,b)=>b.length-a.length).join('|')})\\b`,'gi'),token=>labels[token.toLowerCase()]);
  }
  function rhythmMatchHTML(match){
    const classifications={beat_aligned:'邻近周期性拍点',onset_aligned:'邻近起音候选',unaligned:'未见邻近节奏点',uncertain:'待核对'};
    const classification=Object.hasOwn(classifications,match.classification)?match.classification:'uncertain';
    const offset=value=>rhythmNumber(value)===null?'':`<small>${Number(value)===0?'同刻':`${Number(value)>0?'晚':'早'} ${Math.round(Math.abs(Number(value)))} ms`}</small>`;
    return `<li class="mg-rhythm-match" data-cut-time="${Number(match.cut_time)}"><div class="mg-rhythm-match-heading"><span>切点 ${rhythmTimestamp(match.cut_time,'定位切点')}</span><span class="mg-rhythm-classification ${classification}">${classifications[classification]}</span></div><div class="mg-rhythm-match-times"><div><span>起音候选</span>${rhythmTimestamp(match.onset_time,'定位起音候选')}${offset(match.onset_offset_ms)}</div><div><span>周期性拍点候选</span>${rhythmTimestamp(match.beat_time,'定位周期性拍点候选')}${offset(match.beat_offset_ms)}</div></div>${match.reason?`<details class="mg-rhythm-reason"><summary>判断依据</summary><p>${esc(rhythmReadable(match.reason))}</p></details>`:''}</li>`;
  }
  function rhythmHTML(asset){
    const view=rhythmView(asset),{analysis,evidence,start,end,pages,page,onsets,beats,cuts,matches}=view;
    if(!analysis)return asset.has_audio?'<div class="mg-rhythm-empty"><h3>节奏对照</h3><p>尚未分析节奏</p></div>':'';
    const heading='<div class="mg-rhythm-heading"><h3>节奏对照</h3></div>';
    if(evidence.status!=='analyzed'||end<=start)return `${heading}<p class="mg-panel-empty">${esc(rhythmReadable(analysis.summary)||'暂未获得节奏结果')}</p>`;
    const patterns={beat_driven:'周期性拍点邻近较多',accent_driven:'起音邻近较多',mixed:'两类邻近均有',not_established:'未形成稳定规律'};
    const tolerance=rhythmNumber(evidence.alignment_summary?.tolerance_seconds);
    const position=time=>((Number(time)-start)/(end-start)*100).toFixed(4);
    const marker=(item,type,label,time=item.time)=>{
      const height=type==='onset'?`--rhythm-strength:${Math.max(.12,Math.min(1,Number(item.strength)||.12))};`:'';
      return `<button type="button" class="mg-rhythm-mark mg-rhythm-${type}" data-mg-action="rhythm-seek" data-time="${Number(time)}" tabindex="-1" style="left:${position(time)}%;${height}" aria-label="${label} ${rhythmTimecode(time)}" title="${label} ${rhythmTimecode(time)}"><span></span></button>`;
    };
    const ticks=Array.from({length:5},(_,index)=>start+(end-start)*index/4);
    const pageButtons=pages>1?`<button type="button" data-mg-action="rhythm-page" data-page="${page-1}" aria-label="上一段节奏" ${page===0?'disabled':''}>‹</button><span>${page+1} / ${pages}</span><button type="button" data-mg-action="rhythm-page" data-page="${page+1}" aria-label="下一段节奏" ${page===pages-1?'disabled':''}>›</button>`:'';
    const pulseStatus=!(evidence.beat_candidates||[]).length?'<span class="mg-rhythm-pulse-status">周期性拍点：证据不足</span>':'';
    const totals=evidence.alignment_summary||{},rawMatches=evidence.cut_matches||[],totalCuts=rhythmNumber(totals.cut_count)??rawMatches.length;
    const beatCount=rhythmNumber(totals.beat_candidate?.matched_cuts)??rawMatches.filter(item=>item.beat_candidate?.within_tolerance).length,onsetCount=rhythmNumber(totals.onset?.matched_cuts)??rawMatches.filter(item=>item.onset?.within_tolerance).length;
    const metrics=`<div class="mg-rhythm-metrics"><div><strong>${totalCuts}</strong><span>切点</span></div><div><strong>${beatCount}<small> / ${totalCuts}</small></strong><span>邻近周期候选</span></div><div><strong>${onsetCount}<small> / ${totalCuts}</small></strong><span>邻近起音候选</span></div></div>`;
    const conclusion=analysis.summary?`<details class="mg-rhythm-conclusion"><summary>查看节奏判断</summary><p class="mg-rhythm-summary">${esc(rhythmReadable(analysis.summary))}</p></details>`:'';
    return `<div class="mg-rhythm-heading"><h3>节奏对照</h3>${patterns[analysis.editing_pattern]?`<span class="mg-rhythm-pattern">${patterns[analysis.editing_pattern]}</span>`:''}</div>${metrics}<div class="mg-rhythm-legend"><span><i class="is-onset"></i>起音候选</span><span><i class="is-beat"></i>周期性拍点候选</span><span><i class="is-cut"></i>切点</span>${pulseStatus}</div><div class="mg-rhythm-window"><span>${timecode(start)} – ${timecode(end)}</span><div>${pageButtons}<button type="button" class="mg-rhythm-locate" data-mg-action="rhythm-current">定位当前</button></div></div><div class="mg-rhythm-timeline" data-rhythm-start="${start}" data-rhythm-end="${end}" aria-label="${timecode(start)} 至 ${timecode(end)} 的声音候选与切点对照"><div class="mg-rhythm-track-labels" aria-hidden="true"><span>声音</span><span>切镜</span></div><div class="mg-rhythm-plot"><div class="mg-rhythm-audio-track">${onsets.map(item=>marker(item,'onset','起音候选')).join('')}${beats.map(item=>marker(item,'beat','周期性拍点候选')).join('')}${!onsets.length&&!beats.length?'<span class="mg-rhythm-track-empty">这一段未检出候选</span>':''}</div><div class="mg-rhythm-cut-track">${cuts.map(item=>marker(item,'cut','切点',item.cut_time)).join('')}${!cuts.length?'<span class="mg-rhythm-track-empty">这一段没有切点</span>':''}</div><span class="mg-rhythm-playhead" hidden aria-hidden="true"></span><div class="mg-rhythm-axis" aria-hidden="true">${ticks.map((time,index)=>`<span style="left:${index*25}%">${timecode(time)}</span>`).join('')}</div></div></div>${conclusion}<div class="mg-rhythm-list-heading"><h4>切点对照</h4>${tolerance!==null?`<span>邻近范围 ±${Math.round(tolerance*1000)} ms</span>`:''}</div>${matches.length?`<ol class="mg-rhythm-matches">${matches.map(rhythmMatchHTML).join('')}</ol>`:'<p class="mg-panel-empty">这一段暂无切点对照</p>'}`;
  }
  function updateRhythmPlayhead(root,time){
    const timeline=$('.mg-rhythm-timeline',root);if(!timeline)return;
    const start=Number(timeline.dataset.rhythmStart),end=Number(timeline.dataset.rhythmEnd),playhead=$('.mg-rhythm-playhead',timeline);
    playhead.hidden=time<start||time>end;
    if(!playhead.hidden)playhead.style.left=`${Math.max(0,Math.min(100,(time-start)/(end-start)*100))}%`;
    $$('.mg-rhythm-match',root).forEach(row=>row.classList.toggle('is-current',Math.abs(time-Number(row.dataset.cutTime))<=.15));
  }
  function setRhythmPage(page){
    const root=rootFor('reference'),asset=assetById(state.reference.detailId);if(!root||!asset)return;
    const view=rhythmView(asset);
    state.reference.rhythmPages.set(asset.id,Math.max(0,Math.min(view.pages-1,Number(page)||0)));
    patchHTML('.mg-detail-rhythm',rhythmHTML(asset),root);
    updateActiveNode();
  }
  function structureHTML(asset){
    const nodes=presentationNodes(asset),result=presentationFor(asset),semantic=semanticFor(asset);
    if(!nodes.length)return '';
    const kind=semantic?'narrative':result?.kind||'uncertain',label=semantic?'章节结构':result?.label||presentationKinds[kind],summary=semantic?.thesis||result?.summary;
    const limitations=[...new Set([...(semantic?.limitations||[]),...(result?.limitations||[])].filter(Boolean))];
    return `<div class="mg-block-title"><div class="mg-structure-heading"><h3>${esc(label)}</h3>${semantic?`<button type="button" class="mg-all-chapters" data-mg-action="chapter-reset" data-id="${esc(asset.id)}" aria-pressed="true">全部 ${semantic.chapters.length} 章</button>`:''}</div>${presentationButton(asset)}</div>${summary?`<p class="mg-presentation-summary">${esc(summary)}</p>`:''}${presentationTrackHTML(asset)}${limitations.length?`<details class="mg-presentation-limitations"><summary>待核对 ${limitations.length} 项</summary><ul>${limitations.map(item=>`<li>${esc(item)}</li>`).join('')}</ul></details>`:''}`;
  }
  function miniAssetsHTML(exclude){
    return S.assets.filter(asset=>asset.id!==exclude&&asset.kind!=='audio'&&!isReference(asset)).map(asset=>`<button type="button" class="${S.selected.has(asset.id)?'selected':''}" title="${esc(asset.name)}" aria-label="${S.selected.has(asset.id)?'取消选择':'选择'}${esc(asset.name)}" data-mg-action="select" data-id="${esc(asset.id)}" aria-pressed="${S.selected.has(asset.id)}">${thumb(asset)?`<img src="${esc(thumb(asset))}" alt="">`:'<span>图片</span>'}</button>`).join('');
  }
  function remixHTML(asset){
    const maximum=Math.min(600,Math.max(4,Math.round(asset.duration)||30));
    return `<form id="remix-form" class="mg-remix-form side-form"><h2>用这个结构剪新片</h2><label>新片讲什么<textarea name="brief" required placeholder="写下这条新片的主题与内容"></textarea></label><div class="field-pair"><label>画面比例<select name="ratio"><option>9:16</option><option>16:9</option><option>1:1</option></select></label><label>目标秒数<input type="number" name="duration" min="4" max="600" value="${maximum}"></label></div><label>候选成片<select name="candidate_count"><option value="3">3 条</option><option value="4">4 条</option><option value="5">5 条</option></select></label><div class="mg-block-title"><h3>选择素材</h3><button type="button" class="button small quiet" data-action="upload" data-purpose="remix">添加</button></div><div class="mini-assets">${miniAssetsHTML(asset.id)}</div><p class="mg-selection-count"></p><button class="button primary full" type="submit">生成候选成片</button></form>`;
  }
  function mountDetail(kind,asset){
    const root=rootFor(kind),detail=$('.mg-detail',root);
    detail.dataset.assetId=asset.id;
    const reference=kind==='reference';
    detail.innerHTML=`<div class="mg-back-row"><button type="button" class="mg-back" data-mg-action="back" data-kind="${kind}"><span aria-hidden="true">‹</span> ${reference?'返回样片':'返回相册'}</button></div><div class="mg-detail-heading-slot">${detailHeading(asset,kind)}</div>${reference?`<div class="mg-reference-layout"><div class="mg-reference-overview"><div class="mg-structure">${structureHTML(asset)}</div><div class="mg-detail-evidence">${referenceFramesHTML(asset)}</div></div><div class="mg-reference-workbench"><div class="mg-reference-viewer"><div class="mg-block-title mg-original-heading"><h3>原片</h3><span>${fmt(asset.duration)}</span></div><div class="mg-player" data-player-asset="${esc(asset.id)}">${mediaTag(asset,'reference-player')}</div></div><aside class="mg-reference-inspector"><div class="mg-inspector-tabs" role="tablist" aria-label="样片内容"><button type="button" id="mg-tab-content" role="tab" aria-selected="true" aria-controls="mg-panel-content" tabindex="0" data-mg-action="inspector-tab" data-tab="content">内容段</button><button type="button" id="mg-tab-shots" role="tab" aria-selected="false" aria-controls="mg-panel-shots" tabindex="-1" data-mg-action="inspector-tab" data-tab="shots">镜头</button><button type="button" id="mg-tab-audio" role="tab" aria-selected="false" aria-controls="mg-panel-audio" tabindex="-1" data-mg-action="inspector-tab" data-tab="audio">声音</button><button type="button" id="mg-tab-remix" role="tab" aria-selected="false" aria-controls="mg-panel-remix" tabindex="-1" data-mg-action="inspector-tab" data-tab="remix">剪新片</button></div><div class="mg-inspector-body"><section class="mg-inspector-panel" id="mg-panel-content" role="tabpanel" aria-labelledby="mg-tab-content"><div class="mg-detail-presentation">${presentationLinkHTML(asset)}</div><div class="mg-detail-summary">${summaryHTML(asset)}</div><div class="mg-detail-segments">${segmentsHTML(asset,kind)||'<p class="mg-panel-empty">暂无内容分析</p>'}</div></section><section class="mg-inspector-panel" id="mg-panel-shots" role="tabpanel" aria-labelledby="mg-tab-shots" hidden><div class="mg-detail-shots">${shotsHTML(asset)}</div></section><section class="mg-inspector-panel" id="mg-panel-audio" role="tabpanel" aria-labelledby="mg-tab-audio" hidden><div class="mg-detail-rhythm">${rhythmHTML(asset)}</div><div class="mg-detail-transcript">${transcriptHTML(asset)||'<p class="mg-panel-empty">原片没有音轨</p>'}</div></section><section class="mg-inspector-panel mg-remix" id="mg-panel-remix" role="tabpanel" aria-labelledby="mg-tab-remix" hidden>${remixHTML(asset)}</section></div></aside></div></div>`:`<div class="mg-analysis-layout"><div class="mg-player ${asset.kind==='audio'?'mg-audio-player':''}" data-player-asset="${esc(asset.id)}">${asset.kind==='audio'?'<span aria-hidden="true" class="mg-audio-symbol">♫</span>':''}${mediaTag(asset)}</div><div class="mg-analysis-copy"><div class="mg-detail-summary">${summaryHTML(asset)}</div><div class="mg-whole-select"></div></div></div><div class="mg-detail-segments">${segmentsHTML(asset,kind)}</div><div class="mg-detail-evidence">${evidenceHTML(asset)}</div><div class="mg-detail-transcript">${transcriptHTML(asset)}</div>`}`;
    const markup={
      '.mg-detail-heading-slot':detailHeading(asset,kind),
      '.mg-detail-presentation':presentationLinkHTML(asset),
      '.mg-detail-summary':summaryHTML(asset),
      '.mg-detail-segments':segmentsHTML(asset,kind),
      '.mg-detail-transcript':transcriptHTML(asset),
      '.mg-detail-rhythm':rhythmHTML(asset),
      '.mg-structure':structureHTML(asset),
      '.mg-detail-evidence':reference?referenceFramesHTML(asset):evidenceHTML(asset),
      '.mg-detail-shots':shotsHTML(asset),
    };
    for(const [selector,html] of Object.entries(markup)){
      const target=$(selector,detail);if(target)target._mgMarkup=html;
    }
    bindPlayer(kind);
    if(reference)setInspectorTab('content');
    syncSelection();
    ctx.restoreDrafts?.();
  }
  function patchHTML(selector,html,root){
    const target=$(selector,root);
    if(target&&target._mgMarkup!==html){
      const scroll=target.scrollTop,scrollLeft=target.scrollLeft;
      const usageHosts=$$('[data-usage-scope]',target);
      target.innerHTML=html;
      for(const previous of usageHosts){
        const replacement=$$('[data-usage-scope]',target).find(slot=>slot.dataset.usageScope===previous.dataset.usageScope&&slot.dataset.usageAssetId===previous.dataset.usageAssetId);
        if(replacement)replacement.replaceWith(previous);
      }
      target._mgMarkup=html;
      target.scrollTop=scroll;target.scrollLeft=scrollLeft;
    }
  }
  function refreshDetail(kind,asset){
    const root=rootFor(kind),detail=$('.mg-detail',root);
    if(!detail||detail.dataset.assetId!==asset.id)return;
    patchHTML('.mg-detail-heading-slot',detailHeading(asset,kind),detail);
    patchHTML('.mg-detail-summary',summaryHTML(asset),detail);
    patchHTML('.mg-detail-segments',segmentsHTML(asset,kind),detail);
    patchHTML('.mg-detail-transcript',transcriptHTML(asset),detail);
    if(kind==='reference'){patchHTML('.mg-detail-presentation',presentationLinkHTML(asset),detail);patchHTML('.mg-structure',structureHTML(asset),detail);patchHTML('.mg-detail-shots',shotsHTML(asset),detail);patchHTML('.mg-detail-rhythm',rhythmHTML(asset),detail)}
    patchHTML('.mg-detail-evidence',kind==='reference'?referenceFramesHTML(asset):evidenceHTML(asset),detail);
    syncSelection();
    updateActiveNode();
    ctx.onContentChange?.();
  }
  function syncSelection(){
    const root=$('.mg-root');if(!root)return;
    const kind=root.dataset.kind,asset=assetById(state[kind].detailId);
    $$('.mg-card',root).forEach(card=>{
      const selected=S.selected.has(card.dataset.assetId);
      card.classList.toggle('selected',kind==='analysis'&&selected);
      const button=$('.mg-card-select',card);
      if(button){button.classList.toggle('selected',selected);button.setAttribute('aria-pressed',String(selected));button.textContent=selected?'✓':'＋';const label=selected?'取消选择':'选入剪辑';button.title=label;button.setAttribute('aria-label',`${label} ${assetById(card.dataset.assetId)?.name||''}`)}
    });
    const selectedCount=$('.mg-selected-count',root);
    if(selectedCount)selectedCount.textContent=S.selected.size?`已选 ${S.selected.size} 份`:'';
    if(!asset)return;
    const whole=$('.mg-whole-select',root);
    if(whole){
      const selected=S.selected.has(asset.id);
      whole.innerHTML=`<button type="button" class="button ${selected?'mg-selected-button':''}" data-mg-action="select" data-id="${esc(asset.id)}" aria-pressed="${selected}">${selected?'取消选择':'选择整份素材'}</button>`;
    }
    $$('.mg-segment[data-segment-index]',root).forEach(row=>{
      const selected=selectedSegment(asset.id,Number(row.dataset.segmentIndex));
      row.classList.toggle('selected',kind==='analysis'&&selected);
      const button=$('[data-mg-action=segment-select]',row);
      if(button){button.setAttribute('aria-pressed',String(selected));button.textContent=selected?'已选中':'选入剪辑'}
    });
    const mini=$('#remix-form .mini-assets',root);
    if(mini){
      const html=miniAssetsHTML(asset.id);
      if(mini.innerHTML!==html)mini.innerHTML=html;
      $('.mg-selection-count',root).textContent=`已选 ${selectedAssets().filter(item=>item.id!==asset.id).length} 份素材`;
    }
  }
  function bindPlayer(kind){
    const root=rootFor(kind),player=$('.mg-player video,.mg-player audio',root);
    if(!player)return;
    player.addEventListener('timeupdate',updateActiveNode);
    player.addEventListener('seeked',updateActiveNode);
    player.addEventListener('loadedmetadata',updateActiveNode);
  }
  function updateActiveNode(){
    const root=$('.mg-root[data-kind=reference]'),player=root&&$('.mg-player video',root);
    if(!player)return;
    const asset=assetById(state.reference.detailId),chapter=asset&&chapterFor(asset);
    $$('.mg-structure-node[aria-pressed],.mg-presentation-node[aria-pressed]',root).forEach(node=>node.setAttribute('aria-pressed',String(node.dataset.nodeId===chapter?.id)));
    $('.mg-all-chapters',root)?.setAttribute('aria-pressed',String(!chapter));
    const nodes=$$('.mg-structure-node,.mg-presentation-node',root);
    nodes.forEach((node,index)=>{
      const start=Number(node.dataset.time),end=Number(node.dataset.end);
      const active=player.currentTime>=start&&(player.currentTime<end||index===nodes.length-1&&player.currentTime===end);
      node.classList.toggle('active',active);
      if(active)node.setAttribute('aria-current','true');else node.removeAttribute('aria-current');
    });
    $$('.mg-segment,.mg-shot-row',root).forEach(row=>row.classList.toggle('playing',player.currentTime>=Number(row.dataset.start)&&player.currentTime<Number(row.dataset.end)));
    const evidence=$$('.mg-evidence button',root);
    evidence.forEach((button,index)=>button.classList.toggle('active',player.currentTime>=Number(button.dataset.time)&&(index===evidence.length-1||player.currentTime<Number(evidence[index+1].dataset.time))));
    updateRhythmPlayhead(root,player.currentTime);
  }
  function setInspectorTab(tab,focus=false){
    const root=rootFor('reference');if(!root)return;
    $$('.mg-inspector-tabs button',root).forEach(button=>{const active=button.dataset.tab===tab;button.setAttribute('aria-selected',String(active));button.tabIndex=active?0:-1;if(active&&focus)button.focus({preventScroll:true})});
    $$('.mg-inspector-panel',root).forEach(panel=>panel.hidden=panel.id!==`mg-panel-${tab}`);
    if(tab==='audio')updateActiveNode();
  }
  function seek(time){
    const root=$('.mg-root'),player=root&&$('.mg-player video,.mg-player audio',root);
    if(!player)return;
    const set=()=>{player.currentTime=Math.max(0,Math.min(Number(time)||0,Number.isFinite(player.duration)?Math.max(0,player.duration-.01):Number(time)||0));updateActiveNode()};
    if(player.readyState>=1)set();else player.addEventListener('loadedmetadata',set,{once:true});
  }
  function open(kind,id){
    const asset=assetById(id);
    if(!asset||!state[kind]||!assetsFor(kind).some(item=>item.id===id))return;
    const current=state[kind],root=rootFor(kind);
    if(!current.detailId&&root){
      current.scroll=window.scrollY;current.anchorId=id;
      const card=$$('.mg-card,.mg-reference-card',root).find(item=>item.dataset.assetId===id);
      current.anchorOffset=card?.getBoundingClientRect().top??null;
    }
    ctx.captureDrafts?.();
    current.detailId=id;current.restore++;
    if(kind==='reference')S.referenceId=id;else S.analysisId=id;
    if(routeIs(kind)){
      render(kind);
      window.scrollTo({top:0,behavior:'instant'});
      $('.mg-back',rootFor(kind))?.focus({preventScroll:true});
    }
  }
  function showGallery(kind){
    const current=state[kind];if(!current)return;
    ctx.captureDrafts?.();
    const root=rootFor(kind);
    if(root)$$('.mg-detail video,.mg-detail audio',root).forEach(player=>player.pause());
    current.detailId=null;
    const token=++current.restore;
    if(!routeIs(kind))return;
    render(kind);
    requestAnimationFrame(()=>{
      if(current.restore!==token||current.detailId||!routeIs(kind))return;
      const card=$$('.mg-card,.mg-reference-card',rootFor(kind)).find(item=>item.dataset.assetId===current.anchorId);
      const top=card&&current.anchorOffset!==null?window.scrollY+card.getBoundingClientRect().top-current.anchorOffset:current.scroll;
      window.scrollTo({top:Math.max(0,top),behavior:'instant'});
      $('[data-mg-action=open]',card||rootFor(kind))?.focus({preventScroll:true});
    });
  }
  function refresh(){
    const kind=S.route;
    if(!state[kind])return;
    render(kind);
  }
  function renameCard(assetId){
    const asset=assetById(assetId);if(!asset||state.reference.renaming?.saving)return;
    state.reference.renaming={id:assetId,value:asset.name,original:asset.name,saving:false};
    refreshGallery('reference');
    const input=$(`.mg-rename-form[data-asset-id="${CSS.escape(assetId)}"] input`,rootFor('reference'));
    input?.focus({preventScroll:true});input?.select();
  }
  function cancelRename(){
    const edit=state.reference.renaming;if(!edit||edit.saving)return;
    state.reference.renaming=null;refreshGallery('reference');
    const card=$$('.mg-reference-card',rootFor('reference')).find(item=>item.dataset.assetId===edit.id);
    $('[data-mg-action=rename]',card||rootFor('reference'))?.focus({preventScroll:true});
  }
  async function saveRename(form){
    const edit=state.reference.renaming;if(!edit||edit.saving||edit.id!==form.dataset.assetId)return;
    edit.value=form.elements.name.value;edit.saving=true;
    $$('input,button',form).forEach(element=>element.disabled=true);
    const submit=$('[type=submit]',form);submit.textContent='保存中';
    try{
      const asset=await ctx.api(`/assets/${encodeURIComponent(edit.id)}/name`,{name:edit.value,expected_name:edit.original},'PATCH');
      const index=S.assets.findIndex(item=>item.id===asset.id);
      if(index>=0)S.assets[index]=asset;
      if(state.reference.renaming===edit)state.reference.renaming=null;
      refresh();
      ctx.toast('名称已保存');
    }catch(error){
      edit.saving=false;ctx.toast(error.message||'名称未保存',true);
      if(form.isConnected){$$('input,button',form).forEach(element=>element.disabled=false);submit.textContent='保存';form.elements.name.focus({preventScroll:true})}
    }
  }
  document.addEventListener('click',event=>{
    const button=event.target.closest('[data-mg-action]');if(!button)return;
    const action=button.dataset.mgAction,id=button.dataset.id,kind=button.dataset.kind||$('.mg-root')?.dataset.kind;
    if(action?.startsWith('calendar-')){
      const current=state.analysis;
      if(action==='calendar-day')current.day=current.day===button.dataset.day?null:button.dataset.day;
      if(action==='calendar-close')current.day=null;
      if(action==='calendar-today'){current.month=shanghaiDay().slice(0,7);current.day=null}
      if(action==='calendar-prev'||action==='calendar-next'){
        const [year,month]=current.month.split('-').map(Number),date=new Date(Date.UTC(year,month-1+(action==='calendar-next'?1:-1),1));
        current.month=date.toISOString().slice(0,7);current.day=null;
      }
      if(action==='calendar-summary'){
        button.disabled=true;
        ctx.api(`/material-calendar/${button.dataset.day}/summarize`,{}).then(receipt=>{
          if(receipt.job_id)ctx.track(receipt,()=>{current.loadedAt=0;refresh();ctx.toast('当日素材已汇总')});
          else ctx.toast('等待单份素材分析完成');
          current.loadedAt=0;refresh();
        }).catch(error=>{ctx.toast(error.message,true);button.disabled=false});return;
      }
      refreshGallery('analysis');syncSelection();return;
    }
    if(action==='open'){open(kind,id);return}
    if(action==='open-seek'){const asset=assetById(id);if(semanticFor(asset))state.reference.chapters.set(id,button.dataset.nodeId);open('reference',id);seek(Number(button.dataset.time));setInspectorTab(isNarrative(asset)?'content':'shots');return}
    if(action==='chapter-reset'){state.reference.chapters.delete(id);refreshDetail('reference',assetById(id));return}
    if(action==='presentation'){
      const asset=assetById(id);if(!asset?.analysis||presentationBusy(asset))return;
      state.reference.presentationPending.add(id);refresh();
      Promise.resolve().then(async()=>{
        const receipt=await ctx.api(`/references/${encodeURIComponent(id)}/${refreshesSemantics(asset)?'semantics':'presentation'}`,{base_analysis_id:asset.analysis.analysis_id});
        await ctx.track(receipt,()=>ctx.toast('剪辑方式分析完成'));
        ctx.toast('正在分析剪辑方式');
      }).catch(error=>ctx.toast(error.message||'剪辑方式分析未完成',true)).finally(()=>{state.reference.presentationPending.delete(id);refresh()});return;
    }
    if(action==='presentation-link'){
      const asset=assetById(id);if(!asset)return;
      const link=presentationFor(asset)?.links?.find(item=>item.from===button.dataset.from&&item.to===button.dataset.to);
      if(!hasLinkEvidence(link))return;
      state.reference.presentationLinks.set(id,`${button.dataset.from}|${button.dataset.to}`);
      open('reference',id);patchHTML('.mg-detail-presentation',presentationLinkHTML(asset),rootFor('reference'));setInspectorTab('content');$('#mg-panel-content',rootFor('reference'))?.scrollTo({top:0,behavior:'instant'});return;
    }
    if(action==='presentation-link-close'){state.reference.presentationLinks.delete(id);patchHTML('.mg-detail-presentation','',rootFor('reference'));return}
    if(action==='presentation-evidence'){seek(Number(button.dataset.time));return}
    if(action==='presentation-seek'){const asset=assetById(state.reference.detailId);if(semanticFor(asset)){state.reference.chapters.set(asset.id,button.dataset.nodeId);refreshDetail('reference',asset);$('#mg-panel-content',rootFor('reference'))?.scrollTo({top:0,behavior:'instant'});}seek(Number(button.dataset.time));setInspectorTab(isNarrative(asset)?'content':'shots');return}
    if(action==='rename'){renameCard(id);return}
    if(action==='rename-cancel'){cancelRename();return}
    if(action==='reference-reset-search'){state.reference.search='';$('#mg-reference-search',rootFor('reference')).value='';refreshGallery('reference');ctx.onContentChange?.();return}
    if(action==='back'){showGallery(kind);return}
    if(action==='toggle-date'){const section=button.closest('.mg-date');setDateOpen(section,!section.classList.contains('is-open'),kind);return}
    if(action==='filter'){S.filter=id;$$('[data-mg-action=filter]',rootFor(kind)).forEach(item=>{item.classList.toggle('active',item.dataset.id===id);item.setAttribute('aria-pressed',String(item.dataset.id===id))});refreshGallery(kind);syncSelection();return}
    if(action==='reset-filters'){S.filter='all';S.search='';const root=rootFor(kind);$('#mg-search',root).value='';$$('[data-mg-action=filter]',root).forEach(item=>{item.classList.toggle('active',item.dataset.id==='all');item.setAttribute('aria-pressed',String(item.dataset.id==='all'))});refreshGallery(kind);syncSelection();return}
    if(action==='select-visible'){visibleAssets('analysis').forEach(asset=>S.selected.add(asset.id));persistSelection();syncSelection();return}
    if(action==='inspector-tab'){setInspectorTab(button.dataset.tab);return}
    if(action==='rhythm-page'){setRhythmPage(button.dataset.page);return}
    if(action==='rhythm-current'){const player=$('.mg-player video',rootFor('reference'));if(player)setRhythmPage(Math.floor(player.currentTime/rhythmWindowSeconds));return}
    if(action==='rhythm-seek'){const time=Number(button.dataset.time);setRhythmPage(Math.floor(time/rhythmWindowSeconds));seek(time);return}
    if(action==='shot-seek'){seek(Number(button.dataset.time));setInspectorTab('shots');const panel=$('#mg-panel-shots',rootFor('reference')),row=panel&&$$('.mg-shot-row',panel).find(item=>Number(item.dataset.start)===Number(button.dataset.time));if(row&&!button.closest('.mg-shot-list'))panel.scrollTo({top:panel.scrollTop+row.getBoundingClientRect().top-panel.getBoundingClientRect().top-12,behavior:'instant'});return}
    if(action==='seek'||action==='node-seek'){seek(Number(button.dataset.time));if(kind==='reference'&&!button.closest('.mg-detail-transcript')){setInspectorTab('content');const panel=$('#mg-panel-content',rootFor(kind)),row=$$('.mg-segment',panel).find(item=>Number(button.dataset.time)>=Number(item.dataset.start)&&Number(button.dataset.time)<Number(item.dataset.end));if(row)panel.scrollTo({top:panel.scrollTop+row.getBoundingClientRect().top-panel.getBoundingClientRect().top-12,behavior:'instant'})}return}
    if(action==='select'){
      if(isReference(assetById(id)))return;
      if(S.selected.has(id)){S.selected.delete(id);S.segments=S.segments.filter(segment=>segment.asset_id!==id)}
      else S.selected.add(id);
      persistSelection();syncSelection();return;
    }
    if(action==='segment-select'){
      const asset=assetById(id),index=Number(button.dataset.index),segment=asset?.analysis?.segments?.[index];
      if(!segment||isReference(asset))return;
      if(selectedSegment(id,index)){
        S.segments=S.segments.filter(item=>!(item.asset_id===id&&item.index===index));
        if(!S.segments.some(item=>item.asset_id===id))S.selected.delete(id);
      }else{
        S.segments.push({asset_id:id,start:segment.start,end:segment.end,index});
        S.selected.add(id);
      }
      persistSelection();syncSelection();return;
    }
    if(action==='analyze'){
      button.disabled=true;
      Promise.resolve(ctx.analyze?.(id,kind==='reference')).catch(error=>ctx.toast(error.message||'分析暂时不可用',true)).finally(()=>{if(button.isConnected)button.disabled=false});
    }
  });
  document.addEventListener('submit',event=>{if(!event.target.matches('.mg-rename-form'))return;event.preventDefault();void saveRename(event.target)});
  document.addEventListener('input',event=>{
    if(event.target.matches('.mg-rename-form input')){if(state.reference.renaming)state.reference.renaming.value=event.target.value;return}
    if(event.target.id==='mg-reference-search'){state.reference.search=event.target.value;refreshGallery('reference');ctx.onContentChange?.();return}
    if(event.target.id!=='mg-search')return;
    S.search=event.target.value;if(String(S.search).trim())grouped('analysis').forEach(group=>state.analysis.openDates.add(group.key));refreshGallery('analysis');syncSelection();
  });
  document.addEventListener('keydown',event=>{if(event.key==='Escape'&&event.target.closest('.mg-rename-form')){event.preventDefault();cancelRename();return}const tab=event.target.closest('.mg-inspector-tabs button');if(!tab)return;const keys=['ArrowLeft','ArrowRight','Home','End'];if(!keys.includes(event.key))return;event.preventDefault();const tabs=['content','shots','audio','remix'],index=tabs.indexOf(tab.dataset.tab),next=event.key==='Home'?0:event.key==='End'?tabs.length-1:(index+(event.key==='ArrowRight'?1:tabs.length-1))%tabs.length;setInspectorTab(tabs[next],true)});
  return {render,open,showGallery,refresh};
}
