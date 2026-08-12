# CarrotLink 페어링 구현 계획

조사 근거는 [`SUNNYPILOT_PAIRING_RESEARCH.md`](SUNNYPILOT_PAIRING_RESEARCH.md)에 있다.

## 목표 수직 슬라이스

사용자가 offroad 상태의 Comma에서 QR을 열고, 기존 CarrotLink dashboard에 로그인한 휴대폰으로 스캔하면 그 사용자와 장치가 연결된다. 장치는 페어링 완료를 확인한 뒤 CarrotLink 전용 key로 outbound WebSocket을 열 수 있다. 해제하면 연결이 즉시 끊기고 재인증할 수 없다.

이 단계에서 원격 설정을 구현하지 않는다. 페어링·폐기·재연결을 먼저 검증한다.

## 결정

| 항목 | 결정 |
|---|---|
| 사용자 인증 | 기존 `ui_users` + signed HttpOnly session cookie 재사용 |
| 장치 bootstrap 증명 | 기존 comma key로 서명한 짧은 JWT; 등록 시에만 사용 |
| 지속 장치 인증 | `/persist/carrotlink`의 전용 ES256 key |
| pairing code | `crypto/rand` 32 bytes, DB에는 SHA-256 hash만 저장 |
| QR 만료 | 5분 |
| QR URL | `https://<dashboard>/pair#<code>` |
| QR에 포함되는 값 | 무작위 code 하나; device ID, JWT, private key 없음 |
| claim | 로그인된 same-origin session만 가능, 단일 DB transaction |
| ownership | 초기에는 device당 owner 1명 |
| 연결 | 기존 outbound TLS WebSocket + JSON-RPC transport 재사용 |
| offline command queue | 만들지 않음; 장치 offline이면 즉시 실패 |
| remote settings | M4에서 단일 key만, 장치 측 allowlist + offroad 강제 |
| 초기 안전한 설정 | `ShowDateTime` 후보; 구현 전 실장치 의미 재확인 |
| 감사 | code/token/raw identifier를 기록하지 않고 event/result만 기록 |

## 신뢰 경계

```text
Comma UI
  └─ code 요청 (comma-key signed bootstrap JWT)
       └─ CarrotLink API ── pairing_sessions(hash, expiry, state)
                            │
Phone browser ── existing session ── claim transaction
                            │
                            └─ device_owners(user_id, device_id)
                                      │
Comma agent ── CarrotLink-key signed JWT ── WSS/JSON-RPC
```

서버가 신뢰하는 것:

- bootstrap request: 등록된 comma public key로 검증된 짧은 JWT
- claim request: 기존 signed session cookie의 user ID
- WebSocket request: paired·not-revoked 장치의 CarrotLink public key로 검증된 짧은 JWT

서버가 신뢰하지 않는 것:

- QR code 자체의 device ownership
- URL의 device ID나 user ID
- 플랫폼이 보낸 설정 key/value
- browser가 보낸 장치 선택값

## 위협과 통제

| 위협 | 통제 |
|---|---|
| 촬영되거나 browser history에 남은 QR 재사용 | 256-bit random code, 5분 만료, hash 저장, 단일 사용 transaction |
| reverse proxy·analytics·Referer에 code 노출 | query가 아닌 URL fragment 사용, claim 직후 `history.replaceState` |
| 로그인 redirect 중 fragment 유실 | `/pair`가 code를 tab-local `sessionStorage`에 보관하고 성공·실패·만료 시 즉시 삭제 |
| device ID만 아는 공격자의 등록 | 기존 comma key의 서명과 등록된 serial 대조 |
| CarrotLink 침해가 comma.ai identity로 확장 | 지속 연결에는 별도 `/persist/carrotlink` key 사용 |
| 두 사용자의 동시 claim | DB row lock/조건부 update와 device당 active owner unique 제약 |
| 다른 사용자의 장치 조회·해제 | 모든 browser endpoint에서 owner row 확인 |
| 폐기된 장치의 재접속 | WebSocket handshake에서 paired/not-revoked 확인, unpair 시 active socket 종료 |
| command replay·지연 전달 | UUID command ID, expiry, device-side recently-seen ID 저장 |
| 위험 Params 쓰기 | 장치 측 allowlist, 자료형·범위·unit, expected-current, offroad 검증 |
| token/code가 로그에 남음 | request URL에 secret 금지, body/header redaction, hash·event type만 감사 기록 |
| 플랫폼 장애가 운행을 방해 | agent 실패 격리, offline command queue 없음, legacy Carrot Web 유지 |

