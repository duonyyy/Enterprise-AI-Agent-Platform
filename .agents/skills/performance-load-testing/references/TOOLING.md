# Công cụ và kỹ thuật triển khai

## k6 example — safe-by-default (test-only target)
```javascript
import http from 'k6/http';
import { check } from 'k6';

const baseUrl = __ENV.TEST_BASE_URL;
if (!baseUrl || !/^https?:\/\/(localhost|127\.0\.0\.1)(:\d+)?$/.test(baseUrl)) {
  throw new Error('Set TEST_BASE_URL to a local test server; customize allowlist only with authorization');
}
export const options = {
  scenarios: { smoke: { executor: 'constant-vus', vus: 2, duration: '20s' } },
  thresholds: {
    http_req_failed: ['rate<0.01'],
    http_req_duration: ['p(95)<800'],
    checks: ['rate>0.99'],
  },
};
export default function () {
  const res = http.get(`${baseUrl}/health`);
  check(res, { 'health response 200': (r) => r.status === 200 });
}
```
Chỉ ví dụ local smoke; threshold `800ms` và `1%` là **minh họa**, không phải SLO mặc định.
Chạy: `TEST_BASE_URL=http://127.0.0.1:8000 k6 run perf-smoke.js` sau khi khởi động app test.

## Load model
- `constant-vus` là closed workload theo concurrent VUs.
- `constant-arrival-rate` là open workload theo rate iterations; kiểm tra `dropped_iterations`.
- `iterations` không luôn bằng HTTP requests: một iteration có thể tạo nhiều requests.

## Report bắt buộc
Thu thập p50, p95, p99, max, sample count, achieved RPS, error rate, VU utilization, dropped iterations, CPU/RAM/DB pool, version/config và thời gian chạy.
