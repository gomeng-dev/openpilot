# CarrotLink 프로젝트

> 이 문서는 CarrotLink의 목표와 경계를 기록하는 살아 있는 기준 문서다. 설계 결정이나 범위가 바뀌면 관련 코드보다 먼저 또는 같은 커밋에서 이 문서를 갱신한다.

## 한 줄 목표

Comma에서 QR로 CarrotLink 플랫폼과 안전하게 페어링하고, 플랫폼에서 차량 상태를 확인하고 CarrotPilot 설정을 변경하면 페어링된 Comma가 이를 적용하는 원격 관리 환경을 만든다.

`CarrotLink`는 가칭이다.

## 기준 저장소

### Comma 측

- 저장소: `gomeng-dev/openpilot`
- upstream: `ajouatom/openpilot`
- 작업 기준 브랜치: `carrot`
- 시작 기준: `8dab4c59254ae0e711a14a7161535c448da2b850`
- 로컬 경로: `/Volumes/SSD/CarrotLink/openpilot`

역할:

- 기존 CarrotPilot과 레거시 Carrot Web을 보존한다.
- 페어링 QR 표시와 해제 UX를 추가한다.
- CarrotLink 플랫폼과의 인증된 통신을 담당한다.
- 상태 및 설정 스키마를 플랫폼에 제공한다.
- 플랫폼에서 승인된 설정 변경만 기존 Params 의미와 범위를 보존해 적용한다.

### 플랫폼 측

- 저장소: `gomeng-dev/comma-log-manager`
- 현재 로컬 경로: `/Users/son2688s/docker/comma-log-manager`
- 현재 작업 브랜치: `automatic-backup`

역할:

- 기존 로그 수집·대시보드를 확장한다.
- `sunnypilot/sunnylink-frontend`의 제품 흐름을 참고해 계정, 장치, 페어링, 상태, 설정 UX를 제공한다.
- 페어링 세션, 장치 인증, 명령 전달, 결과 및 감사 기록을 관리한다.
- 장치가 여러 대여도 장치별 권한과 상태를 분리한다.

SunnyPilot과 SunnyLink는 동작과 UX를 이해하기 위한 참고 구현이며, 코드나 서비스 계약을 무단 복제하지 않는다.

조사 결과와 구현 계획:

- [`docs/SUNNYPILOT_PAIRING_RESEARCH.md`](docs/SUNNYPILOT_PAIRING_RESEARCH.md)
- [`docs/CARROTLINK_PAIRING_PLAN.md`](docs/CARROTLINK_PAIRING_PLAN.md)

## 제품 원칙

1. **레거시 유지**: Comma 자체 Carrot Web은 제거하지 않는다. 플랫폼 장애나 미페어링 상태에서도 로컬 관리가 가능해야 한다.
2. **가벼운 장치 클라이언트**: Comma에는 새 프런트엔드 런타임이나 무거운 프레임워크를 추가하지 않는다. 기존 Python/웹 구조와 설치된 의존성을 우선 재사용한다.
3. **아웃바운드 우선**: Comma에 공개 포트를 여는 방식보다 장치가 플랫폼에 만드는 인증된 아웃바운드 연결을 우선 검토한다.
4. **기존 의미 보존**: Params 키, 자료형, 단위, 범위, 기본값, 삭제 동작을 변경하지 않는다.
5. **차량 제어와 분리**: 페어링·웹·설정 동기화 작업은 CAN 송신, lateral/longitudinal 제어 및 튜닝을 건드리지 않는다.
6. **실패 시 안전**: 연결이 끊기거나 명령 검증에 실패하면 원격 변경을 거부하고 기존 CarrotPilot 운행을 방해하지 않는다.
7. **관찰 가능성**: 페어링, 해제, 설정 요청, 적용 결과를 민감정보 없이 감사 가능하게 남긴다.
8. **upstream 추적 가능성**: CarrotLink 변경은 작고 독립적인 커밋으로 유지해 `upstream/carrot` 동기화를 어렵게 만들지 않는다.

## 보안 경계

구현 전에 페어링 및 장치 인증 위협 모델을 문서화한다. 최소 요구사항은 다음과 같다.

