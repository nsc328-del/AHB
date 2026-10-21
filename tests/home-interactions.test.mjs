import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import test from 'node:test';

const source = await readFile(new URL('../static/home-interactions.js', import.meta.url), 'utf8');
const {createHomeInteractions, homeMoveIndex, resolveHomeDrop} = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);

test('asset center replaces; edges insert; moving uses original boundary and rejects no-op', () => {
  const clipIds = ['a', 'b', 'c'];
  const nodes = clipIds.map((clipId, index) => ({clipId, index, rect: {left: index * 140, right: index * 140 + 100, top: 0, bottom: 64}}));
  const slots = [{index: 3, rect: {left: 396, right: 430, top: 0, bottom: 64}}];
  const hit = (type, x, clipId) => resolveHomeDrop({type, point: {x, y: 30}, clipId, nodes, slots, clipIds});
  assert.deepEqual(hit('asset', 190), {mode: 'replace', index: 1, targetClipId: 'b'});
  assert.equal(hit('asset', 145).index, 1);
  assert.equal(hit('asset', 235).index, 2);
  assert.equal(hit('asset', 410).index, 3);
  assert.equal(hit('node', 190, 'a').mode, 'insert');
  assert.equal(hit('node', 145, 'a'), null); // a already sits immediately before b.
  assert.equal(homeMoveIndex(0, 3, 3), 2);
  assert.equal(homeMoveIndex(2, 0, 3), 0);
  assert.equal(homeMoveIndex(1, 1, 3), null);
  assert.equal(homeMoveIndex(1, 2, 3), null);
  assert.equal(homeMoveIndex(-1, 2, 3), null);
  assert.equal(hit('asset', 600), null);
});

class Surface {
  constructor() { this.handlers = new Map(); }
  addEventListener(name, fn) { const set = this.handlers.get(name) || new Set(); set.add(fn); this.handlers.set(name, set); }
  removeEventListener(name, fn) { this.handlers.get(name)?.delete(fn); }
  fire(name, event = {}) { for (const fn of [...(this.handlers.get(name) || [])]) fn({button: 0, pointerId: 1, isPrimary: true, cancelable: true, preventDefault() {}, stopImmediatePropagation() {}, ...event}); }
  count() { return [...this.handlers.values()].reduce((sum, set) => sum + set.size, 0); }
}
class Element extends Surface {
  constructor(kind = '') {
    super(); this.kind = kind; this.dataset = {}; this.children = []; this.textContent = kind;
    this.isConnected = true; this.scrollLeft = 0; this.scrollTop = 0;
    this.style = {transform: '', transition: '', setProperty(key, value) { this[key] = value; }, removeProperty(key) { delete this[key]; }};
    const classes = new Set(); this.classList = {add: (...names) => names.forEach(name => classes.add(name)), remove: (...names) => names.forEach(name => classes.delete(name)), contains: name => classes.has(name)};
    this.rect = {left: 0, top: 0, width: 100, height: 64, right: 100, bottom: 64};
  }
  closest(selector) {
    if (selector === '*') return this;
    if (this.kind === 'material' && selector.includes('.hc-material')) return this;
    return null;
  }
  getBoundingClientRect() { return this.rect; }
  querySelector(selector) { return this.queries?.[selector] || null; }
  querySelectorAll() { return []; }
  setAttribute() {}
  append(child) { this.children.push(child); child.parent = this; }
  remove() { if (this.parent) this.parent.children = this.parent.children.filter(child => child !== this); this.isConnected = false; }
  contains(child) { return child === this || this.children.some(item => item.contains(child)); }
  setPointerCapture() {}
  hasPointerCapture() { return false; }
}
function fixture(onDragStart) {
  const doc = new Surface(), win = new Surface(), root = new Element('root'), viewport = new Element('viewport'), canvas = new Element('canvas'), material = new Element('material'), node = new Element('node');
  material.dataset.assetId = 'asset';
  node.rect = {left: 200, top: 0, width: 100, height: 64, right: 300, bottom: 64};
  viewport.rect = {left: 180, top: 0, width: 400, height: 200, right: 580, bottom: 200};
  root.queries = {'.hc-graph-scroll': viewport, '.hc-canvas': canvas};
  root.append(material); root.append(viewport); viewport.append(canvas); canvas.append(node);
  doc.defaultView = win; doc.body = new Element('body'); doc.createElement = () => new Element(); root.ownerDocument = doc;
  win.matchMedia = () => ({matches: true}); win.performance = {now: () => 100};
  const frames = new Map(); let sequence = 0;
  win.requestAnimationFrame = fn => { frames.set(++sequence, fn); return sequence; };
  win.cancelAnimationFrame = id => frames.delete(id);
  const state = {pending: false, branchId: 'one', clips: ['clip'], zoom: 1}, drops = [], dragStates = [];
  const controller = createHomeInteractions({root, getState: () => state, getTargets: () => ({nodes: [{element: node, clipId: 'clip', index: 0}], slots: []}), onDrop: value => { drops.push(value); return {clipId: 'clip'}; }, onDragState: value => { dragStates.push(value); if (value.active) onDragStart?.({material, node}); }});
  return {doc, win, root, viewport, canvas, material, node, state, frames, drops, dragStates, controller};
}

