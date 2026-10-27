const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const {join} = require('node:path');
const {test} = require('node:test');
const vm = require('node:vm');

const source = readFileSync(join(__dirname, '../openwrt/data/www/iptv/actions.js'), 'utf8');
function fixture(fetcher, storage = {}) {
  const elements = {
    loginUser: {value: ' example '}, loginPass: {value: ' sample +&% secret '},
    loginSubmit: {disabled: false, textContent: '登录'},
    loginMessage: {hidden: true, textContent: ''}, loginLayer: {style: {}}
  };
  const calls = [], timers = new Map();
  let starts = 0;
  const context = vm.createContext({
    document: {getElementById: id => elements[id]}, sessionStorage: storage,
    window: {iptvStart: () => starts++}, URLSearchParams, AbortController,
    fetch: async (...args) => {calls.push(args); return fetcher(...args)},
    setTimeout: fn => {timers.set(1, fn); return 1}, clearTimeout: id => timers.delete(id),
    alert: () => {throw Error('unexpected alert')}
  });
  vm.runInContext(source, context);
  return {elements, calls, timers, storage, starts: () => starts, login: () => context.login()};
}
const response = (body, status = 200) => ({ok: status === 200, status, text: async () => body});

test('login sends credentials only in POST body, trims username but preserves password', async () => {
  const f = fixture(() => response('OK\n'));
  assert.equal(await f.login(), true);
  const [url, options] = f.calls[0];
  assert.equal(url, '/cgi-bin/iptv-action?action=auth');
  assert.equal(options.method, 'POST');
  assert.equal(options.cache, 'no-store');
  assert.equal(options.redirect, 'error');
  assert.equal(new URLSearchParams(options.body).get('user'), 'example');
  assert.equal(new URLSearchParams(options.body).get('pass'), ' sample +&% secret ');
  assert.equal(f.storage.iptvAuthed, '1');
  assert.equal(f.elements.loginPass.value, '');
  assert.equal(f.starts(), 1);
  assert.equal(f.timers.size, 0);
});

test('only explicit FAIL is described as an incorrect account or password', async () => {
  const f = fixture(() => response('FAIL'));
  assert.equal(await f.login(), false);
  assert.match(f.elements.loginMessage.textContent, /账号或密码不正确/);
  assert.equal(f.storage.iptvPass, undefined);
  assert.equal(f.elements.loginSubmit.disabled, false);
  assert.equal(f.starts(), 0);
});

for (const [name, reply, pattern] of [
  ['service failure', response('FAIL', 503), /HTTP 503/],
  ['unexpected HTML', response('<html>upstream error</html>'), /返回异常/],
  ['empty reply', response(''), /返回异常/],
]) test(name + ' is not a password rejection', async () => {
  const f = fixture(() => reply);
  await f.login();
  assert.match(f.elements.loginMessage.textContent, pattern);
  assert.doesNotMatch(f.elements.loginMessage.textContent, /账号或密码不正确|<html>/);
  assert.equal(f.storage.iptvAuthed, undefined);
});

test('network errors are sanitized', async () => {
  const f = fixture(() => {throw Error('private sample secret')});
  await f.login();
  assert.match(f.elements.loginMessage.textContent, /无法连接/);
  assert.doesNotMatch(f.elements.loginMessage.textContent, /sample|secret/);
});

test('timeout and duplicate clicks do not submit repeated requests', async () => {
  const f = fixture((url, {signal}) => new Promise((resolve, reject) => {
    signal.addEventListener('abort', () => reject(Object.assign(Error('aborted'), {name: 'AbortError'})));
  }));
  const first = f.login();
  assert.equal(await f.login(), false);
  f.timers.get(1)();
  await first;
  assert.equal(f.calls.length, 1);
  assert.match(f.elements.loginMessage.textContent, /超时/);
  assert.equal(f.elements.loginSubmit.disabled, false);
});

test('empty fields do not submit', async () => {
  const f = fixture(() => response('OK'));
  f.elements.loginUser.value = '  ';
  assert.equal(await f.login(), false);
  assert.equal(f.calls.length, 0);
});

test('blocked storage is reported separately after successful authentication', async () => {
  const f = fixture(() => response('OK'), new Proxy({}, {set() {throw Error('denied')}}));
  await f.login();
  assert.match(f.elements.loginMessage.textContent, /账号已验证/);
  assert.equal(f.starts(), 0);
});
