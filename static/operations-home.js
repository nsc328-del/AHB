import {PLATFORMS,WEEKDAYS,pad,parseDate,shiftDate,chinaToday,isDate,dayLabel,safeURL,chinaDateTime,prioritizeHotspots} from './operations-data.js?v=20261021-ops-seasons1';

const ACTIVE=new Set(['queued','running']);
const glyphs={arrow:'<path d="M5 12h14m-5-5 5 5-5 5"/>',plus:'<path d="M12 5v14M5 12h14"/>',edit:'<path d="m15 5 4 4M4 20l5-1L20 8a2.8 2.8 0 0 0-4-4L5 15z"/>',chevron:'<path d="m9 5 7 7-7 7"/>',external:'<path d="M14 4h6v6m0-6-9 9M9 5H4v15h15v-5"/>',refresh:'<path d="M20 7v5h-5M4 17v-5h5M5 8a8 8 0 0 1 13-3l2 3M4 16l2 3a8 8 0 0 0 13-3"/>',bowl:'<path d="M3 11h18c-1 6-4 9-9 9s-8-3-9-9ZM9 21h6M7 7c-3-3 3-3 0-6m5 6c-3-3 3-3 0-6m5 6c-3-3 3-3 0-6"/>'};
const icon=name=>`<svg viewBox="0 0 24 24" aria-hidden="true">${glyphs[name]||''}</svg>`;