test('active cancellation preserves grab offset, restores source, and removes gesture listeners/frames', () => {
  for (const reason of ['escape', 'blur', 'pointercancel', 'destroy']) {
    const f = fixture();
    f.root.fire('pointerdown', {target: f.material, clientX: 20, clientY: 18});
    f.doc.fire('pointermove', {target: f.material, clientX: 245, clientY: 32});
    const ghost = f.doc.body.children[0];
    assert.equal(ghost.style.transform, 'translate3d(225px,14px,0)');
    assert.ok(f.material.classList.contains('hc-drag-source'));
    assert.ok(f.node.classList.contains('is-drop-replace'));
    if (reason === 'escape') f.doc.fire('keydown', {key: 'Escape'});
    if (reason === 'blur') f.win.fire('blur');
    if (reason === 'pointercancel') f.doc.fire('pointercancel');
    if (reason === 'destroy') f.controller.destroy();
    assert.equal(f.drops.length, 0);
    assert.equal(f.doc.body.children.length, 0);
    assert.equal(f.material.classList.contains('hc-drag-source'), false);
    assert.equal(f.node.classList.contains('is-drop-replace'), false);
    assert.equal(f.frames.size, 0);
    assert.equal(f.doc.count(), 0);
    assert.equal(f.win.count(), 0);
    assert.deepEqual(f.dragStates.map(value => value.active), [true, false]);
    f.controller.destroy();
    assert.equal(f.root.count(), 0);
  }
  const f = fixture(({material, node}) => {
    // A previous FLIP finishes on pickup: the source may move, and targets must use final bounds.
    material.rect = {...material.rect, left: 40, right: 140};
    node.rect = {...node.rect, left: 350, right: 450};
  });
  f.root.fire('pointerdown', {target: f.material, clientX: 20, clientY: 18});
  f.doc.fire('pointermove', {clientX: 400, clientY: 32});
  assert.equal(f.doc.body.children[0].style.transform, 'translate3d(380px,14px,0)');
  assert.ok(f.node.classList.contains('is-drop-replace'));
  f.controller.destroy();
});

test('only one release commits; stale branch cancels; pan never commits; keyboard clicks remain available', async () => {
  const f = fixture();
  f.root.fire('pointerdown', {target: f.material, clientX: 20, clientY: 18});
  f.doc.fire('pointermove', {target: f.material, clientX: 245, clientY: 32});
  f.doc.fire('pointerup', {target: f.material, clientX: 245, clientY: 32});
  f.doc.fire('pointerup', {target: f.material, clientX: 245, clientY: 32});
  await Promise.resolve();
  assert.equal(f.drops.length, 1);
  assert.deepEqual(f.drops[0], {type: 'asset', assetId: 'asset', clipId: undefined, mode: 'replace', index: 0, targetClipId: 'clip'});
  assert.equal(f.doc.body.children.length, 0);
  let suppressed = 0;
  f.root.fire('click', {detail: 0, stopImmediatePropagation() { suppressed++; }});
  assert.equal(suppressed, 0);
  f.root.fire('click', {detail: 1, stopImmediatePropagation() { suppressed++; }});
  assert.equal(suppressed, 1);
  f.root.fire('pointerdown', {target: f.material, clientX: 20, clientY: 18});
  f.doc.fire('pointermove', {clientX: 245, clientY: 32});
  f.state.branchId = 'other';
  f.doc.fire('pointerup', {clientX: 245, clientY: 32});
  assert.equal(f.drops.length, 1);
  f.viewport.scrollLeft = 100;
  f.root.fire('pointerdown', {target: f.canvas, clientX: 250, clientY: 150});
  f.doc.fire('pointermove', {clientX: 270, clientY: 160});
  assert.equal(f.viewport.scrollLeft, 80);
  f.doc.fire('pointerup', {clientX: 270, clientY: 160});
  assert.equal(f.drops.length, 1);
  f.controller.destroy();
});