## Pairing 상태 기계

```text
issued ──claim──> claimed
  │                 │
  ├─expire──> expired
  ├─cancel──> cancelled
  └─second claim──> reject

claimed ──unpair──> revoked
```

규칙:

- `issued`만 claim 가능하다.
- `expires_at <= now`면 claim할 수 없다.
- claim은 `SELECT ... FOR UPDATE` 또는 조건부 `UPDATE ... WHERE state='issued'` 한 transaction으로 직렬화한다.
- 이미 owner가 있는 device의 새 claim은 거부한다. 공유 사용자는 후속 요구가 생길 때 별도 설계한다.
- unpair는 owner row를 revoke하고 active WebSocket을 닫는다.
- 같은 QR의 재사용, 동시 claim, 만료 claim은 모두 DB 상태 변경 없이 실패한다.

## API 계약 v1

### 버전 정책

- HTTP major version은 `/v1/carrotlink` path에 고정한다.
- pairing과 WebSocket hello에 `protocol_version: 1`을 기록한다.
- 같은 major에서는 optional field만 추가한다. 기존 field의 의미·자료형은 바꾸지 않는다.
- 장치와 서버가 지원하지 않는 major는 연결을 거부하고 기존 CarrotPilot은 계속 동작한다.
- 설정 schema에는 `schema_version`과 `schema_hash`를 넣어 frontend cache와 `expected_current` 검사를 묶는다.

### 장치 bootstrap

`POST /v1/carrotlink/devices/register`

인증:

- comma key로 서명한 5분 이내 JWT
- claims: `identity=<comma_dongle_id>`, `purpose=carrotlink-register`, `iat`, `nbf`, `exp`, `jti`

body:

```json
{
  "public_key": "<CarrotLink ES256 public key>",
  "serial": "<hardware serial>"
}
```

응답:

```json
{
  "device_id": "<opaque server id>"
}
```

서버는 서명 검증 전 `identity`를 public-key lookup 용도로만 읽고, 해당 comma registration의 public key로 JWT를 검증한 뒤 submitted serial을 대조한다. raw IMEI는 받거나 저장하지 않는다. `jti`는 등록 replay 방지용으로 짧게 저장한다.

### pairing session 발급

`POST /v1/carrotlink/devices/{device_id}/pairing-sessions`

인증:

- CarrotLink key로 서명한 device JWT
- claims: `identity`, `purpose=pairing-session`, `iat`, `nbf`, `exp`, `jti`

응답:

```json
{
  "pairing_url": "https://<dashboard>/pair#<code>",
  "expires_at": "<RFC3339>"
}
```

서버는 code 원문을 응답 한 번에만 반환하고 hash만 저장한다.

### browser claim

`POST /v1/carrotlink/pairing-sessions/claim`

인증:

- 기존 dashboard session cookie
- same-origin `Origin` 또는 `Sec-Fetch-Site` 검증
- JSON body와 `Content-Type: application/json`

body:

```json
{
  "code": "<fragment에서 읽은 code>"
}
```

응답:

```json
{
  "device_id": "<opaque server id>",
  "status": "paired"
}
```

code를 fragment에 넣으므로 HTTP request, reverse proxy access log, `Referer`에 자동 포함되지 않는다. `/pair` page의 JS가 fragment를 읽어 로그인 완료 후 POST한다. page는 claim 완료 직후 `history.replaceState`로 fragment를 지운다.

### 장치 상태 확인

`GET /v1/carrotlink/devices/{device_id}/pairing`

- device JWT 인증
- 응답: `pending | paired | revoked`
- QR dialog가 열린 동안에만 2초 간격, 최대 5분 polling
- paired 전환 시 dialog를 닫고 agent 연결을 시작

### unpair

`DELETE /v1/carrotlink/devices/{device_id}/pairing`

- owner session만 가능
- owner revoke, active WebSocket close, pending command 취소
- device가 offline이어도 서버 측 revoke는 즉시 완료
- 장치는 다음 status 조회 또는 WebSocket 인증 실패에서 `revoked`를 로컬 unpaired 상태로 영속화하고 agent를 중지한다. 자동 재등록·재연결 loop는 만들지 않는다.
- 전용 private key는 자동 삭제하지 않는다. 사용자가 새 pairing을 명시적으로 시작할 때 서버 credential과 함께 회전한다.

