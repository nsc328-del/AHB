const number = (value, fallback = 0) => Number.isFinite(Number(value)) ? Number(value) : fallback;
const stableJSON = value => JSON.stringify(value, function (key, item) {
  return item && typeof item === 'object' && !Array.isArray(item)
    ? Object.keys(item).sort().reduce((object, name) => { object[name] = item[name]; return object; }, {}) : item;
});
const plainEscape = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const shortTime = value => {
  const seconds = Math.max(0, number(value));
  return `${String(Math.floor(seconds / 60)).padStart(2, '0')}:${(seconds % 60).toFixed(1).padStart(4, '0')}`;
};

// Match the renderer's playback fields, not a model's labels or explanations.
// New rendering effects must also enter this key before they can share a prefix.
export function candidateClipSignature(clip, plan = {}) {
  const shot = {asset_id:String(clip.asset_id || '')};
  shot.start = number(clip.start);
  shot.end = number(clip.end, shot.start + number(clip.duration));
  shot.duration = number(clip.duration, shot.end - shot.start);
  shot.speed = number(clip.speed, 1);
  shot.caption = String(clip.caption || '');
  shot.transition = clip.transition || 'cut';
  shot.transition_duration = shot.transition === 'fade' ? number(clip.transition_duration, .25) : 0;
  const context = {
    ratio:plan.ratio || '', width:plan.width || null, height:plan.height || null,
    font:plan.font || 'Hiragino Sans GB', music_asset_id:plan.music_asset_id || null,
    music_volume:plan.music_asset_id ? number(plan.music_volume, .16) : null,
    music_duration:plan.music_asset_id ? number(plan.duration) : null,
  };
  return stableJSON({shot, context});
}

export function orderedCandidatePlans(result) {
  const plans = result?.plans || [], lookup = new Map(plans.map(plan => [plan.id, plan]));
  const order = result?.batch?.display_order?.length ? result.batch.display_order : result?.batch?.plan_ids || plans.map(plan => plan.id);
  return [...new Set([...order, ...plans.map(plan => plan.id)])].map(id => lookup.get(id)).filter(Boolean);
}

export function buildCandidateForest(result) {
  const plans = orderedCandidatePlans(result), roots = [], nodes = [], leaves = [], paths = new Map();
  const add = (siblings, clip, plan, depth, time, parent) => {
    const signature = candidateClipSignature(clip, plan);
    let node = siblings.find(item => item.signature === signature);
    if (!node) {
      node = {id:`cg-node-${nodes.length}`, signature, clip, depth, time, parent, children:[], endings:[], planIds:[]};
      nodes.push(node); siblings.push(node);
    }
    node.planIds.push(plan.id);
    return node;
  };
  for (const plan of plans) {
    let siblings = roots, parent = null, time = 0;
    const path = [];
    (plan.clips || []).forEach((clip, depth) => {
      parent = add(siblings, clip, plan, depth, time, parent);
      path.push(parent); siblings = parent.children; time += number(clip.duration, number(clip.end) - number(clip.start));
    });
    paths.set(plan.id, path);
    if (parent) parent.endings.push(plan);
    else leaves.push({plan, node:null, row:leaves.length});
  }
  let row = leaves.length;
  function place(node) {
    const rows = [];
    for (const plan of node.endings) {
      const leaf = {plan, node, row:row++};
      leaves.push(leaf); rows.push(leaf.row);
    }
    for (const child of node.children) rows.push(...place(child));
    node.row = (Math.min(...rows) + Math.max(...rows)) / 2;
    return rows;
  }
  for (const root of roots) { place(root); row += .32; }
  return {plans, roots, nodes, leaves, paths, rows:Math.max(1, row - .32), depth:Math.max(1, ...nodes.map(node => node.depth + 1))};
}

