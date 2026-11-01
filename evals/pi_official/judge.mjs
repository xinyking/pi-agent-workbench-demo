/** Invoke the exact native judges used by pinned Pi; no Agent or model runs. */
import { readFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
import http from 'node:http';
import https from 'node:https';
import net from 'node:net';
import { syncBuiltinESMExports } from 'node:module';

const denyNetwork = () => { throw new Error('Network disabled for native judge replay'); };
http.request = http.get = https.request = https.get = net.connect = net.createConnection = denyNetwork;
globalThis.fetch = denyNetwork;
syncBuiltinESMExports();

const { StructuredOutputJudge, ToolCallJudge } = await import('vitest-evals/judges');
const pkg = JSON.parse(readFileSync(new URL('./node_modules/vitest-evals/package.json', import.meta.url)));
if (pkg.version !== '0.15.0') throw new Error('Pinned vitest-evals 0.15.0 required');
const samples = JSON.parse(readFileSync(process.argv[2], 'utf8'));
if (!Array.isArray(samples) || !samples.length) throw new Error('Samples must be a nonempty array');
const results = [];
for (const sample of samples) {
  const context = {
    input: sample.input,
    output: sample.check_output,
    toolCalls: sample.tools.map(t => ({ name: t.name, arguments: t.input_parameters })),
  };
  const tool = await ToolCallJudge({
    expectedTools: sample.expected_tools.map(t => ({ name: t.name, arguments: t.input_parameters })),
    ordered: false, requireAll: true, allowExtras: true, params: 'strict',
  }).assess(context);
  const business = await StructuredOutputJudge({
    expected: sample.check_expected, match: 'strict', allowExtras: false,
  }).assess(context);
  for (const result of [tool, business]) {
    if (!Number.isFinite(result.score) || result.score < 0 || result.score > 1) {
      throw new Error('Native judge returned invalid/missing score');
    }
  }
  results.push({
    sample_id: sample.sample_id,
    tool: { ...tool, passed: tool.score === 1 },
    business: { ...business, passed: business.score === 1 },
  });
}
const judgeHash = createHash('sha256').update(readFileSync(
  new URL('./node_modules/vitest-evals/dist/judges/index.mjs', import.meta.url))).digest('hex');
process.stdout.write(JSON.stringify({ version: pkg.version, node_version: process.version,
  judge_entry_sha256: judgeHash, network: false, results }));
