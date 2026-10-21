import {createCandidateGraph} from './candidate-graph.js?v=20261020-meter3';
const platforms = {xiaohongshu:'小红书',douyin:'抖音',channels:'视频号'};
const metricNames = {views:'播放',likes:'点赞',comments:'评论',saves:'收藏',shares:'分享',recommends:'推荐',completion_rate:'完播率',bounce_2s:'2 秒跳出',avg_watch_seconds:'平均观看'};
const unknownReasons={not_observed:'尚未采集到此指标',zero_may_mean_unavailable:'平台暂未提供，当前零值尚未确认',unit_unknown:'原始数值的单位尚未确认',out_of_range:'原始数值超出有效范围'};
const dateText = value => {
  if(!value)return '时间未知';
  const date=new Date(value);
  return Number.isNaN(date.getTime())?String(value):date.toLocaleString('zh-CN',{year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false});
};

export function createDataWorkflow(ctx){
  const {S,api,esc,fmt,toast,run,track}=ctx;
  const $=(selector,root=document)=>root.querySelector(selector);
  const $$=(selector,root=document)=>[...root.querySelectorAll(selector)];
  const D={
    referenceIds:new Set(JSON.parse(localStorage.getItem('video-studio-case-references')||'[]')),
    cases:new Map(),details:new Map(),status:null,homeQuery:{platform:'',account:'',query:''},workspaceId:null,workspaceSequence:0,
    homeResults:null,homeSequence:0,historyOpen:false,historyLoading:null,projectId:null,tab:'candidates',decisions:null,
    batches:new Map(),batchId:null,expandedPlan:null,reason:'',bindings:null,
    publicationQuery:{platform:'',account:'',query:''},publicationResults:null,
    selectedPublication:null,selectedRender:null,pendingCandidate:null,projectSequence:0,candidateCount:3,
  };
  const platformName=value=>platforms[value]||value||'平台未知';
  const sourceText=source=>source?.label||source?.kind||'来源未标注';
  const shortError=error=>esc(error.message||'暂时无法读取，请重试。');
  const caseLabel=item=>item?.title||'未命名发布记录';
  function publicationCopy(text,id,tag='h3'){
    const value=String(text);
    const expandable=Array.from(value).length>40||value.split(/\r?\n/).length>2;
    return `<div class="publication-copy"><${tag} class="publication-copy-text" id="${esc(id)}">${esc(value)}</${tag}>${expandable?`<button type="button" class="copy-toggle" data-dw-action="copy-toggle" aria-controls="${esc(id)}" aria-expanded="false">展开文案</button>`:''}</div>`;
  }
  const renderLabel=render=>{
    const timestamp=render.created_at?new Date(render.created_at):null;
    const time=timestamp&&!Number.isNaN(timestamp.getTime())?timestamp.toLocaleString('zh-CN',{year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false}):'生成时间未知';
    return `成片 v${render.version} · ${fmt(render.duration)} · ${time}`;
  };
  const safeLink=value=>{
    if(!value)return '';
    try{const url=new URL(value,location.origin);return ['http:','https:'].includes(url.protocol)?esc(url.href):''}catch{return ''}
  };

  function metricText(metric){
    if(metric?.unknown_reason==='unit_unknown'){
      const raw=metric.raw??metric.source_value;
      if(typeof raw==='string'||typeof raw==='number')return `${raw}（单位待核）`;
    }
    if(!metric||metric.unknown||metric.value===null||metric.value===undefined)return '未知';
    const n=Number(metric.value);
    if(!Number.isFinite(n))return '未知';
    const value=metric.unit==='ratio'?`${(n*100).toLocaleString('zh-CN',{maximumFractionDigits:2})}%`:
      metric.unit==='seconds'?`${n.toLocaleString('zh-CN',{maximumFractionDigits:2})} 秒`:
      n.toLocaleString('zh-CN',{maximumFractionDigits:2});
    return `${metric.approximate?'约 ':''}${value}`;
  }
  function metricsHTML(metrics={},compact=false){
    const keys=compact?['views','likes','comments','shares']:Object.keys(metricNames);
    return `<dl class="case-metrics ${compact?'compact':''}">${keys.map(key=>{
      const m=metrics[key],unknown=!m||m.unknown||m.value==null;
      return `<div><dt>${metricNames[key]}</dt><dd class="${unknown?'unknown':''}" title="${esc(unknownReasons[m?.unknown_reason]||m?.source_name||'尚未采集到此指标')}">${esc(metricText(m))}</dd></div>`;
    }).join('')}</dl>`;
  }
  function caseMeta(item){
    return `<span>${esc(platformName(item.platform))}</span><span>${esc(item.account||'账号未知')}</span><span>发布 ${esc(dateText(item.published_at))}</span>`;
  }
  function observationHTML(item){
    return `<div class="observation"><div class="observation-heading"><strong>${esc(dateText(item.captured_at))}</strong><span>${item.age_hours==null?'发布间隔未知':`发布后 ${Number(item.age_hours).toLocaleString('zh-CN',{maximumFractionDigits:1})} 小时`}</span><span>${esc(sourceText(item.source))}</span></div>${metricsHTML(item.metrics)}</div>`;
  }
  async function detail(id){
    const result=await api('/history/'+encodeURIComponent(id));
    D.details.set(id,result);
    if(result.case)D.cases.set(id,result.case);
    return result;
  }
  function historyDetailHTML(result){
    const item=result.case;
    return `<div class="case-detail-heading"><h3>历次采集</h3>${safeLink(item?.source_url)?`<a class="small-link" href="${safeLink(item.source_url)}" target="_blank" rel="noreferrer">查看原发布</a>`:''}</div>${(result.observations||[]).map(observationHTML).join('')||'<p class="muted">尚无采集记录。</p>'}`;
  }
  function caseRow(item,purpose){
    const selected=purpose==='publication'?D.selectedPublication===item.id:purpose==='history'?false:D.referenceIds.has(item.id);
    return `<article class="history-case ${selected?'selected':''}" data-case-id="${esc(item.id)}">
      <div class="case-topline"><div class="case-meta">${caseMeta(item)}</div>${purpose==='publication'&&item.binding?'<span class="tag">已有成片关联</span>':''}</div>
      <div class="case-main"><div>${publicationCopy(caseLabel(item),`case-copy-${item.id}`)}<p class="case-captured">采集 ${esc(dateText(item.captured_at))}<span>${esc(sourceText(item.source))}</span></p></div>${metricsHTML(item.metrics,true)}</div>
      <div class="case-actions"><button class="button small quiet" data-dw-action="case-detail" data-id="${esc(item.id)}" aria-expanded="false">采集记录</button>${purpose==='history'?'':`<button class="button small ${selected?'selected-action':''}" data-dw-action="${purpose==='publication'?'publication-select':'case-select'}" data-id="${esc(item.id)}" aria-pressed="${selected}">${selected?(purpose==='publication'?'已选发布记录':'已加入参考'):(purpose==='publication'?'选这条发布':'加入参考')}</button>`}</div>
      <div class="case-detail" data-case-detail="${esc(item.id)}" hidden></div>
    </article>`;
  }
  function filterHTML(purpose){
    const q=purpose==='home'?D.homeQuery:D.publicationQuery;
    return `<form id="${purpose==='home'?'history-search-form':'publication-search-form'}" class="history-filter" data-dw-form="${purpose==='home'?'history-search':'publication-search'}"><label><span class="sr-only">平台</span><select name="platform"><option value="">全部平台</option>${Object.entries(platforms).map(([value,label])=>`<option value="${value}" ${q.platform===value?'selected':''}>${label}</option>`).join('')}</select></label><label><span class="sr-only">账号</span><input name="account" list="history-account-options" value="${esc(q.account)}" placeholder="账号"></label><label class="history-keyword"><span class="sr-only">关键词</span><input name="query" value="${esc(q.query)}" placeholder="搜索标题或内容"></label><button class="button" type="submit">查找</button></form>`;
  }
  function updateReferenceCount(){
    $$('[data-case-reference-count]').forEach(el=>el.textContent=D.referenceIds.size?`历史案例 (${D.referenceIds.size})`:'历史案例');
    const el=$('#case-reference-summary');
    if(el)el.innerHTML=D.referenceIds.size?`<span>已选 ${D.referenceIds.size} 条历史参考</span><button type="button" data-dw-action="case-clear">清空参考</button>`:'';
    const count=$('#history-selected-count');
    if(count)count.textContent=D.referenceIds.size?`已选 ${D.referenceIds.size} 条参考`:'';
  }
  function persistReferences(){
    localStorage.setItem('video-studio-case-references',JSON.stringify([...D.referenceIds]));
    updateReferenceCount();
  }
  function homeHTML(){
    return `<section class="content-strip history-section" id="history-cases"><div class="strip-heading"><div class="history-title"><h2>历史案例</h2><span id="history-selected-count" class="muted"></span></div><button class="button small" data-dw-action="history-toggle" aria-controls="history-browser" aria-expanded="${D.historyOpen}">${D.historyOpen?'收起案例':'浏览案例'}</button></div><div id="history-browser" ${D.historyOpen?'':'hidden'}><div class="history-source-row"><p class="muted history-status" id="history-status">正在读取历史记录</p><button class="button small quiet" data-dw-action="history-sync">更新采集记录</button></div>${filterHTML('home')}<div id="history-results" aria-live="polite"><p class="muted">正在读取案例…</p></div><datalist id="history-account-options"></datalist></div></section>`;
  }
  function showHistoryStatus(status){
    D.status=status;
    const el=$('#history-status');
    if(!el)return;
    el.textContent=status.error?status.error:
      `${status.cases_count??0} 条发布记录${status.latest_captured_at?`　最近采集 ${dateText(status.latest_captured_at)}`:''}`;
  }
  function nextOffset(result){
    if(result?.next_offset!==undefined)return result.next_offset;
    return (result?.total||0)>(result?.cases?.length||0)?result.cases.length:null;
  }
  function moreHTML(result,purpose){
    return nextOffset(result)!==null?`<div class="history-more"><button class="button" data-dw-action="history-more" data-purpose="${purpose}">加载更多</button></div>`:'';
  }
  function resultCount(result){
    return `已显示 ${result.cases?.length||0} / ${result.total??result.cases?.length??0} 条记录`;
  }
  async function searchHistory(purpose,append=false){
    const isHome=purpose==='home',previous=isHome?D.homeResults:D.publicationResults;
    const query=append?(previous?._query||{}):(isHome?D.homeQuery:D.publicationQuery);
    const offset=append?nextOffset(previous):0;
    if(offset===null)return;
    const sequence=isHome?++D.homeSequence:++D.projectSequence;
    const params=new URLSearchParams({...query,limit:'30',offset:String(offset)});
    const result=await api((isHome&&D.workspaceId?`/workspaces/${encodeURIComponent(D.workspaceId)}/history?`:'/history?')+params.toString());
    if(sequence!==(isHome?D.homeSequence:D.projectSequence))return;
    (result.cases||[]).forEach(item=>D.cases.set(item.id,item));
    const newCases=(result.cases||[]).filter(item=>!append||!(previous?.cases||[]).some(old=>old.id===item.id));
    result.cases=append?[...(previous?.cases||[]),...newCases]:(result.cases||[]);
    result._query={...query};
    const accounts=result.facets?.accounts||[];
    const list=$('#history-account-options');
    if(list)list.innerHTML=accounts.map(value=>`<option value="${esc(value)}"></option>`).join('');
    if(isHome)D.homeResults=result;else D.publicationResults=result;
    const host=$(isHome?'#history-results':'#publication-search-results');
    if(host){
      if(append){
        $('.history-more',host)?.remove();
        host.insertAdjacentHTML('beforeend',newCases.map(item=>caseRow(item,isHome?'history':'publication')).join('')+moreHTML(result,purpose));
        const count=$('.result-count',host);if(count)count.textContent=resultCount(result);
      }else host.innerHTML=`<div class="result-count">${resultCount(result)}</div>${result.cases.length?result.cases.map(item=>caseRow(item,isHome?'history':'publication')).join(''):'<div class="data-empty">没有匹配记录。</div>'}${moreHTML(result,purpose)}`;
    }
  }
  async function renderHistory(){
    const sequence=++D.workspaceSequence;
    const route=S.route,id=route.split('/')[1];
    $('#app').innerHTML='<section class="section"><div class="section-head"><h1>历史数据分析</h1></div><div class="loading">读取发布记录</div></section>';
    try{
      const {workspaces}=await api('/workspaces');
      if(sequence!==D.workspaceSequence||S.route!==route)return;
      if(!id){
        $('#app').innerHTML=`<section class="section business-workspaces"><div class="section-head"><h1>历史数据分析</h1></div><div class="business-workspace-grid">${workspaces.map(w=>`<a class="business-workspace" href="#history/${esc(w.id)}"><div class="workspace-mark">${esc(w.name.slice(0,1))}</div><h2>${esc(w.name)}</h2><p>${w.cases_count} 条发布记录</p><div class="workspace-platforms">${w.platforms.map(platformName).map(esc).join(' / ')}</div><span class="workspace-open">查看数据 <span aria-hidden="true">↗</span></span></a>`).join('')}</div></section>`;
        return;
      }
      const workspace=workspaces.find(w=>w.id===id);
      if(!workspace)throw new Error('项目不存在。');
      if(D.workspaceId!==id){D.homeResults=null;D.homeQuery={platform:'',account:'',query:''}}
      D.workspaceId=id;D.historyOpen=true;
      $('#app').innerHTML=`<section class="section workspace-history"><a class="back-link" href="#history">‹ 历史数据分析</a><div class="section-head"><div><h1>${esc(workspace.name)}</h1><p class="muted">发布数据</p></div><button class="button" data-dw-action="history-sync">更新采集记录</button></div><div id="history-cases"><p class="muted history-status" id="history-status"></p>${filterHTML('home')}<div id="history-results"><div class="loading">读取案例</div></div><datalist id="history-account-options"></datalist></div></section>`;
      const [status]=await Promise.all([api('/history/status'),searchHistory('home')]);
      if(sequence===D.workspaceSequence&&S.route===route)showHistoryStatus(status);
    }catch(error){
      if(sequence===D.workspaceSequence&&S.route===route)$('#app').innerHTML=`<section class="section"><a class="back-link" href="#history">‹ 历史数据分析</a><div class="data-error">${shortError(error)}</div></section>`;
    }
  }
  function mountHome(){
    if(!$('#history-cases')){
      const sections=$$('.content-strip');
      sections.at(-1)?.insertAdjacentHTML('beforebegin',homeHTML());
    }
    updateReferenceCount();
    const existing=D.homeResults;
    if(existing)$('#history-results').innerHTML=`<div class="result-count">${resultCount(existing)}</div>${existing.cases.map(item=>caseRow(item,'reference')).join('')}${moreHTML(existing,'home')}`;
    if(D.status)showHistoryStatus(D.status);
    if(D.historyOpen)void ensureHistoryLoaded();
  }
  async function ensureHistoryLoaded(){
    if(D.homeResults&&D.status)return;
    if(D.historyLoading)return D.historyLoading;
    D.historyLoading=(async()=>{
      try{
        const [status]=await Promise.all([api('/history/status'),searchHistory('home')]);
        showHistoryStatus(status);
      }catch(error){const el=$('#history-status');if(el)el.textContent=error.message}
      finally{D.historyLoading=null}
    })();
    return D.historyLoading;
  }
  function setHistoryOpen(open,scroll=false){
    D.historyOpen=open;
    const browser=$('#history-browser'),button=$('[data-dw-action=history-toggle]');
    if(!browser||!button)return;
    browser.hidden=!open;
    button.setAttribute('aria-expanded',String(open));
    button.textContent=open?'收起案例':'浏览案例';
    if(open){
      void ensureHistoryLoaded();
      if(scroll)$('#history-cases').scrollIntoView({block:'start',behavior:'auto'});
    }
  }

  function projectHTML(){
    return `<section class="project-data-workspace" id="project-data-workspace" data-project-id="${esc(S.project.id)}"><div class="data-tabs" role="tablist" aria-label="作品记录">${[['candidates','候选成片'],['decisions','选择与修改'],['publications','发布表现']].map(([id,label])=>`<button role="tab" type="button" data-dw-action="project-tab" data-id="${id}" aria-selected="${D.tab===id}" class="${D.tab===id?'active':''}">${label}</button>`).join('')}</div><div id="project-data-content"></div></section>`;
  }
  function mountProject(){
    const project=S.project;
    if(!project)return;
    if(D.projectId!==project.id){
      D.projectId=project.id;D.tab='candidates';D.decisions=null;D.bindings=null;D.batchId=null;
      D.expandedPlan=null;D.reason='';D.selectedPublication=null;D.selectedRender=null;D.publicationResults=null;D.candidateCount=project.latest_candidate_batch_count||3;D.batchId=project.latest_candidate_batch_id||null;D.pendingCandidate=project.active_candidate_job_id||null;
    }
    if($('#project-data-workspace'))return;
    $('.project-workspace')?.insertAdjacentHTML('beforeend',projectHTML());
    if(!$('#history-account-options'))$('#project-data-workspace')?.insertAdjacentHTML('beforeend','<datalist id="history-account-options"></datalist>');
    renderProjectTab();
    if(!D.decisions)loadDecisions().catch(showPanelError);
  }
  function showPanelError(error){
    const host=$('#project-data-content');
    if(host)host.innerHTML=`<p class="data-error">${shortError(error)}</p><button class="button small" data-dw-action="data-retry">重新读取</button>`;
  }
  function currentBatch(){
    return D.batches.get(D.batchId)||null;
  }
  function batchPlans(result){
    const lookup=new Map((result?.plans||[]).map(plan=>[plan.id,plan]));
    return (result?.batch?.display_order||result?.batch?.plan_ids||[]).map(id=>lookup.get(id)).filter(Boolean);
  }
  function viewedIds(result){
    return [...new Set((result?.events||[]).filter(e=>e.event_type==='viewed').map(e=>e.plan_id))];
  }
  async function loadDecisions(){
    const id=D.projectId;
    if(!id)return;
    const result=await api(`/projects/${id}/decisions`);
    if(D.projectId!==id)return;
    D.decisions=result;
    for(const batch of result.batches||[]){
      const record={batch,plans:batch.plans||[],previews:batch.previews||{},events:batch.events||[]};
      D.batches.set(batch.id,record);
    }
    if(!D.batchId&&result.batches?.length)D.batchId=result.batches[0].id;
    if(['candidates','decisions'].includes(D.tab))renderProjectTab();
  }
  const candidateGraph=createCandidateGraph({S,esc,fmt,
    onView:async planId=>{await candidateEvent('viewed',planId);},
    onSelect:planId=>adoptCandidate(planId),
    onRetry:planId=>retryCandidatePreviews(planId),
    onError:error=>toast(error.message||'候选预览暂时不可用',true),
  });
  function candidateHeadHTML(){
    const result=currentBatch(),batch=result?.batch;
    const busy=!!D.pendingCandidate||['queued','running'].includes(batch?.status);
    return `<div class="data-panel-head"><div><h2>候选成片</h2>${batch?`<p class="muted">${esc(dateText(batch.created_at))}${batch.decision_status==='selected'?'　已采用':batch.decision_status==='rejected_all'?'　本批未采用':''}</p>`:''}</div><div class="actions">${(D.decisions?.batches?.length||0)>1?`<select id="candidate-batch-select" aria-label="候选组">${D.decisions.batches.map(b=>`<option value="${esc(b.id)}" ${b.id===D.batchId?'selected':''}>${esc(dateText(b.created_at))}${b.id===D.batchId?' · 当前':''}</option>`).join('')}</select>`:''}<select id="candidate-count-select" aria-label="候选数量" ${busy?'disabled':''}>${[3,4,5].map(n=>`<option value="${n}" ${D.candidateCount===n?'selected':''}>${n} 条候选</option>`).join('')}</select><button class="button primary" data-dw-action="generate-candidates" ${busy?'disabled':''}>${busy?'正在准备预览':batch?'再生成一组':'生成候选成片'}</button></div></div>${batch?.error?`<p class="data-error">${esc(batch.error)}</p>`:''}`;
  }
  function candidateFooterHTML(){
    const result=currentBatch(),batch=result?.batch;
    if(!(result?.plans||[]).length)return '';
    return `<div class="candidate-decision"><label>选择理由（可选）<textarea id="candidate-reason" placeholder="留下这次取舍的原因">${esc(D.reason)}</textarea></label>${(result.events||[]).some(event=>event.event_type==='selected')?'':`<button class="button" data-dw-action="candidate-reject" ${batch?.status!=='succeeded'?'disabled':''}>本批都不采用</button>`}</div>`;
  }
  function candidatesHTML(){return `<div id="candidate-panel"><div id="candidate-panel-head">${candidateHeadHTML()}</div><div id="candidate-graph-host"></div><div id="candidate-panel-footer">${candidateFooterHTML()}</div></div>`;}
  async function adoptCandidate(id){
    const projectId=D.projectId,batchId=D.batchId,result=currentBatch(),reason=D.reason;
    const onProject=()=>D.projectId===projectId&&S.route===`project/${projectId}`&&S.project?.id===projectId;
    if(S.project?.plan)await ctx.savePlan();
    await candidateEvent('selected',id,{result,reason});
    if(onProject()){await refreshBatch(batchId);if(onProject())await ctx.refreshProjectResult(projectId,false);if(onProject())await loadDecisions();}
    toast('已采用这条成片');
  }
  async function retryCandidatePreviews(planId){
    const projectId=D.projectId,batchId=D.batchId;
    const response=await api(`/candidate-batches/${batchId}/retry-previews`,planId?{plan_ids:[planId]}:{});
    if(D.projectId===projectId&&D.batchId===batchId&&S.route===`project/${projectId}`){D.pendingCandidate=response.job_id;await refreshBatch(batchId);}
    await track(response,async()=>{if(D.projectId===projectId&&S.route===`project/${projectId}`){D.pendingCandidate=null;await refreshBatch(batchId);await loadDecisions();}toast('候选预览已完成')});
    toast('正在重新渲染预览');
  }
  function decisionsHTML(){
    const result=D.decisions;
    if(!result)return '<p class="muted">正在读取选择与修改记录…</p>';
    const planNames=new Map((result.batches||[]).flatMap(b=>(b.plans||[]).map(p=>[p.id,p.candidate_strategy?.label||p.title])));
    const entries=[
      ...(result.events||[]).map(e=>({...e,entry:'event'})),
      ...(result.revisions||[]).map(r=>({...r,entry:'revision'})),
    ].sort((a,b)=>String(b.created_at||'').localeCompare(String(a.created_at||'')));
    return `<div class="data-panel-head"><h2>选择与修改</h2><button class="button small" data-dw-action="refresh-decisions">刷新记录</button></div>${entries.length?`<div class="decision-log">${entries.map(entry=>{
      if(entry.entry==='revision')return `<article class="decision-entry"><div class="decision-date">${esc(dateText(entry.created_at))}</div><div><h3>方案 v${entry.version} ${entry.origin?.source==='candidate'?'候选方案':'修改记录'}</h3><p>${esc(entry.summary||'')}</p>${entry.changes?.length?`<ul>${entry.changes.map(change=>`<li>${esc(change)}</li>`).join('')}</ul>`:''}</div></article>`;
      const name={viewed:'查看预览',selected:'采用成片',rejected_all:'本批都不采用'}[entry.event_type]||entry.event_type;
      return `<article class="decision-entry"><div class="decision-date">${esc(dateText(entry.created_at))}</div><div><h3>${esc(name)}${entry.plan_id?`：${esc(planNames.get(entry.plan_id)||'候选方案')}`:''}</h3>${entry.reason?`<p>${esc(entry.reason)}</p>`:''}<p class="decision-context">展示顺序：${(entry.displayed_plan_ids||[]).map((id,index)=>`${index+1}. ${planNames.get(id)||'方案'}`).map(esc).join(' / ')||'未记录'}　已查看 ${(entry.viewed_plan_ids||[]).length} 个</p></div></article>`;
    }).join('')}</div>`:'<div class="data-empty">暂无选择与修改记录。</div>'}`;
  }
  function bindingHTML(binding){
    const item=binding.publication||D.cases.get(binding.publication_id)||{};
    return `<article class="publication-binding" data-binding-id="${esc(binding.id)}"><div class="binding-heading"><div><span class="tag blue">成片 v${binding.version??'?'}</span>${publicationCopy(caseLabel(item),`binding-copy-${binding.id}`)}<div class="case-meta">${caseMeta(item)}</div></div>${binding.status==='active'?`<button class="button small quiet" data-dw-action="binding-remove" data-id="${esc(binding.id)}">撤销关联</button>`:'<span class="tag">已撤销</span>'}</div><p class="case-captured">确认关联 ${esc(dateText(binding.confirmed_at))}　采集 ${esc(dateText(item.captured_at))}</p>${metricsHTML(item.metrics)}<button class="button small quiet" data-dw-action="binding-detail" data-id="${esc(binding.publication_id)}">查看历次表现</button><div class="binding-observations" hidden></div></article>`;
  }
  function publicationsHTML(){
    const renders=S.project?.renders||[];
    if(!D.selectedRender&&renders.length)D.selectedRender=renders.find(r=>r.plan_id===S.project.plan?.id)?.id||renders[0].id;
    const selectedRender=renders.find(r=>r.id===D.selectedRender),item=D.cases.get(D.selectedPublication);
    return `<div class="data-panel-head"><div><h2>发布表现</h2><p class="muted">${(D.bindings||[]).filter(b=>b.status==='active').length} 条已关联发布</p></div><button class="button small" data-dw-action="refresh-bindings">刷新表现</button></div><div id="publication-bindings">${(D.bindings||[]).map(bindingHTML).join('')}</div>${renders.length?`<details class="publication-connector" ${D.selectedPublication?'open':''}><summary>关联平台发布记录</summary><label class="render-selector">选择确切成片<select id="binding-render">${renders.map(render=>`<option value="${esc(render.id)}" ${render.id===D.selectedRender?'selected':''}>${esc(renderLabel(render))}</option>`).join('')}</select></label>${filterHTML('publication')}<div id="publication-search-results">${D.publicationResults?`<div class="result-count">${resultCount(D.publicationResults)}</div>${(D.publicationResults.cases||[]).map(c=>caseRow(c,'publication')).join('')}${moreHTML(D.publicationResults,'publication')}`:'<p class="muted">尚未选择发布记录。</p>'}</div><div id="publication-confirm">${confirmationHTML(selectedRender,item)}</div></details>`:'<div class="data-empty">还没有成片。</div>'}`;
  }
  function confirmationHTML(render,item){
    if(!render||!item)return '';
    return `<div class="publication-confirm"><div><strong>${esc(renderLabel(render))}</strong><span>关联到</span><strong>${esc(platformName(item.platform))} / ${esc(item.account||'账号未知')}</strong>${publicationCopy(caseLabel(item),`confirm-copy-${item.id}`,'p')}</div><button class="button primary" data-dw-action="binding-confirm">确认关联</button></div>`;
  }
  async function loadBindings(){
    const id=D.projectId;
    const result=await api(`/projects/${id}/publications`);
    if(D.projectId!==id)return;
    D.bindings=result.bindings||[];
    if(D.tab==='publications')renderProjectTab();
  }
  function renderProjectTab(){
    const host=$('#project-data-content');
    if(!host)return;
    $$('.data-tabs button').forEach(button=>{const selected=button.dataset.id===D.tab;button.classList.toggle('active',selected);button.setAttribute('aria-selected',String(selected))});
    if(D.tab==='candidates'){
      if(!$('#candidate-panel',host))host.innerHTML=candidatesHTML();
      else for(const [selector,markup] of [['#candidate-panel-head',candidateHeadHTML()],['#candidate-panel-footer',candidateFooterHTML()]]){
        const node=$(selector,host);if(node&&!node.contains(document.activeElement)&&node.innerHTML!==markup)node.innerHTML=markup;
      }
      const busy=!!D.pendingCandidate||['queued','running'].includes(currentBatch()?.batch?.status);
      const generate=$('[data-dw-action=generate-candidates]',host),count=$('#candidate-count-select',host);
      if(generate){generate.disabled=busy;generate.textContent=busy?'正在准备预览':currentBatch()?'再生成一组':'生成候选成片';}
      if(count)count.disabled=busy;
      candidateGraph.mount($('#candidate-graph-host',host),currentBatch()||{batch:{status:D.pendingCandidate?'running':'empty',count:D.candidateCount},plans:[],previews:{},events:[]});
    }else{candidateGraph.dispose?.();host.innerHTML=D.tab==='decisions'?decisionsHTML():publicationsHTML();}
  }
  async function refreshBatch(id){
    const result=await api(`/candidate-batches/${id}`);
    if(D.projectId!==result.batch?.project_id)return;
    D.batches.set(id,result);
    if(!D.batchId)D.batchId=id;
    if(D.batchId===id&&D.tab==='candidates')renderProjectTab();
    return result;
  }
  async function candidateEvent(type,planId,context={}){
    const result=context.result||currentBatch();
    if(!result)throw new Error('候选方案尚未准备完成。');
    const visualOrder=candidateGraph.displayedPlanIds||[],ids=batchPlans(result).map(p=>p.id);
    const displayed=visualOrder.length===ids.length&&visualOrder.every(id=>ids.includes(id))?visualOrder:ids;
    const payload={event_type:type,displayed_plan_ids:displayed,viewed_plan_ids:viewedIds(result),reason:(context.reason??D.reason).trim()};
    if(planId)payload.plan_id=planId;
    const event=await api(`/candidate-batches/${result.batch.id}/events`,payload);
    result.events=[...(result.events||[]),event.event||event];
    return event;
  }
  async function onJobs(jobs){
    if(!D.projectId||S.route!==`project/${D.projectId}`)return;
    const batch=currentBatch()?.batch;
    const batchJob=S.jobs.find(job=>job.id===(D.pendingCandidate||batch?.job_id||S.project?.active_candidate_job_id));
    if(D.batchId&&(D.pendingCandidate||['queued','running'].includes(batch?.status))){
      if(batchJob&&!['queued','running'].includes(batchJob.status))D.pendingCandidate=null;
      await refreshBatch(D.batchId);
    }
    const relevant=jobs.filter(job=>['candidates','revision','render','plan'].includes(job.kind));
    if(relevant.length){await loadDecisions();if(D.tab==='publications')await loadBindings();}
  }

  document.addEventListener('click',event=>{
    const button=event.target.closest('[data-dw-action]');
    if(!button)return;
    const {dwAction:action,id}=button.dataset;
    run(async()=>{
      if(action==='reference-open'){setHistoryOpen(true,true);return}
      if(action==='history-toggle'){setHistoryOpen(!D.historyOpen);return}
      if(action==='copy-toggle'){
        const title=$('.publication-copy-text',button.closest('.publication-copy'));
        const expanded=title.classList.toggle('expanded');
        button.setAttribute('aria-expanded',String(expanded));
        button.textContent=expanded?'收起文案':'展开文案';
        return;
      }
      if(action==='case-select'){
        if(D.referenceIds.has(id))D.referenceIds.delete(id);else D.referenceIds.add(id);
        persistReferences();button.setAttribute('aria-pressed',String(D.referenceIds.has(id)));button.textContent=D.referenceIds.has(id)?'已加入参考':'加入参考';button.closest('.history-case').classList.toggle('selected',D.referenceIds.has(id));return;
      }
      if(action==='case-clear'){D.referenceIds.clear();persistReferences();$$('[data-dw-action=case-select]').forEach(b=>{b.textContent='加入参考';b.setAttribute('aria-pressed','false');b.closest('.history-case').classList.remove('selected')});return}
      if(action==='case-detail'||action==='binding-detail'){
        const target=action==='case-detail'?$('.case-detail',button.closest('.history-case')):$('.binding-observations',button.closest('.publication-binding'));
        if(!target.hidden){target.hidden=true;button.setAttribute('aria-expanded','false');return}
        target.hidden=false;button.setAttribute('aria-expanded','true');target.innerHTML='<p class="muted">正在读取采集记录…</p>';
        try{target.innerHTML=historyDetailHTML(await detail(id))}catch(error){target.innerHTML=`<p class="data-error">${shortError(error)}</p>`}
        return;
      }
      if(action==='history-more'){await searchHistory(button.dataset.purpose,true);return}
      if(action==='history-sync'){const status=await api('/history/sync',{});showHistoryStatus(status);await searchHistory('home');toast(status.error?'采集未更新，保留已有记录':'采集记录已更新',!!status.error);return}
      if(action==='project-tab'){D.tab=id;renderProjectTab();if(id==='decisions')await loadDecisions();if(id==='publications')await loadBindings();return}
      if(action==='data-retry'||action==='refresh-decisions'){await loadDecisions();return}
      if(action==='refresh-bindings'){await loadBindings();return}
      if(action==='generate-candidates'){
        const projectId=S.project.id;
        const references=[...(S.project.reference_case_ids||[])];
        const onProject=()=>D.projectId===projectId&&S.route===`project/${projectId}`&&S.project?.id===projectId;
        if(S.project?.plan)await ctx.savePlan();
        const count=D.candidateCount;
        const result=await api(`/projects/${projectId}/candidates`,{count,reference_case_ids:references});
        if(onProject()){D.pendingCandidate=result.job_id;D.batchId=result.batch_id;await refreshBatch(result.batch_id)}
        await track(result,async job=>{if(onProject()){D.pendingCandidate=null;await refreshBatch(job.result?.batch_id||result.batch_id);await loadDecisions()}toast('候选成片已准备好')});
        toast('正在生成候选成片');return;
      }
      if(action==='candidate-select'){await adoptCandidate(id);return}
      if(action==='candidate-reject'){await candidateEvent('rejected_all');await refreshBatch(D.batchId);await loadDecisions();toast('已记录：本批都不采用');return}
      if(action==='publication-select'){
        D.selectedPublication=id;
        $$('#publication-search-results .history-case').forEach(row=>row.classList.toggle('selected',row.dataset.caseId===id));
        $$('[data-dw-action=publication-select]').forEach(b=>{b.textContent=b.dataset.id===id?'已选发布记录':'选这条发布';b.setAttribute('aria-pressed',String(b.dataset.id===id))});
        $('#publication-confirm').innerHTML=confirmationHTML((S.project.renders||[]).find(r=>r.id===D.selectedRender),D.cases.get(id));return;
      }
      if(action==='binding-confirm'){
        if(!D.selectedRender||!D.selectedPublication)throw new Error('请选择成片与发布记录。');
        await api(`/projects/${D.projectId}/publications`,{publication_id:D.selectedPublication,render_id:D.selectedRender,confirmed:true});
        D.selectedPublication=null;await loadBindings();toast('发布记录已关联到所选成片');return;
      }
      if(action==='binding-remove'){await api(`/projects/${D.projectId}/publications/${id}`,undefined,'DELETE');await loadBindings();toast('已撤销关联');return}
    },['case-select','case-clear','publication-select','project-tab','reference-open','history-toggle','copy-toggle'].includes(action)?null:button);
  });
  document.addEventListener('submit',event=>{
    const form=event.target,action=form.dataset.dwForm;
    if(!action)return;
    event.preventDefault();
    run(async()=>{
      const values=Object.fromEntries(new FormData(form));
      if(action==='history-search'){D.homeQuery=values;await searchHistory('home')}
      if(action==='publication-search'){D.publicationQuery=values;await searchHistory('publication')}
    },$('button[type=submit]',form));
  });
  document.addEventListener('input',event=>{
    if(event.target.id==='candidate-reason')D.reason=event.target.value;
    const action=event.target.closest('[data-dw-form]')?.dataset.dwForm;
    if(action==='history-search')D.homeQuery=Object.fromEntries(new FormData(event.target.form));
    if(action==='publication-search')D.publicationQuery=Object.fromEntries(new FormData(event.target.form));
  });
  document.addEventListener('change',event=>{
    if(event.target.id==='candidate-count-select')D.candidateCount=Number(event.target.value);
    if(event.target.id==='candidate-batch-select'){D.batchId=event.target.value;D.expandedPlan=null;D.reason='';run(()=>refreshBatch(D.batchId))}
    if(event.target.id==='binding-render'){D.selectedRender=event.target.value;$('#publication-confirm').innerHTML=confirmationHTML((S.project.renders||[]).find(r=>r.id===D.selectedRender),D.cases.get(D.selectedPublication))}
  });
  return {renderHistory,mountProject,onJobs,leaveProject:()=>candidateGraph.dispose?.(),referenceCaseIds:()=>[...D.referenceIds],referenceCount:()=>D.referenceIds.size};
}
