'use strict';
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const {join} = require('node:path');
const vm = require('node:vm');
const test = require('node:test');

function fixture(options = {}) {
  const state = {
    models: [{id: 'existing', provider: 'bailian', model: 'old', visible: true, enabled: true}],
    config: {other: 'keep', config: {chat: {keep: 7}, advanced: {proxyMode: 'manual', proxyURL: 'http://127.0.0.1:8897', unrelated: 42}}},
    httpProxy: 'http://127.0.0.1:8897', localProxy: 'http://127.0.0.1:8897', fallbackProxy: '',
    validations: [], additions: [], updates: 0, supported: true,
    catalogReads: 0, catalogModels: [], catalogError: false,
  };
  state.setUserProxy = value => { state.localProxy = value; state.httpProxy = value === undefined ? state.fallbackProxy : value; };
  const native = {
    _isCustomModelSupportedByCurrentAccount: () => state.supported,
    getModels: () => ({records: state.models}),
    getApiKey: () => { throw new Error('Existing credentials must not be read'); },
    fetchBYOKConfig: async authority => {
      assert.equal(authority, null);
      state.catalogReads++;
      if (state.catalogError) throw new Error('fixture native directory unavailable');
      return {providers: options.noOpenRouter ? [] : [{key: 'openrouter', types: [{key: 'pg', models: state.catalogModels}]}]};
    },
    checkByokConfig: async (input, authority) => {
      assert.equal(authority, null);
      state.validations.push(input);
      return {success: input.model !== options.invalidModel};
    },
    addModel: async input => {
      if (input.model === options.saveFailure) throw new Error('disk failure');
      state.additions.push(input);
      const saved = {...input, apiKey: undefined, id: 'added-' + input.model, enabled: true, visible: true};
      state.models.push(saved);
      return saved;
    },
  };
  const configuration = {
    getValue: key => { assert.equal(key, 'http.proxy'); return state.httpProxy; },
    inspect: key => { assert.equal(key, 'http.proxy'); return {userLocalValue: state.localProxy}; },
    updateValue: async (key, value, target) => {
      assert.equal(key, 'http.proxy');
      assert.equal(target, 3, 'must not write workspace or remote settings');
      state.updates++;
      state.setUserProxy(value);
    },
  };
  const accessor = {get: name => ({ICustomModelService: native, IConfigurationService: configuration})[name]};
  const document = {querySelectorAll: () => [{__reactFiber$test: {
    memoizedState: {memoizedState: [accessor, []], next: null}, return: null,
  }}]};
  const run = vm.runInNewContext('(' + readFileSync(join(__dirname, '../client_api.js'), 'utf8') + ')', {document});
  return {state, call: request => run('qoder_ide', request)};
}
const item = id => ({source_id: id, target_id: 'internal/' + id, display_name: '模型 ' + id});
const request = models => ({action: 'import', proxy_port: 8897, models});
const plain = value => JSON.parse(JSON.stringify(value));

test('each catalog read uses the selected IDE result and reports its read time', async () => {
  const {state, call} = fixture();
  state.catalogModels = [{key: 'native/model', display_name: {cn_zh: 'Current name', en_us: 'English name'},
    parameters: {api_key: 'fixture-private-value'}}];
  const first = await call({action: 'catalog'});
  assert.equal(first.sources['native/model'], 'Current name');
  state.catalogModels = [{key: 'native/model', display_name: 'Changed name'}];
  const second = await call({action: 'catalog'});
  assert.equal(state.catalogReads, 2);
  assert.deepEqual(plain(second.sources), {'native/model': 'Changed name'});
  assert.match(second.catalog_read_at, /^\d{4}-\d{2}-\d{2}T/);
  assert.ok(!JSON.stringify(first).includes('fixture-private-value'));
  assert.equal(state.validations.length, 0);
  assert.equal(state.additions.length, 0);
  assert.equal(state.updates, 0);
});

test('a failed native catalog read cannot silently return the previous reference names', async () => {
  const {state, call} = fixture();
  state.catalogModels = [{key: 'native/model', display_name: 'Old reference'}];
  await call({action: 'catalog'});
  state.catalogError = true;
  await assert.rejects(call({action: 'catalog'}), /fixture native directory unavailable/);
  assert.equal(state.catalogReads, 2);
  assert.equal(state.additions.length, 0);
});

test('native validation precedes saving; upstream ID and credentials never enter the native model', async () => {
  const {state, call} = fixture();
  const original = JSON.stringify(state.models[0]);
  const result = await call(request([{...item('qwen3.7-plus'), context_length: 32768, vision: false}]));
  assert.equal(result.status, 'completed');
  assert.equal(result.added.length, 1);
  assert.equal(state.validations[0].model, 'qwen3.7-plus');
  assert.equal(state.validations[0].parameters.api_key, 'local-proxy');
  assert.equal(state.additions[0].apiKey, 'local-proxy');
  assert.equal(state.additions[0].model, 'qwen3.7-plus');
  assert.equal(state.additions[0].displayName, '模型 qwen3.7-plus');
  assert.equal(state.additions[0].max_input_tokens, 32768);
  assert.equal(JSON.stringify(state.models[0]), original);
  assert.ok(!JSON.stringify(state.additions).includes('internal/'));
});