## 데이터 모델

기존 `devices`와 `ui_users`를 유지하고 세 table만 추가한다.

```sql
carrotlink_devices(
  device_id UUID PRIMARY KEY,
  comma_dongle_id TEXT UNIQUE NOT NULL REFERENCES devices(dongle_id),
  public_key TEXT NOT NULL,
  revoked_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL
)

carrotlink_pairing_sessions(
  id UUID PRIMARY KEY,
  device_id UUID NOT NULL REFERENCES carrotlink_devices(device_id),
  code_hash BYTEA UNIQUE NOT NULL,
  state TEXT NOT NULL CHECK (state IN ('issued','claimed','cancelled','expired')),
  expires_at TIMESTAMPTZ NOT NULL,
  claimed_by INTEGER REFERENCES ui_users(id),
  claimed_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL
)

carrotlink_device_owners(
  device_id UUID PRIMARY KEY REFERENCES carrotlink_devices(device_id),
  user_id INTEGER NOT NULL REFERENCES ui_users(id),
  created_at TIMESTAMPTZ NOT NULL,
  revoked_at TIMESTAMPTZ
)
```

`pairing_sessions` 정리는 request 시 lazy expiry + 하루 1회 기존 maintenance 경로로 충분하다. 새 worker는 만들지 않는다.

## 구현 순서

### 1. 플랫폼: 안전한 pairing state

저장소: `/Users/son2688s/docker/comma-log-manager`

수정 대상:

- `dashboard/sql/migrations/`: 위 3 table migration
- `dashboard/sql/queries/`: register, issue, claim, status, revoke query
- `dashboard/internal/api/pairing.go`: 현재 안내-only `/pair`를 session issue/claim/status/revoke handler로 교체
- `dashboard/internal/api/pilotauth.go`: 기존 comma/SunnyLink 호환 코드는 유지하고 CarrotLink register endpoint에서 검증 helper만 재사용
- `dashboard/internal/api/middleware/auth_db.go`: 서명 검증 helper 재사용, purpose/jti/clock-skew 검증 추가
- `dashboard/cmd/server/routes.go`: device/session route를 기존 middleware에 명시적으로 연결
- `dashboard/internal/ws/handler.go`: CarrotLink device lookup과 paired/revoked 검사
- `dashboard/internal/ws/hub.go`: unpair 시 해당 client를 닫는 최소 `Disconnect(deviceID)` 추가

검사:

- register token의 서명·purpose·exp·jti replay
- code hash만 DB에 저장
- 만료·재사용·동시 claim 실패
- 로그인하지 않은 claim 401
- 다른 user의 status/unpair 403
- unpair 직후 active socket 종료와 재접속 401
- offline 장치가 다시 연결했을 때 revoked 상태 영속화와 agent 중지

### 2. 장치: 전용 identity와 pairing client

저장소: `/Volumes/SSD/CarrotLink/openpilot`

최소 파일:

- `selfdrive/carrot/carrotlink.py`: key 생성, register, pairing session 요청, signed JWT, status polling
- `selfdrive/carrot/tests/test_carrotlink.py`: JWT claims, QR URL, key permission, polling state의 작은 test

규칙:

- key 위치: `Paths.persist_root()/carrotlink/id_ecdsa`와 `.pub`
- directory `0700`, private key `0600`
- 이미 있으면 절대 재생성하지 않음
- comma key는 bootstrap registration 서명에만 사용
- API host는 하나의 명시적 `CARROTLINK_API_HOST` 환경값 또는 기존 빌드 설정을 사용; runtime에서 임의 host 입력 기능은 만들지 않음
- register/pairing은 offroad에서만 시작
- 오류는 pairing UI에 짧은 상태로 보여 주고 운행 프로세스에 영향을 주지 않음

새 daemon은 아직 만들지 않는다. M2에서는 UI가 호출하는 짧은 request/poll worker면 충분하다. 지속 WebSocket agent는 M3에서 추가한다.

### 3. 장치 UI: 기존 QR dialog 재사용

수정 대상:

- `selfdrive/ui/widgets/pairing_dialog.py`
- `selfdrive/ui/mici/widgets/pairing_dialog.py`
- `selfdrive/ui/layouts/settings/device.py` 및 mici 대응 진입점

방법:

