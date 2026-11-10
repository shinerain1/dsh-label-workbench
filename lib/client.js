import { spawn } from 'node:child_process';
export class BackendClient {
    config;
    lifetime = new AbortController();
    active = new Set();
    constructor(config) {
        this.config = config;
    }
    call(operation, args, signal, invocation) {
        const fused = AbortSignal.any([signal, this.lifetime.signal, AbortSignal.timeout(this.config.requestTimeoutMs)]);
        const { request_key, ...parameters } = args;
        const payload = JSON.stringify({ protocol_version: 1, backend_version: '0.1.0', operation, args: parameters, request_key, dsh_context: invocation });
        if (Buffer.byteLength(payload) > 4 * 1024 * 1024)
            throw new Error('REQUEST_TOO_LARGE: maximum 4 MiB');
        const task = this.request(payload, fused).finally(() => this.active.delete(task));
        this.active.add(task);
        return task;
    }
    async dispose() {
        this.lifetime.abort(new Error('Plugin unloaded'));
        await Promise.allSettled([...this.active]);
    }
    async request(payload, signal) {
        signal.throwIfAborted();
        const raw = await new Promise((resolve, reject) => {
            const env = Object.fromEntries(Object.entries(process.env).filter(([name]) => !/KEY|PASSWORD|SECRET|TOKEN/i.test(name) && !/^DSH_/i.test(name)));
            const child = spawn(this.config.pythonExecutable, ['-m', 'dsh_label_workbench', '--root', this.config.root, 'rpc'], {
                shell: false, windowsHide: true, stdio: ['pipe', 'pipe', 'pipe'], env: { ...env, PYTHONUTF8: '1' },
            });
            const chunks = [];
            let bytes = 0, stderr = '';
            let failure;
            const abort = () => { failure = new Error('REQUEST_ABORTED: commit may already exist. Resume the project; retry mutations with the same request_key.'); child.kill(); };
            signal.addEventListener('abort', abort, { once: true });
            if (signal.aborted)
                abort();
            child.stdout.on('data', (chunk) => {
                bytes += chunk.length;
                if (bytes > 8 * 1024 * 1024) {
                    failure = new Error('RESPONSE_TOO_LARGE');
                    child.kill();
                }
                else
                    chunks.push(chunk);
            });
            child.stderr.on('data', (chunk) => { stderr = (stderr + chunk.toString('utf8')).slice(-2000); });
            child.on('error', error => { failure = error; });
            child.stdin.on('error', error => { failure ??= error; });
            child.on('close', code => {
                signal.removeEventListener('abort', abort);
                if (failure)
                    reject(failure);
                else if (code !== 0)
                    reject(new Error(`BACKEND_UNAVAILABLE: Python exited ${code}; ${stderr}`));
                else
                    resolve(Buffer.concat(chunks).toString('utf8'));
            });
            child.stdin.end(payload);
        });
        const envelope = JSON.parse(raw);
        if (envelope?.protocol_version !== 1 || typeof envelope.ok !== 'boolean')
            throw new Error('INVALID_BACKEND_RESPONSE');
        if (!envelope.ok)
            throw new Error(`${envelope.error?.code}: ${envelope.error?.message}`);
        if (!envelope.result || typeof envelope.result !== 'object' || Array.isArray(envelope.result))
            throw new Error('INVALID_BACKEND_RESPONSE');
        return envelope.result;
    }
}