test('repeat import skips existing models without revalidation or overwriting', async () => {
  const {state, call} = fixture();
  await call(request([item('model-a')]));
  const before = JSON.stringify(state.models);
  const repeated = await call(request([{...item('model-a'), display_name: 'should not replace'}]));
  assert.equal(repeated.added.length, 0);
  assert.equal(repeated.skipped.length, 1);
  assert.equal(state.validations.length, 1);
  assert.equal(JSON.stringify(state.models), before);
});

test('failed native validation prevents saving the entire new batch', async () => {
  const {state, call} = fixture({invalidModel: 'bad'});
  const result = await call(request([item('good'), item('bad')]));
  assert.equal(result.status, 'failed');
  assert.equal(state.models.length, 1);
  assert.equal(state.additions.length, 0);
  assert.equal(result.failed[0].source_id, 'bad');
});

test('partial persistence reports saved models and a retry preserves them', async () => {
  const {state, call} = fixture({saveFailure: 'bad'});
  const result = await call(request([item('good'), item('bad')]));
  assert.equal(result.status, 'partial');
  assert.equal(result.added[0].source_id, 'good');
  const retry = await call(request([item('good')]));
  assert.equal(retry.skipped.length, 1);
  assert.equal(state.additions.length, 1);
});

test('account policy and regional provider availability cannot be bypassed', async () => {
  const one = fixture();
  one.state.supported = false;
  assert.equal((await one.call(request([item('model')]))).code, 'CLIENT_BYOK_NOT_AVAILABLE');
  assert.equal(one.state.validations.length, 0);
  const cn = fixture({noOpenRouter: true});
  assert.equal((await cn.call(request([item('model')]))).code, 'CLIENT_OPENROUTER_UNAVAILABLE');
  assert.equal(cn.state.additions.length, 0);
  assert.equal(cn.state.updates, 0);
});

test('a mismatched client proxy stops import before any upstream validation', async () => {
  const {state, call} = fixture();
  state.setUserProxy('http://127.0.0.1:9999');
  assert.equal((await call(request([item('model')]))).code, 'CLIENT_PROXY_MISMATCH');
  assert.equal(state.validations.length, 0);
});

test('proxy changes preserve unrelated settings and restore only the tool-owned value', async () => {
  const {state, call} = fixture();
  const original = plain((await call({action: 'proxy_get'})).proxy);
  const applied = {proxyMode: 'manual', httpProxy: 'http://127.0.0.1:8898'};
  assert.equal((await call({action: 'proxy_set', proxy: applied, expected: original})).ok, true);
  assert.equal(state.httpProxy, applied.httpProxy);
  assert.equal(state.config.config.chat.keep, 7);
  assert.equal(state.config.config.advanced.unrelated, 42);
  assert.equal(state.config.config.advanced.proxyURL, 'http://127.0.0.1:8897', 'legacy configuration is not the write target');
  state.setUserProxy('http://127.0.0.1:8888');
  const result = await call({action: 'proxy_restore', proxy: original, expected: applied});
  assert.equal(result.changed_by_user, true);
  assert.equal(state.httpProxy, 'http://127.0.0.1:8888');
  assert.equal(state.updates, 1);
});

test('credential-bearing proxy settings are neither exported nor modified', async () => {
  const {state, call} = fixture();
  state.setUserProxy('http://fixture-user:fixture-password@proxy.invalid:80');
  const value = await call({action: 'proxy_get'});
  assert.equal(value.code, 'CLIENT_PROXY_CONTAINS_CREDENTIALS');
  assert.ok(!JSON.stringify(value).includes('fixture-password'));
  const result = await call({action: 'proxy_set', proxy: {proxyMode: 'manual', httpProxy: 'http://127.0.0.1:8897'}});
  assert.equal(result.ok, false);
  assert.equal(state.updates, 0);
});

test('restoring after the user adds proxy credentials leaves that new setting untouched', async () => {
  const {state, call} = fixture();
  const applied = {proxyMode: 'manual', httpProxy: 'http://127.0.0.1:8897'};
  state.setUserProxy('http://fixture-user:fixture-password@proxy.invalid:80');
  const result = await call({action: 'proxy_restore', proxy: {proxyMode: 'system', httpProxy: ''}, expected: applied});
  assert.equal(result.changed_by_user, true);
  assert.equal(state.updates, 0);
  assert.ok(!JSON.stringify(result).includes('fixture-password'));
});

test('an absent local proxy setting is removed on restore instead of leaving an explicit override', async () => {
  const {state, call} = fixture();
  state.setUserProxy(undefined);
  const original = plain((await call({action: 'proxy_get'})).proxy);
  assert.equal(original.userProxyPresent, false);
  const applied = {proxyMode: 'manual', httpProxy: 'http://127.0.0.1:8898', userProxyPresent: true, userProxyValue: 'http://127.0.0.1:8898'};
  await call({action: 'proxy_set', proxy: applied, expected: original});
  // A remote window can have a different effective proxy; restoration still owns only USER_LOCAL.
  state.httpProxy = 'http://fixture-user:fixture-password@remote-proxy.invalid:80';
  const result = await call({action: 'proxy_restore', proxy: original, expected: applied});
  assert.equal(result.restored, true);
  assert.equal(state.localProxy, undefined);
  assert.ok(!JSON.stringify(result).includes('fixture-password'));
});

test('invalid and duplicate input IDs do not cause native writes', async () => {
  for (const batch of [[item('bad id')], [item('same'), item('same')]]) {
    const {state, call} = fixture();
    assert.equal((await call(request(batch))).code, 'CLIENT_IMPORT_INVALID');
    assert.equal(state.validations.length, 0);
    assert.equal(state.additions.length, 0);
  }
});