- QR에는 장기 비밀값을 넣지 않는다.
- 페어링 코드는 단일 사용, 짧은 만료, 재사용 방지를 적용한다.
- 장치 자격 증명은 장치별로 발급·폐기할 수 있어야 한다.
- 모든 원격 통신은 TLS와 서버 인증을 사용한다.
- 원격 설정 명령은 허용 목록, 자료형, 범위 및 최신 상태를 검증한다.
- 임의 셸 실행이나 범용 파일 쓰기 API를 페어링 채널에 제공하지 않는다.
- 명령 ID와 만료 시각으로 재전송 및 중복 적용을 방지한다.
- 플랫폼에서 장치 연결을 즉시 해제할 수 있어야 한다.
- 토큰, QR payload, 고객·장치 식별자는 로그와 진단에서 최소화하거나 마스킹한다.

## 확정된 페어링 결정

- 기존 dashboard의 `ui_users`와 signed HttpOnly session cookie를 재사용한다.
- comma key는 초기 장치 소유 증명에만 사용하고 지속 연결에는 CarrotLink 전용 ES256 key를 사용한다.
- QR에는 5분짜리 256-bit random single-use code만 넣고 서버는 SHA-256 hash만 저장한다.
- pairing code는 query가 아니라 URL fragment에 넣어 HTTP access log와 `Referer` 노출을 피한다.
- 초기 권한 모델은 장치당 owner 1명이다. 공유·역할 기능은 요구가 생길 때 추가한다.
- 기존 dashboard의 WebSocket Hub, JSON-RPC correlation, JWT 검증, PostgreSQL/sqlc 구조를 재사용한다.
- offline command queue는 만들지 않는다. 장치가 offline이면 요청은 즉시 실패한다.
- 원격 설정은 장치 측 allowlist와 offroad 검증을 모두 통과한 단일 key 변경부터 시작한다.

## 초기 기능 범위

- Comma에서 페어링 QR 생성·표시
- 플랫폼에서 QR 승인 및 장치 등록
- 연결/마지막 접속/버전 등 읽기 전용 장치 상태
- Carrot 설정 스키마와 현재값 조회
- 단일 설정 변경 요청, 장치 측 검증·적용, 결과 회신
- 페어링 해제 및 장치 자격 증명 폐기
- 플랫폼의 반응형 설정 UX

## 명시적 비목표

초기 범위에서는 다음을 만들지 않는다.

- 원격 차량 제어 또는 CAN 명령
- 원격 튜닝 자동화
- 범용 원격 터미널
- 레거시 Carrot Web 제거
- 대규모 실시간 영상 중계
- SunnyLink API와의 호환성 보장
- 여러 메시지 브로커나 플러그인 시스템을 위한 선행 추상화

## 개발 규칙

- 구현 순서는 [`MILESTONES.md`](MILESTONES.md)를 따른다.
- 단계 착수 시 해당 마일스톤의 상태와 결정사항을 갱신한다.
- 완료 표시는 실행 증거가 있을 때만 한다.
- Comma checkout 변경, 빌드, 서비스 재시작, 재부팅 및 실차 배포는 별도 사용자 승인 후 수행한다.
- 플랫폼 작업은 `comma-log-manager`, 장치 작업은 이 저장소에서 분리한다.
- 두 저장소에 걸친 계약 변경은 같은 계약 버전과 상호 호환성 검증을 기록한다.

## 미정 사항

아래 항목은 구현 전에 조사하고 ADR 또는 이 문서에 결정 근거를 남긴다.

- 전용 key rotation UX와 분실 장치 복구 절차
- 향후 다중 owner 또는 read-only 공유가 실제로 필요한지
- `ShowDateTime`을 첫 원격 설정으로 사용할지 실장치 전 최종 확인

## 변경 기록

- 2026-08-12: 프로젝트 목표, 저장소 책임, 안전 경계 및 초기 범위를 문서화했다.
- 2026-08-12: SunnyPilot/SunnyLink 공개 구현을 조사하고 CarrotLink 페어링 계약·위협 모델·최소 구현 계획을 확정했다.
