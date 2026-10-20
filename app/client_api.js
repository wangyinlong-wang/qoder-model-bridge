async function qoderClientApi(client, request) {
  // Only model metadata and the two proxy settings leave the renderer.
  // No account tokens, existing model keys, or conversation data are read.
  const fail = code => ({ok: false, code});
  if (client !== 'qoder_ide') return fail('CLIENT_INVALID');
  function accessor() {
    const seen = new Set();
    for (const element of document.querySelectorAll('*')) {
      const key = Object.keys(element).find(k => k.startsWith('__reactFiber$'));
      if (!key) continue;
      for (let fiber = element[key]; fiber && !seen.has(fiber); fiber = fiber.return) {
        seen.add(fiber);
        for (let hook = fiber.memoizedState, count = 0; hook && count++ < 100; hook = hook.next) {
          const value = Array.isArray(hook.memoizedState) ? hook.memoizedState[0] : hook.memoizedState;
          if (!value || typeof value.get !== 'function') continue;
          try {
            const service = value.get('ICustomModelService');
            if (service && typeof service.getModels === 'function' && typeof service.addModel === 'function') return value;
          } catch { /* Unrelated contexts are ignored. */ }
        }
      }
      if (seen.size > 5000) break;
    }
    return null;
  }
  const services = accessor();
  if (!services) return fail('CLIENT_INTERFACE_NOT_READY');
  const models = services.get('ICustomModelService');
  const configuration = services.get('IConfigurationService');
  if (!configuration || typeof configuration.getValue !== 'function'
      || typeof configuration.inspect !== 'function' || typeof configuration.updateValue !== 'function'
      || typeof models.checkByokConfig !== 'function'
      || typeof models._isCustomModelSupportedByCurrentAccount !== 'function') {
    return fail('CLIENT_INTERFACE_UNSUPPORTED');
  }
  const supported = () => models._isCustomModelSupportedByCurrentAccount() === true;
  const records = () => models.getModels().records || [];
  function proxy() {
    const url = configuration.getValue('http.proxy') || '';
    const user = configuration.inspect('http.proxy')?.userLocalValue;
    if (typeof url !== 'string' || user !== undefined && typeof user !== 'string') {
      throw new Error('CLIENT_PROXY_INVALID');
    }
    return {data: {proxyMode: url ? 'manual' : 'system', httpProxy: url,
      userProxyPresent: user !== undefined, userProxyValue: user === undefined ? null : user},
      credentials: url.includes('@') || (user || '').includes('@')};
  }
  const userValue = value => value.userProxyPresent === false ? undefined : value.userProxyValue ?? value.httpProxy;
  const sameUserProxy = (a, b) => userValue(a) === userValue(b);
  const sameProxy = (a, b) => a?.proxyMode === b?.proxyMode && a?.httpProxy === b?.httpProxy
    && (!Object.hasOwn(b, 'userProxyPresent') || sameUserProxy(a, b));
  async function setProxy(value, expected, restoring = false) {
    const current = proxy();
    if (expected && !(restoring ? sameUserProxy(current.data, expected) : sameProxy(current.data, expected))) {
      return {ok: true, changed_by_user: true};
    }
    if (current.credentials && !restoring) return fail('CLIENT_PROXY_CONTAINS_CREDENTIALS');
    if (!value || !['manual', 'system'].includes(value.proxyMode)
        || typeof value.httpProxy !== 'string' || value.httpProxy.includes('@')
        || userValue(value) !== undefined && (typeof userValue(value) !== 'string' || userValue(value).includes('@'))) {
      return fail('CLIENT_PROXY_CONTAINS_CREDENTIALS');
    }
    // USER_LOCAL = 3. New IDE versions read this setting directly; older versions
    // synchronize it into their application settings. Never write workspace/SSH settings.
    await configuration.updateValue('http.proxy', userValue(value), 3);
    const after = proxy();
    if (restoring) return sameUserProxy(after.data, value) ? {ok: true, restored: true} : fail('CLIENT_PROXY_NOT_APPLIED');
    return !after.credentials && sameProxy(after.data, value) ? {ok: true, proxy: after.data} : fail('CLIENT_PROXY_NOT_APPLIED');
  }
  function safeModel(model) {
    return {id: model.id, provider: model.provider, model: model.model,
      display_name: model.displayName || model.model, enabled: model.enabled !== false,
      visible: model.visible !== false};
  }
  if (request.action === 'health') return {ok: true, client, transport: 'anonymous-pipe', byok_supported: supported()};
  if (request.action === 'proxy_get') {
    const value = proxy();
    return value.credentials ? fail('CLIENT_PROXY_CONTAINS_CREDENTIALS') : {ok: true, proxy: value.data};
  }
  if (request.action === 'proxy_set') {
    if (request.proxy?.proxyMode !== 'manual'
        || !/^http:\/\/127\.0\.0\.1:\d{1,5}$/.test(request.proxy?.httpProxy || '')) {
      return fail('CLIENT_PROXY_INVALID');
    }
    return setProxy(request.proxy, request.expected);
  }
  if (request.action === 'proxy_restore') return setProxy(request.proxy, request.expected, true);
  if (request.action === 'models') return {ok: true, models: records().map(safeModel), byok_supported: supported()};
  if (!['catalog', 'import'].includes(request.action)) return fail('CLIENT_ACTION_UNSUPPORTED');
  if (!supported()) return fail('CLIENT_BYOK_NOT_AVAILABLE');

  // null selects the local IDE backend, even while an SSH workspace is open.
  const catalog = await models.fetchBYOKConfig(null);
  const provider = (catalog.providers || []).find(p => p.key === 'openrouter' && p.enabled !== false);
  const type = provider?.types?.find(t => t.key === 'pg' && t.enabled !== false);
  if (!provider || !type) return fail('CLIENT_OPENROUTER_UNAVAILABLE');
  if (request.action === 'catalog') {
    const sources = {};
    for (const item of [...(provider.models || []), ...(type.models || [])]) {
      if (item.enabled === false || typeof item.key !== 'string') continue;
      const name = item.display_name;
      sources[item.key] = typeof name === 'string' ? name : name?.cn_zh || name?.en_us || item.key;
    }
    // This is the time we read the native API, not a claim that its remote cache refreshed.
    return {ok: true, sources, catalog_read_at: new Date().toISOString()};
  }
  if (!Array.isArray(request.models) || request.models.length === 0 || request.models.length > 100
      || !Number.isInteger(request.proxy_port) || request.proxy_port < 1 || request.proxy_port > 65535) {
    return fail('CLIENT_IMPORT_INVALID');
  }
  if (!sameProxy(proxy().data, {proxyMode: 'manual', httpProxy: `http://127.0.0.1:${request.proxy_port}`})) {
    return fail('CLIENT_PROXY_MISMATCH');
  }
  const seen = new Set(), added = [], skipped = [], failed = [], waiting = [];
  for (const item of request.models) {
    if (!item || typeof item.source_id !== 'string'
        || !/^[A-Za-z0-9][A-Za-z0-9._:/-]{0,159}$/.test(item.source_id)
        || typeof item.display_name !== 'string' || item.display_name.length > 512 || seen.has(item.source_id)) {
      return fail('CLIENT_IMPORT_INVALID');
    }
    seen.add(item.source_id);
    const existing = records().find(m => m.provider === 'openrouter' && m.model === item.source_id);
    if (existing) skipped.push({source_id: item.source_id, visible: existing.visible !== false, enabled: existing.enabled !== false});
    else waiting.push(item);
  }
  // Finish native validation before saving anything. Failure does not alter existing models.
  for (const item of waiting) {
    if (!supported()) return fail('CLIENT_BYOK_NOT_AVAILABLE');
    const result = await models.checkByokConfig({provider: 'openrouter', model: item.source_id,
      type: 'pg', parameters: {api_key: 'local-proxy'}}, null);
    if (!result?.success) {
      const nativeCode = typeof result?.errorCode === 'string' && /^[A-Za-z0-9_:-]{1,64}$/.test(result.errorCode) ? result.errorCode : undefined;
      failed.push({source_id: item.source_id, code: 'NATIVE_VALIDATION_FAILED', ...(nativeCode ? {native_code: nativeCode} : {})});
    }
  }
  if (failed.length) return {ok: true, status: 'failed', added, skipped, failed};
  for (const item of waiting) {
    if (!supported()) {
      failed.push({source_id: item.source_id, code: 'CLIENT_BYOK_NOT_AVAILABLE'});
      continue;
    }
    if (!sameProxy(proxy().data, {proxyMode: 'manual', httpProxy: `http://127.0.0.1:${request.proxy_port}`})) {
      failed.push({source_id: item.source_id, code: 'CLIENT_PROXY_CHANGED'});
      continue;
    }
    // Recheck after validation in case a second UI added the same model meanwhile.
    if (records().some(m => m.provider === 'openrouter' && m.model === item.source_id)) {
      skipped.push({source_id: item.source_id});
      continue;
    }
    const input = {provider: 'openrouter', providerDisplayName: 'OpenRouter', model: item.source_id,
      displayName: item.display_name, apiKey: 'local-proxy', byokTypeKey: 'pg'};
    if (typeof item.vision === 'boolean') input.is_vl = item.vision;
    if (typeof item.reasoning === 'boolean') input.is_reasoning = item.reasoning;
    if (Number.isInteger(item.context_length) && item.context_length > 0 && item.context_length <= 10000000) {
      input.max_input_tokens = item.context_length;
    }
    try {
      const saved = await models.addModel(input);
      const found = records().find(m => m.id === saved.id && m.provider === 'openrouter' && m.model === item.source_id);
      if (!found) failed.push({source_id: item.source_id, code: 'NATIVE_SAVE_NOT_CONFIRMED'});
      else {
        added.push({source_id: item.source_id, id: saved.id});
        if (found.enabled === false) failed.push({source_id: item.source_id, code: 'NATIVE_MODEL_DISABLED'});
      }
    } catch {
      failed.push({source_id: item.source_id, code: 'NATIVE_SAVE_FAILED'});
    }
  }
  return {ok: true, status: failed.length ? 'partial' : 'completed', added, skipped, failed};
}
