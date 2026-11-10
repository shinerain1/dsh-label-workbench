import { homedir } from 'node:os';
import { isAbsolute, join } from 'node:path';
import z from '@deepseek-ai/schemastery';
import { defineTool } from '@deepseek-ai/dsh-tools';
import { BackendClient } from './client.js';
export const name = 'label-workbench';
export const inject = ['tools'];
export const Config = z.object({
    pythonExecutable: z.string().default('python').description('Python 3.11+ with dsh-label-workbench installed'),
    root: z.string().default(join(homedir(), '.dsh-label-workbench')).description('Trusted local store; tool writes are confined to this root'),
    requestTimeoutMs: z.number().min(1000).max(300000).default(120000),
});
const project = { type: 'string', required: true, description: 'Immutable project ID' };
const job = { type: 'string', required: true };
const version = { type: 'integer', required: true, description: 'Expected current rule version; reload after conflicts' };
const key = { type: 'string', description: 'Stable key for this intended mutation across PTC reruns; changed arguments with same key are rejected' };
const rule = { type: 'object', required: true, additionalProperties: false, properties: {
        mode: { type: 'string', required: true, enum: ['guidelines', 'decision_list'] },
        labels: { type: 'array', required: true, items: { type: 'string' } },
        guidelines: { type: 'string', required: true, description: 'Natural language guidelines. Any wording change causes full review.' },
        default: { type: 'string', description: 'Required only for executable decision_list' },
        rules: { type: 'array', description: 'Ordered literal substring rules; first match wins; casefold; no regex. This must be the COMPLETE labeling standard for selective carry.', items: { type: 'object', additionalProperties: false, properties: {
                    contains_any: { type: 'array', required: true, items: { type: 'string' } }, label: { type: 'string', required: true },
                } } },
    } };
const specs = [
    { name: 'label_health', operation: 'health', description: 'Check the local annotation backend.', parameters: {} },
    { name: 'label_project_create', operation: 'create', description: 'Snapshot immutable id/text records and rule v1. Single-label text only. Never infer or invent human review.', parameters: { name: { type: 'string', required: true }, records: { type: 'array', required: true, items: { type: 'object', additionalProperties: false, properties: { id: { type: 'string', required: true }, text: { type: 'string', required: true } } } }, rule, request_key: key } },
    { name: 'label_project_status', operation: 'status', description: 'Read active rule, pending count, jobs and isolated stale results. Export requires no pending rows or active jobs.', parameters: { project_id: project } },
    { name: 'label_project_list', operation: 'list', description: 'List local annotation projects.', parameters: {} },
    { name: 'label_workbench_resume', operation: 'resume', description: 'Recover projects associated with this durable DSH Session. Uses host identity, not model-supplied identity; then read project status and job status. No jobs are automatically restarted.', parameters: {} },
    { name: 'label_rule_preview', operation: 'preview', description: 'Preview a rule change. Natural language/wording changes conservatively review ALL records. Selective carry requires complete executable decision lists with unchanged wording and equal old/new/current labels. Human labels always need human reconfirmation.', parameters: { project_id: project, rule } },
    { name: 'label_rule_activate', operation: 'activate', description: 'Atomically activate a new immutable rule version, mark review needs, preserve human history, supersede old jobs and explicitly link safe carry decisions. Old results cannot become current.', parameters: { project_id: project, expected_version: version, rule, request_key: key } },
    { name: 'label_review_queue', operation: 'queue', description: 'Read pending records, previous decisions and suggestions. requires_human rows must be resolved by a person via the local reviewer UI/CLI or confirmed Label Studio import. Never use a script to impersonate a reviewer.', parameters: { project_id: project, pending_only: { type: 'boolean' }, offset: { type: 'integer' }, limit: { type: 'integer' } } },
    { name: 'label_job_create', operation: 'job_create', description: 'Create a durable job over pending rows, bound to rule version and current record revisions. Creating a job does not start external model calls.', parameters: { project_id: project, expected_version: version, record_ids: { type: 'array', items: { type: 'string' } }, request_key: key } },
    { name: 'label_job_claim', operation: 'claim', description: 'Lease at most 500 pending items. Use the returned frozen rule and tokens when labeling. After interruption, expired leases can be reclaimed; an older token is rejected. Model/API costs of expired work are not exactly-once.', parameters: { job_id: job, limit: { type: 'integer' }, lease_seconds: { type: 'integer' } } },
    { name: 'label_job_submit', operation: 'submit', description: 'Commit machine labels only. Stale rule/lease/revision results are quarantined. Human-protected rows retain suggestions for review. Same token+payload replay is idempotent; conflicting replay is rejected.', parameters: { job_id: job, results: { type: 'array', required: true, items: { type: 'object', additionalProperties: false, properties: { record_id: { type: 'string', required: true }, token: { type: 'string', required: true }, label: { type: 'string', required: true } } } }, request_key: key } },
    { name: 'label_job_step', operation: 'step', description: 'Process a bounded batch using ONLY explicit complete decision_list rules; no model call, no natural language interpretation. Can be looped in PTC until completed.', parameters: { job_id: job, limit: { type: 'integer' }, request_key: key } },
    { name: 'label_job_get', operation: 'job_get', description: 'Read persistent job state and pending/leased/done counts after interruption.', parameters: { job_id: job } },
    { name: 'label_job_cancel', operation: 'cancel', description: 'Stop accepting this job. In-flight external compute may continue but its results cannot become current.', parameters: { job_id: job, request_key: key } },
    { name: 'label_record_history', operation: 'history', description: 'Read append-only machine, carry and human decisions with provenance; current labels never replace this history.', parameters: { project_id: project, record_id: { type: 'string', required: true } } },
    { name: 'label_review_bundle', operation: 'review_bundle', description: 'Write Label Studio task JSON, choice configuration and frozen rule under the store. Historical labels remain predictions, not completed annotations. Human import requires local reviewer confirmation.', parameters: { project_id: project } },
    { name: 'label_dataset_export', operation: 'export', description: 'Reject unresolved rows, active jobs, mixed versions or invalid provenance. Export a locally verified ZIP with data, rule versions, decision history, audit chain and checksums. Does not upload data.', parameters: { project_id: project, expected_version: version, request_key: key } },
];
export function apply(ctx, config) {
    if (!isAbsolute(config.root))
        throw new Error('root must be absolute');
    if (!config.pythonExecutable.trim())
        throw new Error('pythonExecutable is required');
    const backend = new BackendClient(config);
    ctx.effect(() => () => backend.dispose());
    for (const spec of specs)
        ctx.tools.register(defineTool({
            name: spec.name, description: spec.description, parameters: spec.parameters,
            output: { schema: { type: 'object', additionalProperties: true }, render: (_args, value) => [{ type: 'text', text: JSON.stringify(value) }] },
            execute: async (args, exec) => {
                const invocation = exec.agent ? { session_id: exec.agent.session.id, call_id: exec.callId } : undefined;
                if (spec.operation === 'resume') {
                    if (!invocation)
                        throw new Error('SESSION_REQUIRED: resume requires a DSH Session');
                    return backend.call('list', { session_id: invocation.session_id }, exec.signal, invocation);
                }
                return backend.call(spec.operation, args, exec.signal, invocation);
            },
            presentCall: args => ({ card: 'generic', title: spec.name, rawInput: args }),
        }));
}
