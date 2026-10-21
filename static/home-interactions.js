// Insertion indices refer to boundaries in the branch before removing a moved clip.
export function homeMoveIndex(from, boundary, length) {
  if (!Number.isInteger(from) || !Number.isInteger(boundary) || from < 0 || from >= length || boundary < 0 || boundary > length) return null;
  const destination = boundary - (from < boundary ? 1 : 0);
  return destination === from ? null : destination;
}

const inside = (point, rect, inset = 0) => point.x >= rect.left - inset && point.x <= rect.right + inset && point.y >= rect.top - inset && point.y <= rect.bottom + inset;

// The caller supplies frozen geometry projected into current viewport coordinates.
// Preview transforms never feed back into the hit test.
export function resolveHomeDrop({type, clipId, point, nodes, slots, clipIds}) {
  const hit = nodes.find(node => inside(point, node.rect));
  let proposal = null;
  if (hit) {
    const fraction = (point.x - hit.rect.left) / Math.max(1, hit.rect.right - hit.rect.left);
    if (type === 'asset' && fraction >= .22 && fraction <= .78) {
      proposal = {mode: 'replace', index: hit.index, targetClipId: hit.clipId};
    } else {
      proposal = {mode: 'insert', index: hit.index + (fraction >= .5 ? 1 : 0), targetClipId: hit.clipId};
    }
  } else {
    const slot = slots.filter(item => inside(point, item.rect, 9)).sort((a, b) =>
      Math.abs(point.x - (a.rect.left + a.rect.right) / 2) - Math.abs(point.x - (b.rect.left + b.rect.right) / 2))[0];
    if (slot) proposal = {mode: 'insert', index: slot.index, targetClipId: clipIds[slot.index] || null};
  }
  if (proposal && type === 'node' && homeMoveIndex(clipIds.indexOf(clipId), proposal.index, clipIds.length) === null) return null;
  return proposal;
}