export function createCandidateGraph(ctx = {}) {
  const esc = ctx.esc || plainEscape, fmt = ctx.fmt || shortTime;
  const G = {result:null, forest:null, host:null, root:null, player:null, playerKey:null, playerErrorKey:null, activePlanId:null, batchId:null, graphKey:null, viewPending:new Map(), localViewed:new Set(), retrying:new Set()};
  const $ = selector => G.root?.querySelector(selector);
  const all = selector => [...(G.root?.querySelectorAll(selector) || [])];
  const asset = id => (ctx.S?.assets || ctx.assets || []).find(item => item.id === id);
  const name = plan => plan?.candidate_strategy?.label || plan?.title || '候选方案';
  const letter = id => String.fromCharCode(65 + Math.max(0, G.forest.plans.findIndex(plan => plan.id === id)));
  const preview = id => G.result?.previews?.[id] || {status:'pending'};
  const ready = id => preview(id).status === 'succeeded' && !!preview(id).render?.url;
  const allReady = () => G.forest.plans.length === (G.result?.batch?.count || G.forest.plans.length) && G.forest.plans.every(plan => ready(plan.id));
  const selectLabel = id => adopted(id) ? '已采用' : allReady() ? '采用这条' : '等待全部预览';
  const explored = plan => !!plan?.candidate_strategy?.exploration || G.result?.batch?.exploration_plan_id === plan?.id;
  const adopted = id => ctx.S?.project?.plan_id === id;
  const viewed = id => G.localViewed.has(id) || (G.result?.events || []).some(event => event.event_type === 'viewed' && event.plan_id === id);
  const stateText = id => ready(id) ? '预览已就绪' : ({queued:'等待预览', pending:'等待预览', running:'正在生成预览', failed:'预览失败', needs_configuration:'预览未完成'}[preview(id).status] || '等待预览');
  const report = error => {
    const text = error?.message || '操作未完成，请重试。';
    const el = $('.cg-operation-error');
    if (el) { el.textContent = text; el.hidden = false; }
    ctx.onError?.(error);
  };
  const image = (clip, className = '') => {
    const a = asset(clip.asset_id);
    return a?.thumbnail_url ? `<img class="${className}" src="${esc(a.thumbnail_url)}" alt="" loading="lazy">` : `<span class="cg-thumb-empty ${className}" aria-hidden="true"></span>`;
  };
  function makeRoot() {
    const root = document.createElement('section');
    root.className = 'cg-root';
    root.setAttribute('aria-label', '候选成片分支');
    root.innerHTML = `<div class="cg-summary"></div><div class="cg-scroll" tabindex="0" aria-label="剪辑分支图，可横向滚动"><div class="cg-canvas"></div></div><div class="cg-mobile-list"></div><section class="cg-inspector" aria-label="候选预览"><div class="cg-preview-media"><div class="cg-preview-player"></div><div class="cg-preview-state"></div></div><div class="cg-preview-copy"></div></section><p class="cg-operation-error" role="alert" hidden></p>`;
    root.addEventListener('click', event => {
      const button = event.target.closest('[data-cg-action]');
      if (!button) return;
      const {cgAction:action, planId:id, nodeId} = button.dataset;
      if (action === 'open') { activate(id, {record:true}); return; }
      if (action === 'node') {
        const node = G.forest.nodes.find(item => item.id === nodeId);
        if (!node) return;
        const target = node.planIds.includes(id) ? id : node.planIds.includes(G.activePlanId) ? G.activePlanId : node.planIds[0];
        activate(target, {record:true, time:node.time}); return;
      }
      if (action === 'seek') { activate(G.activePlanId, {record:true, time:number(button.dataset.time)}); return; }
      if (action === 'select') {
        if (!allReady() || !ready(id) || !viewed(id) || adopted(id)) return;
        button.disabled = true;
        Promise.resolve(ctx.onSelect?.(id)).catch(report).finally(() => paintSelection());
      }
      if (action === 'retry') {
        if (G.retrying.has(id)) return;
        G.retrying.add(id); button.disabled = true;
        Promise.resolve(ctx.onRetry?.(id)).catch(report).finally(() => { G.retrying.delete(id); renderInspector(); });
      }
    });
    return root;
  }
  function statusHTML(id) {
    return `<span class="cg-preview-status ${ready(id) ? 'ready' : ['failed','needs_configuration'].includes(preview(id).status) ? 'failed' : ''}">${esc(stateText(id))}</span>`;
  }
  function terminalHTML(plan, classes = '', style = '') {
    return `<button type="button" class="cg-terminal ${classes}" style="${style}" data-cg-action="open" data-plan-id="${esc(plan.id)}" aria-pressed="${G.activePlanId === plan.id}"><span class="cg-terminal-top"><span class="cg-letter">${letter(plan.id)}</span><strong>${esc(name(plan))}</strong>${explored(plan)?'<span class="cg-exploration">探索片</span>':''}</span><span class="cg-terminal-meta"><span>${esc(fmt(plan.duration))}</span><span>${(plan.clips || []).length} 个镜头</span></span>${statusHTML(plan.id)}</button>`;
  }
  function draw() {
    const forest = G.forest, xStep = 184, nodeWidth = 142, nodeHeight = 58, rowStep = 110, offset = 18;
    const endpointX = forest.depth * xStep + offset, width = endpointX + 230, height = forest.rows * rowStep + offset * 2;
    const x = node => offset + node.depth * xStep, y = node => offset + node.row * rowStep + 44;
    const paths = [];
    for (const node of forest.nodes) {
      if (!node.parent) continue;
      const from = node.parent, fromX = x(from) + nodeWidth, toX = x(node), bend = (fromX + toX) / 2;
      paths.push(`<path data-cg-path="${esc(node.planIds.join(' '))}" d="M${fromX},${y(from)} C${bend},${y(from)} ${bend},${y(node)} ${toX},${y(node)}"/>`);
    }
    for (const leaf of forest.leaves) {
      if (!leaf.node) continue;
      const fromX = x(leaf.node) + nodeWidth, toY = offset + leaf.row * rowStep + 44, bend = fromX + 24;
      paths.push(`<path data-cg-path="${esc(leaf.plan.id)}" d="M${fromX},${y(leaf.node)} C${bend},${y(leaf.node)} ${bend},${toY} ${endpointX},${toY}"/>`);
    }
    $('.cg-canvas').style.width = `${width}px`;
    $('.cg-canvas').style.height = `${height}px`;
    $('.cg-canvas').innerHTML = `<svg class="cg-connections" viewBox="0 0 ${width} ${height}" style="width:${width}px;height:${height}px" aria-hidden="true">${paths.join('')}</svg>${forest.nodes.map(node => `<button type="button" class="cg-node" data-cg-action="node" data-node-id="${node.id}" data-cg-members="${esc(node.planIds.join(' '))}" style="left:${x(node)}px;top:${y(node) - nodeHeight / 2}px" title="${esc(node.clip.label || asset(node.clip.asset_id)?.name || '镜头')}，${esc(fmt(node.clip.duration))}${node.planIds.length>1?`，${node.planIds.length} 条分支共用`:''}">${image(node.clip)}<span><strong>${esc(node.clip.label || `镜头 ${node.depth + 1}`)}</strong><small>${esc(fmt(node.time))} — ${esc(fmt(node.time + number(node.clip.duration)))}</small></span>${node.planIds.length>1?`<b class="cg-shared-count" aria-label="${node.planIds.length} 条分支共用">${node.planIds.length}</b>`:''}</button>`).join('')}${forest.leaves.map(leaf => terminalHTML(leaf.plan, '', `left:${endpointX}px;top:${offset + leaf.row * rowStep}px`)).join('')}`;
    $('.cg-mobile-list').innerHTML = forest.plans.map(plan => `<article class="cg-mobile-branch">${terminalHTML(plan)}<div class="cg-mobile-shots" aria-label="${esc(name(plan))}的镜头顺序">${(forest.paths.get(plan.id) || []).map(node => `<button type="button" data-cg-action="node" data-node-id="${node.id}" data-plan-id="${esc(plan.id)}" title="${esc(node.clip.label || '镜头')}">${image(node.clip)}<span>${esc(node.clip.label || `镜头 ${node.depth + 1}`)}</span><small>${esc(fmt(node.clip.duration))}</small></button>`).join('')}</div></article>`).join('');
    paintSelection();
  }
  function paintSelection() {
    all('[data-cg-action="open"]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.planId === G.activePlanId)));
    all('[data-cg-members]').forEach(button => button.classList.toggle('active', button.dataset.cgMembers.split(' ').includes(G.activePlanId)));
    all('[data-cg-path]').forEach(path => path.classList.toggle('active', path.dataset.cgPath.split(' ').includes(G.activePlanId)));
    all('[data-cg-action="select"]').forEach(button => {
      button.disabled = !allReady() || !ready(button.dataset.planId) || !viewed(button.dataset.planId) || adopted(button.dataset.planId);
      button.textContent = selectLabel(button.dataset.planId);
    });
  }
  function diversityHTML(plan) {
    const d = plan.diversity || {}, values = [];
    // Missing measurements remain absent; no performance score is inferred.
    const similarity = d.history_similarity ?? d.max_history_similarity ?? d.historical_similarity;
    const hasHistory = similarity !== undefined && similarity !== null && Number.isFinite(Number(similarity));
    if (hasHistory) values.push(`<div><dt>与历史成片结构相似度</dt><dd>${Math.round(Number(similarity) * 100)}%</dd></div>`);
    else values.push('<div class="cg-no-history"><dt>历史结构比较</dt><dd>暂无可比历史</dd></div>');
    const penalty = d.novelty_penalty ?? d.history_penalty;
    if (hasHistory && penalty !== undefined && penalty !== null && Number.isFinite(Number(penalty))) values.push(`<div><dt>重复结构降权</dt><dd>${(Number(penalty)*100).toLocaleString('zh-CN', {maximumFractionDigits:1})}%</dd></div>`);
    if (d.minimum_batch_distance !== undefined && d.minimum_batch_distance !== null && Number.isFinite(Number(d.minimum_batch_distance))) values.push(`<div><dt>本批最小结构差异</dt><dd>${Math.round(Number(d.minimum_batch_distance)*100)}%</dd></div>`);
    return values.length ? `<dl class="cg-diversity">${values.join('')}</dl>` : '';
  }
  function renderInspector() {
    if (!G.root) return;
    const plan = G.forest?.plans.find(item => item.id === G.activePlanId), box = $('.cg-inspector');
    box.hidden = !plan;
    if (!plan) { G.player?.pause(); return; }
    const p = preview(plan.id), completed = ready(plan.id), key = completed ? `${plan.id}:${p.render.id || p.render.url}` : null;
    const playerHost = $('.cg-preview-player'), state = $('.cg-preview-state');
    if (completed) {
      if (!G.player) {
        G.player = document.createElement('video');
        G.player.controls = true; G.player.playsInline = true; G.player.preload = 'metadata';
        G.player.addEventListener('play', () => { if (G.activePlanId && ready(G.activePlanId)) void recordView(G.activePlanId); });
        G.player.addEventListener('error', () => {
          if (!G.player?.error) return;
          G.playerErrorKey = G.playerKey;
          state.hidden = false; state.innerHTML = '<span>预览暂时无法播放</span><button type="button" class="button small" data-cg-action="reload-video">重新加载</button>';
          const reload = state.querySelector('button');
          reload.addEventListener('click', () => { G.playerErrorKey = null; G.player.load(); state.hidden = true; }, {once:true});
        });
      }
      if (G.player.parentNode !== playerHost) playerHost.append(G.player);
      if (G.playerKey !== key) {
        G.player.pause(); G.player.src = p.render.url;
        G.player.poster = p.render.thumbnail_url || '';
        G.playerKey = key; G.playerErrorKey = null;
      }
      playerHost.hidden = false; state.hidden = G.playerErrorKey !== key;
    } else {
      G.player?.pause(); playerHost.hidden = true; state.hidden = false;
      state.innerHTML = `<span class="cg-state-symbol" aria-hidden="true">${['failed','needs_configuration'].includes(p.status)?'!':'◷'}</span><strong>${esc(stateText(plan.id))}</strong>${p.error?`<p>${esc(p.error)}</p>`:''}${p.retryable?`<button type="button" class="button small" data-cg-action="retry" data-plan-id="${esc(plan.id)}" ${G.retrying.has(plan.id)?'disabled':''}>${G.retrying.has(plan.id)?'正在提交':'重试这条预览'}</button>`:''}`;
    }
    const copy = $('.cg-preview-copy');
    copy.innerHTML = `<div class="cg-inspector-title"><span class="cg-letter">${letter(plan.id)}</span><h3>${esc(name(plan))}</h3>${explored(plan)?'<span class="cg-exploration">探索片</span>':''}</div><div class="cg-preview-meta"><span>${esc(fmt(plan.duration))}</span><span>${(plan.clips || []).length} 个镜头</span>${viewed(plan.id)?'<span>已查看</span>':''}</div>${plan.difference_summary || plan.change_summary || plan.summary ? `<p class="cg-difference">${esc(plan.difference_summary || plan.change_summary || plan.summary)}</p>`:''}${diversityHTML(plan)}<ol class="cg-clip-list">${(G.forest.paths.get(plan.id) || []).map(node => `<li><button type="button" data-cg-action="seek" data-time="${node.time}"><span>${esc(shortTime(node.time))}</span><strong>${esc(node.clip.label || asset(node.clip.asset_id)?.name || `镜头 ${node.depth + 1}`)}</strong></button>${node.clip.caption?`<p>${esc(node.clip.caption)}</p>`:''}</li>`).join('')}</ol><div class="cg-inspector-actions"><button type="button" class="button primary" data-cg-action="select" data-plan-id="${esc(plan.id)}" ${!allReady() || !completed || !viewed(plan.id) || adopted(plan.id)?'disabled':''}>${selectLabel(plan.id)}</button>${completed && !viewed(plan.id)?`<button type="button" class="button" data-cg-action="open" data-plan-id="${esc(plan.id)}">查看预览</button>`:''}</div>`;
  }
  async function recordView(id) {
    if (!ready(id) || viewed(id)) return;
    if (G.viewPending.has(id)) return G.viewPending.get(id);
    const batchId = G.batchId;
    const task = Promise.resolve().then(() => ctx.onView?.(id)).then(() => {
      if (G.batchId !== batchId) return;
      G.localViewed.add(id); renderInspector(); paintSelection();
    }).catch(report).finally(() => G.viewPending.delete(id));
    G.viewPending.set(id, task); return task;
  }
  function activate(id, {record = false, time = null} = {}) {
    if (!G.forest?.plans.some(plan => plan.id === id)) return;
    G.activePlanId = id;
    $('.cg-operation-error').hidden = true;
    renderInspector(); paintSelection();
    if (time !== null && ready(id) && G.player) {
      const player = G.player, key = G.playerKey;
      const seek = () => { if (G.playerKey === key) player.currentTime = Math.max(0, Math.min(time, number(player.duration, time))); };
      if (player.readyState >= 1) seek(); else player.addEventListener('loadedmetadata', seek, {once:true});
    }
    if (record && ready(id)) void recordView(id);
    ctx.onActivePlanChange?.(id);
  }
  function render(result, host = G.host) {
    if (!host) return;
    G.result = result || {plans:[]}; G.forest = buildCandidateForest(G.result);
    if (!G.root) G.root = makeRoot();
    if (host !== G.host || G.root.parentNode !== host) { G.host = host; host.append(G.root); }
    const batchId = result?.batch?.id || null;
    if (G.batchId !== batchId) {
      G.player?.pause(); G.batchId = batchId; G.activePlanId = null;
      G.localViewed.clear(); G.viewPending.clear(); G.graphKey = null;
    }
    if (!G.forest.plans.some(plan => plan.id === G.activePlanId)) G.activePlanId = G.forest.plans[0]?.id || null;
    const completed = G.forest.plans.filter(plan => ready(plan.id)).length;
    $('.cg-summary').innerHTML = `<span><strong>${G.forest.roots.length}</strong> 种开场<span class="cg-summary-divider"></span><strong>${G.forest.plans.length}</strong> 条分支</span><span class="cg-render-count">${completed} / ${result?.batch?.count || G.forest.plans.length} 条预览已就绪</span>`;
    const graphKey = stableJSON({plans:G.forest.plans, order:result?.batch?.display_order, previews:result?.previews});
    if (graphKey !== G.graphKey) { G.graphKey = graphKey; draw(); }
    renderInspector(); paintSelection();
  }
  return {
    mount:(host, result) => render(result, host), render, activate,
    hide:() => G.player?.pause(),
    dispose:() => { G.player?.pause(); G.root?.remove(); G.host = null; },
    get activePlanId() { return G.activePlanId; },
    get displayedPlanIds() { return window.matchMedia('(max-width:560px)').matches ? G.forest?.plans.map(plan=>plan.id) || [] : G.forest?.leaves.map(leaf=>leaf.plan.id) || []; },
    get player() { return G.player; },
  };
}
