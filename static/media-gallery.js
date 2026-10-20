const galleryStatus={
  not_analyzed:'待分析',queued:'等待分析',running:'分析中',succeeded:'已分析',
  failed:'分析失败',needs_configuration:'服务暂不可用',unknown:'状态待确认',
};
const kindNames={image:'图片',video:'视频',audio:'音频'};

export function createMediaGallery(ctx){
  const {S,esc,fmt,mediaTag,thumb,assetById,selectedAssets,persistSelection}=ctx;
  const $=ctx.$||((selector,root=document)=>root.querySelector(selector));
  const $$=ctx.$$||((selector,root=document)=>[...root.querySelectorAll(selector)]);
  const state={
    analysis:{detailId:null,scroll:0,anchorId:null,anchorOffset:null,openDates:new Set(),knownDates:new Set(),restore:0},
    reference:{detailId:null,scroll:0,anchorId:null,anchorOffset:null,openDates:new Set(),knownDates:new Set(),restore:0},
  };
  const titleFor=kind=>kind==='reference'?'样片分析':'素材分析';
  const uploadFor=kind=>kind==='reference'?'上传样片':'添加素材';
  const routeIs=kind=>S.route===kind;
  const selectedSegment=(id,index)=>ctx.segmentSelected?ctx.segmentSelected(id,index):S.segments.some(item=>item.asset_id===id&&item.index===index);
  const isReference=asset=>asset.purpose==='reference'||asset.analysis?.reference_analysis===true||Array.isArray(asset.analysis?.structure)&&asset.analysis.structure.length>0;
  const assetsFor=kind=>S.assets.filter(asset=>kind==='reference'?asset.kind==='video'&&isReference(asset):['image','video'].includes(asset.kind));

  function status(asset,kind){
    const value=asset.analysis_status||'not_analyzed';
    if(['failed','needs_configuration','unknown','queued','running'].includes(value))return {value,label:galleryStatus[value]};
    const complete=kind==='reference'?isReference(asset)&&!!asset.analysis?.reference_analysis||!!asset.analysis?.structure?.length:!!asset.analysis;
    return complete?{value:'succeeded',label:'已分析'}:{value:'not_analyzed',label:'待分析'};
  }
  function dateInfo(asset){
    const date=new Date(asset.created_at||asset.uploaded_at||'');
    if(Number.isNaN(date.getTime()))return {key:'unknown',label:'日期未记录',time:0};
    const key=`${date.getFullYear()}-${String(date.getMonth()+1).padStart(2,'0')}-${String(date.getDate()).padStart(2,'0')}`;
    return {key,label:date.toLocaleDateString('zh-CN',{year:'numeric',month:'long',day:'numeric'}),time:date.getTime()};
  }
  function grouped(kind){
    const groups=new Map();
    for(const asset of assetsFor(kind)){
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
    return `<button type="button" class="mg-card-preview" data-mg-action="open" data-kind="${kind}" data-id="${esc(asset.id)}" aria-label="查看${esc(asset.name)}">${thumb(asset)?`<img src="${esc(thumb(asset))}" alt="" loading="lazy">`:'<span class="mg-no-thumb">暂无预览</span>'}<span class="mg-card-type">${esc(asset.kind==='video'?fmt(asset.duration):'图片')}</span></button>${kind==='analysis'?`<button type="button" class="mg-card-select ${S.selected.has(asset.id)?'selected':''}" data-mg-action="select" data-id="${esc(asset.id)}" aria-label="${S.selected.has(asset.id)?'取消选择':'选择'}${esc(asset.name)}" aria-pressed="${S.selected.has(asset.id)}">${S.selected.has(asset.id)?'✓':'＋'}</button>`:''}<div class="mg-card-body"><button type="button" class="mg-card-name" data-mg-action="open" data-kind="${kind}" data-id="${esc(asset.id)}">${esc(asset.name)}</button><div class="mg-card-meta"><span>${esc(kindNames[asset.kind]||'素材')}</span>${badge(asset,kind)}</div>${asset.analysis_error?`<p class="mg-card-error">${esc(asset.analysis_error)}</p>`:''}<div class="mg-card-tags">${(asset.analysis?.tags||[]).slice(0,3).map(tag=>`<span>${esc(tag)}</span>`).join('')}</div></div>`;
  }
  function groupSummary(group){
    return `<span class="mg-date-chevron" aria-hidden="true"></span><span class="mg-date-title">${esc(group.label)}</span><span class="mg-date-count">${group.assets.length} 份</span><span class="mg-date-preview" aria-hidden="true">${group.assets.slice(0,5).map(asset=>thumb(asset)?`<img src="${esc(thumb(asset))}" alt="" loading="lazy">`:'<span></span>').join('')}</span>`;
  }
  function rootFor(kind){
    const root=$('.mg-root');
    return root?.dataset.kind===kind?root:null;
  }
  function render(kind){
    if(!state[kind])return;
    let root=rootFor(kind);
    if(!root){
      $('#app').innerHTML=`<section class="mg-root" data-kind="${kind}"><div class="mg-page-heading"><div><h1>${titleFor(kind)}</h1><p class="mg-gallery-count"></p></div>${uploadButton(kind,true)}</div><div class="mg-gallery"></div><div class="mg-detail" hidden></div></section>`;
      root=rootFor(kind);
      root.addEventListener('toggle',event=>{
        const group=event.target;
        if(!group.matches?.('details.mg-date'))return;
        if(group.open)state[kind].openDates.add(group.dataset.date);else state[kind].openDates.delete(group.dataset.date);
      },true);
    }
    refreshGallery(kind);
    const asset=assetById(state[kind].detailId);
    if(asset&&assetsFor(kind).some(item=>item.id===asset.id)){
      $('.mg-gallery',root).hidden=true;
      const detail=$('.mg-detail',root);
      detail.hidden=false;
      if(detail.dataset.assetId!==asset.id)mountDetail(kind,asset);
      else refreshDetail(kind,asset);
    }else{
      state[kind].detailId=null;
      $('.mg-gallery',root).hidden=false;
      $('.mg-detail',root).hidden=true;
    }
  }
  function refreshGallery(kind){
    const root=rootFor(kind);
    if(!root)return;
    const groups=grouped(kind),gallery=$('.mg-gallery',root),current=state[kind];
    $('.mg-gallery-count',root).textContent=`${assetsFor(kind).length} ${kind==='reference'?'条样片':'份素材'}`;
    if(!groups.length){
      gallery.innerHTML=`<div class="mg-empty"><p>${kind==='reference'?'还没有样片':'还没有素材'}</p>${uploadButton(kind,true)}</div>`;
      return;
    }
    $('.mg-empty',gallery)?.remove();
    const wasEmpty=current.knownDates.size===0;
    const validDates=new Set(groups.map(group=>group.key));
    $$('details.mg-date',gallery).forEach(element=>{if(!validDates.has(element.dataset.date))element.remove()});
    for(const [index,group] of groups.entries()){
      if(!current.knownDates.has(group.key)){
        if(index===0)current.openDates.add(group.key);
        current.knownDates.add(group.key);
      }
      if(wasEmpty&&index===0)current.openDates.add(group.key);
      let section=$$('details.mg-date',gallery).find(item=>item.dataset.date===group.key);
      if(!section){
        section=document.createElement('details');section.className='mg-date';section.dataset.date=group.key;
        section.innerHTML='<summary></summary><div class="mg-album"></div>';
        section.open=current.openDates.has(group.key);
      }
      const summary=$('summary',section),html=groupSummary(group);
      if(summary.innerHTML!==html)summary.innerHTML=html;
      const album=$('.mg-album',section),ids=new Set(group.assets.map(asset=>asset.id));
      $$('.mg-card',album).forEach(item=>{if(!ids.has(item.dataset.assetId))item.remove()});
      group.assets.forEach((asset,assetIndex)=>{
        let card=$$('.mg-card',album).find(item=>item.dataset.assetId===asset.id);
        if(!card){card=document.createElement('article');card.className='mg-card';card.dataset.assetId=asset.id}
        const content=cardContent(asset,kind);
        if(card.innerHTML!==content)card.innerHTML=content;
        card.classList.toggle('selected',kind==='analysis'&&S.selected.has(asset.id));
        if(album.children[assetIndex]!==card)album.insertBefore(card,album.children[assetIndex]||null);
      });
      if(gallery.children[index]!==section)gallery.insertBefore(section,gallery.children[index]||null);
    }
  }
  function analyzeButton(asset,kind){
    const value=status(asset,kind);
    return `<button type="button" class="button small" data-mg-action="analyze" data-kind="${kind}" data-id="${esc(asset.id)}" ${['queued','running'].includes(value.value)?'disabled':''}>${['queued','running'].includes(value.value)?value.label:asset.analysis?'重新分析':'开始分析'}</button>`;
  }
  function detailHeading(asset,kind){
    return `<div class="mg-detail-heading"><div><h2>${esc(asset.name)}</h2><p>${esc(mediaInfo(asset))}</p></div><div class="mg-detail-status">${badge(asset,kind)}${analyzeButton(asset,kind)}</div></div>${asset.analysis_error?`<p class="mg-error">${esc(asset.analysis_error)}</p>`:''}`;
  }
  function summaryHTML(asset){
    const analysis=asset.analysis;
    if(!analysis)return '';
    return `<div class="mg-summary">${analysis.summary?`<p>${esc(analysis.summary)}</p>`:''}<div class="mg-tags">${(analysis.tags||[]).map(tag=>`<span>${esc(tag)}</span>`).join('')}</div></div>`;
  }
  function segmentsHTML(asset,kind){
    const segments=asset.analysis?.segments||[];
    if(!segments.length)return '';
    return `<div class="mg-block-title"><h3>${kind==='reference'?'镜头内容':'可用片段'}</h3><span>${segments.length} 段</span></div><div class="mg-segments">${segments.map((segment,index)=>`<article class="mg-segment ${selectedSegment(asset.id,index)&&kind==='analysis'?'selected':''}" data-segment-index="${index}"><button type="button" class="mg-time" data-mg-action="seek" data-time="${Number(segment.start)||0}">${fmt(segment.start)} – ${fmt(segment.end)}</button><div><p>${esc(segment.description||segment.content||segment.summary||segment.label||'')}</p><div class="mg-tags">${[segment.role,segment.shot_type,segment.motion,...(segment.tags||[])].filter(Boolean).slice(0,6).map(tag=>`<span>${esc(tag)}</span>`).join('')}</div></div>${kind==='analysis'?`<button type="button" class="button small" data-mg-action="segment-select" data-id="${esc(asset.id)}" data-index="${index}" aria-pressed="${selectedSegment(asset.id,index)}">${selectedSegment(asset.id,index)?'已选中':'选入剪辑'}</button>`:''}</article>`).join('')}</div>`;
  }
  function evidenceHTML(asset){
    const frames=asset.analysis?.evidence_frames||[];
    if(!frames.length)return '';
    return `<div class="mg-block-title"><h3>分析画面</h3><span>${frames.length} 帧</span></div><div class="mg-evidence">${frames.map(frame=>`<button type="button" data-mg-action="seek" data-time="${Number(frame.time)||0}"><img src="${esc(frame.url)}" alt="${fmt(frame.time)}画面" loading="lazy"><span>${fmt(frame.time)}</span></button>`).join('')}</div>`;
  }
  function transcriptHTML(asset){
    if(!ctx.transcriptPanel)return '';
    return ctx.transcriptPanel(asset).replaceAll('data-action="seek-asset"','data-mg-action="seek"');
  }
  function structureFor(asset){
    const structure=asset.analysis?.structure;
    return Array.isArray(structure)&&structure.length?structure:asset.analysis?.reference_analysis?asset.analysis.segments||[]:[];
  }
  function structureHTML(asset){
    const nodes=structureFor(asset);
    if(!nodes.length)return '';
    return `<div class="mg-block-title"><h3>剪辑结构</h3><span>${fmt(asset.duration)}</span></div><ol class="mg-structure-track">${nodes.map((node,index)=>`<li><button type="button" class="mg-structure-node" data-mg-action="node-seek" data-index="${index}" data-time="${Number(node.start)||0}" data-end="${Number(node.end)||0}" aria-label="${index+1}. ${esc(node.role||node.label||'镜头')}，${fmt(node.start)}至${fmt(node.end)}"><span class="mg-node-order">${index+1}</span><span class="mg-node-copy"><strong>${esc(node.role||node.label||`镜头 ${index+1}`)}</strong><small>${fmt(node.start)} – ${fmt(node.end)}</small></span></button></li>`).join('')}</ol>`;
  }
  function miniAssetsHTML(exclude){
    return S.assets.filter(asset=>asset.id!==exclude&&asset.kind!=='audio').map(asset=>`<button type="button" class="${S.selected.has(asset.id)?'selected':''}" title="${esc(asset.name)}" aria-label="${S.selected.has(asset.id)?'取消选择':'选择'}${esc(asset.name)}" data-mg-action="select" data-id="${esc(asset.id)}" aria-pressed="${S.selected.has(asset.id)}">${thumb(asset)?`<img src="${esc(thumb(asset))}" alt="">`:'<span>图片</span>'}</button>`).join('');
  }
  function remixHTML(asset){
    const maximum=Math.min(600,Math.max(4,Math.round(asset.duration)||30));
    return `<form id="remix-form" class="mg-remix-form side-form"><h2>用这个结构剪一条</h2><label>新片讲什么<textarea name="brief" required placeholder="写下这条新片的主题与内容"></textarea></label><div class="field-pair"><label>画面比例<select name="ratio"><option>9:16</option><option>16:9</option><option>1:1</option></select></label><label>目标秒数<input type="number" name="duration" min="4" max="600" value="${maximum}"></label></div><div class="mg-block-title"><h3>选择素材</h3><button type="button" class="button small quiet" data-action="upload" data-purpose="remix">添加</button></div><div class="mini-assets">${miniAssetsHTML(asset.id)}</div><p class="mg-selection-count"></p><button class="button primary full" type="submit">按结构生成新片</button></form>`;
  }
  function mountDetail(kind,asset){
    const root=rootFor(kind),detail=$('.mg-detail',root);
    detail.dataset.assetId=asset.id;
    detail.innerHTML=`<div class="mg-back-row"><button type="button" class="mg-back" data-mg-action="back" data-kind="${kind}"><span aria-hidden="true">‹</span> 返回相册</button></div><div class="mg-detail-heading-slot">${detailHeading(asset,kind)}</div>${kind==='reference'?`<div class="mg-reference-layout"><div class="mg-reference-main"><div class="mg-structure">${structureHTML(asset)}</div><div class="mg-player" data-player-asset="${esc(asset.id)}">${mediaTag(asset,'reference-player')}</div><div class="mg-detail-summary">${summaryHTML(asset)}</div><div class="mg-detail-segments">${segmentsHTML(asset,kind)}</div><div class="mg-detail-transcript">${transcriptHTML(asset)}</div></div><aside class="mg-remix">${remixHTML(asset)}</aside></div>`:`<div class="mg-analysis-layout"><div class="mg-player" data-player-asset="${esc(asset.id)}">${mediaTag(asset)}</div><div class="mg-analysis-copy"><div class="mg-detail-summary">${summaryHTML(asset)}</div><div class="mg-whole-select"></div></div></div><div class="mg-detail-segments">${segmentsHTML(asset,kind)}</div><div class="mg-detail-evidence">${evidenceHTML(asset)}</div><div class="mg-detail-transcript">${transcriptHTML(asset)}</div>`}`;
    const markup={
      '.mg-detail-heading-slot':detailHeading(asset,kind),
      '.mg-detail-summary':summaryHTML(asset),
      '.mg-detail-segments':segmentsHTML(asset,kind),
      '.mg-detail-transcript':transcriptHTML(asset),
      '.mg-structure':structureHTML(asset),
      '.mg-detail-evidence':evidenceHTML(asset),
    };
    for(const [selector,html] of Object.entries(markup)){
      const target=$(selector,detail);if(target)target._mgMarkup=html;
    }
    bindPlayer(kind);
    syncSelection();
    ctx.restoreDrafts?.();
  }
  function patchHTML(selector,html,root){
    const target=$(selector,root);
    if(target&&target._mgMarkup!==html){
      const scroll=target.scrollTop;
      target.innerHTML=html;
      target._mgMarkup=html;
      target.scrollTop=scroll;
    }
  }
  function refreshDetail(kind,asset){
    const root=rootFor(kind),detail=$('.mg-detail',root);
    if(!detail||detail.dataset.assetId!==asset.id)return;
    patchHTML('.mg-detail-heading-slot',detailHeading(asset,kind),detail);
    patchHTML('.mg-detail-summary',summaryHTML(asset),detail);
    patchHTML('.mg-detail-segments',segmentsHTML(asset,kind),detail);
    patchHTML('.mg-detail-transcript',transcriptHTML(asset),detail);
    if(kind==='reference')patchHTML('.mg-structure',structureHTML(asset),detail);
    else patchHTML('.mg-detail-evidence',evidenceHTML(asset),detail);
    syncSelection();
    updateActiveNode();
  }
  function syncSelection(){
    const root=$('.mg-root');if(!root)return;
    const kind=root.dataset.kind,asset=assetById(state[kind].detailId);
    $$('.mg-card',root).forEach(card=>{
      const selected=S.selected.has(card.dataset.assetId);
      card.classList.toggle('selected',kind==='analysis'&&selected);
      const button=$('.mg-card-select',card);
      if(button){button.classList.toggle('selected',selected);button.setAttribute('aria-pressed',String(selected));button.textContent=selected?'✓':'＋'}
    });
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
    const nodes=$$('.mg-structure-node',root);
    nodes.forEach((node,index)=>{
      const start=Number(node.dataset.time),end=Number(node.dataset.end);
      const active=player.currentTime>=start&&(player.currentTime<end||index===nodes.length-1&&player.currentTime===end);
      node.classList.toggle('active',active);
      if(active)node.setAttribute('aria-current','true');else node.removeAttribute('aria-current');
    });
  }
  function seek(time){
    const root=$('.mg-root'),player=root&&$('.mg-player video,.mg-player audio',root);
    if(!player)return;
    const set=()=>{player.currentTime=Math.max(0,Math.min(Number(time)||0,Number.isFinite(player.duration)?Math.max(0,player.duration-.01):Number(time)||0));updateActiveNode()};
    if(player.readyState>=1)set();else player.addEventListener('loadedmetadata',set,{once:true});
  }
  function open(kind,id){
    const asset=assetById(id);
    if(!asset||!state[kind])return;
    const current=state[kind],root=rootFor(kind);
    if(!current.detailId&&root){
      current.scroll=window.scrollY;current.anchorId=id;
      const card=$$('.mg-card',root).find(item=>item.dataset.assetId===id);
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
      const card=$$('.mg-card',rootFor(kind)).find(item=>item.dataset.assetId===current.anchorId);
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
  document.addEventListener('click',event=>{
    const button=event.target.closest('[data-mg-action]');if(!button)return;
    const action=button.dataset.mgAction,id=button.dataset.id,kind=button.dataset.kind||$('.mg-root')?.dataset.kind;
    if(action==='open'){open(kind,id);return}
    if(action==='back'){showGallery(kind);return}
    if(action==='seek'||action==='node-seek'){seek(Number(button.dataset.time));return}
    if(action==='select'){
      if(S.selected.has(id)){S.selected.delete(id);S.segments=S.segments.filter(segment=>segment.asset_id!==id)}
      else S.selected.add(id);
      persistSelection();syncSelection();return;
    }
    if(action==='segment-select'){
      const asset=assetById(id),index=Number(button.dataset.index),segment=asset?.analysis?.segments?.[index];
      if(!segment)return;
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
  return {render,open,showGallery,refresh};
}
