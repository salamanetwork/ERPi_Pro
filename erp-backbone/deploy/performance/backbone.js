import http from 'k6/http';
import { check, fail, sleep } from 'k6';
import { SharedArray } from 'k6/data';
import exec from 'k6/execution';

const base = (__ENV.BASE_URL || '').replace(/\/$/, '');
const profile = __ENV.PROFILE || 'mixed';
if (!/^https:\/\//.test(base) || !['mixed', 'rate'].includes(profile) || __ENV.STAGING_ONLY !== 'yes') {
  throw new Error('Set an HTTPS BASE_URL, valid PROFILE, and STAGING_ONLY=yes for a disposable staging database.');
}
const mixed = profile === 'mixed';
const workspaceMode = __ENV.WORKSPACE_MODE === 'yes';
const vus = Number(__ENV.VUS || 100);
const duration = __ENV.DURATION || '30m';
const rate = Number(__ENV.RATE || 20);
const month = __ENV.K6_MONTH || new Date().toISOString().slice(0, 7);
const start = month + '-01';
const after = new Date(start + 'T00:00:00Z');
after.setUTCMonth(after.getUTCMonth() + 1);
after.setUTCDate(0);
const end = after.toISOString().slice(0, 10);
const credentials = new SharedArray('benchmark credentials', () => [
  { username: __ENV.K6_USERNAME || 'benchmark', password: __ENV.K6_PASSWORD },
]);
const site = Number(__ENV.K6_SITE_ID);
const jar = () => http.cookieJar();
const lock = {
  mixed: { executor: 'constant-vus', vus, duration, exec: 'traffic' },
  rate: { executor: 'constant-arrival-rate', rate, timeUnit: '1s', duration,
    preAllocatedVUs: 100, maxVUs: 200, exec: 'traffic' },
};
export const options = {
  scenarios: { [profile]: lock[profile] },
  thresholds: {
    'http_req_duration{class:daily}': ['p(95)<=1000'],
    'http_req_duration{class:report}': ['p(95)<=3000'],
    'http_req_failed{suite:benchmark}': ['rate<0.01'],
    'checks{suite:benchmark}': ['rate>0.99'],
    ...(!mixed ? { dropped_iterations: ['count==0'] } : {}),
    ...(workspaceMode ? {
      'http_req_duration{workspace:counters}': ['p(95)<=1000'],
      'http_req_duration{workspace:dashboard}': ['p(95)<=1000'],
    } : {}),
  },
  noCookiesReset: true,
};

export function setup() {
  if (!credentials[0].password || !Number.isSafeInteger(site) || site < 1) fail('Missing K6_PASSWORD or K6_SITE_ID');
  const checkReady = http.get(base + '/health/ready', { redirects: 0 });
  if (checkReady.status !== 200 || checkReady.json('status') !== 'ready') fail('Staging readiness check failed');
  const bootstrap = http.get(base + '/api/session');
  if (bootstrap.status !== 200 || !jar().cookiesForURL(base).csrftoken) fail('Cannot obtain CSRF cookie');
  const login = http.post(base + '/api/login', JSON.stringify(credentials[0]), {
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': jar().cookiesForURL(base).csrftoken[0],
      Origin: base },
  });
  if (login.status !== 200 || login.json('ok') !== true) fail('Benchmark login needs a staging-only account without MFA');
  const cookies = jar().cookiesForURL(base);
  const workspace = http.get(base + '/api/workspace');
  if (!cookies.sessionid || !cookies.csrftoken || workspace.status !== 200 ||
      !workspace.json('sites').some(row => row.id === site && row.name.startsWith('Benchmark site '))) {
    fail('Expected a synthetic benchmark site on staging');
  }
  return { session: cookies.sessionid[0], csrf: cookies.csrftoken[0], runId: Date.now().toString(36) };
}

export function traffic(data) {
  jar().set(base, 'sessionid', data.session, { secure: true, path: '/' });
  jar().set(base, 'csrftoken', data.csrf, { secure: true, path: '/' });
  const slot = exec.scenario.iterationInTest % 10;
  const tags = { suite: 'benchmark', class: [6, 8, 9].includes(slot) ? 'report' : 'daily' };
  if (workspaceMode && slot < 2) tags.workspace = slot === 0 ? 'counters' : 'dashboard';
  let response;
  if (slot === 7) {
    response = http.post(base + '/api/work-orders', JSON.stringify({
      title: 'Benchmark write', description: 'Synthetic staging traffic', site_id: site,
    }), { tags, headers: { 'Content-Type': 'application/json', 'X-CSRFToken': data.csrf,
      Origin: base, 'Idempotency-Key': 'bench-' + data.runId + '-' + profile + '-' + exec.scenario.iterationInTest } });
    check(response, { 'created a work order': res => res.status === 201 && Number.isInteger(res.json('id')) }, tags);
  } else {
    const path = slot === 0 && workspaceMode ? '/api/workspace/counters'
      : slot === 1 && workspaceMode ? '/api/workspace/dashboard'
      : slot < 2 ? '/api/inbox'
      : slot < 4 ? '/api/search?q=Benchmark'
      : slot < 6 ? '/api/inventory/balances'
      : slot === 6 ? '/api/dashboards/stock'
      : slot === 8 ? '/api/dashboards/finance'
      : '/api/accounting/reports/trial?start=' + start + '&end=' + end;
    response = http.get(base + path, { tags, redirects: 0 });
    check(response, { 'returned expected JSON': res => res.status === 200 &&
      (workspaceMode && slot < 2 ? Number.isInteger(res.json('inbox')) &&
        (slot !== 0 || Number.isInteger(res.json('unread'))) :
        Array.isArray(res.json([6, 8].includes(slot) ? 'cards' : 'items'))) }, tags);
  }
  if (mixed) sleep(1);
}