- QR 생성·texture·5분 refresh 코드는 그대로 둔다.
- dialog에 pairing URL provider와 title/instructions만 주입할 수 있게 최소 확장한다.
- 기존 comma pairing call site는 기본값으로 동작하게 유지한다.
- CarrotLink button은 offroad + network available에서만 활성화한다.
- paired 전환, 만료, 취소, network failure를 표시한다.

별도 QR library, webview, frontend runtime은 추가하지 않는다.

### 4. 플랫폼 UI: 단일 `/pair` page

수정 대상:

- `dashboard/web/src/app/pair/page.tsx`
- 기존 API client/session helper
- 장치 목록의 unpair action

동작:

1. fragment code를 읽는다.
2. code가 없으면 잘못된 링크를 표시한다.
3. code를 `sessionStorage`에 임시 저장하고 URL fragment를 즉시 지운다.
4. 로그인하지 않았으면 기존 `/login?next=/pair` 흐름을 재사용한다.
5. `/pair`로 돌아오면 같은 tab의 임시 code를 읽는다.
6. 사용자 확인 후 claim API를 한 번 호출한다.
7. 성공·실패·만료 시 임시 code를 삭제하고, 성공하면 장치 화면으로 이동한다.

QR scanner, Logto, 새 state library, 새 modal framework는 추가하지 않는다.

### 5. M3: 최소 persistent agent

M2가 통과한 뒤에만 추가한다.

- 기존 `websocket_client`로 outbound WSS
- 기존 platform `ws.Client`, `Hub`, `RPCCaller` 재사용
- CarrotLink 전용 signed JWT, 5분 이하 수명
- TLS verification 필수; localhost 예외도 production 코드에는 두지 않음
- 지수 backoff + jitter, network 없어도 조용히 대기
- RPC는 `getState` 하나로 시작

SunnyPilot의 uploader, log forwarding, local proxy, thread pool, sponsor/role 코드는 가져오지 않는다.

### 6. M4: 단일 안전 설정

pairing과 read-only agent가 검증된 뒤에만 추가한다.

장치 RPC:

- `getSettingsSchema`
- `getSettings(keys)`
- `setSetting(command)`

`setSetting` command:

```json
{
  "command_id": "<UUID>",
  "key": "ShowDateTime",
  "value": 1,
  "expires_at": "<RFC3339>",
  "expected_current": 0
}
```

결과:

```json
{
  "command_id": "<UUID>",
  "status": "applied",
  "key": "ShowDateTime",
  "previous": 0,
  "current": 1,
  "error_code": null
}
```

`status`는 `applied | rejected | expired | conflict | duplicate` 중 하나다. 동일 `command_id`가 다시 오면 값을 재적용하지 않고 저장된 결과를 반환한다.

장치 검증 순서:

1. paired·not revoked
2. command ID 미사용
3. 만료 전
4. offroad
5. key가 장치 측 allowlist에 있음
6. value가 int이며 `carrot_settings.json` min/max/unit 만족
7. current value가 `expected_current`와 일치
8. 기존 typed Params writer로 적용
9. read-back 후 결과 회신

초기 allowlist:

```python
REMOTE_SETTINGS = {"ShowDateTime"}
```

`ShowDateTime`은 UI 표시만 바꾸는 후보지만 실장치 적용 전 사용처와 회귀를 다시 확인한다. steering, longitudinal, CAN, safety, SSH, update, reboot 관련 key는 허용하지 않는다.

## 검증 순서

1. 순수 unit test: token, hash, expiry, state transition, allowlist
2. Go integration test: PostgreSQL transaction과 동시 claim
3. mock device WebSocket: connect → status → revoke → reconnect reject
4. PC UI: QR URL·fragment 제거·로그인 복귀
5. Comma 배포 전 diff/rollback 검토
6. 사용자 승인 후 offroad 실제 장치에서 pairing만 검증
7. 별도 승인 후 `ShowDateTime` 0↔1 한 key 검증

실차 검증에서도 CAN 송신, tuning, 서비스 재시작은 하지 않는다.

## 명시적 비구현

- 다중 owner, 초대, 역할
- offline command queue와 retry worker
- batch setting 변경
- cloud message broker
- certificate authority나 mTLS
- refresh token
- 원격 shell/file API
- SunnyLink API compatibility
- log upload, video, local proxy

위 기능은 실제 요구가 생기고 현재 단순 모델이 부족하다는 측정 결과가 있을 때 추가한다.