export function createOperationsHome({api,track,esc,toast,onStart,usageSlot,onContentChange}) {
  let data=null,selectedDate=null,selectedMonth=null,activeId=null,view='week',editOpen=false,ideaDraft=null,contextDraft=null,contextOpen=false,lastRemoved=null,loadSequence=0,saving=null,busy=false,signalsExpanded=false;
  const jobs=new Map(),requests=new Map(),tracked=new Set(),finished=new Set();
  const root=()=>document.querySelector('.op-home');
  const isHere=()=>['','#operations'].includes(location.hash);
  const today=()=>data?.today||chinaToday();
  const year=()=>Number(data?.annual_plan?.year)||Number(today().slice(0,4));
  const ideas=()=>data?.ideas||[];
  const byDate=date=>ideas().filter(i=>i.date===date);
  const months=()=>data?.annual_plan?.months||[];
  const month=()=>months().find(m=>Number(m.month)===selectedMonth)||{month:selectedMonth,theme:'',focus:'',events:[]};
  const activeIdea=()=>ideaDraft?.value||byDate(selectedDate).find(i=>i.id===activeId)||byDate(selectedDate)[0]||null;
  const dirty=()=>!!(ideaDraft?.dirty||contextDraft?.dirty);
  const running=()=>[...jobs.values()].some(j=>ACTIVE.has(j.status));
  const buttonDisabled=()=>busy||running()?'disabled':'';
  const dateLabel=value=>value?String(value).slice(0,10):'日期未提供';
  const link=(url,title,cls='')=>safeURL(url)?`<a class="${cls}" href="${esc(safeURL(url))}" target="_blank" rel="noopener noreferrer">${esc(title)}${icon('external')}</a>`:`<span>${esc(title)}</span>`;
  function sourcesHTML(sources){return sources?.length?`<div class="op-source-links">${sources.map(s=>link(s.url,`${s.title||s.source||'来源'}${s.date?` · ${dateLabel(s.date)}`:''}`)).join('')}</div>`:''}
  function syncJobs(next){
    for(const j of [...(next.latest_jobs||[]),...(next.active_jobs||[])]){const id=j.id||j.job_id;if(id)jobs.set(id,{...jobs.get(id),...j,id})}
    const last=next.last_generation;if(last?.job_id&&!jobs.has(last.job_id))jobs.set(last.job_id,{...last,id:last.job_id,kind:'operations_plan',status:'succeeded'});
    for(const j of jobs.values())if(ACTIVE.has(j.status)&&!tracked.has(j.id)){
      tracked.add(j.id);void track({job_id:j.id},job=>acceptJob(job)).catch(e=>{tracked.delete(j.id);toast(e.message,true)});
    }
  }
  function acceptData(next){
    data=next;selectedDate??=today();selectedMonth??=Number(selectedDate.slice(5,7));
    if(!isDate(selectedDate)||Number(selectedDate.slice(0,4))!==year()){selectedDate=`${year()}-${pad(Number(today().slice(5,7)))}-01`;selectedMonth=Number(selectedDate.slice(5,7));activeId=null}
    syncJobs(next);
  }
  async function fetchState(){const next=await api('/operations');acceptData(next);return next}
  function yearHTML(){return `<section class="op-year" aria-label="年度运营计划"><div class="op-section-heading"><h2>年度运营计划 <span>${year()}</span><small class="op-draft-label">草案</small></h2><button class="op-text-button" data-op="context">${icon('edit')}品牌与月度重点</button></div><div class="op-month-track" role="group" aria-label="选择月份">${months().map(m=>`<button class="op-month ${Number(m.month)===selectedMonth?'is-active':''}" data-op="month" data-month="${Number(m.month)}" aria-pressed="${Number(m.month)===selectedMonth}"><span>${pad(m.month)}<small>月</small></span><strong>${esc(m.theme||'未安排')}</strong></button>`).join('')}</div><div id="op-month-focus">${monthFocusHTML()}</div><div id="op-context-host">${contextHTML()}</div></section>`}
  function monthFocusHTML(){const m=month();return `<div class="op-month-focus"><p><span>${selectedMonth}月重点</span>${esc(m.focus||'尚未填写')}</p><div class="op-events">${(m.events||[]).map(e=>`<button data-op="day" data-date="${year()}-${pad(selectedMonth)}-${pad(e.day)}"><time>${selectedMonth}.${pad(e.day)}</time>${esc(e.label)}</button>`).join('')}</div></div>`}
  function contextHTML(){
    if(!contextOpen||!contextDraft)return '';
    const v=contextDraft.value;
    return `<form id="op-context-editor" class="op-context-editor"><div class="op-context-fields"><label>品牌资料<textarea name="profile" required rows="4" maxlength="6000">${esc(v.profile)}</textarea></label><div><label>${contextDraft.month}月主题<input name="theme" required maxlength="40" value="${esc(v.theme)}"></label><label>月度重点<textarea name="focus" rows="3" maxlength="1000">${esc(v.focus)}</textarea></label></div><label>活动节点<textarea name="events" rows="4" maxlength="2000" placeholder="每行一项，例如：15 门店上新">${esc(v.events)}</textarea></label></div>${sourcesHTML(data.brand?.sources)}<div class="op-editor-bottom"><span class="op-edit-status">${contextDraft.dirty?'有未保存修改':''}</span><div class="actions"><button type="button" class="op-text-button" data-op="cancel-context">取消</button><button class="button small" type="submit">保存品牌与月度重点</button></div></div></form>`;
  }
  function shotsHTML(idea){return idea.shots?.length?`<div class="op-shot-sequence" aria-label="建议拍摄画面">${idea.shots.map((shot,i)=>`<div class="op-shot"><span class="op-shot-index">${pad(i+1)}</span><span>${esc(shot)}</span>${i<idea.shots.length-1?icon('arrow'):''}</div>`).join('')}</div>`:''}
  function editorHTML(idea){
    return `<div class="op-editor-grid ${editOpen?'is-open':''}" aria-hidden="${!editOpen}" ${editOpen?'':'inert'}><div class="op-editor-clip"><form class="op-editor" id="op-idea-editor"><label>选题<input name="title" required maxlength="100" value="${esc(idea.title)}" placeholder="选题标题"></label><label>想怎么拍<textarea name="angle" rows="3" maxlength="2000" placeholder="想讲什么、想拍到什么">${esc(idea.angle)}</textarea></label><div class="op-editor-meta"><label>安排日期<input name="date" type="date" required min="${year()}-01-01" max="${year()}-12-31" value="${esc(idea.date)}"></label><label>平台<select name="platform">${Object.entries(PLATFORMS).map(([key,label])=>`<option value="${key}" ${idea.platform===key?'selected':''}>${label}</option>`).join('')}</select></label><label>秒数<input name="duration" type="number" required min="5" max="120" step="1" value="${esc(idea.duration??30)}"></label></div><label>建议镜头<textarea name="shots" rows="3" maxlength="2000">${esc((idea.shots||[]).join('\n'))}</textarea></label><div class="op-editor-bottom"><span class="op-edit-status">${ideaDraft?.dirty?'有未保存修改':''}</span><div class="actions"><button type="button" class="op-text-button" data-op="cancel-idea">取消</button><button class="button small" type="submit">保存修改</button></div></div></form></div></div>`;
  }
  function ideaSourcesHTML(idea){
    const ids=new Set(idea.source_ids||[]),all=[...(idea.sources||[]),...(data.hotspots?.items||[]),...(data.hotspots?.sources||[]),...(data.brand?.sources||[])],seen=new Set();
    const refs=all.filter(s=>ids.has(s.id)&&!seen.has(s.id)&&seen.add(s.id));
    return refs.length?`<details class="op-idea-sources"><summary>${esc(idea.basis||'选题参考')} · ${refs.length} 条来源</summary>${sourcesHTML(refs.map(s=>({title:s.title||s.source,url:s.url,date:s.published_at||s.date})))}</details>`:idea.basis?`<span class="op-idea-basis">${esc(idea.basis)}</span>`:'';
  }
  function dailyHTML(){
    const idea=activeIdea(),items=byDate(selectedDate),d=parseDate(selectedDate),current=selectedDate===today();
    return `<div class="op-section-heading"><h2>${current?'今日发什么':`${dayLabel(selectedDate)}发什么`}</h2><button class="op-text-button" data-op="add">${icon('plus')}记一个选题</button></div><div class="op-daily-card"><div class="op-daily-top"><span class="op-day-date">${pad(d.getMonth()+1)}<span>/</span>${pad(d.getDate())}<small>星期${WEEKDAYS[d.getDay()]}</small></span><button class="op-text-button" data-op="generate-day" ${buttonDisabled()}>${current?'生成今日选题':'生成当日选题'}</button></div>${idea?`<div class="op-idea-body"><div class="op-idea-meta"><span>${esc(idea.category||'内容想法')}</span><span>${esc(PLATFORMS[idea.platform]||'抖音')} / ${Number(idea.duration)||30} 秒</span><span>${idea.origin==='ai'?'AI 选题':'手动选题'}${idea.edited?' · 已修改':''}</span></div><h3>${esc(idea.title||'新选题')}</h3><p class="op-idea-angle">${esc(idea.angle)}</p>${shotsHTML(idea)}${ideaSourcesHTML(idea)}<div class="op-idea-actions"><button class="button primary" data-op="start">开始制作${icon('arrow')}</button><button class="op-text-button" data-op="edit" aria-expanded="${editOpen}">${icon('edit')}调整内容</button>${idea.id?`<button class="op-text-button op-remove" data-op="remove" data-id="${esc(idea.id)}">移出计划</button>`:''}</div>${editorHTML(idea)}</div>${items.length>1?`<div class="op-other-ideas" role="group" aria-label="当天选题">${items.map(item=>`<button data-op="idea" data-id="${esc(item.id)}" aria-pressed="${item.id===idea.id}" class="${item.id===idea.id?'is-active':''}"><span>${esc(item.category||'想法')}</span>${esc(item.title||'新选题')}</button>`).join('')}</div>`:''}`:`<div class="op-empty-day"><h3>这一天还没有选题</h3><button class="button primary" data-op="generate-day" ${buttonDisabled()}>${current?'生成今日选题':'生成当日选题'}</button></div>`}</div>${lastRemoved?'<button class="op-undo" data-op="undo">已移出计划 · 撤销</button>':''}`;
  }
  function signalsHTML(){
    const h=data.hotspots||{},all=prioritizeHotspots(h.items||[]),list=signalsExpanded?all:all.slice(0,5),updated=h.fetched_at;
    return `<div class="op-section-heading"><h2>近期风向</h2><button class="op-text-button" data-op="refresh-hotspots" ${buttonDisabled()}>${icon('refresh')}刷新</button></div><div class="op-trend-coverage"><span>抖音热榜线索</span><span>小红书 · 未接入</span></div>${updated?`<p class="op-fetched-at">采集于 ${esc(chinaDateTime(updated))}</p>`:''}${h.stale?'<p class="op-source-status">本次未更新，保留上次结果</p>':''}<div class="op-signals">${list.length?list.map(s=>`<article class="op-signal"><div class="op-signal-source"><span>${esc(s.source||'搜搜热 · 抖音热榜')}${Number.isInteger(s.rank)?` · 第 ${s.rank} 位`:''}</span><time>快照 ${esc(chinaDateTime(s.observed_at).replace(/^\d{4}-/,''))}</time></div><span class="op-trend-tag">${esc(s.content_category)}</span><h3>${esc(s.title)}</h3>${s.freshness==='stale'?'<span class="op-signal-freshness">较早榜单</span>':''}${s.summary?`<p>${esc(s.summary)}</p>`:''}<div class="op-signal-bottom">${link(s.url,'查看话题')}<button class="op-text-button" data-op="generate-source" data-id="${esc(s.id)}" ${buttonDisabled()}>用这个生成选题</button></div></article>`).join(''):'<div class="op-source-empty">本次热榜暂无适合餐饮的线索</div>'}</div>${all.length>5?`<button class="op-text-button op-signals-more" data-op="signals-more">${signalsExpanded?'收起':`展开其余 ${all.length-5} 条`}</button>`:''}${h.sources?.some(s=>s.status&& !['ok','succeeded','success'].includes(s.status))?`<div class="op-source-status">${h.sources.filter(s=>s.status&&!['ok','succeeded','success'].includes(s.status)).map(s=>`<p>${esc(s.name||s.title||s.id||'来源')}：${esc(s.error||s.message||(s.status==='stale'?'未获取到最新内容':'本次未更新'))}</p>`).join('')}</div>`:''}`;
  }
  function weekDates(){const d=parseDate(selectedDate),offset=(d.getDay()+6)%7;return Array.from({length:7},(_,i)=>shiftDate(selectedDate,i-offset))}
  function calendarHTML(){
    const days=view==='week'?weekDates():Array.from({length:new Date(year(),selectedMonth,0).getDate()},(_,i)=>`${year()}-${pad(selectedMonth)}-${pad(i+1)}`),offset=(parseDate(days[0]).getDay()+6)%7;
    return `<div class="op-calendar-heading"><div class="op-view-toggle" role="group" aria-label="计划视图"><button data-op="view" data-view="week" aria-pressed="${view==='week'}">本周计划</button><button data-op="view" data-view="month" aria-pressed="${view==='month'}">本月计划</button></div><div class="op-calendar-nav"><button data-op="previous" aria-label="上一${view==='week'?'周':'月'}">${icon('chevron')}</button><span>${view==='week'?`${dayLabel(days[0])} — ${dayLabel(days[6])}`:`${year()}年${selectedMonth}月`}</span><button data-op="next" aria-label="下一${view==='week'?'周':'月'}">${icon('chevron')}</button><button data-op="today" class="op-today-link">回到今天</button><button class="button small" data-op="generate-week" ${buttonDisabled()}>生成本周内容</button></div></div><div class="op-calendar-grid ${view==='month'?'is-month':'is-week'}">${['一','二','三','四','五','六','日'].map(d=>`<div class="op-weekday">周${d}</div>`).join('')}${view==='month'?Array.from({length:offset},()=>'<div class="op-calendar-pad"></div>').join(''):''}${days.map(value=>{const d=parseDate(value),items=byDate(value);return `<button class="op-calendar-day ${value===selectedDate?'is-selected':''} ${value===today()?'is-today':''}" data-op="day" data-date="${value}" ${Number(value.slice(0,4))!==year()?'disabled':''} aria-label="${dayLabel(value)}，${items.length}个选题" aria-pressed="${value===selectedDate}"><time>${d.getDate()}${value===today()?'<small>今天</small>':''}</time><div class="op-day-ideas">${items.slice(0,3).map(i=>`<span class="op-calendar-idea"><i></i>${esc(i.title)}</span>`).join('')}${items.length>3?`<small>另 ${items.length-3} 个选题</small>`:''}</div></button>`}).join('')}</div>`;
  }
  function jobHTML(){
    const seen=new Set(),list=[...jobs.values()].sort((a,b)=>(Date.parse(b.created_at||b.generated_at||b.updated_at)||0)-(Date.parse(a.created_at||a.generated_at||a.updated_at)||0)).filter(j=>{const kind=(j.kind||'').includes('hotspot')?'hotspots':'plan';if(seen.has(kind))return false;seen.add(kind);return true}).filter(j=>!((j.kind||'').includes('hotspot')&&j.status==='succeeded')).slice(0,2);
    return list.map(j=>{const failed=['failed','needs_configuration','unknown'].includes(j.status),active=ACTIVE.has(j.status),label=(j.kind||'').includes('hotspot')?'风向更新':'选题生成';return `<div class="op-job ${failed?'is-error':j.status==='succeeded'?'is-complete':''}" data-job-id="${esc(j.id)}"><div><strong>${label}</strong><span>${esc(active?(j.message||'处理中'):failed?(j.error||j.message||'未完成'):j.status==='succeeded'?`新增 ${j.result?.added??j.added??0} 个选题`:'')}</span></div>${active?`<progress max="100" value="${Number(j.progress)||0}" aria-label="${label}进度"></progress>`:''}<div class="op-job-actions">${!(j.kind||'').includes('hotspot')?usageSlot('job',j.id,'选题用量'):''}${failed?`<button class="op-text-button" data-op="retry" data-id="${esc(j.id)}">重试</button>`:''}</div></div>`}).join('');
  }
  function paintJobs(){const el=root()?.querySelector('#op-jobs');if(el)el.innerHTML=jobHTML();root()?.querySelectorAll('[data-op="generate-day"],[data-op="generate-week"],[data-op="generate-source"],[data-op="refresh-hotspots"],[data-op="retry"]').forEach(b=>b.disabled=busy||running());onContentChange?.()}
  function paintData(){
    if(!root())return;
    if(!editOpen)document.querySelector('#op-daily').innerHTML=dailyHTML();
    if(!contextOpen){document.querySelector('#op-month-focus').innerHTML=monthFocusHTML();document.querySelector('#op-context-host').innerHTML=contextHTML()}
    document.querySelector('#op-signals').innerHTML=signalsHTML();document.querySelector('#op-calendar').innerHTML=calendarHTML();
    root().querySelectorAll('[data-op="month"]').forEach(b=>{const active=Number(b.dataset.month)===selectedMonth;b.classList.toggle('is-active',active);b.setAttribute('aria-pressed',String(active));const m=months().find(m=>Number(m.month)===Number(b.dataset.month));b.querySelector('strong').textContent=m?.theme||'未安排'});paintJobs();
  }
  function mount(){
    document.querySelector('#app').innerHTML=`<div class="op-home"><header class="op-header"><div class="op-brand"><div class="op-brand-mark" aria-hidden="true">${icon('bowl')}</div><div><div class="op-brand-caption">品牌运营</div><h1>${esc(data.brand?.name||'运营首页')}</h1></div></div><div class="op-header-right"><a href="#analysis" class="op-text-button">素材&分析${icon('arrow')}</a></div></header><div id="op-jobs" class="op-jobs" aria-live="polite">${jobHTML()}</div>${yearHTML()}<div class="op-main-grid"><section id="op-daily" aria-label="每日选题">${dailyHTML()}</section><aside id="op-signals" aria-label="平台流行线索">${signalsHTML()}</aside></div><section id="op-calendar" class="op-calendar" aria-label="内容日历">${calendarHTML()}</section></div>`;
    const host=root();host.addEventListener('click',e=>void handleClick(e));host.addEventListener('input',handleInput);host.addEventListener('change',handleInput);host.addEventListener('submit',e=>void handleSubmit(e));onContentChange?.();
    const track=host.querySelector('.op-month-track'),active=track.querySelector('.is-active');if(active&&active.offsetLeft+active.offsetWidth>track.clientWidth)track.scrollLeft=active.offsetLeft-track.offsetLeft-track.clientWidth/2+active.offsetWidth/2;
  }
  async function render(){
    const seq=++loadSequence;
    if(!data)document.querySelector('#app').innerHTML='<div class="section"><p class="muted">读取运营计划…</p></div>';
    else if(!root())mount();
    try{await fetchState();if(seq!==loadSequence||!isHere())return;if(root())paintData();else mount()}
    catch(e){if(seq!==loadSequence||!isHere())return;if(root())toast(e.message,true);else {document.querySelector('#app').innerHTML=`<div class="section"><p class="error-block">${esc(e.message)}</p><button class="button" id="op-reload">重新读取</button></div>`;document.querySelector('#op-reload').onclick=()=>void render()}}
  }
  function setDate(value,id=null){if(!isDate(value)||Number(value.slice(0,4))!==year())return;selectedDate=value;selectedMonth=Number(value.slice(5,7));activeId=id;ideaDraft=null;editOpen=false;contextOpen=false;contextDraft=null;paintData()}
  function openEditor(){
    editOpen=true;const panel=root()?.querySelector('.op-editor-grid');
    if(!panel){document.querySelector('#op-daily').innerHTML=dailyHTML();return}
    panel.inert=false;panel.setAttribute('aria-hidden','false');panel.classList.add('is-open');
    root()?.querySelector('[data-op="edit"]')?.setAttribute('aria-expanded','true');
  }
  function beginEdit(idea){
    ideaDraft={value:structuredClone(idea),revision:idea.revision,dirty:false};
    const form=root()?.querySelector('#op-idea-editor');
    if(form){for(const name of ['title','angle','date','platform','duration','shots'])if(form.elements[name])form.elements[name].value=name==='shots'?(idea.shots||[]).join('\n'):idea[name]??'';form.querySelector('.op-edit-status').textContent=''}
    openEditor();
  }
  function beginContext(){const m=month();contextDraft={month:selectedMonth,revision:data.revision,plan:structuredClone(data.annual_plan),value:{profile:typeof data.brand.profile==='string'?data.brand.profile:JSON.stringify(data.brand.profile||{},null,2),theme:m.theme||'',focus:m.focus||'',events:(m.events||[]).map(e=>`${e.day} ${e.label}`).join('\n')},dirty:false};contextOpen=true;document.querySelector('#op-context-host').innerHTML=contextHTML()}
  function updateIdea(idea){data.ideas=[...ideas().filter(i=>i.id!==idea.id),idea]}
  async function saveIdea(){
    if(!ideaDraft?.dirty)return true;
    const form=root()?.querySelector('#op-idea-editor');if(form&&!form.reportValidity())return false;
    const v=ideaDraft.value;if(!v.title.trim()||!isDate(v.date)||Number(v.date.slice(0,4))!==year()||!Number.isInteger(Number(v.duration))||Number(v.duration)<5||Number(v.duration)>120){toast('请填写选题和有效日期、秒数。',true);return false}
    if(v.shots.length>8||v.shots.some(s=>s.length>200)){toast('最多填写8个镜头，每个镜头不超过200字。',true);return false}
    const fields={title:v.title.trim(),angle:v.angle.trim(),category:v.category||'内容想法',date:v.date,platform:v.platform,duration:Number(v.duration),shots:v.shots};
    const result=await api(v.id?`/operations/ideas/${encodeURIComponent(v.id)}`:'/operations/ideas',v.id?{expected_revision:ideaDraft.revision,...fields}:fields,v.id?'PATCH':'POST');
    updateIdea(result.idea);selectedDate=result.idea.date;selectedMonth=Number(selectedDate.slice(5,7));activeId=result.idea.id;ideaDraft={value:structuredClone(result.idea),revision:result.idea.revision,dirty:false};await fetchState();return true;
  }
  async function saveContext(){
    if(!contextDraft?.dirty)return true;
    const form=root()?.querySelector('#op-context-editor');if(form&&!form.reportValidity())return false;
    const c=contextDraft,events=[];
    for(const line of c.value.events.split('\n').filter(x=>x.trim())){const match=line.trim().match(/^(\d{1,2})[\s、:：.-]+(.+)$/);if(!match||Number(match[1])<1||Number(match[1])>new Date(c.plan.year,c.month,0).getDate()){toast('活动节点请按“日期 活动名称”填写，每行一项。',true);return false}events.push({day:Number(match[1]),label:match[2].trim()})}
    const plan=structuredClone(c.plan),index=plan.months.findIndex(m=>Number(m.month)===c.month),m={month:c.month,theme:c.value.theme.trim(),focus:c.value.focus.trim(),events};if(index>=0)plan.months[index]=m;else plan.months.push(m);
    acceptData(await api('/operations/context',{expected_revision:c.revision,brand:{profile:c.value.profile.trim()},annual_plan:plan},'PUT'));contextDraft=null;contextOpen=false;const host=root()?.querySelector('#op-context-host');if(host)host.innerHTML='';return true;
  }
  async function savePending(){
    if(saving)return saving;
    saving=(async()=>{try {if(!await saveContext()||!await saveIdea())return false;return true}catch(e){toast(e.message,true);return false}finally{saving=null}})();return saving;
  }
  function rememberRequest(job){
    if(requests.has(job.id))return requests.get(job.id);
    if((job.kind||'').includes('hotspot'))return {path:'/operations/hotspots/refresh',payload:{}};
    const payload=job.operation_request||job.request;
    return payload?.scope&&payload?.date?{path:'/operations/generate',payload:{scope:payload.scope,date:payload.date,source_ids:payload.source_ids||[]}}:null;
  }
  async function startJob(request){
    if(busy||running())return;
    busy=true;paintJobs();
    try{if(!await savePending())return;const response=await api(request.path,request.payload),id=response.job_id||response.id;requests.set(id,request);jobs.set(id,{id,kind:request.path.includes('hotspots')?'operations_hotspots':'operations_plan',status:'queued',created_at:new Date().toISOString()});tracked.add(id);await track(response,job=>acceptJob(job));paintJobs()}
    catch(e){toast(e.message,true)}finally{busy=false;paintJobs()}
  }
  async function acceptJob(job){
    const id=job.id||job.job_id;jobs.set(id,{...jobs.get(id),...job,id});if(ACTIVE.has(job.status)){paintJobs();return}
    if(finished.has(id))return;finished.add(id);
    try{await fetchState();if(isHere())paintData();if(job.status==='succeeded'){const result=job.result||{},count=result.added??result.added_count??result.created_count??result.ideas?.length??result.idea_ids?.length;toast((job.kind||'').includes('hotspot')?'风向已更新':Number.isFinite(Number(count))?`新增 ${Number(count)} 个选题`:'选题已生成')}else toast(job.error||job.message||'生成未完成，可重试。',true)}catch(e){toast(e.message,true)}
  }
  async function onJobs(all){
    const relevant=all.filter(j=>jobs.has(j.id)||String(j.kind||'').startsWith('operations_'));
    for(const j of relevant){const before=jobs.get(j.id),merged={...before,...j};jobs.set(j.id,merged);if(before&&before.status!==j.status&&!ACTIVE.has(j.status))await acceptJob(merged)}if(root())paintJobs();
  }
  async function handleClick(event){
    const b=event.target.closest('[data-op]');if(!b||!root()?.contains(b)||b.disabled)return;
    const action=b.dataset.op;
    try {
      if(action==='cancel-idea'){ideaDraft=null;editOpen=false;document.querySelector('#op-daily').innerHTML=dailyHTML();return}
      if(action==='cancel-context'){contextDraft=null;contextOpen=false;document.querySelector('#op-context-host').innerHTML='';return}
      if(action==='edit'){if(!ideaDraft)beginEdit(activeIdea());else openEditor();return}
      if(action==='generate-day'||action==='generate-week'||action==='generate-source')return startJob({path:'/operations/generate',payload:{scope:action==='generate-week'?'week':'day',date:selectedDate,...(action==='generate-source'?{source_ids:[b.dataset.id]}:{})}});
      if(action==='refresh-hotspots')return startJob({path:'/operations/hotspots/refresh',payload:{}});
      if(action==='retry'){const j=jobs.get(b.dataset.id),request=j&&rememberRequest(j);if(request)return startJob(request);toast('原操作记录不完整，请重新选择日期生成。',true);return}
      if(action==='signals-more'){signalsExpanded=!signalsExpanded;document.querySelector('#op-signals').innerHTML=signalsHTML();return}
      if(!await savePending())return;
      if(action==='context'){if(contextOpen){contextOpen=false;contextDraft=null;document.querySelector('#op-context-host').innerHTML=''}else beginContext();return}
      if(action==='month'){const m=Number(b.dataset.month);setDate(`${year()}-${pad(m)}-${Number(today().slice(0,4))===year()&&Number(today().slice(5,7))===m?today().slice(8):'01'}`);return}
      if(action==='day'){setDate(b.dataset.date);return}
      if(action==='idea'){activeId=b.dataset.id;ideaDraft=null;editOpen=false;paintData();return}
      if(action==='view'){view=b.dataset.view;document.querySelector('#op-calendar').innerHTML=calendarHTML();return}
      if(action==='today'){setDate(today());return}
      if(action==='previous'||action==='next'){const step=action==='previous'?-1:1;if(view==='week')setDate(shiftDate(selectedDate,step*7));else {const m=selectedMonth+step;if(m>=1&&m<=12)setDate(`${year()}-${pad(m)}-01`)}return}
      if(action==='add'){ideaDraft={value:{title:'',angle:'',category:'内容想法',date:selectedDate,platform:'douyin',duration:30,shots:[],origin:'manual'},dirty:true};editOpen=true;document.querySelector('#op-daily').innerHTML=dailyHTML();root().querySelector('[name="title"]')?.focus({preventScroll:true});return}
      if(action==='remove'){const idea=ideas().find(i=>i.id===b.dataset.id);if(!idea)return;const result=await api(`/operations/ideas/${encodeURIComponent(idea.id)}?expected_revision=${idea.revision}`,undefined,'DELETE');lastRemoved=result.idea;data.ideas=ideas().filter(i=>i.id!==idea.id);ideaDraft=null;editOpen=false;activeId=null;paintData();return}
      if(action==='undo'&&lastRemoved){const result=await api(`/operations/ideas/${encodeURIComponent(lastRemoved.id)}/restore`,{expected_revision:lastRemoved.revision});updateIdea(result.idea);lastRemoved=null;setDate(result.idea.date,result.idea.id);return}
      if(action==='start'){const idea=activeIdea();if(!idea)return;if(!idea.title?.trim()||!idea.angle?.trim()){beginEdit(idea);toast('先填写选题和内容想法。',true);return}onStart({...idea,brand:data.brand.name});return}
    }catch(e){toast(e.message,true)}
  }
  function handleInput(event){
    const field=event.target,form=field.closest('form');
    if(form?.id==='op-idea-editor'){
      ideaDraft??={value:structuredClone(activeIdea()),revision:activeIdea()?.revision,dirty:false};
      if(['title','angle','date','platform','duration'].includes(field.name))ideaDraft.value[field.name]=field.value;
      else if(field.name==='shots')ideaDraft.value.shots=field.value.split('\n').map(s=>s.trim()).filter(Boolean);else return;
      ideaDraft.dirty=true;form.querySelector('.op-edit-status').textContent='有未保存修改';
    }
    if(form?.id==='op-context-editor'&&contextDraft){contextDraft.value[field.name]=field.value;contextDraft.dirty=true;form.querySelector('.op-edit-status').textContent='有未保存修改'}
  }
  async function handleSubmit(event){
    if(!['op-idea-editor','op-context-editor'].includes(event.target.id))return;event.preventDefault();
    const button=event.submitter;if(button)button.disabled=true;
    try{if(await savePending()){ideaDraft=null;editOpen=false;await fetchState();paintData();toast('已保存')}}catch(e){toast(e.message,true)}finally{if(button?.isConnected)button.disabled=false}
  }
  // Keep the pending form in memory for back/forward navigation; explicit links save first.
  document.addEventListener('click',event=>{const anchor=event.target.closest('a[href]');if(!anchor||!root()||!dirty()||event.defaultPrevented||event.metaKey||event.ctrlKey||event.shiftKey||event.altKey||anchor.target==='_blank')return;const url=new URL(anchor.href,location.href);if(url.origin!==location.origin||url.pathname!==location.pathname||url.hash===location.hash)return;event.preventDefault();event.stopPropagation();void savePending().then(ok=>{if(ok){ideaDraft=null;editOpen=false;contextDraft=null;contextOpen=false;location.hash=url.hash}})},true);
  window.addEventListener('beforeunload',event=>{if(dirty()){event.preventDefault();event.returnValue=''}});
  return {render,onJobs};
}
