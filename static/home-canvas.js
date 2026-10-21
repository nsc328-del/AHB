import {createHomeInteractions} from './home-interactions.js?v=20261021-home7';
import {buildCandidateForest} from './candidate-graph.js?v=20261020-home1';
import {isReferenceAsset} from './media-gallery.js?v=20261020-referencecuts3';

const STORAGE='video-studio-home-canvas-v1';
const sampleNames=new Set(['blue.png','orange.png','green.png']);
const signature=plan=>JSON.stringify({ratio:plan.ratio,clips:plan.clips.map(({asset_id,start,end,duration,caption,transition})=>({asset_id,start,end,duration,caption,transition}))});
const copy=value=>structuredClone(value);
const seconds=value=>`${Number(value||0).toFixed(1)}s`;

export function createHomeCanvas(ctx){
  const {S,esc,api,toast,track,thumb}=ctx;
  const H={ready:false,seeded:false,userEdited:false,branches:[],active:'a',clipId:null,picked:null,title:'我的新片',ratio:'9:16',example:true,
    projectId:null,head:null,pending:null,previews:{},undo:[],drag:null,dirty:false,request:null,recovering:false,forest:null,root:null,editorOpen:false,zoom:1,zoomMode:'fit',materialFilter:'all',focusMode:false,interactions:null,resizeObserver:null,instructions:{},nodeTasks:{},fieldDrafts:{},inputRevisions:{},suppressClickUntil:0};
  const $=(selector,root=H.root)=>root?.querySelector(selector);
  const all=(selector,root=H.root)=>[...(root?.querySelectorAll(selector)||[])];
  const assets=()=>S.assets.filter(asset=>!isReferenceAsset(asset)&&['image','video'].includes(asset.kind));
  const asset=id=>assets().find(item=>item.id===id);
  const branch=()=>H.branches.find(item=>item.id===H.active)||H.branches[0];
  const clip=()=>branch()?.clips.find(item=>item.id===H.clipId)||branch()?.clips[0];
  const branchPlan=item=>({...item,title:H.title,ratio:H.ratio,duration:item.clips.reduce((n,c)=>n+c.duration,0)});
  const hasFieldDrafts=b=>b.clips.some(c=>Object.keys(H.fieldDrafts[c.id]||{}).length);
  function newClip(a,duration=2){
    const end=a.kind==='video'?Math.min(duration,Number(a.duration)):duration;
    return {id:crypto.randomUUID(),asset_id:a.id,start:0,end,duration:end,label:a.name,caption:'',transition:'cut',role:'body'};
  }
  function store(){
    const {branches,active,clipId,picked,title,ratio,example,projectId,head,pending,previews,request,editorOpen,instructions,nodeTasks,fieldDrafts,inputRevisions,seeded,userEdited}=H;
    sessionStorage.setItem(STORAGE,JSON.stringify({branches,active,clipId,picked,title,ratio,example,projectId,head,pending,previews,request,editorOpen,instructions,nodeTasks,fieldDrafts,inputRevisions,seeded,userEdited}));
  }
  function initialize(){
    if(S.assetsLoaded===false)return false;
    if(H.ready&&(H.seeded||H.userEdited))return true;
    H.ready=true;
    try{
      const saved=JSON.parse(sessionStorage.getItem(STORAGE)||'null');
      if(saved?.branches?.length===3&&(saved.userEdited||saved.branches.some(item=>item.clips?.length))&&saved.branches.every(item=>Array.isArray(item.clips))){
        Object.assign(H,saved);H.editorOpen=false;H.seeded=H.branches.some(b=>b.clips.length>0);for(const task of Object.values(H.nodeTasks))task.busy=false;if(H.pending)H.pending.needsCheck=true;for(const task of Object.values(H.nodeTasks))if(['submitting','running'].includes(task.status))task.status='needs_check';return true;
      }
    }catch{}
    const selected=assets().filter(a=>S.selected.has(a.id)),samples=['blue.png','orange.png','green.png'].map(name=>assets().find(a=>a.name===name)).filter(Boolean);
    const chosen=selected.length?selected.slice(0,5):samples;
    H.example=chosen.length>0&&chosen.every(a=>sampleNames.has(a.name));
    const orders=[chosen,chosen.length>2?[chosen[0],...chosen.slice(1).reverse()]:[...chosen].reverse(),chosen.length>1?[chosen.at(-1),...chosen.slice(0,-1)]:chosen];
    H.branches=orders.map((items,index)=>({id:['a','b','c'][index],name:['顺序展开','换个顺序','从结尾开始'][index],clips:items.map(a=>newClip(a))}));
    H.clipId=branch().clips[0]?.id||null;H.seeded=chosen.length>0;
    store();return true;
  }
  function remember(){
    H.undo.push({branches:copy(H.branches),active:H.active,clipId:H.clipId,example:H.example});
    if(H.undo.length>20)H.undo.shift();
  }
  function edit(fn){
    if(H.pending)return;
    remember();fn();H.userEdited=true;H.dirty=true;H.request=null;store();paintGraph();paintInspector();paintPreview();paintActions();
  }
  function choose(id,clipId){
    if(!H.branches.some(item=>item.id===id))return;
    H.active=id;H.clipId=clipId||branch().clips[0]?.id||null;H.editorOpen=true;store();paintGraph();paintInspector();paintPreview();paintActions();
  }
  function replace(assetId){
    const a=asset(assetId),c=clip();if(!a||!c||H.pending)return;
    edit(()=>{delete H.fieldDrafts[c.id];const duration=a.kind==='video'?Math.min(c.duration,Number(a.duration)):c.duration;Object.assign(c,{asset_id:a.id,start:0,end:duration,duration});H.example=H.branches.every(b=>b.clips.every(c=>sampleNames.has(asset(c.asset_id)?.name)));});
  }
  function append(assetId,at=branch().clips.length){
    const a=asset(assetId);if(!a||H.pending)return;
    if(branch().clips.length>=12){toast('这个分支最多放 12 个镜头',true);return}
    edit(()=>{const c=newClip(a);branch().clips.splice(Math.max(0,Math.min(at,branch().clips.length)),0,c);H.clipId=c.id;H.example=H.branches.every(b=>b.clips.every(c=>sampleNames.has(asset(c.asset_id)?.name)));});
  }
  function move(from,to){
    const b=branch();
    if(from===to||from<0||to<0||from>=b.clips.length||to>=b.clips.length||H.pending)return;
    edit(()=>{const [c]=b.clips.splice(from,1);b.clips.splice(to,0,c);H.clipId=c.id});
  }
  const displayName=a=>({'blue.png':'蓝色方块','orange.png':'橙色圆形','green.png':'绿色三角'}[a?.name]||a?.name||'镜头');
  const glyph={upload:'<path d="M12 16V4m-4 4 4-4 4 4M4 15v5h16v-5"/>',spark:'<path d="m12 3 2.8 6.2L21 12l-6.2 2.8L12 21l-2.8-6.2L3 12l6.2-2.8Z"/>',film:'<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M7 4v16M17 4v16M3 9h4m-4 6h4M17 9h4m-4 6h4"/>',undo:'<path d="M8 5 3 10l5 5m-5-5h10a7 7 0 0 1 7 7"/>',fit:'<path d="M8 3H3v5m13-5h5v5M3 16v5h5m13-5v5h-5"/>',play:'<path d="m9 5 11 7-11 7Z"/>',plus:'<path d="M12 5v14M5 12h14"/>',grip:'<path d="M8 5h.01M8 12h.01M8 19h.01M15 5h.01M15 12h.01M15 19h.01"/>'};
  const icon=name=>`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${glyph[name]||glyph.plus}</svg>`;
  function materialMarkup(a){
    return `<button type="button" class="hc-material ${H.picked===a.id?'picked':''}" draggable="false" data-home-action="pick" data-asset-id="${esc(a.id)}" title="${esc(a.name)}" aria-label="选择素材 ${esc(a.name)}" aria-pressed="${H.picked===a.id}"><span class="hc-material-image">${thumb(a)?`<img src="${esc(thumb(a))}" alt="" loading="lazy" draggable="false">`:'<span class="hc-blank">▧</span>'}<small>${a.kind==='video'?seconds(a.duration):'图片'}</small></span><span class="hc-material-name">${esc(displayName(a))}</span></button>`;
  }
  function paintAssets(){
    const host=$('.hc-material-strip');if(!host)return;
    const values=assets().filter(a=>H.materialFilter==='all'||(H.materialFilter==='sample'?sampleNames.has(a.name):!sampleNames.has(a.name))),known=new Map(all('.hc-material',host).map(node=>[node.dataset.assetId,node]));
    const ids=new Set(values.map(a=>a.id));
    known.forEach((node,id)=>{if(!ids.has(id))node.remove()});
    $('.hc-material-empty',host)?.remove();
    if(!values.length)host.innerHTML='<span class="hc-material-empty">添加图片或视频</span>';
    values.forEach((a,index)=>{
      let node=known.get(a.id);
      if(!node){const template=document.createElement('template');template.innerHTML=materialMarkup(a);node=template.content.firstElementChild}
      node.classList.toggle('picked',H.picked===a.id);node.setAttribute('aria-pressed',String(H.picked===a.id));node.draggable=false;
      if(host.children[index]!==node)host.insertBefore(node,host.children[index]||null);
    });
    $('.hc-material-count').textContent=`${values.length}`;all('[data-home-filter]').forEach(b=>{b.classList.toggle('active',b.dataset.homeFilter===H.materialFilter);b.setAttribute('aria-pressed',String(b.dataset.homeFilter===H.materialFilter))});
  }
  function nodeKey(node){
    const id=node.planIds.includes(H.active)?H.active:node.planIds[0];
    return H.branches.find(b=>b.id===id)?.clips[node.depth]?.id||node.id;
  }
  function position(el,x,y,w,h){
    const oldX=parseFloat(el.style.left),oldY=parseFloat(el.style.top);
    Object.assign(el.style,{left:`${x}px`,top:`${y}px`,...(w?{width:`${w}px`}:{}),...(h?{height:`${h}px`}:{})});
    if(Number.isFinite(oldX)&&!H.drag&&!matchMedia('(prefers-reduced-motion: reduce)').matches&&(oldX!==x||oldY!==y)){
      el.getAnimations().forEach(a=>a.cancel());el.animate([{translate:`${oldX-x}px ${oldY-y}px`},{translate:'0px 0px'}],{duration:320,easing:'cubic-bezier(.2,.8,.2,1)'});
    }
  }
  function setZoom(value,mode='manual'){
    H.zoom=Math.max(.5,Math.min(1.5,value));H.zoomMode=mode;
    const canvas=$('.hc-canvas'),space=$('.hc-canvas-space');if(!canvas||!space)return;
    canvas.style.transform=`scale(${H.zoom})`;space.style.width=`${parseFloat(canvas.style.width)*H.zoom}px`;space.style.height=`${parseFloat(canvas.style.height)*H.zoom}px`;
    $('.hc-zoom-value').textContent=`${Math.round(H.zoom*100)}%`;
  }
  function fitCanvas(){
    const viewport=$('.hc-graph-scroll'),canvas=$('.hc-canvas');if(!viewport||!canvas)return;
    setZoom(Math.min(1,(viewport.clientWidth-40)/parseFloat(canvas.style.width),(viewport.clientHeight-24)/parseFloat(canvas.style.height)), 'fit');
  }
  function paintGraph(){
    const host=$('.hc-canvas');if(!host)return;
    const scroll=$('.hc-graph-scroll'),plans=H.branches.map(branchPlan),forest=buildCandidateForest({plans});H.forest=forest;
    const selected=clip(),selectedDepth=branch().clips.indexOf(selected),selectedNode=forest.paths.get(H.active)?.[selectedDepth];
    const expanded=!!selectedNode&&H.editorOpen,width=152,nodeHeight=136,expandedWidth=326,expandedHeight=426,step=204,rowStep=148,left=46,top=82;
    const extraWidth=expanded?expandedWidth-width:0,extraHeight=expanded?expandedHeight-nodeHeight:0;
    const selectedRow=selectedNode?.row??-1;
    const rowY=row=>top+row*rowStep+(expanded?(row>selectedRow+.001?extraHeight:Math.abs(row-selectedRow)<.001?extraHeight/2:0):0);
    const nodeX=node=>left+node.depth*step+(expanded&&node.depth>selectedDepth?extraWidth:0),nodeY=node=>rowY(node.row);
    const nodeWidth=node=>node===selectedNode&&expanded?expandedWidth:width;
    const endpointX=left+forest.depth*step+extraWidth,canvasWidth=endpointX+148,canvasHeight=Math.max(420,top+(forest.rows-1)*rowStep+84+extraHeight);
    const paths=[];
    for(const node of forest.nodes)if(node.parent){
      const x1=nodeX(node.parent)+nodeWidth(node.parent),x2=nodeX(node),y1=nodeY(node.parent),y2=nodeY(node),bend=(x1+x2)/2;
      paths.push({key:`${nodeKey(node.parent)}:${nodeKey(node)}`,active:node.planIds.includes(H.active),d:`M${x1} ${y1} C${bend} ${y1} ${bend} ${y2} ${x2} ${y2}`});
    }
    for(const leaf of forest.leaves)if(leaf.node){
      const x1=nodeX(leaf.node)+nodeWidth(leaf.node),y1=nodeY(leaf.node),y2=rowY(leaf.row),bend=(x1+endpointX)/2;
      paths.push({key:`end:${leaf.plan.id}`,active:leaf.plan.id===H.active,d:`M${x1} ${y1} C${bend} ${y1} ${bend} ${y2} ${endpointX} ${y2}`});
    }
    host.style.width=`${canvasWidth}px`;host.style.height=`${canvasHeight}px`;
    let svg=$('.hc-links',host);if(!svg){svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.classList.add('hc-links');svg.setAttribute('aria-hidden','true');host.prepend(svg)}
    svg.setAttribute('width',canvasWidth);svg.setAttribute('height',canvasHeight);svg.setAttribute('viewBox',`0 0 ${canvasWidth} ${canvasHeight}`);
    const oldPaths=new Map([...svg.children].map(p=>[p.dataset.key,p]));
    paths.forEach(p=>{let el=oldPaths.get(p.key);if(!el){el=document.createElementNS('http://www.w3.org/2000/svg','path');el.dataset.key=p.key;svg.append(el)}el.setAttribute('class',p.active?'active':'');el.setAttribute('d',p.d);el.style.d=`path('${p.d}')`;oldPaths.delete(p.key)});oldPaths.forEach(el=>el.remove());
    const existing=new Map(all('.hc-node',host).map(node=>[node.dataset.nodeKey,node])),valid=new Set();
    for(const node of forest.nodes){
      const key=nodeKey(node),a=asset(node.clip.asset_id),active=node.planIds.includes(H.active),open=node===selectedNode&&expanded;
      valid.add(key);let card=existing.get(key);
      if(!card){card=document.createElement('article');card.className='hc-node';card.dataset.nodeKey=key;card.innerHTML='<button type="button" class="hc-node-summary" data-home-action="node"></button><div class="hc-node-editor" hidden></div>';host.append(card)}
      card.className=`hc-node ${active?'in-branch':''} ${node===selectedNode?'selected':''} ${open?'is-expanded':''}`;
      Object.assign(card.dataset,{nodeId:node.id,clipId:key,depth:String(node.depth),members:node.planIds.join(' ')});
      position(card,nodeX(node),nodeY(node)-(open?expandedHeight:nodeHeight)/2,open?expandedWidth:width,open?expandedHeight:nodeHeight);
      const summary=$('.hc-node-summary',card),html=`<span class="hc-node-image">${a&&thumb(a)?`<img src="${esc(thumb(a))}" alt="" draggable="false">`:'<span class="hc-blank">▧</span>'}<span class="hc-shot-index">${String(node.depth+1).padStart(2,'0')}</span><span class="hc-node-grip">${icon('grip')}</span></span><span class="hc-node-caption"><strong>${esc(displayName(a))}</strong><small>${seconds(node.clip.duration)}</small></span>${node.planIds.length>1?`<b class="hc-shared" title="${node.planIds.length} 条分支共用开头">共用开头</b>`:''}`;
      if(summary._markup!==html){summary.innerHTML=html;summary._markup=html}
      summary.dataset.nodeId=node.id;summary.draggable=false;summary.setAttribute('aria-expanded',String(open));summary.setAttribute('aria-label',`镜头 ${node.depth+1} ${a?.name||''}，${seconds(node.clip.duration)}，点击编辑`);
      $('.hc-node-editor',card).hidden=!open;
    }
    existing.forEach((node,key)=>{if(!valid.has(key))node.remove()});
    const endpoints=new Map(all('.hc-branch',host).map(node=>[node.dataset.branchId,node]));
    for(const leaf of forest.leaves){
      let end=endpoints.get(leaf.plan.id);
      if(!end){end=document.createElement('button');end.type='button';end.dataset.homeAction='branch';end.dataset.branchId=leaf.plan.id;host.append(end)}
      end.className=`hc-branch ${leaf.plan.id===H.active?'active':''}`;end.setAttribute('aria-pressed',String(leaf.plan.id===H.active));
      end.innerHTML=`<span class="hc-branch-play">${icon('play')}<b>${leaf.plan.id.toUpperCase()}</b></span><strong>${esc(leaf.plan.name)}</strong><small>${leaf.plan.clips.length} 镜头 · ${seconds(leaf.plan.duration)}</small>`;
      position(end,endpointX,rowY(leaf.row)-50,116,100);
    }
    all('.hc-insert-slot',host).forEach(el=>el.remove());
    const activePath=forest.paths.get(H.active)||[];
    for(let i=0;i<=activePath.length;i++){
      const current=activePath[i],prev=activePath[i-1],x=current?nodeX(current)-26:prev?nodeX(prev)+nodeWidth(prev)+26:left,y=current?nodeY(current):prev?nodeY(prev):top;
      const slot=document.createElement('button');slot.type='button';slot.className='hc-insert-slot';slot.dataset.index=String(i);slot.dataset.homeAction='insert-picked';slot.setAttribute('aria-label',`在第 ${i+1} 位插入所选素材`);slot.innerHTML=icon('plus');Object.assign(slot.style,{left:`${x-12}px`,top:`${y-12}px`});host.append(slot);
    }
    $('.hc-branch-label').textContent=branch().name;$('.hc-branch-duration').textContent=`${branch().clips.length} 个镜头 / ${seconds(branchPlan(branch()).duration)}`;
    $('.hc-example-label').hidden=!H.example;$('.hc-add-shot').disabled=!!H.pending||!H.picked;$('.hc-undo').disabled=!!H.pending||!H.undo.length;
    paintInspector();if(scroll)scroll.classList.toggle('has-expanded-node',expanded);
    setZoom(H.zoom,H.zoomMode);if(H.zoomMode==='fit'&&!expanded)requestAnimationFrame(fitCanvas);
    if(expanded&&H.zoom<.85)setZoom(.85,'manual');
    const mini=$('.hc-branch-switch');if(mini)mini.innerHTML=H.branches.map(b=>`<button type="button" data-home-action="branch" data-branch-id="${b.id}" class="${b.id===H.active?'active':''}" aria-pressed="${b.id===H.active}">${b.id.toUpperCase()}<span>${esc(b.name)}</span></button>`).join('');
  }
  function paintInspector(){
    const card=$('.hc-node.is-expanded'),host=card&&$('.hc-node-editor',card),c=clip(),b=branch();if(!host||!c)return;
    H.clipId=c.id;
    const a=asset(c.asset_id),index=b.clips.indexOf(c),task=H.nodeTasks[c.id],busy=['submitting','running'].includes(task?.status),checking=task?.status==='needs_check';
    const drafts=H.fieldDrafts[c.id]||{},fieldValue=(name,value)=>esc(drafts[name]??value);
    const prompt=H.instructions[c.id]||'',options=assets().map(a=>`<option value="${esc(a.id)}" ${a.id===c.asset_id?'selected':''}>${esc(a.name)}</option>`).join('');
    const html=`<form class="hc-node-ai-form" data-clip-id="${esc(c.id)}"><textarea class="hc-node-ai-input" name="home-instruction" rows="2" aria-label="镜头 ${index+1} 的剪辑要求" placeholder="写下这个镜头怎么剪…" ${H.pending?'disabled':''}>${esc(prompt)}</textarea><div class="hc-node-ai-actions"><span class="hc-node-usage">${task?.jobId&&ctx.usageSlot?ctx.usageSlot('job',task.jobId,'镜头修改用量'):''}</span><button type="submit" class="button small primary" ${busy||H.pending||!prompt.trim()&&!checking?'disabled':''}>${busy?'正在修改…':checking?'查询这次修改':'应用到镜头'}</button></div></form><p class="hc-node-message ${task?.status==='failed'||task?.status==='stale'?'has-error':''}" role="status">${esc(task?.message||'')}</p><div class="hc-node-parameters"><label class="hc-node-source-label">素材<select name="home-source" aria-label="镜头素材" ${H.pending?'disabled':''}>${options}</select></label><div class="hc-trim-fields"><label>入点<input type="number" name="home-start" min="0" step="0.1" value="${fieldValue('home-start',c.start)}" ${H.pending||a?.kind==='image'?'disabled':''}></label><label>时长<input type="number" name="home-duration" min="0.2" max="600" step="0.1" value="${fieldValue('home-duration',c.duration)}" ${H.pending?'disabled':''}></label><label class="hc-caption">字幕<input name="home-caption" value="${fieldValue('home-caption',c.caption)}" maxlength="1200" placeholder="可选" ${H.pending?'disabled':''}></label></div></div><div class="hc-node-footer"><button type="button" data-home-action="move-back" aria-label="镜头前移" ${H.pending||index===0?'disabled':''}>←</button><button type="button" data-home-action="move-next" aria-label="镜头后移" ${H.pending||index===b.clips.length-1?'disabled':''}>→</button><button type="button" data-home-action="remove" ${H.pending?'disabled':''}>移除</button><button type="button" class="hc-node-collapse" data-home-action="collapse">收起</button></div>`;
    if(host._markup!==html){
      const focused=host.contains(document.activeElement)?document.activeElement:null,name=focused?.name,start=focused?.selectionStart,end=focused?.selectionEnd;
      const usageHost=$('[data-usage-scope]',host);host.innerHTML=html;host._markup=html;
      const nextUsage=$('[data-usage-scope]',host);if(usageHost&&nextUsage&&usageHost.dataset.usageJobId===nextUsage.dataset.usageJobId)nextUsage.replaceWith(usageHost);
      if(name){const next=$(`[name="${name}"]`,host);next?.focus({preventScroll:true});if(typeof start==='number'&&next?.setSelectionRange)next.setSelectionRange(start,end)}
    }
    ctx.onContentChange?.();
  }
  function paintActions(){
    const button=$('.hc-confirm');if(!button)return;
    const valid=branch().clips.length&&branch().clips.every(c=>asset(c.asset_id));
    const nodeBusy=hasPendingNodeEdit(branch());
    button.disabled=H.recovering||H.pending&&!H.pending.needsCheck||!H.pending&&(!valid||nodeBusy);
    button.textContent=H.recovering?'正在查询预览…':H.pending?.needsCheck?'继续查询预览':H.pending?`${H.pending.branchName}生成中…`:nodeBusy?'等待镜头修改':'生成这条预览';
    $('.hc-ratio').disabled=!!H.pending;
    $('.hc-title-input').disabled=!!H.pending;
    paintAssets();
  }
  function paintPreview(){
    const host=$('.hc-render-preview');if(!host)return;
    const result=H.previews[H.active],matches=!hasFieldDrafts(branch())&&result?.signature===signature(branchPlan(branch()));
    if(!result?.render?.url){host.hidden=true;return}
    host.hidden=false;
    const key=result.render.url;
    if(host.dataset.previewKey===key){$('h2',host).textContent=`${result.branchName} · ${matches?'预览':'上次预览'}`;return;}
    host.dataset.previewKey=key;
    host.innerHTML=`<div class="hc-preview-heading"><h2>${esc(result.branchName)} · ${matches?'预览':'上次预览'}</h2><div class="actions"><a class="button small" href="${esc(result.render.url)}" download>下载</a><a class="button small" href="#project/${esc(H.projectId)}">打开作品</a></div></div><video src="${esc(result.render.url)}" ${result.render.thumbnail_url?`poster="${esc(result.render.thumbnail_url)}"`:''} controls playsinline preload="metadata"></video>`;
  }
  function render(){
    const wasSeeded=H.seeded;
    if(!initialize()){document.querySelector('#app').innerHTML='<div class="loading">读取素材</div>';H.root=null;return}
    if(!document.querySelector('.hc-root')){
      document.querySelector('#app').innerHTML=`<section class="hc-root"><header class="hc-heading"><h1>同一组画面，<br>不止一种讲法。</h1><div class="hc-entry-actions"><button type="button" class="button primary hc-import" data-action="upload" data-purpose="create">${icon('upload')}导入素材</button><div class="hc-secondary-entries"><a href="#reference">${icon('film')}参考样片</a><a href="#brief">${icon('spark')}从想法开始</a></div></div></header><section class="hc-studio" aria-label="剪辑桌面"><div class="hc-project-line"><span class="hc-project-mark">${icon('film')}</span><input class="hc-title-input" name="home-title" aria-label="作品名称" value="${esc(H.title)}" maxlength="100"><span class="hc-example-label" ${H.example?'':'hidden'}>示例</span><div class="hc-project-tools"><button type="button" class="hc-undo" data-home-action="undo" title="撤销上次调整">${icon('undo')}撤销</button><label>画幅<select class="hc-ratio" name="home-ratio" aria-label="视频画幅">${['9:16','16:9','1:1','3:4'].map(value=>`<option ${H.ratio===value?'selected':''}>${value}</option>`).join('')}</select></label><button type="button" class="hc-focus" data-home-action="focus" aria-label="展开剪辑桌面" aria-pressed="false">${icon('fit')}</button></div></div><div class="hc-desk"><aside class="hc-materials"><div class="hc-material-heading"><h2>素材</h2><span class="hc-material-count"></span><button type="button" data-action="upload" data-purpose="create" aria-label="添加素材">${icon('plus')}</button></div><div class="hc-material-filters"><button type="button" data-home-filter="all">全部</button><button type="button" data-home-filter="sample">示例</button><button type="button" data-home-filter="mine">我的</button></div><div class="hc-material-strip" aria-label="可拖入分支的素材"></div><a class="hc-library-link" href="#analysis">打开素材库 <span>↗</span></a></aside><div class="hc-editor"><section class="hc-graph-panel"><div class="hc-graph-heading"><div class="hc-branch-switch" aria-label="视频分支"></div><div class="hc-zoom"><button type="button" data-home-action="zoom-out" aria-label="缩小画布">−</button><button type="button" class="hc-zoom-value" data-home-action="fit" aria-label="适应画布">100%</button><button type="button" data-home-action="zoom-in" aria-label="放大画布">＋</button></div></div><div class="hc-graph-scroll" tabindex="0" aria-label="视频分支图"><div class="hc-canvas-space"><div class="hc-canvas"></div></div></div><div class="hc-graph-footer"><div class="hc-current-branch"><span class="hc-branch-label"></span><small class="hc-branch-duration"></small></div><button type="button" class="hc-add-shot" data-home-action="add-picked">${icon('plus')}镜头</button><button type="button" class="button primary hc-confirm" data-home-action="confirm">生成这条预览</button></div></section></div></div><div class="hc-statusbar"><span class="hc-drop-feedback" role="status">${H.picked?'已选素材':'本次草稿'}</span></div></section><p class="hc-error" role="alert" hidden></p><section class="hc-render-preview" hidden></section></section>`;
      H.root=document.querySelector('.hc-root');bind();paintAssets();paintGraph();paintInspector();paintActions();paintPreview();
      H.resizeObserver?.disconnect();H.resizeObserver=new ResizeObserver(()=>{if(H.zoomMode==='fit'&&!H.editorOpen&&!H.drag)fitCanvas()});H.resizeObserver.observe($('.hc-graph-scroll'));

    }else{H.root=document.querySelector('.hc-root');paintAssets();if(!wasSeeded&&H.seeded)paintGraph();paintActions();paintPreview();}
    onJobs(S.jobs);
  }
  function error(text){const node=$('.hc-error');if(node){node.hidden=!text;node.textContent=text||''}}
  function reusableRequest(plan,planSignature){
    const request=H.request,params=request?.params;
    return request?.signature===planSignature&&params?.branch_id===H.active&&params.branch_name===branch().name&&params.plan?.title===plan.title&&params.project_id===H.projectId&&params.expected_plan_id===H.head;
  }
  async function confirm(){
    if(H.pending){if(H.pending.needsCheck)return resumeSubmission();return}
    if(!branch().clips.length||!branch().clips.every(c=>asset(c.asset_id))||H.recovering)return;
    if(hasFieldDrafts(branch())){toast('请先完成镜头参数的修改',true);return}
    if(hasPendingNodeEdit(branch()))return;
    error('');
    const plan=branchPlan(branch()),planSignature=signature(plan);
    const params=reusableRequest(plan,planSignature)?H.request.params:{submission_id:crypto.randomUUID(),project_id:H.projectId,expected_plan_id:H.head,branch_id:H.active,branch_name:branch().name,plan};
    H.request={signature:planSignature,params};
    H.pending={branchId:params.branch_id,branchName:params.branch_name,signature:planSignature,jobId:null,needsCheck:true};
    store();return resumeSubmission();
  }
  async function resumeSubmission(){
    if(!H.pending||H.recovering)return;
    const pending=H.pending,request=H.request;
    if(!pending.jobId&&!request?.params){H.pending=null;store();error('这次提交信息已不完整，请重新确认');paintActions();paintInspector();paintGraph();return}
    H.recovering=true;pending.needsCheck=false;error('');store();paintActions();paintInspector();paintGraph();
    try{
      if(!pending.jobId){
        // A lost POST reply is retried verbatim, including its branch and submission id.
        const response=await api('/home/previews',request.params);
        H.projectId=response.project_id;H.head=response.plan_id;pending.jobId=response.job_id;store();
      }
      await track({job_id:pending.jobId},job=>finish(job));
      const job=S.jobs.find(item=>item.id===pending.jobId);
      if(job&&!['queued','running'].includes(job.status))finish(job);
    }catch(e){
      if(!pending.jobId&&e.status>=400&&e.status<500){
        // A definite validation rejection created no accepted render to recover.
        H.pending=null;H.request=null;
        if(H.projectId&&/已有新版本|方案已发生修改/.test(String(e.message))){
          try{const latest=await api(`/projects/${H.projectId}`);H.head=latest.plan_id;error('作品已有新版本，当前草稿已保留。再次确认将保存为下一版。')}
          catch{error('作品已有新版本，暂时无法读取。保留了当前草稿，请稍后再试。')}
        }else error(e.message||'预览未提交，请调整后重试');
      }else{
        // Keep accepted jobs and uncertain submissions attached to their original branch.
        pending.needsCheck=true;
        error(pending.jobId?'预览已提交，暂时无法查询进度。可继续查询本次预览。':'暂时无法确认提交结果。可继续查询本次预览。');
      }
    }finally{
      H.recovering=false;store();
      if(S.route==='create'){paintActions();paintInspector();paintGraph();paintPreview();}
    }
  }
  const nodeSnapshot=(plan,instruction,ids)=>JSON.stringify({plan,instruction,asset_ids:ids});
  function nodeTaskCurrent(task){
    const b=H.branches.find(item=>item.id===task.branchId);
    return !!b?.clips.some(c=>c.id===task.clipId)&&(H.inputRevisions[task.branchId]||0)===(task.inputRevision||0)&&nodeSnapshot(branchPlan(b),(H.instructions[task.clipId]||'').trim(),task.request.asset_ids)===task.snapshot;
  }
  const hasPendingNodeEdit=b=>Object.values(H.nodeTasks).some(task=>task.branchId===b.id&&['submitting','running','needs_check'].includes(task.status)&&nodeTaskCurrent(task));
  async function applyNodeInstruction(){
    const c=clip(),b=branch();if(!c||H.pending)return;
    const prior=H.nodeTasks[c.id];
    if(prior?.status==='needs_check')return resumeNodeTask(prior);
    if(['submitting','running'].includes(prior?.status))return;
    const instruction=(H.instructions[c.id]||'').trim();if(!instruction)return;
    if(hasFieldDrafts(b)){toast('请先完成镜头参数的修改',true);return}
    const plan=copy(branchPlan(b)),ids=[c.asset_id];
    const request={submission_id:crypto.randomUUID(),instruction,plan,clip_id:c.id,asset_ids:ids};
    const task={branchId:b.id,branchName:b.name,clipId:c.id,status:'needs_check',jobId:null,signature:null,request,snapshot:nodeSnapshot(plan,instruction,ids),inputRevision:H.inputRevisions[b.id]||0,message:''};
    H.nodeTasks[c.id]=task;store();return resumeNodeTask(task);
  }
  async function resumeNodeTask(task){
    if(!task||task.busy)return;
    task.busy=true;task.status=task.jobId?'running':'submitting';task.message='';store();paintInspector();paintActions();
    try{
      if(!task.jobId){
        const response=await api('/home/node-edits',task.request);
        if(response.clip_id!==task.clipId||!response.signature)throw new Error('镜头修改回执不完整');
        task.jobId=response.job_id;task.signature=response.signature;task.status='running';store();paintInspector();
      }
      await track({job_id:task.jobId},job=>finishNodeTask(task,job));
      const job=S.jobs.find(item=>item.id===task.jobId);if(job&&!['queued','running'].includes(job.status))finishNodeTask(task,job);
    }catch(e){
      task.status=!task.jobId&&e.status>=400&&e.status<500?'failed':'needs_check';
      task.message=task.status==='failed'?(e.message||'镜头修改未提交'):'暂时无法查询，可继续查询这次修改。';
    }finally{task.busy=false;store();if(S.route==='create'){paintInspector();paintActions()}}
  }
  function finishNodeTask(task,job){
    if(!task||task.jobId!==job.id||!['running','submitting','needs_check'].includes(task.status)||['queued','running'].includes(job.status))return;
    if(job.status!=='succeeded'){
      task.status='failed';task.message=job.error||'这次修改未完成，请重试';
    }else{
      const result=job.result,b=H.branches.find(item=>item.id===task.branchId),index=b?.clips.findIndex(c=>c.id===task.clipId)??-1;
      if(index<0||!nodeTaskCurrent(task)||H.pending?.branchId===task.branchId||result?.clip_id!==task.clipId||result?.signature!==task.signature||result?.clip?.id!==task.clipId){
        task.status='stale';task.message='内容已变化，未覆盖当前镜头。请重新提交。';
      }else{
        remember();b.clips[index]=copy(result.clip);H.request=null;task.status='succeeded';task.message=result.explanation||'已应用到这个镜头';
        H.example=H.branches.every(item=>item.clips.every(c=>sampleNames.has(asset(c.asset_id)?.name)));
        if(S.route==='create'){paintGraph();paintPreview()}
      }
    }
    store();if(S.route==='create'){paintInspector();paintActions()}
  }
  function finish(job){
    const pending=H.pending;
    if(!pending||pending.jobId!==job.id||['queued','running'].includes(job.status))return;
    if(job.status==='succeeded'&&job.result?.url){
      H.previews[pending.branchId]={signature:pending.signature,branchName:pending.branchName,render:job.result};
      H.request=null;toast(`${pending.branchName}预览已完成`);
    }else{H.request=null;error(job.error||'预览未完成，可重新生成')}
    H.pending=null;store();
    if(S.route==='create'){paintActions();paintInspector();paintGraph();paintPreview();}
  }
  function onJobs(jobs){if(H.pending?.jobId){const job=jobs.find(item=>item.id===H.pending.jobId);if(job)finish(job)}for(const task of Object.values(H.nodeTasks)){const job=task.jobId&&jobs.find(item=>item.id===task.jobId);if(job)finishNodeTask(task,job)}}
  function bind(){
    H.root.addEventListener('submit',event=>{if(!event.target.matches('.hc-node-ai-form'))return;event.preventDefault();void applyNodeInstruction()});
    H.root.addEventListener('input',event=>{
      const field=event.target;
      if(field.name==='home-instruction'){
        const form=field.closest('.hc-node-ai-form'),id=form?.dataset.clipId;if(!id)return;
        H.instructions[id]=field.value;store();const task=H.nodeTasks[id];
        form.querySelector('[type=submit]').disabled=!!H.pending||['submitting','running'].includes(task?.status)||!field.value.trim()&&task?.status!=='needs_check';return;
      }
      if(field.name==='home-title'){H.title=field.value;H.request=null;store();return}
      if(!['home-start','home-duration','home-caption'].includes(field.name)||!clip())return;
      const id=clip().id;H.fieldDrafts[id]??={};H.fieldDrafts[id][field.name]=field.value;
      H.inputRevisions[H.active]=(H.inputRevisions[H.active]||0)+1;H.request=null;store();paintPreview();
    });
    H.root.addEventListener('click',event=>{
      if(performance.now()<H.suppressClickUntil&&event.detail!==0){event.preventDefault();event.stopPropagation();return}
      const filter=event.target.closest('[data-home-filter]');if(filter){H.materialFilter=filter.dataset.homeFilter;paintAssets();return}
      const button=event.target.closest('[data-home-action]');if(!button)return;
      const action=button.dataset.homeAction;
      if(action==='pick'){H.picked=button.dataset.assetId;store();paintAssets();paintInspector();paintGraph();$('.hc-drop-feedback').textContent=`已选 ${displayName(asset(H.picked))}`;return}
      if(action==='branch'){H.editorOpen=false;H.active=button.dataset.branchId;H.clipId=branch().clips[0]?.id||null;store();paintGraph();paintPreview();paintActions();return}
      if(action==='node'){
        const node=H.forest.nodes.find(n=>n.id===button.dataset.nodeId);if(!node)return;
        const id=node.planIds.includes(H.active)?H.active:node.planIds[0],target=H.branches.find(b=>b.id===id);
        choose(id,target.clips[node.depth]?.id);requestAnimationFrame(()=>{const card=$('.hc-node.is-expanded'),view=$('.hc-graph-scroll');if(card&&view){const r=card.getBoundingClientRect(),v=view.getBoundingClientRect();if(r.right>v.right-20)view.scrollLeft+=(r.right-v.right)+24;if(r.left<v.left+20)view.scrollLeft-=v.left-r.left+24;if(r.bottom>v.bottom-20)view.scrollTop+=r.bottom-v.bottom+24;if(r.top<v.top+20)view.scrollTop-=v.top-r.top+24}});return;
      }
      if(action==='collapse'){H.editorOpen=false;H.zoomMode='fit';store();paintGraph();return}
      if(action==='zoom-in')return setZoom(H.zoom+.1);
      if(action==='zoom-out')return setZoom(H.zoom-.1);
      if(action==='fit')return fitCanvas();
      if(action==='focus'){H.focusMode=!H.focusMode;H.root.classList.toggle('is-focused',H.focusMode);button.setAttribute('aria-pressed',String(H.focusMode));button.setAttribute('aria-label',H.focusMode?'收起剪辑桌面':'展开剪辑桌面');requestAnimationFrame(()=>H.zoomMode==='fit'&&fitCanvas());return}
      if(action==='insert-picked'){if(!H.picked){toast('先在左侧选一份素材');return}return append(H.picked,Number(button.dataset.index))}
      if(action==='confirm')return void confirm();
      if(action==='replace')return replace(H.picked);
      if(action==='add-picked')return append(H.picked);
      if(action==='move-back')return move(branch().clips.indexOf(clip()),branch().clips.indexOf(clip())-1);
      if(action==='move-next')return move(branch().clips.indexOf(clip()),branch().clips.indexOf(clip())+1);
      if(action==='remove')return edit(()=>{branch().clips=branch().clips.filter(c=>c.id!==clip()?.id);H.clipId=branch().clips[0]?.id||null});
      if(action==='undo'&&H.undo.length&&!H.pending){Object.assign(H,H.undo.pop());H.request=null;store();paintGraph();paintInspector();paintPreview();paintActions();}
    });
    H.root.addEventListener('change',event=>{
      const field=event.target,c=clip(),a=asset(c?.asset_id);
      if(field.name==='home-source'){replace(field.value);return}
      if(field.name==='home-title'){H.title=field.value.trim()||'我的新片';H.request=null;store();return}
      if(field.name==='home-ratio'){H.ratio=field.value;H.request=null;store();paintGraph();paintPreview();return}
      if(!c||!['home-start','home-duration','home-caption'].includes(field.name))return;
      const start=field.name==='home-start'?Number(field.value):c.start,duration=field.name==='home-duration'?Number(field.value):c.duration;
      if(field.value.trim()===''&&field.name!=='home-caption'||!Number.isFinite(start+duration)||start<0||duration<.2||duration>600||a?.kind==='video'&&start+duration>Number(a.duration)+.02){toast('入点或时长超出素材范围',true);return}
      if(H.fieldDrafts[c.id]){delete H.fieldDrafts[c.id][field.name];if(!Object.keys(H.fieldDrafts[c.id]).length)delete H.fieldDrafts[c.id]}
      edit(()=>{c.start=a?.kind==='image'?0:start;c.duration=duration;c.end=c.start+duration;if(field.name==='home-caption')c.caption=field.value});
      paintPreview();
    });
    H.interactions?.destroy();
    H.interactions=createHomeInteractions({root:H.root,getState:()=>({pending:!!H.pending,branchId:H.active,clips:branch().clips,zoom:H.zoom}),onDragState:state=>{H.drag=state.active?{active:true}:null;if(state.active){all('.hc-node,.hc-branch').forEach(n=>n.getAnimations().forEach(a=>a.finish()));H.suppressClickUntil=Infinity}else H.suppressClickUntil=performance.now()+250},onFeedback:message=>{const n=$('.hc-drop-feedback');if(n)n.textContent=message||'本次草稿'},onDrop:drop=>{
      if(H.pending||drop.type==='asset'&&(!asset(drop.assetId)||drop.mode==='insert'&&branch().clips.length>=12))return false;
      H.drag={active:true};H.zoomMode='manual';
      if(drop.type==='asset'){
        if(drop.mode==='replace'){H.clipId=drop.targetClipId;replace(drop.assetId)}else append(drop.assetId,drop.index);
      }else{
        const from=branch().clips.findIndex(c=>c.id===drop.clipId),to=drop.index-(from<drop.index?1:0);move(from,Math.min(branch().clips.length-1,to));
      }
      H.drag=null;return {clipId:H.clipId};
    }});
  }
  function leave(){all('video').forEach(player=>player.pause());H.interactions?.destroy();H.interactions=null;H.resizeObserver?.disconnect();H.resizeObserver=null;H.drag=null;H.focusMode=false}
  function canAcceptAsset(){initialize();return !H.pending&&branch().clips.length<12}
  function acceptAsset(assetId){
    initialize();const a=asset(assetId);if(!a||!canAcceptAsset())return false;
    edit(()=>{const c=newClip(a,a.duration||2);branch().clips.push(c);H.clipId=c.id;H.example=false;H.editorOpen=false;H.zoomMode='fit'});return true;
  }
  return {render,onJobs,leave,canAcceptAsset,acceptAsset};
}