export function createHomeInteractions({root, getState, getTargets, onDrop, onFeedback = () => {}, onDragState = () => {}}) {
  const doc = root.ownerDocument, win = doc.defaultView;
  const permanent = [], transient = [];
  let gesture = null, disposed = false, suppressClickUntil = 0;
  const listen = (target, name, fn, options, bucket = permanent) => {
    target.addEventListener(name, fn, options);
    bucket.push(() => target.removeEventListener(name, fn, options));
  };
  const removeListeners = bucket => { while (bucket.length) bucket.pop()(); };
  const query = selector => root.querySelector(selector);
  const reducedMotion = () => !!win.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
  const zoom = () => Math.max(.1, Number(getState().zoom) || 1);
  const clips = state => (state.clips || []).map(clip => typeof clip === 'string' ? clip : clip.id);

  function targets() {
    if (getTargets) return getTargets();
    const state = getState(), ids = clips(state);
    return {
      nodes: [...root.querySelectorAll('.hc-node[data-clip-id]')].filter(element => ids.includes(element.dataset.clipId)).map(element => ({element, clipId: element.dataset.clipId, index: ids.indexOf(element.dataset.clipId)})),
      slots: [...root.querySelectorAll('.hc-insert-slot[data-index]')].filter(element => !element.dataset.branchId || element.dataset.branchId === state.branchId).map(element => ({element, index: Number(element.dataset.index)})),
    };
  }

  function captureGeometry(g) {
    const origin = g.canvas.getBoundingClientRect(), scale = zoom(), current = targets();
    const capture = (item, isNode) => {
      const visual = isNode ? item.element.querySelector('.hc-node-summary') || item.element : item.element;
      const r = visual.getBoundingClientRect();
      return {...item, visual, box: {x: (r.left - origin.left) / scale, y: (r.top - origin.top) / scale, width: r.width / scale, height: r.height / scale}};
    };
    g.nodes = (current.nodes || []).filter(item => g.clipIds.includes(item.clipId)).map(item => capture(item, true));
    g.slots = (current.slots || []).filter(item => item.index >= 0 && item.index <= g.clipIds.length).map(item => capture(item, false));
  }

  function project(g, list) {
    const origin = g.canvas.getBoundingClientRect(), scale = zoom();
    return list.map(item => ({...item, rect: {
      left: origin.left + item.box.x * scale, top: origin.top + item.box.y * scale,
      right: origin.left + (item.box.x + item.box.width) * scale,
      bottom: origin.top + (item.box.y + item.box.height) * scale,
    }}));
  }

  function compatible(g) {
    const state = getState();
    return !disposed && !state.pending && state.branchId === g.branchId && JSON.stringify(clips(state)) === g.order;
  }

  function clearPreview(g) {
    for (const item of g.preview || []) {
      item.element.classList.remove('is-drop-replace', 'is-drop-insert', 'hc-shift-preview');
      if (item.visual) {
        item.visual.style.transform = item.transform;
        item.visual.style.transition = item.transition;
        item.visual.style.removeProperty('--hc-preview-x');
      }
    }
    g.preview = [];
  }

  function preview(g, next) {
    const key = next ? `${next.mode}:${next.index}:${next.targetClipId || ''}` : '';
    if (key === g.targetKey) return;
    clearPreview(g); g.target = next; g.targetKey = key;
    if (!next) { onFeedback(''); return; }
    if (next.mode === 'replace') {
      const node = g.nodes.find(item => item.clipId === next.targetClipId);
      if (node) { node.element.classList.add('is-drop-replace'); g.preview.push({element: node.element}); }
      onFeedback('松手替换镜头');
      return;
    }
    const slot = g.slots.find(item => item.index === next.index);
    if (slot) { slot.element.classList.add('is-drop-insert'); g.preview.push({element: slot.element}); }
    for (const node of g.nodes.filter(item => [next.index - 1, next.index].includes(item.index) && item.clipId !== g.clipId)) {
      const shift = (node.index < next.index ? -8 : 8) / zoom();
      g.preview.push({element: node.element, visual: node.visual, transform: node.visual.style.transform, transition: node.visual.style.transition});
      node.element.classList.add('hc-shift-preview');
      node.visual.style.setProperty('--hc-preview-x', `${shift}px`);
      node.visual.style.transition = reducedMotion() ? 'none' : 'transform 150ms cubic-bezier(.2,.8,.2,1)';
      node.visual.style.transform = `translateX(${shift}px)`;
    }
    onFeedback(g.type === 'asset' ? '松手插入镜头' : '松手调整顺序');
  }

  function makeGhost(g) {
    const r = g.sourceRect;
    // A shallow visual snapshot: no live players, forms or IDs get duplicated.
    const ghost = doc.createElement('div');
    ghost.className = `hc-drag-ghost hc-drag-ghost--${g.type}`;
    ghost.setAttribute('aria-hidden', 'true');
    const picture = g.source.querySelector('img');
    if (picture) { const img = doc.createElement('img'); img.src = picture.currentSrc || picture.src; img.alt = ''; img.draggable = false; ghost.append(img); }
    const label = doc.createElement('span');
    label.textContent = g.source.querySelector('.hc-node-title, strong')?.textContent || [...g.source.querySelectorAll('span')].map(node => node.textContent.trim()).filter(Boolean).at(-1) || g.source.textContent.trim();
    ghost.append(label);
    Object.assign(ghost.style, {position: 'fixed', left: '0', top: '0', width: `${r.width}px`, height: `${r.height}px`, margin: '0', boxSizing: 'border-box', pointerEvents: 'none', zIndex: '10000', transformOrigin: 'top left', transition: 'none'});
    g.ghost = ghost; g.grabX = g.startX - r.left; g.grabY = g.startY - r.top;
    doc.body.append(ghost); g.source.classList.add('hc-drag-source'); root.classList.add('is-dragging');
  }

  function locateGhost(g) {
    if (!g.ghost) return;
    g.x = g.point.x - g.grabX; g.y = g.point.y - g.grabY;
    g.ghost.style.transform = `translate3d(${g.x}px,${g.y}px,0)`;
  }

  function updateTarget(g) {
    const rect = g.viewport.getBoundingClientRect();
    preview(g, inside(g.point, rect) ? resolveHomeDrop({type: g.type, clipId: g.clipId, point: g.point, nodes: project(g, g.nodes), slots: project(g, g.slots), clipIds: g.clipIds}) : null);
  }

  function tick(g, now) {
    if (gesture !== g || g.phase !== 'dragging') return;
    if (!compatible(g)) { cancel(); return; }
    const dt = Math.min(32, g.lastFrame ? now - g.lastFrame : 16); g.lastFrame = now;
    const r = g.viewport.getBoundingClientRect(), p = g.point;
    if (inside(p, r)) {
      const edge = 44, speed = .65 * dt;
      const velocity = (v, start, end) => v < start + edge ? -Math.min(1, (start + edge - v) / edge) : v > end - edge ? Math.min(1, (v - end + edge) / edge) : 0;
      g.viewport.scrollLeft += velocity(p.x, r.left, r.right) * speed;
      g.viewport.scrollTop += velocity(p.y, r.top, r.bottom) * speed;
    }
    updateTarget(g);
    g.frame = win.requestAnimationFrame(time => tick(g, time));
  }

  function releasePointer(g) {
    removeListeners(transient);
    if (g.frame) win.cancelAnimationFrame(g.frame);
    try { if (g.source.hasPointerCapture?.(g.pointerId)) g.source.releasePointerCapture(g.pointerId); } catch {}
    root.classList.remove('is-dragging', 'is-panning');
    clearPreview(g); onFeedback('');
    if (g.active) onDragState({active: false, type: g.type});
  }

  function cleanup(g) {
    g.animation?.cancel(); g.flashAnimation?.cancel();
    g.receiver?.classList.remove('hc-drop-receiver', 'hc-drop-flash');
    g.source.classList.remove('hc-drag-source');
    g.ghost?.remove();
    if (gesture === g) gesture = null;
  }

  async function settle(g, receiver, returning = false) {
    if (gesture !== g || disposed) { cleanup(g); return; }
    if (!g.ghost || !receiver?.isConnected) { cleanup(g); return; }
    const r = receiver.getBoundingClientRect();
    g.receiver = returning ? null : receiver;
    if (!returning) receiver.classList.add('hc-drop-receiver');
    if (!reducedMotion() && g.ghost.animate) {
      const sx = r.width / Math.max(1, g.sourceRect.width), sy = r.height / Math.max(1, g.sourceRect.height);
      g.animation = g.ghost.animate([
        {transform: `translate3d(${g.x}px,${g.y}px,0) scale(1)`, opacity: 1},
        {transform: `translate3d(${r.left}px,${r.top}px,0) scale(${sx},${sy})`, opacity: 1},
      ], {duration: returning ? 170 : 190, easing: 'cubic-bezier(.2,.8,.2,1)', fill: 'forwards'});
      try { await g.animation.finished; } catch { return; }
      if (gesture !== g || disposed) return;
    }
    g.ghost.remove(); g.source.classList.remove('hc-drag-source');
    receiver.classList.remove('hc-drop-receiver');
    if (!returning) {
      receiver.classList.add('hc-drop-flash');
      if (!reducedMotion() && receiver.animate) {
        g.flashAnimation = receiver.animate([{boxShadow: '0 0 0 3px rgba(58,65,237,.28)'}, {boxShadow: '0 0 0 0 rgba(58,65,237,0)'}], {duration: 300, easing: 'ease-out'});
        try { await g.flashAnimation.finished; } catch { return; }
      }
    }
    cleanup(g);
  }

  function cancel({animate = true} = {}) {
    const g = gesture; if (!g) return;
    if (g.active) suppressClickUntil = win.performance.now() + 400;
    const canReturn = g.phase === 'dragging';
    g.phase = 'cancelled'; releasePointer(g);
    if (animate && canReturn && !disposed) void settle(g, g.source, true); else cleanup(g);
  }

  function move(event) {
    const g = gesture; if (!g || event.pointerId !== g.pointerId || !['pressed', 'dragging'].includes(g.phase)) return;
    if (!compatible(g)) { cancel(); return; }
    g.point = {x: event.clientX, y: event.clientY};
    if (!g.active) {
      if (Math.hypot(event.clientX - g.startX, event.clientY - g.startY) < 5) return;
      g.active = true; g.phase = 'dragging';
      // The layout owner finishes any earlier layout motion before the target snapshot.
      // Pickup geometry remains the pointerdown geometry, preserving the contact point.
      onDragState({active: true, type: g.type});
      if (g.type === 'pan') { root.classList.add('is-panning'); }
      else { captureGeometry(g); makeGhost(g); g.frame = win.requestAnimationFrame(time => tick(g, time)); }
    }
    if (event.cancelable) event.preventDefault();
    if (g.type === 'pan') {
      g.viewport.scrollLeft = g.scrollX - (event.clientX - g.startX);
      g.viewport.scrollTop = g.scrollY - (event.clientY - g.startY);
    } else { locateGhost(g); updateTarget(g); }
  }

  async function end(event) {
    const g = gesture; if (!g || event.pointerId !== g.pointerId) return;
    move(event); if (gesture !== g || g.phase === 'cancelled') return;
    const target = g.target, valid = compatible(g);
    g.phase = 'settling'; releasePointer(g);
    if (g.active) { suppressClickUntil = win.performance.now() + 400; if (event.cancelable) event.preventDefault(); }
    if (!g.active || g.type === 'pan') { cleanup(g); return; }
    if (!valid || !target) { void settle(g, g.source, true); return; }
    try {
      const result = await onDrop({type: g.type, assetId: g.assetId, clipId: g.clipId, ...target});
      if (gesture !== g || disposed) return;
      if (result === false) { void settle(g, g.source, true); return; }
      // The owner commits synchronously or resolves after its keyed DOM update.
      const id = result?.clipId || (g.type === 'node' ? g.clipId : target.mode === 'replace' ? target.targetClipId : null);
      const fresh = targets().nodes || [];
      const destination = fresh.find(item => id ? item.clipId === id : item.index === target.index)?.element;
      void settle(g, destination?.querySelector('.hc-node-summary') || destination);
    } catch {
      if (gesture === g) { onFeedback('未能移动，请重试'); void settle(g, g.source, true); }
    }
  }

  function begin(event) {
    if (disposed || getState().pending || event.isPrimary === false || event.button !== 0) return;
    const element = event.target.closest?.('*');
    if (!element || element.closest('.hc-node-editor,input,textarea,select,[contenteditable="true"]')) return;
    const material = element.closest('.hc-material[data-asset-id]'), summary = element.closest('.hc-node-summary');
    const node = summary?.closest('.hc-node[data-clip-id]');
    const state = getState(), ids = clips(state);
    if (summary && (!node || !ids.includes(node.dataset.clipId))) return;
    const viewport = query('.hc-graph-scroll'), canvas = query('.hc-canvas');
    if (!viewport || !canvas) return;
    const pan = !material && !node && viewport.contains(element) && !element.closest('button,a,.hc-node,.hc-insert-slot');
    if (!material && !node && !pan) return;
    cancel({animate: false});
    const source = material || summary || viewport;
    const g = gesture = {type: material ? 'asset' : node ? 'node' : 'pan', assetId: material?.dataset.assetId, clipId: node?.dataset.clipId,
      branchId: state.branchId, clipIds: ids, order: JSON.stringify(ids), source, viewport, canvas, pointerId: event.pointerId,
      startX: event.clientX, startY: event.clientY, point: {x: event.clientX, y: event.clientY}, scrollX: viewport.scrollLeft, scrollY: viewport.scrollTop,
      sourceRect: source.getBoundingClientRect(), phase: 'pressed', active: false, preview: [], target: null};
    listen(doc, 'pointermove', move, {passive: false}, transient);
    listen(doc, 'pointerup', end, undefined, transient);
    listen(doc, 'pointercancel', e => { if (e.pointerId === g.pointerId) cancel(); }, undefined, transient);
    listen(doc, 'keydown', e => { if (e.key === 'Escape') { e.preventDefault(); cancel(); } }, undefined, transient);
    listen(win, 'blur', () => cancel({animate: false}), undefined, transient);
    listen(doc, 'visibilitychange', () => { if (doc.hidden) cancel({animate: false}); }, undefined, transient);
    listen(source, 'lostpointercapture', e => { if (e.pointerId === g.pointerId && g.phase !== 'settling') cancel(); }, undefined, transient);
    try { source.setPointerCapture?.(event.pointerId); } catch {}
  }

  listen(root, 'pointerdown', begin);
  listen(root, 'dragstart', event => { if (event.target.closest?.('.hc-material,.hc-node-summary')) event.preventDefault(); });
  listen(root, 'click', event => { if (event.detail !== 0 && win.performance.now() < suppressClickUntil) { event.preventDefault(); event.stopImmediatePropagation(); } }, true);
  return {cancel, destroy() { if (disposed) return; disposed = true; cancel({animate: false}); removeListeners(permanent); removeListeners(transient); }};
}
