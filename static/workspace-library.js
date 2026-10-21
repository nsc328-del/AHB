import {createCutInspector} from './cut-inspector.js?v=20261021-action1';
const dateText = value => {
  if (!value) return '时间未知';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? '时间未知' : date.toLocaleString('zh-CN', {
    year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false,
  });
};

export function createWorkspaceLibrary({S, api, esc, fmt}) {
  const selectedRenders = new Map();
  const cutInspector = createCutInspector({api,esc});
  let sequence = 0;
  let projects = [];
  const $ = selector => document.querySelector(selector);
  const routeFor = (groupId, projectId) => `#workspace/${encodeURIComponent(groupId)}${projectId ? `/${encodeURIComponent(projectId)}` : ''}`;
  const titleOf = project => project.title || project.plan?.title || '未命名作品';
  const isPreview = render => render?.candidate_preview && !render.adopted;
  const renderLabel = render => `${isPreview(render) ? '预览' : '成片'} v${render.version || 1}`;
  const rendersOf = project => [...(project.renders || []), ...(project.candidate_renders || [])].sort((a, b) =>
    Number(isPreview(a)) - Number(isPreview(b)) || String(b.created_at || '').localeCompare(String(a.created_at || '')) || Number(b.version || 0) - Number(a.version || 0));
  const groupOf = project => {
    const group = project.business_project;
    if (group?.id && group?.name) return {id: String(group.id), name: String(group.name)};
    return {id: 'ungrouped', name: '未分组'};
  };
  const localMedia = value => {
    if (!value) return '';
    try {
      const url = new URL(value, location.origin);
      return url.origin === location.origin && url.pathname.startsWith('/api/media/') ? esc(url.href) : '';
    } catch { return ''; }
  };
  const groupsOf = items => {
    const groups = new Map();
    for (const project of items) {
      const group = groupOf(project);
      if (!groups.has(group.id)) groups.set(group.id, {...group, projects: []});
      groups.get(group.id).projects.push(project);
    }
    return [...groups.values()].sort((a, b) =>
      (a.id === 'ungrouped') - (b.id === 'ungrouped') || a.name.localeCompare(b.name, 'zh-CN'));
  };
  function projectCard(project, group) {
    const renders = rendersOf(project), latest = renders[0];
    const preview = localMedia(latest?.thumbnail_url);
    return `<a class="workspace-film-card" href="${routeFor(group.id, project.id)}">
      <div class="workspace-film-cover">${preview ? `<img src="${preview}" alt="" loading="lazy">` : '<span class="workspace-film-placeholder" aria-hidden="true">▷</span>'}
        <span class="workspace-film-badge">${latest ? fmt(latest.duration) : project.plan ? '仅有方案' : '方案未完成'}</span>
      </div>
      <div class="workspace-film-caption"><h2>${esc(titleOf(project))}</h2><p>${esc(dateText(latest?.created_at || project.created_at))}</p><span>${[project.renders?.length ? `${project.renders.length} 个成片版本` : '', project.candidate_renders?.length ? `${project.candidate_renders.length} 条待选预览` : ''].filter(Boolean).join(' · ') || '尚无成片'}</span></div>
    </a>`;
  }
  function homeHTML(groups) {
    return `<section class="section business-workspaces"><div class="section-head"><h1>工作空间</h1><span class="muted">${projects.length} 部作品</span></div>
      ${groups.length ? `<div class="business-workspace-grid">${groups.map(group => {
        const finished = group.projects.filter(project => project.renders?.length).length;
        const previews = group.projects.filter(project => !project.renders?.length && project.candidate_renders?.length).length;
        const pending = group.projects.length - finished - previews;
        return `<a class="business-workspace" href="${routeFor(group.id)}"><div class="workspace-mark">${esc(group.name.slice(0, 1))}</div><h2>${esc(group.name)}</h2><p>${[finished ? `${finished} 部成片` : '', previews ? `${previews} 部待选预览` : '', pending ? `${pending} 部未成片` : ''].filter(Boolean).join(' · ')}</p><div class="workspace-platforms">${esc(dateText(group.projects[0]?.updated_at))}</div><span class="workspace-open">查看作品 <span aria-hidden="true">↗</span></span></a>`;
      }).join('')}</div>` : '<div class="data-empty">还没有作品。</div>'}
    </section>`;
  }
  function groupHTML(group) {
    const finished = group.projects.filter(project => rendersOf(project).length);
    const pending = group.projects.filter(project => !rendersOf(project).length);
    return `<section class="section workspace-film-library"><a class="back-link" href="#workspace">‹ 工作空间</a><div class="section-head"><h1>${esc(group.name)}</h1><span class="muted">${group.projects.length} 部作品</span></div>
      ${finished.length ? `<div class="workspace-film-grid">${finished.map(project => projectCard(project, group)).join('')}</div>` : '<div class="data-empty">还没有成片。</div>'}
      ${pending.length ? `<section class="workspace-draft-section"><div class="strip-heading"><h2>未成片</h2><span class="muted">${pending.length} 部</span></div><div class="workspace-film-grid">${pending.map(project => projectCard(project, group)).join('')}</div></section>` : ''}
    </section>`;
  }
  function detailHTML(group, project) {
    const renders = rendersOf(project);
    const render = renders.find(item => item.id === selectedRenders.get(project.id)) || renders[0];
    const plan = render ? (project.versions || []).find(item => item.id === render.plan_id) : project.plan;
    if (render) selectedRenders.set(project.id, render.id);
    const media = localMedia(render?.url), preview = localMedia(render?.thumbnail_url), report = localMedia(project.review_report_url);
    const animationRevision = render?.animation_revision_id || plan?.origin?.revision_id;
    const animation = project.animation_project_id ? new URLSearchParams({animation_id: project.animation_project_id,
      ...(animationRevision ? {revision_id: animationRevision} : {})}) : null;
    return `<section class="section workspace-film-detail" data-workspace-project="${esc(project.id)}"><a class="back-link" href="${routeFor(group.id)}">‹ ${esc(group.name)}</a>
      <div class="section-head"><div><h1>${esc(titleOf(project))}</h1><p class="muted">${esc(dateText(project.created_at))}</p></div>${media || animation || report ? `<div class="actions">${report ? `<a class="button" href="${report}" target="_blank" rel="noopener">实测报告 ↗</a>` : ''}${animation ? `<a class="button" href="${esc(`#generate/animation?${animation}`)}">编辑动画</a>` : ''}${media ? `<a class="button" href="${media}" download>${isPreview(render) ? '下载预览' : '下载成片'}</a>` : ''}</div>` : ''}</div>
      ${renders.length ? `<div class="workspace-render-versions" role="group" aria-label="视频版本">${renders.map(item => `<button type="button" class="button small ${item.id === render?.id ? 'primary' : ''}" data-workspace-render="${esc(item.id)}" aria-pressed="${item.id === render?.id}">${esc(renderLabel(item))}<span>${esc(dateText(item.created_at))}</span></button>`).join('')}</div>
        ${render.compiled_timeline?.clips?.length ? `<div class="workspace-view-switch" role="group" aria-label="查看方式"><button type="button" data-cut-view="film" aria-pressed="true">成片</button><button type="button" data-cut-view="inspect" aria-pressed="false">剪辑拆解</button></div><section class="workspace-cut-inspector" hidden aria-label="剪辑拆解"></section>` : ''}<div class="workspace-film-player">${media ? `<video src="${media}" ${preview ? `poster="${preview}"` : ''} controls playsinline preload="metadata"></video>` : '<div class="data-empty">成片文件不可用。</div>'}</div>
        <div class="workspace-film-meta"><span>${esc(renderLabel(render))}${isPreview(render) ? ' · 待选择' : ''}${project.latest_candidate_batch_status === 'failed' ? ' · 本批未全部完成' : ''}</span><span>${fmt(render.duration)}${render.width && render.height ? ` · ${Number(render.width)} × ${Number(render.height)}` : ''}</span></div>`
        : `<div class="workspace-unrendered"><span aria-hidden="true">▷</span><h2>${project.plan ? '仅有剪辑方案' : '方案未完成'}</h2></div>`}
      ${plan?.summary ? `<p class="workspace-film-summary">${esc(plan.summary)}</p>` : ''}
    </section>`;
  }
  function commit(markup) {
    const app = $('#app'), oldDetail = app.querySelector('[data-workspace-project]');
    const oldPlayer = oldDetail?.querySelector('.workspace-film-player video');
    const template = document.createElement('template');
    template.innerHTML = markup;
    const nextDetail = template.content.querySelector('[data-workspace-project]');
    const nextPlayer = nextDetail?.querySelector('.workspace-film-player video');
    if (oldPlayer && nextPlayer && oldDetail.dataset.workspaceProject === nextDetail.dataset.workspaceProject &&
        oldPlayer.getAttribute('src') === nextPlayer.getAttribute('src')) {
      const sections = ['.back-link', '.section-head', '.workspace-render-versions',
        '.workspace-view-switch', '.workspace-cut-inspector', '.workspace-film-player', '.workspace-film-meta', '.workspace-film-summary'];
      for (const [index, selector] of sections.entries()) {
        if (['.workspace-film-player','.workspace-cut-inspector','.workspace-view-switch'].includes(selector)) continue;
        const before = oldDetail.querySelector(selector), after = nextDetail.querySelector(selector);
        if (before && after) {
          if (before.outerHTML !== after.outerHTML) before.replaceWith(after);
        } else if (before) before.remove();
        else if (after) {
          const following = sections.slice(index + 1).map(item => oldDetail.querySelector(item)).find(Boolean);
          oldDetail.insertBefore(after, following || null);
        }
      }
      return;
    }
    oldDetail?.querySelectorAll('video').forEach(video => video.pause());
    app.replaceChildren(template.content);
  }
  function renderCurrent() {
    const [, encodedGroup, encodedProject] = S.route.split('/');
    const groups = groupsOf(projects);
    if (!encodedGroup) return commit(homeHTML(groups));
    const groupId = decodeURIComponent(encodedGroup);
    const group = groups.find(item => item.id === groupId);
    if (!group) return commit('<section class="section"><a class="back-link" href="#workspace">‹ 工作空间</a><div class="data-empty">项目不存在。</div></section>');
    if (!encodedProject) return commit(groupHTML(group));
    const project = group.projects.find(item => item.id === decodeURIComponent(encodedProject));
    if (!project) return commit(`<section class="section"><a class="back-link" href="${routeFor(group.id)}">‹ ${esc(group.name)}</a><div class="data-empty">作品不存在。</div></section>`);
    commit(detailHTML(group, project));
    cutInspector.mount($('[data-workspace-project]'), selectedRenders.get(project.id));
  }
  async function renderWorkspace() {
    const request = ++sequence, route = S.route;
    if (!$('#app .business-workspaces, #app .workspace-film-library, #app .workspace-film-detail')) {
      $('#app').innerHTML = '<section class="section"><div class="section-head"><h1>工作空间</h1></div><div class="loading">读取作品</div></section>';
    }
    try {
      const result = await api('/projects');
      if (request !== sequence || S.route !== route) return;
      projects = result.projects || [];
      S.projects = projects;
      renderCurrent();
    } catch (error) {
      if (request === sequence && S.route === route) commit(`<section class="section"><a class="back-link" href="#workspace">‹ 工作空间</a><div class="data-error">${esc(error.message || '作品暂时无法读取。')}</div></section>`);
    }
  }
  document.addEventListener('click', event => {
    const button = event.target.closest('[data-workspace-render]');
    if (!button || !S.route.startsWith('workspace/')) return;
    const projectId = button.closest('[data-workspace-project]')?.dataset.workspaceProject;
    const project = projects.find(item => item.id === projectId);
    if (!project || !rendersOf(project).some(render => render.id === button.dataset.workspaceRender)) return;
    if (selectedRenders.get(projectId) === button.dataset.workspaceRender) return;
    selectedRenders.set(projectId, button.dataset.workspaceRender);
    renderCurrent();
  });
  return {renderWorkspace};
}
