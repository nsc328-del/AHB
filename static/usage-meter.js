const tokenIcon = '<svg viewBox="0 0 24 24" aria-hidden="true"><ellipse cx="12" cy="5" rx="7" ry="3"/><path d="M5 5v7c0 1.7 3.1 3 7 3s7-1.3 7-3V5M5 12v7c0 1.7 3.1 3 7 3s7-1.3 7-3v-7M5 8.5c0 1.7 3.1 3 7 3s7-1.3 7-3"/></svg>';
const known = value => typeof value === 'number' && Number.isFinite(value) && value >= 0;
const exact = value => known(value) ? new Intl.NumberFormat('zh-CN', {maximumFractionDigits:2}).format(value) : '未记录';
const compact = value => {
  if (!known(value)) return '未记录';
  const unit = value >= 1e6 ? 1e6 : value >= 1e3 ? 1e3 : 1;
  return `${new Intl.NumberFormat('en', {maximumFractionDigits:unit === 1 ? 0 : 1}).format(value / unit)}${unit === 1e6 ? 'M' : unit === 1e3 ? 'K' : ''}`;
};
const percent = value => known(value) ? `${new Intl.NumberFormat('zh-CN', {maximumFractionDigits:1}).format(value * 100)}%` : '未记录';
const operations = {animation_plan:'动画方案', animation_render:'动画生成', animation_revise:'修改动画', animation_scene_revision:'修改动画脚本', animation_source:'动画画面生成', animation_source_patch:'动画局部修改', animation_visual_review:'动画画面复核', asset_analysis:'素材分析', reference_analysis:'样片分析', analysis:'素材分析', reference:'样片分析', reference_repair:'样片局部复核', reference_interval_repair:'样片局部复核', reference_rhythm:'声音节奏分析', reference_presentation:'剪辑方式分析',reference_semantics:'文案结构分析',reference_semantics_alignment:'语义时间对齐', reference_presentation_correction:'连接证据校正', reference_organization:'组织方式识别', material_analysis:'素材分析', reference_visual_observe:'局部画面观察', reference_visual_verify:'画面证据复核', reference_visual_reconcile:'画面结论校正', plan:'剪辑方案', planning:'剪辑方案', candidate:'候选成片', candidates:'候选成片', candidate_plan:'候选成片', revise:'修改方案', revision:'修改方案', generation_plan:'生成分镜', storyboard:'生成分镜', generation:'视频生成', video_generation:'视频生成'};
const timestamp = value => {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? '' : new Intl.DateTimeFormat('zh-CN', {timeZone:'Asia/Shanghai', month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit', hour12:false}).format(date);
};

export function usageSummary(data) {
  const a = data?.aggregates || {}, c = data?.coverage || {};
  if (known(a.total_tokens)) return {text:`${compact(a.total_tokens)} tok`, detail:known(a.cache_hit_rate) ? `缓存命中 ${percent(a.cache_hit_rate)}` : '', state:(c.token_total_unknown_calls || c.token_unknown_calls || c.token_partial_calls) ? 'partial' : 'recorded'};
  if (known(a.video_seconds)) return {text:`${exact(a.video_seconds)} 秒`, detail:'视频用量', state:'recorded'};
  if (['input_tokens', 'output_tokens', 'cache_hit_tokens', 'cache_miss_tokens'].some(key => known(a[key]))) return {text:'部分用量', detail:'', state:'partial'};
  if (c.token_unknown_calls > 0) return {text:'用量未返回', detail:'', state:'unreported'};
  if (['empty', 'none'].includes(c.scope_status || data?.scope_status)) return {text:'暂无用量', detail:'', state:'empty'};
  return {text:'未记录', detail:'', state:'unrecorded'};
}

export function createUsageMeter({api, onError} = {}) {
  if (typeof api !== 'function') throw new Error('Usage meter requires an API reader.');
  const instances = new Map(), requests = new Map(), cache = new Map();
  let active = null, serial = 0, disposed = false;
  const visibilityObserver = typeof IntersectionObserver === 'function' ? new IntersectionObserver(entries => {
    if (disposed) return;
    for (const entry of entries) {
      const item = instances.get(entry.target);
      if (entry.isIntersecting && item && !item.loading) void load(item);
    }
  }) : null;
  const keyFor = options => JSON.stringify([options.scope || 'global', options.projectId || '', options.assetId || '', options.jobId || '', options.batchId || '', options.animationId || '']);
  const optionsFor = host => ({
    scope:host.dataset.usageScope || 'global',
    projectId:host.dataset.usageProjectId,
    assetId:host.dataset.usageAssetId,
    jobId:host.dataset.usageJobId,
    batchId:host.dataset.usageBatchId,
    animationId:host.dataset.usageAnimationId,
    label:host.dataset.usageLabel,
  });
  const valid = options => options.scope === 'global' || !!({project:options.projectId, asset:options.assetId, job:options.jobId, animation:options.animationId}[options.scope]);
  const visible = item => {
    if (active === item) return true;
    const box = item.host.getBoundingClientRect();
    return item.host.isConnected && item.host.getClientRects().length > 0 && getComputedStyle(item.host).visibility !== 'hidden' && box.bottom > 0 && box.top < innerHeight && box.right > 0 && box.left < innerWidth;
  };
  const pathFor = options => {
    const query = new URLSearchParams({scope:options.scope || 'global'});
    for (const [camel, snake] of [['projectId', 'project_id'], ['assetId', 'asset_id'], ['jobId', 'job_id'], ['batchId', 'batch_id'], ['animationId', 'animation_id']]) {
      if (options[camel]) query.set(snake, options[camel]);
    }
    return `/usage?${query}`;
  };
  function node(tag, className, text) {
    const el = document.createElement(tag);
    if (className) el.className = className;
    if (text != null) el.textContent = text;
    return el;
  }
  function setText(el, value) { if (el.textContent !== value) el.textContent = value; }
  function field(panel, label, name, classes = '') {
    const row = node('div', `um-row ${classes}`), title = node('dt', '', label), value = node('dd', '', '未记录');
    row.append(title, value); panel.append(row);
    return {row, title, value, name};
  }
  function ensurePanel(item) {
    if (item.panel) return item.panel;
    const panel = node('section', 'um-popover');
    panel.id = item.panelId;
    panel.tabIndex = -1;
    panel.setAttribute('role', 'dialog');
    panel.setAttribute('aria-modal', 'false');
    panel.setAttribute('aria-label', `${item.options.label || 'AI 用量'}明细`);
    panel.hidden = true;
    const header = node('header', 'um-heading'), title = node('span', 'um-title', 'Token 用量');
    const icon = node('span', 'um-icon'); icon.innerHTML = tokenIcon;
    const close = node('button', 'um-close', '×');
    close.type = 'button'; close.setAttribute('aria-label', '关闭用量明细');
    close.addEventListener('click', () => hide(item, true));
    header.append(icon, title, close);
    const numbers = node('dl', 'um-numbers');
    item.fields = {
      total:field(numbers, 'Token 总量', 'total', 'um-total'),
      hit:field(numbers, '缓存命中', 'hit'),
      miss:field(numbers, '未缓存输入', 'miss'),
      read:field(numbers, '缓存读取', 'read'),
      input:field(numbers, '输入合计', 'input'),
      output:field(numbers, '输出', 'output'),
      seconds:field(numbers, '视频用量', 'seconds', 'um-video'),
    };
    const note = node('p', 'um-note');
    const calls = node('details', 'um-calls'), summary = node('summary', '', '最近调用'), list = node('div', 'um-call-list');
    calls.append(summary, list);
    calls.addEventListener('toggle', () => { if (active === item) place(item); });
    const failure = node('div', 'um-failure'), failureText = node('span', '', '暂时无法读取用量');
    const retry = node('button', 'um-retry', '重试'); retry.type = 'button';
    retry.addEventListener('click', () => load(item, true));
    failure.append(failureText, retry); failure.hidden = true;
    panel.append(header, numbers, note, calls, failure);
    item.panel = panel; item.title = title; item.note = note; item.calls = calls; item.callSummary = summary; item.callList = list; item.callNodes = new Map(); item.failure = failure; item.retry = retry;
    (item.host.closest('dialog[open]') || document.body).append(panel);
    return panel;
  }
  function writeFields(item) {
    if (!item.panel) return;
    const a = item.data?.aggregates || {}, c = item.data?.coverage || {}, fields = item.fields;
    setText(item.title, item.options.label || (known(a.video_seconds) && !known(a.total_tokens) ? '生成用量' : 'Token 用量'));
    setText(fields.total.title, (c.token_total_unknown_calls > 0 || c.token_unknown_calls > 0) && known(a.total_tokens) ? '已记录 Token' : 'Token 总量');
    const values = {total:known(a.total_tokens) ? `${exact(a.total_tokens)} tok` : c.token_unknown_calls > 0 || c.token_reported_calls > 0 ? '用量未返回' : usageSummary(item.data).text,
      hit:percent(a.cache_hit_rate), miss:known(a.cache_miss_tokens) ? `${exact(a.cache_miss_tokens)} tok` : '未记录',
      read:known(a.cache_hit_tokens) ? `${exact(a.cache_hit_tokens)} tok` : '未记录',
      input:known(a.input_tokens) ? `${exact(a.input_tokens)} tok` : '未记录',
      output:known(a.output_tokens) ? `${exact(a.output_tokens)} tok` : '未记录',
      seconds:known(a.video_seconds) ? `${exact(a.video_seconds)} 秒` : '用量未返回'};
    for (const [name, value] of Object.entries(values)) setText(fields[name].value, value);
    const hasTokens = known(a.total_tokens) || c.token_reported_calls > 0 || c.token_unknown_calls > 0;
    fields.total.row.hidden = !hasTokens && known(a.video_seconds);
    for (const name of ['hit', 'miss', 'read', 'output']) fields[name].row.hidden = !hasTokens;
    const counts = c.metric_reported_calls || {};
    const completeCache = known(counts.input_tokens) && counts.input_tokens > 0 && counts.cache_hit_tokens === counts.input_tokens && counts.cache_miss_tokens === counts.input_tokens;
    fields.input.row.hidden = !hasTokens || (completeCache && known(a.cache_miss_tokens) && known(a.cache_hit_tokens));
    fields.seconds.row.hidden = !known(a.video_seconds) && !(c.seconds_reported_calls > 0);
    const notes = [];
    if (c.token_unknown_calls > 0 && known(a.total_tokens)) notes.push(`${exact(c.token_unknown_calls)} 次调用未返回 Token 用量`);
    if (c.token_partial_calls > 0) notes.push(`${exact(c.token_partial_calls)} 次仅返回部分 Token 用量`);
    if (c.seconds_only_calls > 0 && known(a.total_tokens)) notes.push(`${exact(c.seconds_only_calls)} 次仅返回视频秒数`);
    if (known(c.cache_reported_calls) && c.cache_reported_calls < c.token_reported_calls) notes.push('部分调用未返回缓存明细');
    if (c.history_available === false && (c.recorded_calls > 0 || c.scope_status === 'unrecorded')) notes.push('早期用量未记录');
    setText(item.note, notes.join('；')); item.note.hidden = !notes.length;
    item.failure.hidden = !item.error;
    item.retry.disabled = item.loading;
    const records = Array.isArray(item.data?.records) ? item.data.records.slice(0, 5) : [];
    item.calls.hidden = !records.length;
    setText(item.callSummary, `最近 ${records.length} 次调用`);
    const retained = new Set();
    for (const record of records) {
      const id = String(record.id || `${record.created_at}-${record.operation}-${record.job_id}`);
      retained.add(id);
      let call = item.callNodes.get(id);
      if (!call) {
        const root = node('article', 'um-call'), top = node('div', 'um-call-top'), label = node('span'), amount = node('span', 'um-call-amount');
        const meta = node('div', 'um-call-meta'), model = node('span'), time = node('time');
        top.append(label, amount); meta.append(model, time); root.append(top, meta);
        call = {root, label, amount, model, time}; item.callNodes.set(id, call);
      }
      setText(call.label, operations[record.operation] || ({deepseek:'DeepSeek', minimax:'MiniMax'}[record.provider] || 'AI 调用'));
      setText(call.amount, known(record.total_tokens) ? `${exact(record.total_tokens)} tok` : known(record.video_seconds) ? `${exact(record.video_seconds)} 秒` : ['input_tokens', 'output_tokens', 'cache_hit_tokens', 'cache_miss_tokens'].some(key => known(record[key])) ? '部分 Token' : record.usage_status === 'unrecorded' ? '未记录' : '用量未返回');
      setText(call.model, record.model || ({deepseek:'DeepSeek', minimax:'MiniMax'}[record.provider] || ''));
      setText(call.time, timestamp(record.created_at));
      call.time.dateTime = record.created_at || '';
      item.callList.append(call.root);
    }
    for (const [id, call] of item.callNodes) if (!retained.has(id)) { call.root.remove(); item.callNodes.delete(id); }
  }
  function paint(item) {
    const summary = usageSummary(item.data);
    setText(item.amount, !item.data ? item.error && !item.loading ? '用量暂不可用' : '读取用量' : summary.text);
    setText(item.detail, summary.detail); item.detail.hidden = !summary.detail;
    item.button.dataset.usageState = !item.data ? item.error && !item.loading ? 'unavailable' : 'loading' : summary.state;
    item.button.setAttribute('aria-label', `${item.options.label || 'AI 用量'}：${item.amount.textContent}${summary.detail ? `，${summary.detail}` : ''}，查看明细`);
    item.button.title = `${item.options.label || 'AI 用量'}：${known(item.data?.aggregates?.total_tokens) ? `${exact(item.data.aggregates.total_tokens)} tok` : item.amount.textContent}${summary.detail ? `，${summary.detail}` : ''}`;
    item.button.setAttribute('aria-busy', String(item.loading));
    writeFields(item);
    if (active === item) place(item);
  }
  function place(item) {
    if (!item.panel || !item.host.isConnected) return;
    const anchor = item.button.getBoundingClientRect(), panel = item.panel;
    const gap = 10, edge = 12, width = Math.min(344, innerWidth - edge * 2);
    panel.style.width = `${width}px`;
    panel.style.maxHeight = `${Math.max(100, innerHeight - edge * 2)}px`;
    const height = panel.offsetHeight;
    const below = innerHeight - anchor.bottom - gap - edge, above = anchor.top - gap - edge;
    const down = below >= height || below >= above;
    const left = Math.max(edge, Math.min(anchor.right - width, innerWidth - width - edge));
    const top = Math.max(edge, Math.min(down ? anchor.bottom + gap : anchor.top - height - gap, innerHeight - height - edge));
    panel.style.left = `${left}px`; panel.style.top = `${top}px`;
    panel.dataset.side = down ? 'bottom' : 'top';
    panel.style.transformOrigin = `${Math.max(20, Math.min(width - 20, anchor.left + anchor.width / 2 - left))}px ${down ? 'top' : 'bottom'}`;
  }
  function hide(item, restoreFocus = false) {
    if (!item || active !== item) return;
    item.button.setAttribute('aria-expanded', 'false');
    if (item.panel) {
      const opacity = getComputedStyle(item.panel).opacity;
      item.panel.classList.remove('um-open');
      item.panel.inert = true;
      item.closeAnimation?.cancel();
      if (!matchMedia('(prefers-reduced-motion: reduce)').matches && item.panel.isConnected) {
        const animation = item.panel.animate([{opacity}, {opacity:0}], {duration:110, easing:'ease-out', fill:'forwards'});
        item.closeAnimation = animation;
        animation.onfinish = () => { if (active !== item && item.closeAnimation === animation) { item.panel.hidden = true; animation.cancel(); item.closeAnimation = null; } };
      } else item.panel.hidden = true;
    }
    active = null;
    if (restoreFocus && item.button.isConnected) item.button.focus({preventScroll:true});
  }
  function show(item) {
    if (active === item) { hide(item); return; }
    hide(active);
    const panel = ensurePanel(item);
    item.closeAnimation?.cancel(); item.closeAnimation = null; panel.inert = false;
    active = item; writeFields(item);
    panel.hidden = false;
    place(item);
    panel.classList.add('um-open');
    item.button.setAttribute('aria-expanded', 'true');
    // The same anchored panel stays mounted while fresh readings arrive.
    panel.focus({preventScroll:true});
    load(item);
  }
  async function load(item, force = false) {
    if (disposed || !visible(item) || !valid(item.options)) return;
    const key = item.key, cached = cache.get(key);
    if (!force && cached && Date.now() - cached.at < 1500) { item.data = cached.data; paint(item); return cached.data; }
    item.loading = true; paint(item);
    let promise = requests.get(key);
    if (!promise) {
      promise = api(pathFor(item.options));
      requests.set(key, promise);
    }
    try {
      const data = await promise;
      if (disposed || item.key !== key || !instances.has(item.host)) return;
      cache.set(key, {data, at:Date.now()});
      for (const other of instances.values()) if (other.key === key) { other.data = data; other.error = null; other.loading = false; paint(other); }
      return data;
    } catch (error) {
      if (item.key === key && instances.has(item.host)) { item.error = error; item.loading = false; paint(item); }
      onError?.(error);
    } finally { if (requests.get(key) === promise) requests.delete(key); }
  }
  function mount(host, options = {}) {
    if (!host || disposed) return null;
    const settings = {...optionsFor(host), ...options}, key = keyFor(settings);
    if (!valid(settings)) { unmount(host); return null; }
    let item = instances.get(host);
    if (item?.key === key) { item.options = settings; paint(item); return item.handle; }
    if (item) unmount(host);
    const button = node('button', 'um-pill'); button.type = 'button';
    const icon = node('span', 'um-icon'); icon.innerHTML = tokenIcon;
    const amount = node('span', 'um-amount', '读取用量'), detail = node('span', 'um-detail');
    button.append(icon, amount, detail);
    const panelId = `usage-detail-${++serial}`;
    button.setAttribute('aria-haspopup', 'dialog'); button.setAttribute('aria-expanded', 'false'); button.setAttribute('aria-controls', panelId);
    host.classList.add('um-host'); host.replaceChildren(button);
    item = {host, button, amount, detail, panelId, options:settings, key, data:cache.get(key)?.data || null, loading:false, error:null};
    item.handle = {refresh:() => load(item, true), open:() => show(item), close:() => hide(item), destroy:() => unmount(host), update:data => { item.data = data; item.error = null; item.loading = false; paint(item); }};
    instances.set(host, item);
    visibilityObserver?.observe(host);
    button.addEventListener('click', event => { event.stopPropagation(); show(item); });
    paint(item); load(item);
    return item.handle;
  }
  function unmount(host) {
    const item = instances.get(host);
    if (!item) return;
    visibilityObserver?.unobserve(host);
    hide(item); item.closeAnimation?.cancel(); item.panel?.remove(); item.button.remove(); instances.delete(host);
  }
  function prune() { for (const host of instances.keys()) if (!host.isConnected) unmount(host); }
  function hydrate(root = document) {
    prune();
    if (root.matches?.('[data-usage-scope]')) mount(root);
    root.querySelectorAll('[data-usage-scope]').forEach(host => mount(host));
  }
  async function refresh() {
    prune();
    await Promise.allSettled([...instances.values()].filter(visible).map(item => load(item)));
  }
  const outside = event => { if (active && !active.panel.contains(event.target) && !active.button.contains(event.target)) hide(active); };
  const keyboard = event => { if (event.key === 'Escape' && active) { event.preventDefault(); hide(active, true); } };
  const focus = event => { if (active && !active.panel.contains(event.target) && !active.button.contains(event.target)) hide(active); };
  const reposition = () => { if (active) active.host.isConnected ? place(active) : hide(active); };
  document.addEventListener('pointerdown', outside);
  document.addEventListener('keydown', keyboard);
  document.addEventListener('focusin', focus);
  window.addEventListener('resize', reposition);
  window.addEventListener('scroll', reposition, {capture:true, passive:true});
  function dispose() {
    disposed = true;
    visibilityObserver?.disconnect();
    for (const host of [...instances.keys()]) unmount(host);
    document.removeEventListener('pointerdown', outside); document.removeEventListener('keydown', keyboard); document.removeEventListener('focusin', focus);
    window.removeEventListener('resize', reposition); window.removeEventListener('scroll', reposition, true);
  }
  return {mount, hydrate, refresh, close:() => hide(active), unmount, dispose};
}
