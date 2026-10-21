import {BRAND, MONTHS, SEED_IDEAS, SIGNALS} from './operations-data.js?v=20261021-ops1';

const STORAGE_KEY = 'video-studio:operations:longlaixia:v1';
const PLATFORMS = {douyin:'抖音',xiaohongshu:'小红书',channels:'视频号'};
const WEEKDAYS = ['日','一','二','三','四','五','六'];
const pad = n => String(n).padStart(2,'0');
const dateKey = date => `${date.getFullYear()}-${pad(date.getMonth()+1)}-${pad(date.getDate())}`;
const parseDate = value => new Date(`${value}T12:00:00`);
const shiftDate = (value, days) => {const date=parseDate(value);date.setDate(date.getDate()+days);return dateKey(date)};
const chinaToday = () => new Intl.DateTimeFormat('sv-SE',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date());
const isDate = value => /^2026-\d{2}-\d{2}$/.test(value||'') && dateKey(parseDate(value))===value;
const icon = (name) => `<svg viewBox="0 0 24 24" aria-hidden="true">${{
  arrow:'<path d="M5 12h14m-5-5 5 5-5 5"/>',
  plus:'<path d="M12 5v14M5 12h14"/>',
  edit:'<path d="m15 5 4 4M4 20l5-1L20 8a2.8 2.8 0 0 0-4-4L5 15z"/>',
  chevron:'<path d="m9 5 7 7-7 7"/>',
  external:'<path d="M14 4h6v6m0-6-9 9M9 5H4v15h15v-5"/>',
  calendar:'<rect x="4" y="5" width="16" height="16" rx="3"/><path d="M8 3v4m8-4v4M4 11h16"/>',
  check:'<path d="m5 12 4 4L19 6"/>',
  bowl:'<path d="M3 11h18c-1 6-4 9-9 9s-8-3-9-9ZM9 21h6M7 7c-3-3 3-3 0-6m5 6c-3-3 3-3 0-6m5 6c-3-3 3-3 0-6"/>',
}[name]||''}</svg>`;

