// 外観の初期適用・OS追従・手動指定・保存不可を実際のスクリプトで検証。
const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const code = fs.readFileSync('static/js/theme.js', 'utf8');
function boot({saved = null, dark = false, blocked = false} = {}) {
  const events = {}, choices = [], root = {dataset: {}, style: {}}, meta = {};
  let persisted = saved, installed;
  const media = {matches: dark, addEventListener: (_, fn) => { events.os = fn; }};
  const button = {classList: {contains: () => true}, replaceWith: label => { installed = label; }};
  const document = {documentElement: root, querySelector: () => meta,
    querySelectorAll: query => query === '[data-theme-toggle]' ? [button] : choices,
    addEventListener: (name, fn) => { events[name] = fn; },
    createElement: tag => { const node = {children: [], setAttribute() {}, appendChild(child) { this.children.push(child); }, addEventListener(name, fn) { this[name] = fn; }}; if (tag === 'select') choices.push(node); return node; }};
  const window = {matchMedia: () => media, addEventListener: (name, fn) => { events[name] = fn; }, localStorage: {
    getItem() { if (blocked) throw Error('blocked'); return persisted; },
    setItem(_, value) { if (blocked) throw Error('blocked'); persisted = value; }
  }};
  vm.runInNewContext(code, {window, document});
  return {root, media, events, meta, choices, value: () => persisted, control: () => installed};
}
test('初回描画からOSのダークに一致し、OS変更に追従する', () => {
  const app = boot({dark:true});
  assert.equal(app.root.dataset.theme, 'dark'); assert.equal(app.root.dataset.themePreference, 'system');
  assert.equal(app.value(), null); // 自動判定結果を手動選択として保存しない。
  app.media.matches = false; app.events.os(); assert.equal(app.root.dataset.theme, 'light');
  app.events.DOMContentLoaded(); assert.equal(app.choices[0].value, 'system');
});
test('手動指定はOSより優先し、自動へ戻すとその場でOSへ一致する', () => {
  const app = boot({saved:'light', dark:true}); app.events.DOMContentLoaded();
  assert.equal(app.root.dataset.theme, 'light'); app.events.os(); assert.equal(app.root.dataset.theme, 'light');
  app.choices[0].value = 'dark'; app.choices[0].change(); assert.equal(app.value(), 'dark');
  app.media.matches = false; app.events.os(); assert.equal(app.root.dataset.theme, 'dark');
  app.choices[0].value = 'system'; app.choices[0].change(); assert.equal(app.root.dataset.theme, 'light');
  assert.equal(app.meta.content, '#f5f5f7');
});
test('保存禁止・不正な保存値でも表示と選択が動く', () => {
  const app = boot({blocked:true, dark:true}); app.events.DOMContentLoaded();
  app.choices[0].value = 'light'; assert.doesNotThrow(() => app.choices[0].change());
  assert.equal(app.root.dataset.theme, 'light');
  assert.equal(boot({saved:'invalid',dark:true}).root.dataset.theme, 'dark');
});
test('別タブからの変更と保存解除を反映する', () => {
  const app = boot({dark:true}); app.events.DOMContentLoaded();
  app.events.storage({key:'story-manga-appearance',newValue:'light'});
  assert.equal(app.choices[0].value, 'light');
  app.events.storage({key:null,newValue:null}); assert.equal(app.root.dataset.theme, 'dark');
  assert.equal(app.choices[0].value, 'system');
});
