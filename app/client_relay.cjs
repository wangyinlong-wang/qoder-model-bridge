'use strict';

// Electron supplies Node. CDP uses inherited anonymous pipes, never a TCP port.
const {spawn} = require('node:child_process');
const {readFileSync} = require('node:fs');
const {join, dirname, resolve, relative, isAbsolute} = require('node:path');
const {fileURLToPath} = require('node:url');
const readline = require('node:readline');
const {StringDecoder} = require('node:string_decoder');

const client = process.argv[2];
if (client !== 'qoder_ide') process.exit(2);
const env = {...process.env};
delete env.ELECTRON_RUN_AS_NODE;
delete env.NODE_OPTIONS;
const child = spawn(process.execPath, ['--remote-debugging-pipe'], {
  env, stdio: ['ignore', 'ignore', 'ignore', 'pipe', 'pipe'], windowsHide: false,
});
const pending = new Map();
let sequence = 0, buffer = '', sessionId, targetId, busy = false;
const decoder = new StringDecoder('utf8');
const applicationResources = process.platform === 'darwin'
  ? resolve(dirname(process.execPath), '../Resources/app/out')
  : resolve(dirname(process.execPath), 'resources/app/out');
function isWorkbench(target) {
  if (target.type !== 'page' || !/\/workbench(?:-dev)?\.html(?:[?#]|$)/.test(target.url)) return false;
  try {
    const url = new URL(target.url);
    if (url.protocol !== 'file:' && !(url.protocol === 'vscode-file:' && url.hostname === 'vscode-app')) return false;
    const file = url.protocol === 'file:' ? url : new URL('file://' + url.pathname);
    const path = relative(applicationResources, fileURLToPath(file));
    return path && !isAbsolute(path) && !path.startsWith('..');
  } catch { return false; }
}
const send = value => process.stdout.write(JSON.stringify(value) + '\n');
function cdp(method, params = {}, session, timeout = 30000) {
  return new Promise((resolve, reject) => {
    const id = ++sequence;
    const timer = setTimeout(() => {
      pending.delete(id);
      reject(new Error('CLIENT_REQUEST_TIMEOUT'));
    }, timeout);
    pending.set(id, {resolve, reject, timer});
    child.stdio[3].write(JSON.stringify({id, method, params, ...(session ? {sessionId: session} : {})}) + '\0');
  });
}
child.stdio[4].on('data', bytes => {
  buffer += decoder.write(bytes);
  if (buffer.length > 8 * 1024 * 1024) return child.stdio[4].destroy();
  let boundary;
  while ((boundary = buffer.indexOf('\0')) >= 0) {
    const message = buffer.slice(0, boundary);
    buffer = buffer.slice(boundary + 1);
    let result;
    try { result = JSON.parse(message); } catch { continue; }
    const entry = pending.get(result.id);
    if (!entry) continue;
    clearTimeout(entry.timer);
    pending.delete(result.id);
    result.error ? entry.reject(new Error('CLIENT_PROTOCOL_ERROR')) : entry.resolve(result.result);
  }
});
child.on('error', () => send({event: 'error', code: 'CLIENT_START_FAILED'}));
child.on('exit', () => {
  for (const entry of pending.values()) {
    clearTimeout(entry.timer);
    entry.reject(new Error('CLIENT_CLOSED'));
  }
  pending.clear();
  send({event: 'closed'});
  process.exit(0);
});

async function connect() {
  for (let attempt = 0; attempt < 80; attempt++) {
    const {targetInfos} = await cdp('Target.getTargets');
    const candidates = targetInfos.filter(isWorkbench);
    if (candidates.length === 1) {
      targetId = candidates[0].targetId;
      ({sessionId} = await cdp('Target.attachToTarget', {targetId, flatten: true}));
      const info = await execute({action: 'health'});
      if (info.ok) return info;
      await cdp('Target.detachFromTarget', {sessionId});
      sessionId = undefined;
    } else if (candidates.length > 1) {
      throw new Error('CLIENT_MULTIPLE_WINDOWS');
    }
    await new Promise(resolve => setTimeout(resolve, 500));
  }
  throw new Error('CLIENT_INTERFACE_NOT_READY');
}

async function execute(request) {
  const api = readFileSync(join(__dirname, 'client_api.js'), 'utf8');
  const expression = `(${api})(${JSON.stringify(client)},${JSON.stringify(request)})`;
  const result = await cdp('Runtime.evaluate', {
    expression, awaitPromise: true, returnByValue: true, timeout: 120000,
  }, sessionId, 125000);
  if (result.exceptionDetails || !result.result || result.result.type !== 'object') {
    throw new Error('CLIENT_INTERFACE_ERROR');
  }
  return result.result.value;
}

const completed = new Map();
const input = readline.createInterface({input: process.stdin, crlfDelay: Infinity});
input.on('line', async line => {
  let request;
  try { request = JSON.parse(line); } catch { return; }
  const id = request.id;
  if (completed.has(id)) return send(completed.get(id));
  if (busy || !sessionId) return send({id, ok: false, code: 'CLIENT_BUSY_OR_NOT_READY'});
  if (!['health', 'catalog', 'models', 'import', 'proxy_get', 'proxy_set', 'proxy_restore'].includes(request.action)) {
    return send({id, ok: false, code: 'CLIENT_ACTION_UNSUPPORTED'});
  }
  busy = true;
  try {
    const result = {id, ...(await execute(request))};
    completed.set(id, result);
    if (completed.size > 16) completed.delete(completed.keys().next().value);
    send(result);
  }
  catch (error) { send({id, ok: false, code: error.message}); }
  finally { busy = false; }
});
// Keep the pipe owner alive while its Qoder process is open. EOF does not close
// the user's client or its tasks; the helper cannot issue further operations.
input.on('close', () => { sessionId = undefined; });
connect().then(info => send({event: 'ready', pid: child.pid, ...info})).catch(error => {
  send({event: 'error', code: error.message});
  // An already-running normal client does not inherit these anonymous pipes.
  // Its process must be closed by the user before a controlled launch can work.
});