export function createOperationsHome({esc, toast, onStart}) {
  const today=chinaToday();
  let state=load(), editOpen=false, lastRemoved=null;
  let storageFailed=false;
  function load() {
    const initialDate=today.startsWith('2026-')?today:'2026-10-21';
    const initial={version:1,ideas:structuredClone(SEED_IDEAS),selectedDate:initialDate,selectedMonth:Number(initialDate.slice(5,7)),activeId:null,view:'week'};
    try {
      const saved=JSON.parse(localStorage.getItem(STORAGE_KEY)||'null');
      if(saved?.version===1 && Array.isArray(saved.ideas) && saved.ideas.every(i=>typeof i.id==='string' && typeof i.title==='string' && isDate(i.date) && Array.isArray(i.shots))) {
        return {...initial,...saved,selectedDate:isDate(saved.selectedDate)?saved.selectedDate:initialDate,selectedMonth:Math.min(12,Math.max(1,Number(saved.selectedMonth)||10)),view:saved.view==='month'?'month':'week'};
      }
    } catch { /* A corrupt local draft must not prevent opening the other workbench pages. */ }
    return initial;
  }
  function save() {
    try {localStorage.setItem(STORAGE_KEY,JSON.stringify(state));storageFailed=false}
    catch {storageFailed=true}
    const status=document.querySelector('#op-save-state');
    if(status){status.textContent=storageFailed?'未能保存到此浏览器':'计划保存在此浏览器';status.classList.toggle('op-save-error',storageFailed)}
    return !storageFailed;
  }
  const root=()=>document.querySelector('.op-home');
  const byDate=date=>state.ideas.filter(i=>i.date===date);
  function activeIdea() {const ideas=byDate(state.selectedDate);return ideas.find(i=>i.id===state.activeId)||ideas[0]||null}
  function selectedMonth(){return MONTHS.find(m=>m.month===state.selectedMonth)}
  function dayLabel(value){const d=parseDate(value);return `${d.getMonth()+1}月${d.getDate()}日`}
  function yearHTML() {
    return `<section class="op-year" aria-label="年度运营计划"><div class="op-section-heading"><h2>年度运营计划 <span>2026</span></h2><span class="op-subtle">一整年的内容节奏</span></div><div class="op-month-track" role="group" aria-label="选择月份">${MONTHS.map(m=>`<button class="op-month ${m.month===state.selectedMonth?'is-active':''}" data-op="month" data-month="${m.month}" aria-pressed="${m.month===state.selectedMonth}"><span>${pad(m.month)}<small>月</small></span><strong>${esc(m.theme)}</strong></button>`).join('')}</div><div id="op-month-focus">${monthFocusHTML()}</div></section>`;
  }
  function monthFocusHTML() {
    const m=selectedMonth();
    return `<div class="op-month-focus"><p><span>${m.month}月重点</span>${esc(m.focus)}</p><div class="op-events">${m.events.map(e=>`<button data-op="event" data-date="2026-${pad(m.month)}-${pad(e.day)}"><time>${m.month}.${pad(e.day)}</time>${esc(e.label)}</button>`).join('')}</div></div>`;
  }
  function shotHTML(idea) {
    return `<div class="op-shot-sequence" aria-label="建议拍摄画面">${idea.shots.map((shot,i)=>`<div class="op-shot"><span class="op-shot-index">${pad(i+1)}</span><span>${esc(shot)}</span>${i<idea.shots.length-1?icon('arrow'):''}</div>`).join('')}</div>`;
  }
  function editorHTML(idea) {
    return `<div class="op-editor-grid ${editOpen?'is-open':''}" aria-hidden="${!editOpen}" ${editOpen?'':'inert'}><div class="op-editor-clip"><form class="op-editor" id="op-idea-editor" data-idea-id="${esc(idea.id)}"><label>选题<input name="title" aria-label="选题标题" maxlength="100" value="${esc(idea.title)}" placeholder="给这条内容起个名字"></label><label>想怎么拍<textarea name="angle" aria-label="内容想法" rows="3" maxlength="2000" placeholder="写下想讲什么、想拍到什么">${esc(idea.angle)}</textarea></label><div class="op-editor-meta"><label>安排日期<input name="date" type="date" min="2026-01-01" max="2026-12-31" value="${esc(idea.date)}"></label><label>平台<select name="platform">${Object.entries(PLATFORMS).map(([key,label])=>`<option value="${key}" ${idea.platform===key?'selected':''}>${label}</option>`).join('')}</select></label><label>秒数<input name="duration" type="number" min="5" max="120" step="1" value="${Number(idea.duration)||30}"></label></div><label>建议镜头<textarea name="shots" aria-label="建议镜头，每行一个" rows="3" maxlength="1200">${esc(idea.shots.join('\n'))}</textarea></label><div class="op-editor-bottom"><button type="button" class="op-text-button" data-op="remove" data-id="${esc(idea.id)}">移出计划</button><button type="submit" class="button small">收起</button></div></form></div></div>`;
  }
  function dailyHTML() {
    const idea=activeIdea(),items=byDate(state.selectedDate),d=parseDate(state.selectedDate),isToday=state.selectedDate===today;
    return `<div class="op-section-heading"><h2>${isToday?'今日发什么':`${dayLabel(state.selectedDate)}发什么`}</h2><button class="op-text-button" data-op="add">${icon('plus')}记一个选题</button></div><div class="op-daily-card"><div class="op-daily-top"><span class="op-day-date">${pad(d.getMonth()+1)}<span>/</span>${pad(d.getDate())}<small>星期${WEEKDAYS[d.getDay()]}</small></span><span class="op-label">${items.length?`${items.length} 个内容想法`:'留一点创作空间'}</span></div>${idea?`<div class="op-idea-body"><div class="op-idea-meta"><span>${esc(idea.category||'内容想法')}</span><span id="op-platform-meta">${esc(PLATFORMS[idea.platform]||'抖音')} / ${Number(idea.duration)||30} 秒</span></div><h3 id="op-idea-title">${esc(idea.title||'新选题')}</h3><p class="op-idea-angle" id="op-idea-angle">${esc(idea.angle)}</p><div id="op-shot-host">${shotHTML(idea)}</div><p class="op-rationale">${esc(idea.rationale||'从门店实际发生的事开始。')}</p><div class="op-idea-actions"><button class="button primary" data-op="start" data-id="${esc(idea.id)}">开始制作${icon('arrow')}</button><button class="op-text-button" data-op="edit" aria-expanded="${editOpen}" aria-controls="op-idea-editor">${icon('edit')}<span>${editOpen?'收起调整':'调整内容'}</span></button></div>${editorHTML(idea)}</div>${items.length>1?`<div class="op-other-ideas" role="group" aria-label="当天选题">${items.map(item=>`<button data-op="idea" data-id="${esc(item.id)}" aria-pressed="${item.id===idea.id}" class="${item.id===idea.id?'is-active':''}"><span>${esc(item.category||'想法')}</span>${esc(item.title||'新选题')}</button>`).join('')}</div>`:''}`:`<div class="op-empty-day">${icon('bowl')}<h3>这一天，想讲点什么？</h3><p>记下一个想法，或从右侧风向选一个角度。</p><button class="button" data-op="add">${icon('plus')}添加选题</button></div>`}</div>${lastRemoved?'<button class="op-undo" data-op="undo">已移出计划 · 撤销</button>':''}`;
  }
  function signalsHTML() {
    return `<div class="op-section-heading"><h2>近期风向</h2><span class="op-subtle">行业观察</span></div><div class="op-signals">${SIGNALS.map(s=>`<article class="op-signal" data-signal-id="${esc(s.id)}"><div class="op-signal-source"><span>${esc(s.sourceName)}</span><time datetime="${esc(s.date)}">${esc(s.date.slice(5).replace('-','.'))}</time></div><h3>${esc(s.title)}</h3><p>${esc(s.observation)}</p><div class="op-signal-angle"><span>可以拍</span>${esc(s.angle)}</div><div class="op-signal-bottom"><a href="${esc(s.url)}" target="_blank" rel="noopener noreferrer" aria-label="阅读来源：${esc(s.title)}">${esc(s.date)} 原文${icon('external')}</a><button class="op-text-button" data-op="signal" data-id="${esc(s.id)}" ${state.ideas.some(i=>i.signalId===s.id&&i.date===state.selectedDate)?'disabled':''}>${state.ideas.some(i=>i.signalId===s.id&&i.date===state.selectedDate)?icon('check')+'已加入':icon('plus')+'加入选题'}</button></div></article>`).join('')}</div>`;
  }
  function weekDates() {
    const d=parseDate(state.selectedDate),offset=(d.getDay()+6)%7;
    return Array.from({length:7},(_,i)=>shiftDate(state.selectedDate,i-offset));
  }
  function calendarHTML() {
    const days=state.view==='week'?weekDates():Array.from({length:new Date(2026,state.selectedMonth,0).getDate()},(_,i)=>`2026-${pad(state.selectedMonth)}-${pad(i+1)}`);
    const monthOffset=(parseDate(days[0]).getDay()+6)%7;
    const title=state.view==='week'?`${dayLabel(days[0])} — ${dayLabel(days[6])}`:`2026年${state.selectedMonth}月`;
    return `<div class="op-calendar-heading"><div class="op-view-toggle" role="group" aria-label="计划视图"><button data-op="view" data-view="week" aria-pressed="${state.view==='week'}">本周计划</button><button data-op="view" data-view="month" aria-pressed="${state.view==='month'}">本月计划</button></div><div class="op-calendar-nav"><button data-op="previous" aria-label="上一${state.view==='week'?'周':'月'}">${icon('chevron')}</button><span>${title}</span><button data-op="next" aria-label="下一${state.view==='week'?'周':'月'}">${icon('chevron')}</button><button data-op="today" class="op-today-link">回到今天</button></div></div><div class="op-calendar-grid ${state.view==='month'?'is-month':'is-week'}">${['一','二','三','四','五','六','日'].map(d=>`<div class="op-weekday">周${d}</div>`).join('')}${state.view==='month'?Array.from({length:monthOffset},()=>'<div class="op-calendar-pad"></div>').join(''):''}${days.map(value=>{const d=parseDate(value),items=byDate(value);return `<button class="op-calendar-day ${value===state.selectedDate?'is-selected':''} ${value===today?'is-today':''}" data-op="day" data-date="${value}" aria-label="${dayLabel(value)}，${items.length}个选题" aria-pressed="${value===state.selectedDate}"><time datetime="${value}">${d.getDate()}${value===today?'<small>今天</small>':''}</time><div class="op-day-ideas">${items.slice(0,3).map(i=>`<span class="op-calendar-idea"><i></i>${esc(i.title||'新选题')}</span>`).join('')}${items.length>3?`<small>另 ${items.length-3} 个选题</small>`:''}${!items.length?'<span class="op-day-add">＋</span>':''}</div></button>`}).join('')}</div>`;
  }
  function render() {
    document.querySelector('#app').innerHTML=`<div class="op-home"><header class="op-header"><div class="op-brand"><div class="op-brand-mark" aria-hidden="true">${icon('bowl')}</div><div><div class="op-brand-caption"><span>品牌运营</span><span class="op-demo">${esc(BRAND.note)}</span></div><h1>${esc(BRAND.name)}</h1></div></div><div class="op-header-right"><a href="#analysis" class="op-text-button">素材&分析${icon('arrow')}</a><span id="op-save-state">计划保存在此浏览器</span></div></header>${yearHTML()}<div class="op-main-grid"><section id="op-daily" aria-label="每日选题">${dailyHTML()}</section><aside id="op-signals" aria-label="行业风向">${signalsHTML()}</aside></div><section id="op-calendar" class="op-calendar" aria-label="内容日历">${calendarHTML()}</section><footer class="op-footer"><span>隆莱呷内容日历 · 2026</span><a href="${esc(BRAND.source.url)}" target="_blank" rel="noopener noreferrer">品牌资料${icon('external')}</a></footer></div>`;
    const host=root();host.addEventListener('click',handleClick);host.addEventListener('input',handleInput);host.addEventListener('change',handleChange);host.addEventListener('submit',handleSubmit);save();
    // Only the month track scrolls; opening the home page must not steal the page position.
    const track=host.querySelector('.op-month-track'),active=track.querySelector('.is-active');
    if(active.offsetLeft+active.offsetWidth>track.clientWidth)track.scrollLeft=active.offsetLeft-track.offsetLeft-track.clientWidth/2+active.offsetWidth/2;
  }
  function updateDaily(){const el=document.querySelector('#op-daily');if(el)el.innerHTML=dailyHTML()}
  function updateCalendar(){const el=document.querySelector('#op-calendar');if(el)el.innerHTML=calendarHTML()}
  function updateSignals(){const el=document.querySelector('#op-signals');if(el)el.innerHTML=signalsHTML()}
  function updateMonth(){root()?.querySelectorAll('[data-op="month"]').forEach(b=>{const selected=Number(b.dataset.month)===state.selectedMonth;b.classList.toggle('is-active',selected);b.setAttribute('aria-pressed',String(selected))});const el=document.querySelector('#op-month-focus');if(el)el.innerHTML=monthFocusHTML()}
  function chooseDate(value,ideaId=null) {
    if(!isDate(value))return;
    state.selectedDate=value;state.selectedMonth=Number(value.slice(5,7));state.activeId=ideaId;editOpen=false;
    save();updateMonth();updateDaily();updateCalendar();updateSignals();
  }
  function toggleEditor(open) {
    const panel=root()?.querySelector('.op-editor-grid'),button=root()?.querySelector('[data-op="edit"]');if(!panel||!button)return;
    editOpen=open;panel.setAttribute('aria-hidden',String(!open));panel.inert=!open;panel.classList.toggle('is-open',open);button.setAttribute('aria-expanded',String(open));button.querySelector('span').textContent=open?'收起调整':'调整内容';
    if(!open&&panel.contains(document.activeElement))button.focus({preventScroll:true});
  }
  function addIdea(source=null,signalId=null) {
    if(signalId&&state.ideas.some(i=>i.signalId===signalId&&i.date===state.selectedDate))return;
    const idea={id:crypto.randomUUID(),title:'',angle:'',category:'内容想法',date:state.selectedDate,platform:'douyin',duration:30,shots:[],rationale:'从门店实际发生的事开始。',...structuredClone(source||{}),...(signalId?{signalId}:{})};
    idea.date=state.selectedDate;state.ideas.push(idea);state.activeId=idea.id;save();editOpen=!source;updateDaily();updateCalendar();updateSignals();
    if(!source){const panel=root().querySelector('.op-editor-grid');panel.classList.add('is-open');root().querySelector('[name="title"]')?.focus({preventScroll:true})}
    else toast(`已加入${dayLabel(state.selectedDate)}的选题`);
  }
  function handleClick(event) {
    const button=event.target.closest('[data-op]');if(!button||!root()?.contains(button))return;
    const action=button.dataset.op;
    if(action==='month'){state.selectedMonth=Number(button.dataset.month);state.selectedDate=`2026-${pad(state.selectedMonth)}-${state.selectedMonth===Number(today.slice(5,7))&&today.startsWith('2026-')?today.slice(8):'01'}`;state.activeId=null;editOpen=false;save();updateMonth();updateDaily();updateCalendar();updateSignals()}
    if(action==='day'||action==='event')chooseDate(button.dataset.date);
    if(action==='idea'){state.activeId=button.dataset.id;editOpen=false;save();updateDaily()}
    if(action==='edit')toggleEditor(!editOpen);
    if(action==='add')addIdea();
    if(action==='signal'){const signal=SIGNALS.find(s=>s.id===button.dataset.id);if(signal)addIdea(signal.idea,signal.id)}
    if(action==='view'){state.view=button.dataset.view;save();updateCalendar()}
    if(action==='today')chooseDate(today.startsWith('2026-')?today:'2026-10-21');
    if(action==='previous'||action==='next'){
      const step=action==='previous'?-1:1;
      if(state.view==='week'){const next=shiftDate(state.selectedDate,step*7);if(isDate(next))chooseDate(next)}
      else {const month=state.selectedMonth+step;if(month>=1&&month<=12)chooseDate(`2026-${pad(month)}-01`)}
    }
    if(action==='remove'){const idea=state.ideas.find(i=>i.id===button.dataset.id);if(!idea)return;lastRemoved=idea;state.ideas=state.ideas.filter(i=>i.id!==idea.id);state.activeId=null;editOpen=false;save();updateDaily();updateCalendar();updateSignals()}
    if(action==='undo'&&lastRemoved){state.ideas.push(lastRemoved);const old=lastRemoved;lastRemoved=null;chooseDate(old.date,old.id)}
    if(action==='start'){
      const form=root()?.querySelector('#op-idea-editor');
      if(form&&!form.checkValidity()){toggleEditor(true);form.reportValidity();return}
      const idea=activeIdea();if(!idea?.title.trim()||!idea?.angle.trim()){toggleEditor(true);toast('先写下选题和内容想法。',true);return}
      if(storageFailed){toast('计划尚未保存，请先检查浏览器存储。',true);return}
      onStart({title:idea.title,angle:idea.angle,date:idea.date,platform:idea.platform,duration:idea.duration,shots:[...idea.shots],brand:BRAND.name});
    }
  }
  function handleInput(event) {
    const field=event.target,form=field.closest('#op-idea-editor');if(!form)return;
    const idea=state.ideas.find(i=>i.id===form.dataset.ideaId);if(!idea)return;
    if(['title','angle'].includes(field.name))idea[field.name]=field.value;
    else if(field.name==='shots')idea.shots=field.value.split('\n').map(s=>s.trim()).filter(Boolean).slice(0,8);
    else if(field.name==='duration'){if(!field.validity.valid)return;idea.duration=Number(field.value)}
    else if(field.name==='platform')idea.platform=field.value;
    else return;
    save();
    const title=root().querySelector('#op-idea-title'),angle=root().querySelector('#op-idea-angle');title.textContent=idea.title||'新选题';angle.textContent=idea.angle;
    root().querySelector('#op-platform-meta').textContent=`${PLATFORMS[idea.platform]} / ${idea.duration} 秒`;
    if(field.name==='shots')root().querySelector('#op-shot-host').innerHTML=shotHTML(idea);
    if(field.name==='title') {root().querySelectorAll('.op-other-ideas [data-id]').forEach(b=>{if(b.dataset.id===idea.id)b.innerHTML=`<span>${esc(idea.category)}</span>${esc(idea.title||'新选题')}`});updateCalendar()}
  }
  function handleChange(event){
    const field=event.target,form=field.closest('#op-idea-editor');if(!form)return;
    if(field.name==='date') {
      if(!isDate(field.value)){field.setCustomValidity('请选择2026年内的有效日期');field.reportValidity();return}
      field.setCustomValidity('');const idea=state.ideas.find(i=>i.id===form.dataset.ideaId);if(!idea)return;
      idea.date=field.value;state.selectedDate=idea.date;state.selectedMonth=Number(idea.date.slice(5,7));state.activeId=idea.id;save();updateMonth();updateCalendar();updateSignals();editOpen=true;updateDaily();root().querySelector('.op-editor-grid')?.classList.add('is-open');
    }else handleInput(event);
  }
  function handleSubmit(event){if(event.target.id==='op-idea-editor'){event.preventDefault();toggleEditor(false)}}
  return {render};
}
