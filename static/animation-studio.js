const STORAGE = 'video-studio-animation-drafts-v1';
const ACTIVE = new Set(['queued', 'running']);
const TYPES = [['free', '自由动画'], ['type', '文字动效'], ['data', '数据对比'], ['flow', '流程解说']];
const clone = value => structuredClone(value);
const seconds = value => `${Number(value || 0).toFixed(1).replace(/\.0$/, '')} 秒`;
const pathId = value => encodeURIComponent(value);

export function createAnimationStudio(ctx) {
  const { S, api, esc, toast, track } = ctx;
  const A = { root: null, visible: false, mounted: false, animationId: null, requestedRevision: null,
    view: null, list: [], listLoaded: false, loading: false, reading: false, sequence: 0, saving: false,
    error: '', selectedScene: '', expanded: new Set(), drafts: {}, instructions: {}, requests: {},
    composer: { brief: '', style: 'free', ratio: '16:9', duration: '20' } };
  try {
    const saved = JSON.parse(sessionStorage.getItem(STORAGE) || 'null');
    if (saved) {
      for (const key of ['animationId', 'drafts', 'instructions', 'requests']) if (saved[key]) A[key] = saved[key];
      A.composer = { ...A.composer, ...saved.composer };
      for (const request of Object.values(A.requests)) { request.busy = false; request.finishing = false; }
    }
  } catch { /* A damaged local draft does not prevent reading saved projects. */ }
  const $ = (selector, root = A.root) => root?.querySelector(selector);
  const all = (selector, root = A.root) => [...(root?.querySelectorAll(selector) || [])];
  const revision = () => A.view?.revision;
  const historical = () => !!revision() && revision().id !== A.view.current_revision_id;
  const pending = () => Object.values(A.requests).find(r => !r.settled && r.animationId === A.animationId);
  const latestTask = () => Object.values(A.requests).filter(r => r.animationId === A.animationId).sort((a, b) => String(a.startedAt || '').localeCompare(String(b.startedAt || ''))).at(-1);
  const taskAction = request => request?.sourceAction || request?.action;
  const sceneKey = id => `${revision()?.id || ''}:${id}`;
  const scenes = () => revision()?.scenes || [];
  function persist() {
    const { animationId, composer, drafts, instructions, requests } = A;
    try { sessionStorage.setItem(STORAGE, JSON.stringify({ animationId, composer, drafts, instructions, requests })); }
    catch { /* Existing server versions remain authoritative if browser storage is full. */ }
  }
  function syncRoute(animationId, revisionId = null) {
    if (!A.visible || S.route !== 'generate/animation' || location.hash.slice(1).split('?')[0] !== 'generate/animation') return;
    const query = new URLSearchParams();
    if (animationId) query.set('animation_id', animationId);
    if (animationId && revisionId) query.set('revision_id', revisionId);
    const next = `#generate/animation${query.size ? `?${query}` : ''}`;
    if (location.hash !== next) history.replaceState(history.state, '', next);
  }
  function draft() {
    const rev = revision(); if (!rev) return null;
    return A.drafts[rev.id] ||= { dirty: false, scenes: (rev.scenes || []).map(s => ({ id: s.id, title: s.title || '', description: s.description || '', duration: String(s.duration) })) };
  }
  const editable = () => !!revision() && !historical() && !A.saving && !pending();
  function replaceMarkup(host, html) {
    if (!host || host._animationMarkup === html) return;
    host.innerHTML = html; host._animationMarkup = html;
  }
  function shell() {
    return `<section class="as-studio"><header class="as-header"><div><a class="as-back" href="#generate">‹ 素材生成</a><h1>动画短片</h1></div><div class="as-project-picker"><select class="as-project-select" aria-label="动画作品"><option value="">新的动画</option></select><button type="button" class="button small" data-as-action="new">新建</button></div></header><div class="as-layout"><section class="as-work"><form class="as-composer"><div class="as-type-selector" role="group" aria-label="动画类型">${TYPES.map(([id, title]) => `<button type="button" data-as-type="${id}" aria-pressed="${A.composer.style === id}">${title}</button>`).join('')}</div><label class="as-brief-label">视频描述<textarea name="animation-brief" maxlength="12000" placeholder="写下要讲的内容、画面和节奏。" required>${esc(A.composer.brief)}</textarea></label><div class="as-composer-settings"><label>画幅<select name="animation-ratio">${['16:9', '9:16', '1:1', '3:4'].map(r => `<option ${A.composer.ratio === r ? 'selected' : ''}>${r}</option>`).join('')}</select></label><label>时长<span class="as-duration-control"><input type="number" name="animation-duration" min="2" max="60" step="0.1" value="${esc(A.composer.duration)}" required><span>秒</span></span></label><button type="submit" class="button primary as-plan-button">生成方案</button></div></form><div class="as-status" role="status" aria-live="polite"></div><div class="as-error" role="alert" hidden></div><section class="as-plan" hidden><header class="as-plan-heading"><div><h2 class="as-plan-title"></h2><span class="as-plan-meta"></span></div><select class="as-version-select" aria-label="动画版本"></select></header><div class="as-version-note" hidden></div><div class="as-scenes"></div><footer class="as-plan-actions"><span class="as-save-state"></span><button type="button" class="button small as-save" data-as-action="save">保存方案</button><button type="button" class="button primary as-render" data-as-action="render">确认全部并生成</button></footer></section></section><aside class="as-preview"><div class="as-preview-heading"><h2>预览</h2><span class="as-preview-caption"></span></div><div class="as-player-stage"><div class="as-preview-empty"><span class="as-empty-frame" aria-hidden="true"></span><span>等待生成</span></div><video class="as-player" controls playsinline preload="metadata" hidden></video></div><div class="as-preview-tabs" role="group" aria-label="预览片段"></div><div class="as-output-actions"></div><div class="as-usage"></div></aside></div></section>`;
  }
  function mount() {
    if (!document.querySelector('.as-studio')) {
      document.querySelector('#app').innerHTML = shell(); A.root = document.querySelector('.as-studio'); bind();
    } else A.root = document.querySelector('.as-studio');
    A.mounted = true;
  }
  function paintPicker() {
    const items = A.list.slice();
    if (A.view && !items.some(item => item.id === A.view.id)) items.unshift(A.view);
    const host = $('.as-project-select');
    replaceMarkup(host, '<option value="">新的动画</option>' + items.map(item => `<option value="${esc(item.id)}">${esc(item.title || '未命名动画')}</option>`).join(''));
    if (host) host.value = A.animationId || '';
  }
  function paintComposer() {
    all('[data-as-type]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.asType === A.composer.style)));
    const creating = Object.values(A.requests).some(r => taskAction(r) === 'create' && !r.settled);
    const button = $('.as-plan-button'); if (button) { button.disabled = creating; button.textContent = creating ? '正在生成方案…' : '生成方案'; }
  }
  function sceneMarkup(scene, index) {
    return `<article class="as-scene" data-scene-id="${esc(scene.id)}"><div class="as-scene-top"><span class="as-scene-number">${String(index + 1).padStart(2, '0')}</span><input data-as-field="title" aria-label="片段 ${index + 1} 标题" maxlength="160"><label class="as-scene-duration"><input type="number" data-as-field="duration" min="2" max="30" step="any" aria-label="片段 ${index + 1} 时长"><span>秒</span></label></div><textarea data-as-field="description" rows="3" aria-label="片段 ${index + 1} 内容" maxlength="8000"></textarea><div class="as-scene-footer"><button type="button" class="as-text-button" data-as-action="preview-scene">预览片段</button><button type="button" class="as-text-button" data-as-action="expand-revise" aria-expanded="false">修改这段</button><button type="button" class="as-text-button" data-as-action="regenerate">重新生成</button></div><div class="as-scene-revise" hidden><label>修改要求<textarea data-as-instruction rows="2" maxlength="4000" placeholder="例如：标题晚一秒出现，其他内容保持不变。"></textarea></label><button type="button" class="button small primary" data-as-action="revise">修改并生成这段</button></div></article>`;
  }
  function paintPlan() {
    const rev = revision(), planHost = $('.as-plan'); if (!planHost) return;
    planHost.hidden = !rev; if (!rev) { $('.as-scenes').replaceChildren(); return; }
    $('.as-plan-title').textContent = A.view.title || '分段方案';
    const d = draft(); $('.as-plan-meta').textContent = `${d.scenes.length} 个片段 / ${seconds(d.scenes.reduce((n, s) => n + Number(s.duration || 0), 0))}`;
    const versions = A.view.revisions?.length ? A.view.revisions : [rev];
    const selector = $('.as-version-select');
    replaceMarkup(selector, versions.map(item => `<option value="${esc(item.id)}">版本 ${esc(item.version ?? '—')}${item.id === A.view.current_revision_id ? ' · 当前' : ''}</option>`).join(''));
    selector.value = rev.id; selector.disabled = A.saving;
    const note = $('.as-version-note'); note.hidden = !historical();
    replaceMarkup(note, `正在查看历史版本 <button type="button" class="as-text-button" data-as-action="latest">回到当前版本</button>`);
    const list = $('.as-scenes'), existing = new Map(all('.as-scene', list).map(el => [el.dataset.sceneId, el]));
    const renders = new Map((rev.renders || []).map(r => [r.scene_id, r]));
    const canRevise = !!rev.output?.url;
    d.scenes.forEach((scene, index) => {
      let card = existing.get(scene.id);
      if (!card) { const template = document.createElement('template'); template.innerHTML = sceneMarkup(scene, index); card = template.content.firstElementChild; }
      existing.delete(scene.id);
      if (list.children[index] !== card) list.insertBefore(card, list.children[index] || null);
      $('.as-scene-number', card).textContent = String(index + 1).padStart(2, '0');
      for (const name of ['title', 'description', 'duration']) {
        const field = $(`[data-as-field="${name}"]`, card);
        if (document.activeElement !== field && field.value !== String(scene[name] ?? '')) field.value = scene[name] ?? '';
        field.disabled = !editable();
      }
      const prompt = $('[data-as-instruction]', card), instruction = A.instructions[sceneKey(scene.id)] || '';
      if (document.activeElement !== prompt && prompt.value !== instruction) prompt.value = instruction;
      prompt.disabled = historical();
      $('[data-as-action="preview-scene"]', card).hidden = !renders.get(scene.id)?.url;
      for (const action of ['expand-revise', 'regenerate']) {
        const button = $(`[data-as-action="${action}"]`, card); button.hidden = !canRevise;
        button.disabled = !editable() || d.dirty;
      }
      const expanded = A.expanded.has(sceneKey(scene.id));
      $('.as-scene-revise', card).hidden = !expanded;
      $('[data-as-action="expand-revise"]', card).setAttribute('aria-expanded', String(expanded));
      $('[data-as-action="revise"]', card).disabled = !editable() || d.dirty || !instruction.trim();
    });
    existing.forEach(node => node.remove());
    $('.as-save').disabled = !editable() || !d.dirty;
    $('.as-render').hidden = !!rev.output?.url;
    $('.as-render').disabled = !editable() || d.dirty || !d.scenes.length;
    $('.as-render').textContent = pending() ? '正在生成…' : '确认全部并生成';
    $('.as-save-state').textContent = A.saving ? '正在保存…' : d.dirty ? '有修改未保存' : historical() ? '历史版本' : '方案已保存';
  }
  function paintPreview() {
    const rev = revision(), options = [...(rev?.output?.url ? [{ ...rev.output, scene_id: '', label: '完整视频' }] : []), ...(rev?.renders || []).filter(r => r.url).map(r => ({ ...r, label: scenes().find(s => s.id === r.scene_id)?.title || '片段' }))];
    if (!options.some(item => item.scene_id === A.selectedScene)) A.selectedScene = options[0]?.scene_id || '';
    const selected = options.find(item => item.scene_id === A.selectedScene), player = $('.as-player'); if (!player) return;
    player.hidden = !selected; $('.as-preview-empty').hidden = !!selected;
    if (selected) {
      if (player.getAttribute('src') !== selected.url) { player.pause(); player.setAttribute('src', selected.url); player.load(); }
      if (selected.thumbnail_url) player.setAttribute('poster', selected.thumbnail_url); else player.removeAttribute('poster');
    } else if (player.hasAttribute('src')) { player.pause(); player.removeAttribute('src'); player.removeAttribute('poster'); player.load(); }
    $('.as-preview-caption').textContent = selected ? `${selected.label} / ${seconds(selected.duration)}` : '';
    $('.as-player-stage').style.setProperty('--as-ratio', (rev?.ratio || A.composer.ratio).replace(':', '/'));
    replaceMarkup($('.as-preview-tabs'), options.map(item => `<button type="button" data-as-action="preview" data-preview-id="${esc(item.scene_id)}" aria-pressed="${item.scene_id === A.selectedScene}">${esc(item.label)}</button>`).join(''));
    const output = rev?.output;
    replaceMarkup($('.as-output-actions'), output?.url ? `<a class="button small" href="${esc(output.url)}" download>下载视频</a><a class="as-text-button" href="/api/animations/${pathId(A.animationId)}/source?revision_id=${pathId(rev.id)}" download>下载工程</a>${ctx.onUseAsset ? '<button type="button" class="button primary small" data-as-action="use">放入剪辑</button>' : ''}` : '');
    const usage = $('.as-usage');
    const usageId = A.animationId || '';
    if (usage && usage.dataset.animationId !== usageId) {
      usage.innerHTML = usageId ? ctx.usageSlot?.('animation', usageId, '本动画用量') || '' : '';
      usage.dataset.animationId = usageId;
    }
  }
  function paintStatus() {
    const request = pending() || latestTask(), job = request?.jobId && S.jobs.find(j => j.id === request.jobId);
    const busy = request && !request.settled;
    const failed = request?.settled && job && !['succeeded', 'queued', 'running'].includes(job.status);
    const label = A.loading ? '读取动画…' : busy ? request.busy ? '正在提交…' : job?.message || (request.jobId ? '正在处理…' : '提交结果待查询') : '';
    replaceMarkup($('.as-status'), label ? `<div class="as-status-line"><span>${esc(label)}</span>${busy ? `<button type="button" class="as-text-button" data-as-action="recover" ${request.busy ? 'disabled' : ''}>${request.jobId ? '刷新状态' : '继续查询'}</button>` : ''}</div>${job && ACTIVE.has(job.status) ? `<progress max="100" value="${Number(job.progress) || 0}" aria-label="动画任务进度"></progress>` : ''}` : '');
    const error = A.error || (failed ? job.error || job.message || '任务未完成' : ''), host = $('.as-error');
    host.hidden = !error;
    replaceMarkup(host, error ? `<span>${esc(error)}</span><div>${failed && ['create', 'render', 'revise', 'retry'].includes(taskAction(request)) ? `<button type="button" class="button small" data-as-action="retry-job">${taskAction(request) === 'create' ? '重试生成方案' : '重试未完成片段'}</button>` : ''}<button type="button" class="as-text-button" data-as-action="refresh">重新读取</button>${A.animationId ? '<button type="button" class="as-text-button" data-as-action="latest">读取当前版本</button>' : ''}</div>` : '');
  }
  function paint() {
    if (!A.visible || !A.root?.isConnected) return;
    paintPicker(); paintComposer(); paintPlan(); paintPreview(); paintStatus(); ctx.onContentChange?.();
  }
  async function loadList() {
    try { const response = await api('/animations'); A.list = response.animations || []; A.listLoaded = true; if (A.visible) paintPicker(); }
    catch (e) { A.error = e.message || '无法读取动画'; paint(); }
  }
  function accept(view, force = false) {
    if (!view?.id) throw Error('动画记录不完整，请重新读取');
    if (!force && A.view?.id === view.id && revision()?.id !== view.revision?.id && draft()?.dirty) {
      A.error = '已有新版本；当前未保存的修改已保留。保存或切换版本后继续。'; return false;
    }
    A.view = view; A.animationId = view.id;
    A.requestedRevision = view.revision?.id && view.revision.id !== view.current_revision_id ? view.revision.id : null;
    if (!A.list.some(item => item.id === view.id)) A.list.unshift(view);
    persist(); syncRoute(view.id, view.revision?.id); return true;
  }
  async function open(id, revisionId = null) {
    const sequence = ++A.sequence; A.animationId = id || null; A.requestedRevision = revisionId; A.error = ''; A.loading = !!id;
    if (!id) { A.view = null; A.selectedScene = ''; persist(); syncRoute(null); paint(); return; }
    if (A.view?.id !== id) A.view = null;
    persist(); paint();
    try {
      const view = await api(`/animations/${pathId(id)}${revisionId ? `?revision_id=${pathId(revisionId)}` : ''}`);
      if (sequence !== A.sequence) return;
      accept(view, true);
      for (const job of [...(view.jobs || [])].sort((a, b) => String(a.created_at || '').localeCompare(String(b.created_at || '')))) {
        S.jobs = [job, ...S.jobs.filter(item => item.id !== job.id)];
        if (!Object.values(A.requests).some(r => r.jobId === job.id)) {
          const key = `recovered-${job.id}`;
          const action = /plan|storyboard/.test(job.kind || '') ? 'create' : /revis/.test(job.kind || '') ? 'revise' : 'render';
          A.requests[key] = { key, action, animationId: id, revisionId: view.revision?.id, jobId: job.id, settled: !ACTIVE.has(job.status), startedAt: job.created_at || '' };
          if (ACTIVE.has(job.status)) void watch(A.requests[key]);
        }
      }
      persist();
    } catch (e) { if (sequence === A.sequence) A.error = e.message || '无法读取动画'; }
    finally { if (sequence === A.sequence) { A.loading = false; paint(); onJobs(S.jobs); } }
  }
  async function refresh() {
    if (!A.animationId || A.reading) return loadList();
    const id = A.animationId, requested = A.requestedRevision; A.reading = true;
    try {
      const view = await api(`/animations/${pathId(id)}${requested ? `?revision_id=${pathId(requested)}` : ''}`);
      if (A.animationId === id && A.requestedRevision === requested) accept(view);
    } catch (e) { A.error = e.message || '无法刷新动画'; }
    finally { A.reading = false; paint(); }
  }
  async function watch(request) {
    try { await track({ job_id: request.jobId }, job => finish(request, job)); onJobs(S.jobs); }
    catch (e) { request.error = '任务已提交，暂时无法读取进度。'; A.error = request.error; persist(); paint(); }
  }
  async function submit(request) {
    if (request.busy) return;
    request.busy = true; A.error = ''; persist(); paint();
    try {
      if (!request.jobId) {
        const response = await api(request.path, request.params);
        if (!response.job_id) throw Error('服务没有返回任务编号，保留了本次提交');
        request.jobId = response.job_id; request.animationId = response.animation_id || request.animationId;
        if (request.action === 'create') { A.animationId = request.animationId; A.view = null; A.requestedRevision = null; }
        persist();
      }
      await watch(request);
    } catch (e) {
      if (!request.jobId && e.status >= 400 && e.status < 500) request.settled = true;
      request.error = e.message || '暂时无法确认提交结果'; A.error = request.error;
    } finally { request.busy = false; persist(); paint(); }
  }
  function request(action, path, body, animationId = A.animationId, sourceAction = action) {
    const key = crypto.randomUUID(), entry = { key, action, sourceAction, path, params: { submission_id: key, ...body }, animationId, revisionId: revision()?.id, jobId: null, settled: false, busy: false, startedAt: new Date().toISOString() };
    A.requests[key] = entry; persist(); return submit(entry);
  }
  async function finish(request, job) {
    if (!request || request.settled || request.finishing || ACTIVE.has(job.status)) return;
    request.finishing = true;
    try {
      if (job.status === 'succeeded') {
        request.animationId = job.result?.animation_id || request.animationId;
        if (!request.animationId) throw Error('任务已完成，动画记录暂时不可用');
        const view = await api(`/animations/${pathId(request.animationId)}`);
        if (A.animationId === request.animationId && !A.requestedRevision && accept(view)) { A.selectedScene = ''; A.error = ''; }
        A.listLoaded = false;
      } else if (A.animationId === request.animationId) A.error = job.error || job.message || '任务未完成';
      request.settled = true; request.error = job.status === 'succeeded' ? '' : job.error || job.message;
    } catch (e) { request.error = e.message; if (A.animationId === request.animationId) A.error = e.message; }
    finally { request.finishing = false; persist(); paint(); }
  }
  function onJobs(jobs = S.jobs) {
    for (const entry of Object.values(A.requests)) {
      const job = entry.jobId && jobs.find(item => item.id === entry.jobId);
      if (job && !entry.settled) void finish(entry, job);
    }
    if (A.visible) { paintStatus(); paintComposer(); }
  }
  async function createPlan() {
    if (Object.values(A.requests).some(r => taskAction(r) === 'create' && !r.settled)) return;
    const brief = A.composer.brief.trim(), duration = Number(A.composer.duration);
    if (!brief || !Number.isFinite(duration) || duration < 2 || duration > 60) { toast('填写视频描述和 2–60 秒时长', true); return; }
    A.animationId = null; A.view = null; A.requestedRevision = null; A.selectedScene = ''; syncRoute(null);
    await request('create', '/animations', { ...A.composer, brief, duration }, null);
  }
  async function savePlan() {
    if (!editable() || !draft()?.dirty) return;
    const id = A.animationId, revId = revision().id, values = draft().scenes.map(s => ({ ...s, title: s.title.trim(), description: s.description.trim(), duration: Number(s.duration) }));
    if (values.some(s => !s.title || !s.description || !Number.isFinite(s.duration) || s.duration < 2 || s.duration > 30)) { toast('补齐片段标题、内容，每段时长须为 2–30 秒', true); return; }
    const frames = values.reduce((sum, scene) => sum + Math.round(scene.duration * 30), 0);
    if (frames < 60 || frames > 1800) { toast('整片时长须为 2–60 秒', true); return; }
    A.saving = true; A.error = ''; paint();
    try {
      const view = await api(`/animations/${pathId(id)}/revisions/${pathId(revId)}`, { expected_revision_id: revId, scenes: values }, 'PATCH');
      A.drafts[revId].dirty = false;
      if (A.animationId === id) { A.requestedRevision = null; accept(view, true); }
      toast('方案已保存');
    } catch (e) { A.error = e.message || '方案保存失败，修改已保留'; }
    finally { A.saving = false; persist(); paint(); }
  }
  async function renderAll() {
    if (!editable() || draft().dirty || revision().output?.url) return;
    await request('render', `/animations/${pathId(A.animationId)}/render`, { revision_id: revision().id });
  }
  async function reviseScene(id, regenerate = false) {
    if (!editable() || draft().dirty || !revision().output?.url) return;
    const instruction = regenerate ? '保留本段已确认的文字、内容、时长与画面要求，重新生成该片段；其他片段保持不变。' : (A.instructions[sceneKey(id)] || '').trim();
    if (!instruction) return;
    await request('revise', `/animations/${pathId(A.animationId)}/revise`, { revision_id: revision().id, scene_id: id, instruction });
  }
  function bind() {
    A.root.addEventListener('input', event => {
      const field = event.target;
      if (field.name?.startsWith('animation-')) { A.composer[field.name.slice(10)] = field.value; persist(); return; }
      const card = field.closest('[data-scene-id]'); if (!card) return;
      if (field.hasAttribute('data-as-instruction')) {
        A.instructions[sceneKey(card.dataset.sceneId)] = field.value;
        $('[data-as-action="revise"]', card).disabled = !editable() || draft().dirty || !field.value.trim();
      } else if (field.dataset.asField && editable()) {
        const item = draft().scenes.find(s => s.id === card.dataset.sceneId); if (!item) return;
        item[field.dataset.asField] = field.value; draft().dirty = true;
        $('.as-save-state').textContent = '有修改未保存'; $('.as-save').disabled = false; $('.as-render').disabled = true;
        all('[data-as-action="regenerate"], [data-as-action="revise"], [data-as-action="expand-revise"]').forEach(button => button.disabled = true);
      }
      persist();
    });
    A.root.addEventListener('change', event => {
      if (event.target.matches('.as-project-select')) void open(event.target.value);
      if (event.target.matches('.as-version-select')) void open(A.animationId, event.target.value === A.view.current_revision_id ? null : event.target.value);
    });
    A.root.addEventListener('submit', event => { if (event.target.matches('.as-composer')) { event.preventDefault(); void createPlan(); } });
    A.root.addEventListener('click', event => {
      const type = event.target.closest('[data-as-type]'); if (type) { A.composer.style = type.dataset.asType; persist(); paintComposer(); return; }
      const button = event.target.closest('[data-as-action]'); if (!button || button.disabled) return;
      const action = button.dataset.asAction, card = button.closest('[data-scene-id]'), id = card?.dataset.sceneId;
      if (action === 'new') return void open(null);
      if (action === 'latest') return void open(A.animationId);
      if (action === 'refresh') return void refresh();
      if (action === 'save') return void savePlan();
      if (action === 'render') return void renderAll();
      if (action === 'recover') { const entry = pending(); if (entry) return void submit(entry); }
      if (action === 'retry-job') { const entry = latestTask(); if (entry?.jobId && !pending()) return void request('retry', `/animations/${pathId(A.animationId)}/retry`, { job_id: entry.jobId }, A.animationId, taskAction(entry)); }
      if (action === 'preview' || action === 'preview-scene') { A.selectedScene = action === 'preview' ? button.dataset.previewId : id; paintPreview(); return; }
      if (action === 'expand-revise') {
        const key = sceneKey(id); A.expanded.has(key) ? A.expanded.delete(key) : A.expanded.add(key);
        paintPlan(); if (A.expanded.has(key)) $('[data-as-instruction]', card)?.focus({ preventScroll: true }); return;
      }
      if (action === 'revise' || action === 'regenerate') return void reviseScene(id, action === 'regenerate');
      if (action === 'use' && revision()?.output?.asset_id && ctx.onUseAsset) {
        button.disabled = true;
        Promise.resolve(ctx.onUseAsset(revision().output.asset_id)).catch(e => toast(e.message || '无法放入剪辑', true)).finally(() => { if (button.isConnected) button.disabled = false; });
      }
    });
  }
  function render() {
    const entering = !A.visible; A.visible = true; mount(); paint();
    if (!A.listLoaded) void loadList();
    const linkedAnimation = new URLSearchParams(location.hash.split('?')[1] || '').has('animation_id');
    if (entering && !linkedAnimation) {
      if (A.animationId && !A.view) void open(A.animationId, A.requestedRevision);
      else if (A.view) syncRoute(A.view.id, A.view.revision?.id);
    }
    if (entering) for (const entry of Object.values(A.requests)) if (!entry.settled && entry.jobId) void watch(entry);
    onJobs(S.jobs);
  }
  function leave() { A.visible = false; $('.as-player')?.pause(); persist(); }
  return { render, leave, onJobs, open };
}
