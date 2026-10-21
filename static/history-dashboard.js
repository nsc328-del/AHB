/** Platform-scoped publication charts. Each point is one video's latest observation. */
export function createHistoryDashboard({S, api, esc, toast}) {
  const state = {workspace:'xingshang', platform:'douyin', account:'', date_from:'', date_to:'', sequence:0, detailSequence:0, result:null, order:'views', query:''};
  const names = {douyin:'抖音', xiaohongshu:'小红书', channels:'视频号'};
  const metricNames = {views:'播放', likes:'点赞', comments:'评论', saves:'收藏', shares:'分享', recommends:'推荐', completion_rate:'完播率', bounce_2s:'2 秒跳出率', avg_watch_seconds:'平均观看秒数'};
  const $ = selector => document.querySelector(selector);
  const number = value => value == null ? '—' : new Intl.NumberFormat('zh-CN', {maximumFractionDigits:1}).format(value);
  const compact = value => value == null ? '—' : value >= 100000000 ? `${number(value/100000000)} 亿` : value >= 10000 ? `${number(value/10000)} 万` : number(value);
  const dateFormatter = new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hourCycle:'h23'});
  function localParts(value) {
    const parsed=new Date(value);
    return Number.isFinite(parsed.getTime())?Object.fromEntries(dateFormatter.formatToParts(parsed).map(p=>[p.type,p.value])):null;
  }
  const date = value => {
    if(!value)return '—';
    if(/^\d{4}-\d{2}-\d{2}$/.test(value))return value.replaceAll('-','.');
    const p=localParts(value);
    return p?`${p.year}.${p.month}.${p.day}`:'日期未知';
  };
  const stamp = value => {
    const p=value?localParts(value):null;
    return p?`${p.year}-${p.month}-${p.day} ${p.hour}:${p.minute}`:'未采集';
  };
  const metric = item => item?.zero_may_mean_unavailable?'零值待确认':item?.unknown_reason==='unit_unknown'?'口径待确认':!item || item.unknown || item.value == null ? '未采集' : `${item.approximate?'约 ':''}${item.unit==='ratio'?`${number(item.value*100)}%`:compact(item.value)}`;
  const isHistory = () => S.route === 'history' || S.route.startsWith('history/');
  const scopeLabel = data => `${names[data.filters.platform]} / ${data.filters.account || '全部账号'} / 发布于 ${date(data.scope.date_from)} — ${date(data.scope.date_to)}`;
  const selectedParams = () => Object.fromEntries(['platform','account','date_from','date_to'].map(key=>[key,state[key]]));
  const details = new Map();
  const narrowChart=window.matchMedia('(max-width:600px)');

  function projectRail(workspaces) {
    return `<aside class="hd-projects"><h2>项目</h2><nav aria-label="历史数据项目">${workspaces.map(w=>`<a href="#history/${esc(w.id)}" class="${w.id===state.workspace?'active':''}" ${w.id===state.workspace?'aria-current="page"':''}><span class="hd-project-symbol">${esc(w.name.slice(0,1))}</span><span>${esc(w.name)}</span><span class="hd-project-count">${number(w.cases_count)}</span></a>`).join('')}</nav></aside>`;
  }
  function filtersHTML() {
    return `<div class="hd-controls"><div class="hd-platforms" role="tablist" aria-label="发布平台">${Object.entries(names).map(([id,label])=>`<button type="button" role="tab" aria-selected="${state.platform===id}" data-hd-platform="${id}">${label}</button>`).join('')}</div><form class="hd-filter" id="hd-filter"><label><span>账号</span><select name="account" aria-label="账号"><option value="">全部账号</option></select></label><label><span>发布日期</span><input type="date" name="date_from" aria-label="发布开始日期" value="${esc(state.date_from)}"></label><span class="hd-date-separator">至</span><label><span class="sr-only">发布结束日期</span><input type="date" name="date_to" aria-label="发布结束日期" value="${esc(state.date_to)}"></label><button class="button small" type="submit">应用</button><button class="hd-clear" type="button" data-hd-clear>全部日期</button></form></div>`;
  }
  function coverage(item, total) {
    return `${item.count}/${total} 条有数据${item.approximate_count?`，${item.approximate_count} 条为约数`:''}${item.ambiguous_count?`，${item.ambiguous_count} 条零值待确认`:''}`;
  }
  function statsHTML(data) {
    const m=data.metrics, total=data.scope.videos;
    return `<div class="hd-summary"><div class="hd-summary-item"><span>发布视频</span><strong>${number(total)}<small>条</small></strong><p>${data.scope.undated_videos?`${data.scope.undated_videos} 条日期未知，未纳入`:'当前发布窗口'}</p></div><div class="hd-summary-item"><span>单条播放中位数</span><strong>${m.views.approximate_count?'≈ ':''}${compact(m.views.median)}<small>次</small></strong><p>${coverage(m.views,total)}</p></div><div class="hd-summary-item"><span>累计播放合计</span><strong>${m.views.approximate_count?'≈ ':''}${compact(m.views.total)}<small>次</small></strong><p>${m.views.missing_count?`${m.views.missing_count} 条未采集`:'每条视频取最近采集值'}</p></div><div class="hd-summary-item"><span>完播率中位数</span><strong>${m.completion_rate.median==null?'—':`${m.completion_rate.approximate_count?'≈ ':''}${number(m.completion_rate.median*100)}%`}</strong><p>${m.completion_rate.count?coverage(m.completion_rate,total):'尚无可用完播数据'}</p></div></div>`;
  }
  function scatterHTML(data) {
    const points=data.cases.filter(c=>c.metrics.views?.value!=null && !c.metrics.views.unknown && c.metrics.views.value>=0);
    if(!points.length)return '<div class="hd-chart-empty">当前范围暂无播放数据</div>';
    const W=narrowChart.matches?440:860,H=296,L=64,R=24,T=24,B=44,w=W-L-R,h=H-T-B;
    const dates=data.cases.map(c=>Date.parse(`${c.published_date}T00:00:00+08:00`));
    const min=Math.min(...dates), max=Math.max(...dates), range=max-min+86400000;
    const maxValue=Math.max(...points.map(c=>c.metrics.views.value),1);
    const maxLog=Math.max(1,Math.ceil(Math.log10(maxValue+1)));
    const x=c=>L+(Date.parse(c.published_at)-min)/range*w;
    const y=value=>T+h-(Math.log10(value+1)/maxLog)*h;
    const grid=Array.from({length:maxLog+1},(_,power)=>{const value=power?10**power:0;const yy=y(value);return `<line x1="${L}" y1="${yy}" x2="${W-R}" y2="${yy}" class="hd-gridline"/><text x="${L-12}" y="${yy+4}" text-anchor="end">${compact(value)}</text>`}).join('');
    const dayTicks=[0,.25,.5,.75,1].map(f=>{const time=min+range*f;const label=max===min?new Date(time).toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit',timeZone:'Asia/Shanghai'}):new Date(time).toLocaleDateString('zh-CN',{month:'2-digit',day:'2-digit',timeZone:'Asia/Shanghai'});return `<text x="${L+w*f}" y="${H-15}" text-anchor="${f===0?'start':f===1?'end':'middle'}">${label}</text>`}).join('');
    return `<svg class="hd-scatter" viewBox="0 0 ${W} ${H}" role="group" aria-label="按发布时间查看每条视频的累计播放，纵轴为对数刻度">${grid}${dayTicks}${points.map(c=>`<g role="button" tabindex="0" data-hd-case="${esc(c.id)}" aria-label="${esc(`${c.title}，播放 ${metric(c.metrics.views)}`)}"><title>${esc(`${c.title}\n${c.published_date} 发布\n${metric(c.metrics.views)} 次播放\n${stamp(c.captured_at)} 采集`)}</title><circle class="hd-point-hit" cx="${x(c)}" cy="${y(c.metrics.views.value)}" r="10"/><circle class="hd-point ${c.metrics.views.approximate?'is-approximate':''}" cx="${x(c)}" cy="${y(c.metrics.views.value)}" r="4.5"/></g>`).join('')}</svg>`;
  }
  function dailyHTML(data) {
    const rows=data.daily;
    if(!rows.length)return '<div class="hd-chart-empty">暂无发布记录</div>';
    const W=narrowChart.matches?440:860,H=110,L=64,R=24,T=9,B=30,w=W-L-R,h=H-T-B;
    const max=Math.max(...rows.map(d=>d.videos),1);
    const first=Date.parse(rows[0].date),last=Date.parse(rows.at(-1).date),span=Math.max((last-first)/86400000+1,1);
    const width=Math.min(14,w/span*.68);
    return `<svg viewBox="0 0 ${W} ${H}" class="hd-daily" role="img" aria-label="按发布日期统计视频数量"><text x="${L-12}" y="${T+9}" text-anchor="end">${max} 条</text><line x1="${L}" y1="${T+h}" x2="${W-R}" y2="${T+h}" class="hd-gridline"/>${rows.map(d=>{const x=L+((Date.parse(d.date)-first)/86400000+.5)/span*w;return `<rect x="${x-width/2}" y="${T+h-d.videos/max*h}" width="${width}" height="${d.videos/max*h}" rx="2"><title>${d.date} 发布 ${d.videos} 条；播放中位数 ${d.views.approximate_count?'约 ':''}${compact(d.views.median)} 次，${d.views.count} 条有播放数据</title></rect>`}).join('')}<text x="${L}" y="${H-8}">${date(rows[0].date)}</text><text x="${W-R}" y="${H-8}" text-anchor="end">${date(rows.at(-1).date)}</text></svg>`;
  }
  function distributionHTML(data) {
    const max=Math.max(...data.distribution.map(d=>d.count),1);
    return `<div class="hd-bars">${data.distribution.map(d=>`<div class="hd-bar-row"><span>${d.label}</span><div class="hd-bar-track"><i style="width:${d.count/max*100}%"></i></div><b>${d.count}<small> 条</small></b></div>`).join('')}</div><p class="hd-chart-note">${data.metrics.views.missing_count} 条播放未采集${data.metrics.views.approximate_count?`，${data.metrics.views.approximate_count} 条按约数分布`:''}</p>`;
  }
  function interactionsHTML(data) {
    const keys=['likes','comments','saves','shares','recommends'];
    const max=Math.max(...keys.map(key=>data.metrics[key].total||0),1);
    return `<div class="hd-bars hd-interactions">${keys.map(key=>{const m=data.metrics[key];return `<div class="hd-interaction"><div class="hd-interaction-heading"><span>${metricNames[key]}</span><b>${m.approximate_count?'≈ ':''}${compact(m.total)}</b></div><div class="hd-bar-track"><i style="width:${(m.total||0)/max*100}%"></i></div><p>${m.count}/${data.scope.videos} 条有数据${m.ambiguous_count?`，${m.ambiguous_count} 条零值待确认`:''}</p></div>`}).join('')}</div>`;
  }
  function tableHTML(data) {
    const matches=data.cases.filter(c=>!state.query||c.title.toLocaleLowerCase().includes(state.query.toLocaleLowerCase()));
    const rows=state.order==='views'?[...matches].sort((a,b)=>(b.metrics.views?.value??-1)-(a.metrics.views?.value??-1)):matches;
    return `<div class="hd-table-heading"><div><h2>视频明细</h2><span>${number(matches.length)} 条</span></div><div class="hd-table-tools"><input id="hd-query" aria-label="搜索视频标题" placeholder="搜索视频" value="${esc(state.query)}"><select id="hd-order" aria-label="视频排序"><option value="views" ${state.order==='views'?'selected':''}>播放较高</option><option value="latest" ${state.order==='latest'?'selected':''}>最近发布</option></select></div></div><div class="hd-table-scroll"><table class="hd-table"><thead><tr><th>视频</th><th>发布日期</th><th>播放</th><th>点赞</th><th>评论</th><th>收藏</th><th>分享</th><th>采集后距发布</th></tr></thead><tbody>${rows.map(c=>`<tr><td><button type="button" data-hd-case="${esc(c.id)}" title="${esc(c.title)}">${esc(c.title||'未命名视频')}</button></td><td>${date(c.published_date)}</td>${['views','likes','comments','saves','shares'].map(k=>`<td class="${c.metrics[k]?.unknown?'hd-unknown':''}">${metric(c.metrics[k])}</td>`).join('')}<td>${c.age_hours==null?'未知':c.age_hours<24?`${number(c.age_hours)} 小时`:`${number(c.age_hours/24)} 天`}</td></tr>`).join('')}</tbody></table>${rows.length?'':'<div class="hd-chart-empty">没有匹配的视频</div>'}</div>`;
  }
  function contentHTML(data) {
    return `<div class="hd-scope"><span>${esc(scopeLabel(data))}</span><span>最近采集 ${stamp(data.scope.capture_to)}</span></div>${statsHTML(data)}<div class="hd-main-chart"><div class="hd-chart-heading"><h2>每条视频，表现如何</h2><span>累计播放 · 次 · 对数刻度</span></div>${scatterHTML(data)}<div class="hd-chart-foot"><span>按发布时间排列</span><span>● 精确值　○ 平台约数</span></div><div class="hd-daily-heading"><h3>每日发布</h3><span>条</span></div>${dailyHTML(data)}<p class="hd-chart-note">各视频取最近采集的累计值；发布时间与采集间隔不同。</p></div><div class="hd-secondary-charts"><section><div class="hd-chart-heading"><h2>播放分布</h2><span>条视频</span></div>${distributionHTML(data)}</section><section><div class="hd-chart-heading"><h2>互动构成</h2><span>各项累计次数</span></div>${interactionsHTML(data)}</section></div><section id="hd-records" class="hd-records">${tableHTML(data)}</section><p class="hd-source-line">星尚采集记录　${stamp(data.scope.capture_from)} — ${stamp(data.scope.capture_to)}</p>`;
  }
  async function load() {
    const sequence=++state.sequence, route=S.route;
    const root=$('#history-dashboard'), status=$('#hd-status'), content=$('#hd-content');
    if(!root)return;
    root.setAttribute('aria-busy','true');
    content.inert=true;
    status.textContent=`正在读取${names[state.platform]}数据`;
    status.classList.remove('is-error');
    try {
      const params=new URLSearchParams({workspace_id:state.workspace,...selectedParams()});
      const data=await api(`/history/dashboard?${params}`);
      if(sequence!==state.sequence||!isHistory()||S.route!==route||!root.isConnected)return;
      state.result=data;
      const projectCount=root.querySelector('.hd-projects a.active .hd-project-count');
      if(projectCount)projectCount.textContent=number(data.scope.project_videos);
      const account=$('#hd-filter select[name=account]');
      account.innerHTML='<option value="">全部账号</option>'+data.facets.accounts.map(a=>`<option ${a===state.account?'selected':''} value="${esc(a)}">${esc(a)}</option>`).join('');
      content.innerHTML=contentHTML(data);
      status.textContent='';
    } catch(error) {
      if(sequence===state.sequence&&root.isConnected){
        status.textContent=error.message;status.classList.add('is-error');
        if(state.result){
          Object.assign(state,state.result.filters);
          root.querySelectorAll('[data-hd-platform]').forEach(el=>el.setAttribute('aria-selected',String(el.dataset.hdPlatform===state.platform)));
          ['account','date_from','date_to'].forEach(name=>$('#hd-filter').elements[name].value=state[name]);
        }
      }
    } finally {
      if(sequence===state.sequence&&root.isConnected){root.setAttribute('aria-busy','false');content.inert=false}
    }
  }
  function closeDetail() {
    const dialog=$('#hd-detail');
    if(dialog?.open)dialog.close();
    state.detailSequence++;
  }
  function observationHTML(data) {
    const keys=['views','likes','comments','saves','shares','completion_rate'];
    return `<div class="hd-detail-head"><div><span>${names[data.case.platform]} / ${esc(data.case.account)}</span><h2>${esc(data.case.title||'未命名视频')}</h2><p>${date(data.case.published_at)} 发布</p></div><button class="hd-detail-close" type="button" data-hd-close aria-label="关闭视频明细">×</button></div><div class="hd-detail-values">${keys.map(k=>`<div><span>${metricNames[k]}</span><strong>${metric(data.case.metrics[k])}</strong></div>`).join('')}</div><div class="hd-observation-heading"><h3>采集记录</h3><span>${data.observations_count} 次</span></div><div class="hd-observation-scroll"><table class="hd-table"><thead><tr><th>采集时间</th>${keys.map(k=>`<th>${metricNames[k]}</th>`).join('')}</tr></thead><tbody>${data.observations.map(o=>`<tr><td>${stamp(o.captured_at)}</td>${keys.map(k=>`<td>${metric(o.metrics[k])}</td>`).join('')}</tr>`).join('')}</tbody></table></div><p class="hd-detail-source">${esc(data.case.source?.label||'星尚采集记录')}</p>`;
  }
  async function openDetail(id) {
    const dialog=$('#hd-detail');
    if(!dialog)return;
    const sequence=++state.detailSequence;
    if(!dialog.open)dialog.showModal();
    dialog.innerHTML='<div class="hd-detail-head"><h2>视频明细</h2><button type="button" class="hd-detail-close" data-hd-close aria-label="关闭视频明细">×</button></div><div class="hd-detail-loading">读取采集记录…</div>';
    try{
      const data=details.get(id)||await api(`/history/${encodeURIComponent(id)}`);
      details.set(id,data);
      if(sequence===state.detailSequence&&dialog.open&&dialog.isConnected)dialog.innerHTML=observationHTML(data);
    }catch(error){if(sequence===state.detailSequence&&dialog.open)dialog.innerHTML=`<div class="hd-detail-head"><h2>视频明细</h2><button type="button" class="hd-detail-close" data-hd-close aria-label="关闭视频明细">×</button></div><p class="hd-detail-loading">${esc(error.message)}</p>`}
  }
  async function render() {
    const route=S.route, sequence=++state.sequence;
    state.workspace=route.split('/')[1]||'xingshang';
    $('#app').innerHTML='<section class="section"><div class="section-head"><h1>历史数据分析</h1></div><div class="loading">读取项目数据</div></section>';
    try{
      const {workspaces}=await api('/workspaces');
      if(S.route!==route||sequence!==state.sequence)return;
      if(!workspaces.some(w=>w.id===state.workspace))throw new Error('项目不存在');
      $('#app').innerHTML=`<section class="history-dashboard" id="history-dashboard">${projectRail(workspaces)}<div class="hd-main"><div class="hd-heading"><h1>历史数据分析</h1><button type="button" class="button small" data-hd-sync>更新采集</button></div>${filtersHTML()}<div id="hd-status" class="hd-status" role="status"></div><div id="hd-content"><div class="hd-chart-empty">读取发布数据</div></div></div><dialog id="hd-detail" class="hd-detail" aria-label="视频采集明细"></dialog></section>`;
      await load();
    }catch(error){if(S.route===route&&sequence===state.sequence)$('#app').innerHTML=`<section class="section"><h1>历史数据分析</h1><p class="data-error">${esc(error.message)}</p></section>`}
  }
  document.addEventListener('click',event=>{
    const root=event.target.closest('#history-dashboard');
    if(!root)return;
    const point=event.target.closest('[data-hd-case]');
    if(point){void openDetail(point.dataset.hdCase);return}
    if(event.target.closest('[data-hd-close]')){closeDetail();return}
    const platform=event.target.closest('[data-hd-platform]');
    if(platform){
      state.platform=platform.dataset.hdPlatform;state.account='';
      root.querySelectorAll('[data-hd-platform]').forEach(el=>el.setAttribute('aria-selected',String(el===platform)));
      $('#hd-filter select[name=account]').value='';
      void load();return;
    }
    if(event.target.closest('[data-hd-clear]')){
      state.date_from='';state.date_to='';
      ['date_from','date_to'].forEach(name=>$('#hd-filter').elements[name].value='');
      void load();return;
    }
    const sync=event.target.closest('[data-hd-sync]');
    if(sync){
      sync.disabled=true;sync.textContent='正在更新';
      void (async()=>{try{const result=await api('/history/sync',{});details.clear();if(!isHistory())return;if(result.error)throw new Error(result.error);await load();toast?.('采集记录已更新')}catch(error){toast?.(error.message,true)}finally{if(sync.isConnected){sync.disabled=false;sync.textContent='更新采集'}}})();
    }
  });
  document.addEventListener('keydown',event=>{
    const point=event.target.closest('#history-dashboard [data-hd-case]');
    if(point&&point.tagName.toLowerCase()==='g'&&(event.key==='Enter'||event.key===' ')){event.preventDefault();void openDetail(point.dataset.hdCase)}
  });
  document.addEventListener('submit',event=>{
    if(event.target.id!=='hd-filter')return;
    event.preventDefault();
    const values=Object.fromEntries(new FormData(event.target));
    Object.assign(state,values);
    void load();
  });
  document.addEventListener('change',event=>{
    if(event.target.id==='hd-order'){state.order=event.target.value;$('#hd-records').innerHTML=tableHTML(state.result)}
  });
  document.addEventListener('input',event=>{
    if(event.target.id!=='hd-query')return;
    state.query=event.target.value;
    // Replace only table rows, preserving the active input and its caret.
    const template=document.createElement('template');template.innerHTML=tableHTML(state.result);
    $('#hd-records .hd-table-scroll').replaceWith(template.content.querySelector('.hd-table-scroll'));
    $('#hd-records .hd-table-heading>div>span').textContent=template.content.querySelector('.hd-table-heading>div>span').textContent;
  });
  narrowChart.addEventListener('change',()=>{
    if(!isHistory()||!state.result)return;
    const scatter=$('#hd-content .hd-scatter'),daily=$('#hd-content .hd-daily');
    if(scatter)scatter.outerHTML=scatterHTML(state.result);
    if(daily)daily.outerHTML=dailyHTML(state.result);
  });
  return {render};
}
