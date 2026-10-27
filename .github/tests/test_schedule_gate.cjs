const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const workflow = fs.readFileSync(path.join(__dirname, '../workflows/run.yml'), 'utf8');
const tail = workflow.split('          script: |\n')[1];
assert.ok(tail, 'Missing scheduled-run gate script');
const body = [];
for (const line of tail.split(/\r?\n/)) {
  if (line.trim() && !line.startsWith('            ')) break;
  body.push(line.slice(12));
}
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
const gate = new AsyncFunction('github', 'core', 'context', 'Date', body.join('\n'));

class FrozenDate extends Date {
  constructor(value) {
    super(value === undefined ? '2026-10-27T05:00:00Z' : value);
  }
}

function success(created_at, extra = {}) {
  return { id: 42, status: 'completed', conclusion: 'success', created_at, html_url: 'https://example.invalid/run/42', ...extra };
}

async function evaluate({ event = 'schedule', runs = [], fail = false } = {}) {
  const outputs = {}, warnings = [], notices = [], calls = [];
  const github = { rest: { actions: { listWorkflowRuns: async args => {
    calls.push(args);
    if (fail) throw new Error('GitHub unavailable');
    return { data: { workflow_runs: runs } };
  } } } };
  const core = {
    setOutput: (key, value) => { outputs[key] = value; },
    warning: value => warnings.push(value),
    notice: value => notices.push(value),
  };
  const context = { eventName: event, repo: { owner: 'example', repo: 'papers' }, ref: 'refs/heads/main', runId: 99 };
  await gate(github, core, context, FrozenDate);
  return { outputs, warnings, notices, calls };
}

test('manual dispatch always runs without querying earlier runs', async () => {
  const result = await evaluate({ event: 'workflow_dispatch', runs: [success('2026-10-27T02:17:00Z')] });
  assert.equal(result.outputs.should_run, 'true');
  assert.equal(result.calls.length, 0);
});

test('missing earlier success allows a scheduled crawl', async () => {
  const result = await evaluate();
  assert.equal(result.outputs.should_run, 'true');
  assert.equal(result.calls[0].workflow_id, 'run.yml');
  assert.equal(result.calls[0].branch, 'main');
  assert.equal(result.calls[0].status, 'success');
});

test('a successful run today skips a scheduled fallback', async () => {
  const result = await evaluate({ runs: [success('2026-10-27T02:17:00Z')] });
  assert.equal(result.outputs.should_run, 'false');
  assert.equal(result.notices.length, 1);
});

test('Beijing date includes the previous UTC evening', async () => {
  const result = await evaluate({ runs: [success('2026-10-26T16:00:00Z')] });
  assert.equal(result.outputs.should_run, 'false');
});

test('yesterday in Beijing does not suppress todays crawl', async () => {
  const result = await evaluate({ runs: [success('2026-10-26T15:59:59Z')] });
  assert.equal(result.outputs.should_run, 'true');
});

test('the current run is not mistaken for earlier completion', async () => {
  const result = await evaluate({ runs: [success('2026-10-27T02:17:00Z', { id: 99 })] });
  assert.equal(result.outputs.should_run, 'true');
});

test('failed or unfinished runs do not suppress retries', async () => {
  for (const extra of [{ conclusion: 'failure' }, { status: 'in_progress' }]) {
    const result = await evaluate({ runs: [success('2026-10-27T02:17:00Z', extra)] });
    assert.equal(result.outputs.should_run, 'true');
  }
});

test('API lookup failure warns and allows the crawl', async () => {
  const result = await evaluate({ fail: true });
  assert.equal(result.outputs.should_run, 'true');
  assert.equal(result.warnings.length, 1);
});
