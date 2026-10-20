'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const source = fs.readFileSync(process.argv[2], 'utf8');
const deferred = () => {
  let resolve;
  const promise = new Promise((done) => { resolve = done; });
  return {promise, resolve};
};
const response = (data) => ({ok: true, json: async () => JSON.parse(JSON.stringify(data))});
const take = (k) => ({k, dur: 1, peak: -12, speech: [0, 1]});
const session = (chosen = 1, restored = false) => ({
  name: 'Race test', video: null, room: true,
  lines: [
    {n: 1, text: 'Первая фраза.', takes: [take(1), take(2)], chosen, plan: null},
    {n: 2, text: 'Вторая фраза.', takes: restored ? [take(1)] : [], chosen: restored ? 1 : null, plan: null},
  ],
});

// Keep the real renderer: this DOM only supplies the elements it reads and creates.
function documentStub() {
  const ids = new Map();
  class Element {
    constructor(tagName = 'span', id) {
      this.tagName = tagName.toUpperCase();
      this.id = id;
      this.children = [];
      this.style = {};
      this.className = '';
      this.textContent = '';
      this.classList = {toggle: (name, on) => {
        const classes = new Set(this.className.split(/\s+/).filter(Boolean));
        if (on) classes.add(name); else classes.delete(name);
        this.className = [...classes].join(' ');
      }};
    }
    appendChild(child) { this.children.push(child); }
    get firstElementChild() { return this.children[0]; }
    set innerHTML(html) {
      const removeIds = (el) => {
        if (el.id) ids.delete(el.id);
        el.children.forEach(removeIds);
      };
      this.children.forEach(removeIds);
      this.children = [];
      for (const [, id] of html.matchAll(/id="([^"]+)"/g)) {
        const child = new Element('button', id);
        ids.set(id, child);
        this.appendChild(child);
      }
    }
  }
  for (const id of ['video', 'name', 'screen', 'count', 'prev', 'cur', 'next', 'planlen',
    'state', 'takes', 'lines', 'done', 'fit', 'msg', 'mic', 'sound', 'room', 'run']) {
    ids.set(id, new Element('div', id));
  }
  ids.get('fit').appendChild(new Element());
  Object.assign(ids.get('video'), {src: '', duration: NaN, addEventListener() {}});
  return {
    getElementById: (id) => ids.get(id) || null,
    createElement: (tag) => new Element(tag),
    addEventListener() {},
  };
}

async function page() {
  const document = documentStub();
  const oldRead = deferred();
  const oldReadStarted = deferred();
  const restorePost = deferred();
  const restoreStarted = deferred();
  let reads = 0;
  const context = vm.createContext({
    document, navigator: {}, performance: {now: () => 0}, setTimeout, clearTimeout,
    fetch: async (path, options) => {
      if (path === '/api/session') {
        assert.equal(options.method, undefined);
        reads += 1;
        if (reads === 1) return response(session());
        if (reads === 2) { oldReadStarted.resolve(); return oldRead.promise; }
        assert.equal(reads, 3);
        return response(session(2, true));
      }
      assert.equal(options.method, 'POST');
      if (path === '/api/choose?line=1&take=2') return response({ok: true});
      assert.equal(path, '/api/restore?line=2&take=1');
      restoreStarted.resolve();
      return restorePost.promise;
    },
  });
  vm.runInContext(source + '\nglobalThis.studio = {S, choose, restore, goto};', context, {timeout: 1000});
  await new Promise(setImmediate); // finish the application's initial load(false)
  assert.equal(document.getElementById('cur').textContent, 'Первая фраза.');
  return {studio: context.studio, document, oldRead, oldReadStarted, restorePost, restoreStarted};
}

async function restoreRace(oldResponseDuringPost) {
  const {studio, document, oldRead, oldReadStarted, restorePost, restoreStarted} = await page();
  studio.S.discarded.push({n: 2, k: 1});
  const choosing = studio.choose(2);
  await oldReadStarted.promise;
  const restoring = studio.restore();
  await restoreStarted.promise;
  if (oldResponseDuringPost) {
    oldRead.resolve(response(session(2)));
    await choosing;
  }
  restorePost.resolve(response({ok: true}));
  await restoring;
  if (!oldResponseDuringPost) {
    oldRead.resolve(response(session(2)));
    await choosing;
  }
  assert.equal(studio.S.i, 1, 'restore must stay on the restored line after an old choose finishes');
  assert.equal(studio.S.session.lines[1].chosen, 1);
  assert.equal(studio.S.session.lines[1].takes[0].k, 1);
  assert.equal(studio.S.discarded.length, 0);
  assert.equal(studio.S.changingTake, false);
  assert.equal(document.getElementById('cur').textContent, 'Вторая фраза.');
  assert.equal(document.getElementById('count').textContent, 'Фраза 2 из 2');
  const chosen = document.getElementById('takes').children.filter((el) => el.className === 'take chosen');
  assert.equal(chosen.length, 1);
  assert.match(chosen[0].title, /^дубль 1:/);
  assert.equal(document.getElementById('restorebtn'), null);
  assert.equal(document.getElementById('msg').textContent, 'Дубль фразы 2 вернулся и выбран.');
}

async function navigationRace() {
  const {studio, document, oldRead, oldReadStarted} = await page();
  const choosing = studio.choose(2);
  await oldReadStarted.promise;
  await studio.goto(1);
  oldRead.resolve(response(session(2)));
  await choosing;
  assert.equal(studio.S.i, 1, 'an old choose must not undo navigation while its GET was pending');
  assert.equal(document.getElementById('cur').textContent, 'Вторая фраза.');
}

const timeout = setTimeout(() => { throw new Error('studio race test timed out'); }, 5000);
(async () => {
  await restoreRace(false);
  await restoreRace(true);
  await navigationRace();
})().catch((err) => {
  console.error(err);
  process.exitCode = 1;
}).finally(() => clearTimeout(timeout));
