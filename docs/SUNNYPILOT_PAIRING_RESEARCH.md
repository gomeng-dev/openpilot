# SunnyPilot / SunnyLink 페어링 조사

조사일: 2026-08-12

고정 기준:

- `sunnypilot/sunnypilot`: `2d859a8ca610bc20f48b0b3e4f7d570a80b59c29`
- `sunnypilot/sunnylink-frontend`: `a702451a1144edb057ca368f3e962faf6b9e40ae`
- CarrotPilot: `ajouatom/openpilot:carrot` 기준
- CarrotLink 플랫폼 후보: `gomeng-dev/comma-log-manager` `a3d0182d590e93b32a56f7af14d02214ddb90784`

이 문서는 공개 코드로 확인한 사실과 공개되지 않은 backend에 대한 추정을 분리한다.

## 결론

SunnyPilot의 SunnyLink 흐름은 세 단계다.

1. Comma의 기존 `/persist/comma` 키쌍으로 SunnyLink 장치를 먼저 등록한다.[8][10]
2. Comma 화면에 SunnyLink device ID와 1시간짜리 서명 JWT를 담은 QR을 표시한다.[8][14]
3. 사용자가 QR의 `/sso?state=...` URL에서 로그인하면 비공개 backend가 사용자와 장치를 연결하고, 장치는 5초 간격으로 user 목록을 조회해 페어링 완료를 감지한다.[13][14]

공개 frontend는 QR을 스캔하거나 claim API를 호출하지 않는다. 장치별 QR 표시 방법을 안내하고, 로그인된 사용자의 장치 목록·상태·설정을 관리한다.[3][5] 실제 `/sso` claim 처리는 공개되지 않은 backend에 있다.[unverified]

CarrotLink는 이 제품 흐름만 참고한다. JWT를 QR URL에 넣는 방식, comma 키 재사용, 전체 Params에서 일부만 막는 blocklist 방식은 복제하지 않는다.

## 1. 장치 등록

### 확인된 동작

`SunnylinkApi.register_device()`는 다음 순서로 동작한다.

1. `SunnylinkDongleId`가 이미 있으면 재사용한다.
2. 기존 comma `DongleId`, hardware serial, IMEI를 구한다.
3. `BaseApi.get_key_pair()`로 `/persist/comma/id_rsa` 또는 `id_ecdsa` 키를 읽는다.
4. private key로 `{register: true, exp: now+1h}` JWT를 만든다.
5. `POST {SUNNYLINK_API_HOST}/v2/pilotauth/`에 `comma_dongle_id`, serial, IMEI, public key, register token을 query parameter로 보낸다.
6. 응답의 `device_id`를 `SunnylinkDongleId` Params에 저장한다.[10]

부팅 시 별도 registration manager가 네트워크가 생길 때까지 기다렸다가 이 함수를 실행한다. 등록 실패 시 임시 fault를 기록하고 다음 부팅에서 재시도한다.[12]

근거:

- [SunnylinkApi 등록 구현](https://github.com/sunnypilot/sunnypilot/blob/2d859a8ca610bc20f48b0b3e4f7d570a80b59c29/openpilot/sunnypilot/sunnylink/api.py#L65-L151)
- [기존 comma 키쌍 재사용](https://github.com/sunnypilot/sunnypilot/blob/2d859a8ca610bc20f48b0b3e4f7d570a80b59c29/openpilot/common/api/base.py#L67-L73)
- [부팅 등록 manager](https://github.com/sunnypilot/sunnypilot/blob/2d859a8ca610bc20f48b0b3e4f7d570a80b59c29/openpilot/sunnypilot/sunnylink/registration_manager.py#L14-L35)

### 의미

SunnyLink device identity와 comma device identity는 ID 값은 분리하지만 같은 private key를 사용한다. CarrotLink는 이 결합을 피하고 전용 키를 사용한다.

## 2. QR payload와 사용자 claim

### 확인된 동작

SunnyLink pairing dialog는 기존 openpilot `PairingDialog`를 상속해 QR renderer와 5분 refresh를 재사용한다.

GitHub 계정 페어링일 때:

1. `SunnylinkDongleId`를 읽는다.
2. 같은 장치 private key로 기본 1시간 유효 JWT를 만든다.
3. `1|<device_id>|<jwt>` 문자열을 일반 base64로 인코딩한다.
4. QR URL을 `{API_HOST}/sso?state=<base64>`로 만든다.
5. QR은 5분마다 다시 생성된다.[9][14]

따라서 QR을 5분마다 바꾸지만, QR에 들어간 JWT 자체의 기본 만료는 1시간이다.[8][9][14]

`state`는 암호화가 아니라 base64 encoding이므로 스캔한 쪽에서 device ID와 JWT를 복원할 수 있다.[14]

근거:

- [SunnyLink pairing URL 생성](https://github.com/sunnypilot/sunnypilot/blob/2d859a8ca610bc20f48b0b3e4f7d570a80b59c29/openpilot/system/ui/sunnypilot/widgets/sunnylink_pairing_dialog.py#L20-L48)
- [JWT 기본 claim과 1시간 만료](https://github.com/sunnypilot/sunnypilot/blob/2d859a8ca610bc20f48b0b3e4f7d570a80b59c29/openpilot/common/api/base.py#L30-L48)
- [기존 QR renderer의 5분 refresh](https://github.com/sunnypilot/sunnypilot/blob/2d859a8ca610bc20f48b0b3e4f7d570a80b59c29/openpilot/selfdrive/ui/widgets/pairing_dialog.py#L18-L68)

### 공개 계약과 확인되지 않는 backend 동작

legacy v0 OpenAPI schema에는 다음 계약이 남아 있다.

- `GET /sso?state=<state>`
- `GET /sso/auth?...&state=<state>`
- `POST /device/{deviceId}/users/{userId}?pairingToken=<JWT>`
- `PairDeviceWithUser` 설명은 runtime API JWT와 같은 token을 pairing에서는 one-time code로 쓴다고 명시한다.[15]
- 공개 OpenAPI 타입에 `DeviceUserResponseModel(device_id, user_id, token_hash, timestamps)`가 있다.[7]
- 한 장치에 여러 user를 연결할 수 있다.[7]

하지만 `/sso` backend 구현은 공개 저장소에서 찾지 못했다. 따라서 다음은 확정할 수 없다.

- JWT의 실제 단일 사용 marker 저장과 atomic consumption 방식
- `state`의 JWT 서명 검증 및 OAuth user 연결 순서
- 실제 만료 검증 정책
- claim transaction의 동시성 처리
- `token_hash`의 생성·용도·회전 방식
- 로그인 전에 `state`를 어떻게 보존하는지

근거:

- [공개 DeviceUser 타입](https://github.com/sunnypilot/sunnylink-frontend/blob/a702451a1144edb057ca368f3e962faf6b9e40ae/src/sunnylink/v1/schema_api.d.ts#L840-L849)
- [사용자별 장치 조회 계약](https://github.com/sunnypilot/sunnylink-frontend/blob/a702451a1144edb057ca368f3e962faf6b9e40ae/src/sunnylink/v1/schema_api.d.ts#L126-L166)
- [legacy SSO와 one-time pairing contract](https://github.com/sunnypilot/sunnylink-frontend/blob/a702451a1144edb057ca368f3e962faf6b9e40ae/src/sunnylink/v0/schema_api.d.ts#L347-L365)

## 3. 장치의 페어링 완료 감지

### 확인된 동작

`SunnylinkState`는 panel이 열려 있을 때 roles와 users를 5초 간격으로 조회한다.

- `GET device/{SunnylinkDongleId}/users`
- 응답 user 목록이 비어 있지 않으면 paired로 본다.[13]
- 목록을 `SunnylinkCache_Users`에 저장한다.
- dialog는 `not paired → paired` 전환을 감지하면 자동으로 닫힌다.[14]

근거:

- [users polling과 cache](https://github.com/sunnypilot/sunnypilot/blob/2d859a8ca610bc20f48b0b3e4f7d570a80b59c29/openpilot/sunnypilot/sunnylink/sunnylink_state.py#L92-L160)
- [pairing dialog 완료 감지](https://github.com/sunnypilot/sunnypilot/blob/2d859a8ca610bc20f48b0b3e4f7d570a80b59c29/openpilot/system/ui/sunnypilot/widgets/sunnylink_pairing_dialog.py#L45-L48)

## 4. 사용자 로그인과 공개 frontend

### 확인된 동작

SunnyLink frontend는 SvelteKit과 Logto browser SDK를 사용한다.

- OIDC authorization code + PKCE 처리는 Logto SDK가 담당한다.[4]
- callback은 `/auth/callback`에서 처리한 후 dashboard로 이동한다.[6]
- 로그인된 사용자는 `GET /v1/users/self/devices`로 paired device 목록을 가져온다.[5]
- `PairingModal.svelte`는 comma 3X/4에서 SunnyLink 설정 화면과 QR을 여는 방법만 보여 준다. QR payload를 읽거나 claim API를 호출하지 않는다.[3]
- 장치 해제는 `DELETE /device/{deviceId}`, 개별 user 제거는 `DELETE /device/{deviceId}/users/{userId}` 계약을 사용한다.[2]
- deregister modal은 서버 deregister 후 user association을 제거한다.[16] 장치 측에는 `SunnylinkDongleId`와 role/user cache를 지우는 대응 경로가 없어 웹 해제 뒤 로컬 stale state가 남는다.[10][13]

근거:

- [Logto client와 token 관리](https://github.com/sunnypilot/sunnylink-frontend/blob/a702451a1144edb057ca368f3e962faf6b9e40ae/src/lib/logto/auth.svelte.ts#L1-L48)
- [OIDC callback](https://github.com/sunnypilot/sunnylink-frontend/blob/a702451a1144edb057ca368f3e962faf6b9e40ae/src/routes/auth/callback/%2Bpage.svelte#L17-L49)
- [사용자 장치 목록 로드](https://github.com/sunnypilot/sunnylink-frontend/blob/a702451a1144edb057ca368f3e962faf6b9e40ae/src/routes/%2Blayout.ts#L41-L100)
- [PairingModal은 안내 UI](https://github.com/sunnypilot/sunnylink-frontend/blob/a702451a1144edb057ca368f3e962faf6b9e40ae/src/lib/components/PairingModal.svelte#L194-L271)
- [장치·사용자 해제 API 호출](https://github.com/sunnypilot/sunnylink-frontend/blob/a702451a1144edb057ca368f3e962faf6b9e40ae/src/lib/api/device.ts#L666-L682)
- [deregister 순서](https://github.com/sunnypilot/sunnylink-frontend/blob/a702451a1144edb057ca368f3e962faf6b9e40ae/src/lib/components/DeregisterDeviceModal.svelte#L69-L99)

## 5. 장치 연결과 원격 설정

### 확인된 동작

SunnyPilot은 별도 `sunnylinkd`를 실행한다.

- 장치가 `wss://athena.sunnylink.ai`로 outbound WebSocket을 연다.[1][11]
- `Authorization: Bearer <device-signed JWT>`를 보낸다.[11]
- localhost가 아닌 연결은 TLS certificate 검증을 요구한다.[11]
- 예외적으로 `startLocalProxy(remote_ws_uri, ...)`는 전달된 remote URI에 `CERT_NONE`을 사용한다.[11]
- 기존 Athena의 WebSocket/JSON-RPC worker를 광범위하게 재사용한다.
- 설정 조회는 Params key·metadata·value RPC로 제공한다.
- 설정 변경은 `saveParams`로 여러 Params를 쓴다.[2][11]

`saveParams` 보안은 remote 허용 목록이 아니라 `BLOCKED_PARAMS`에 든 일부 key만 막는 방식이다. 또한 공개 함수 자체에는 offroad 강제가 없다.[11]

근거:

- [WebSocket 연결과 TLS](https://github.com/sunnypilot/sunnypilot/blob/2d859a8ca610bc20f48b0b3e4f7d570a80b59c29/openpilot/sunnypilot/sunnylink/athena/sunnylinkd.py#L267-L323)
- [원격 Params RPC](https://github.com/sunnypilot/sunnypilot/blob/2d859a8ca610bc20f48b0b3e4f7d570a80b59c29/openpilot/sunnypilot/sunnylink/athena/sunnylinkd.py#L161-L252)
- [frontend Athena host 분리](https://github.com/sunnypilot/sunnylink-frontend/blob/a702451a1144edb057ca368f3e962faf6b9e40ae/src/lib/api/client.ts#L16-L40)
- [frontend 설정 변경 호출](https://github.com/sunnypilot/sunnylink-frontend/blob/a702451a1144edb057ca368f3e962faf6b9e40ae/src/lib/api/device.ts#L719-L751)

## 6. 기존 comma-log-manager에서 재사용할 것

현재 플랫폼 저장소에는 SunnyLink 호환 실험 코드가 이미 있다.

재사용:

- device-signed RS256/ES256 JWT 검증
- WebSocket upgrade, ping/pong, 1MB read limit
- device별 단일 active connection Hub
- JSON-RPC request/response correlation과 30초 timeout
- PostgreSQL migration/sqlc 구조
- 기존 UI session과 Next.js dashboard

교체 또는 보완:

- pilotauth 직후 `is_paired=true`로 보는 자동 pairing
- token을 확인만 하고 소유권을 만들지 않는 `/pair`
- 사용자↔장치 ownership 부재
- SunnyLink 명칭의 보조 device ID/public key
- blocklist 기반 `saveParams`
- 모든 session user가 모든 device에 접근 가능한 현재 규칙

관련 로컬 파일:

- `dashboard/internal/api/pilotauth.go`
- `dashboard/internal/api/pairing.go`
- `dashboard/internal/api/device.go`
- `dashboard/internal/api/sunnylink_params.go`
- `dashboard/internal/ws/handler.go`
- `dashboard/internal/ws/methods.go`
- `dashboard/internal/ws/sunnylink_methods.go`
- `dashboard/sql/migrations/011_sunnylink_dongle.up.sql`

## 7. CarrotLink에 적용하지 않을 SunnyLink 선택

| SunnyLink 선택 | CarrotLink 결정 | 이유 |
|---|---|---|
| comma private key 재사용 | CarrotLink 전용 key 생성 | 신뢰 영역과 폐기 범위 분리 |
| device ID + 1시간 JWT를 QR query에 포함 | 5분짜리 무작위 단일 사용 code만 QR fragment에 포함 | URL/access/referrer log와 camera history 노출 최소화 |
| user 목록을 paired 상태로 해석 | 명시적 owner row와 consumed session | ownership·동시성·폐기 상태를 직접 표현 |
| 전체 Params - blocklist | schema와 별도의 remote allowlist | 새 위험 key가 자동 허용되는 fail-open 방지 |
| batch `saveParams` | 초기에는 단일 key command | 실패·감사·중복 적용을 단순하게 검증 |
| 전체 Athena daemon 재사용 | 기존 transport + 최소 RPC 4개 | upload/proxy/log 기능은 페어링·설정에 불필요 |
| Logto 추가 | 기존 dashboard session 재사용 | 새 identity provider와 frontend dependency가 불필요 |
| 다중 user/roles | 초기 owner 1명 | 현재 개인 dashboard 요구를 충족하며 권한 모델을 최소화 |
| 웹 deregister 뒤 로컬 ID/cache 보존 | revoke를 장치가 영속 반영하고 agent 중지 | stale identity와 무한 재연결 방지 |

## 8. 조사 한계

- SunnyLink backend source는 공개 GitHub 조직에서 확인하지 못했다.
- legacy schema는 pairing token을 one-time code라고 명시하지만 서버의 실제 `/sso`, atomic replay 방지, token hash, ownership transaction은 검증하지 못했다.
- staging/production 서비스에 임의 요청을 보내지 않았다.
- 실제 Comma나 CarrotLink backend를 변경·재시작하지 않았다.

## Sources

[1] https://github.com/sunnypilot/sunnylink-frontend/blob/a702451a1144edb057ca368f3e962faf6b9e40ae/src/lib/api/client.ts
[2] https://github.com/sunnypilot/sunnylink-frontend/blob/a702451a1144edb057ca368f3e962faf6b9e40ae/src/lib/api/device.ts
[3] https://github.com/sunnypilot/sunnylink-frontend/blob/a702451a1144edb057ca368f3e962faf6b9e40ae/src/lib/components/PairingModal.svelte
[4] https://github.com/sunnypilot/sunnylink-frontend/blob/a702451a1144edb057ca368f3e962faf6b9e40ae/src/lib/logto/auth.svelte.ts
[5] https://github.com/sunnypilot/sunnylink-frontend/blob/a702451a1144edb057ca368f3e962faf6b9e40ae/src/routes/%2Blayout.ts
[6] https://github.com/sunnypilot/sunnylink-frontend/blob/a702451a1144edb057ca368f3e962faf6b9e40ae/src/routes/auth/callback/%2Bpage.svelte
[7] https://github.com/sunnypilot/sunnylink-frontend/blob/a702451a1144edb057ca368f3e962faf6b9e40ae/src/sunnylink/v1/schema_api.d.ts
[8] https://github.com/sunnypilot/sunnypilot/blob/2d859a8ca610bc20f48b0b3e4f7d570a80b59c29/openpilot/common/api/base.py
[9] https://github.com/sunnypilot/sunnypilot/blob/2d859a8ca610bc20f48b0b3e4f7d570a80b59c29/openpilot/selfdrive/ui/widgets/pairing_dialog.py
[10] https://github.com/sunnypilot/sunnypilot/blob/2d859a8ca610bc20f48b0b3e4f7d570a80b59c29/openpilot/sunnypilot/sunnylink/api.py
[11] https://github.com/sunnypilot/sunnypilot/blob/2d859a8ca610bc20f48b0b3e4f7d570a80b59c29/openpilot/sunnypilot/sunnylink/athena/sunnylinkd.py
[12] https://github.com/sunnypilot/sunnypilot/blob/2d859a8ca610bc20f48b0b3e4f7d570a80b59c29/openpilot/sunnypilot/sunnylink/registration_manager.py
[13] https://github.com/sunnypilot/sunnypilot/blob/2d859a8ca610bc20f48b0b3e4f7d570a80b59c29/openpilot/sunnypilot/sunnylink/sunnylink_state.py
[14] https://github.com/sunnypilot/sunnypilot/blob/2d859a8ca610bc20f48b0b3e4f7d570a80b59c29/openpilot/system/ui/sunnypilot/widgets/sunnylink_pairing_dialog.py
[15] https://github.com/sunnypilot/sunnylink-frontend/blob/a702451a1144edb057ca368f3e962faf6b9e40ae/src/sunnylink/v0/schema_api.d.ts
[16] https://github.com/sunnypilot/sunnylink-frontend/blob/a702451a1144edb057ca368f3e962faf6b9e40ae/src/lib/components/DeregisterDeviceModal.svelte
