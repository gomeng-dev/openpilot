# CarrotLink 마일스톤

상태 표기: `[ ]` 예정 · `[-]` 진행 중 · `[x]` 검증 완료

## M0 — 저장소와 기준 문서

- [x] `ajouatom/openpilot`을 `gomeng-dev/openpilot`로 포크
- [x] 외장 SSD에 `carrot` 브랜치 clone
- [x] `origin`과 `upstream` remote 분리
- [x] `origin/carrot`와 `upstream/carrot` 시작 SHA 일치 확인
- [x] 프로젝트 목표와 경계 문서 작성
- [x] 마일스톤 문서 작성

완료 조건:

- 두 문서가 저장소 루트에 있고 `carrot` 브랜치에 커밋되어 있다.
- 작업 트리가 깨끗하며 포크에서 커밋을 확인할 수 있다.

## M1 — 참고 구현 조사와 계약 결정

- [x] SunnyPilot 장치 측 페어링 흐름 조사
- [x] SunnyLink frontend의 계정·장치·설정 흐름 조사
- [x] 현재 Carrot Web 설정 API와 Params 적용 경로 조사
- [x] 기존 `comma-log-manager` 인증·데이터 모델 조사
- [x] 위협 모델과 신뢰 경계 문서화
- [x] 최소 장치-플랫폼 계약 및 버전 전략 결정

완료 조건:

- QR 수명, 장치 인증, 폐기, 재전송 방지, 명령 결과 계약이 문서화된다.
- 구현하지 않을 항목과 근거가 명확하다.

근거:

- [`docs/SUNNYPILOT_PAIRING_RESEARCH.md`](docs/SUNNYPILOT_PAIRING_RESEARCH.md)
- [`docs/CARROTLINK_PAIRING_PLAN.md`](docs/CARROTLINK_PAIRING_PLAN.md)

## M2 — 로컬 페어링 수직 슬라이스

- [-] Comma에서 단일 사용 페어링 세션 생성 — client/worker mock 검증 완료, 실장치 검증 대기
- [-] 기존 Comma 설정 UI의 `PairingDialog`를 재사용해 CarrotLink QR·취소 UX 추가 — tici/mici 연결 완료, device render 검증 대기
- [x] 플랫폼에서 QR 승인 및 장치 등록
- [x] CarrotLink 전용 장치 key 생성·저장
- [-] 페어링/해제 최소 통합 검사 — 로컬 claim 검증 완료, 실제 Comma E2E와 해제 UX 대기

완료 조건:

- mock 또는 개발 환경에서 QR 생성부터 등록·해제까지 재현 가능하다.
- QR이나 로그에 장기 비밀값이 노출되지 않는다.

## M3 — 읽기 전용 장치 연결

- [ ] 장치의 인증된 아웃바운드 연결
- [ ] 플랫폼에서 연결 상태와 마지막 접속 표시
- [ ] CarrotPilot 버전, 브랜치 및 기본 장치 상태 표시
- [ ] 재연결과 자격 증명 폐기 검증

완료 조건:

- 네트워크 단절·복구 후 자동으로 안전하게 재연결된다.
- 폐기된 장치는 다시 연결할 수 없다.

## M4 — 설정 조회와 단일 변경

- [ ] 장치가 기존 Carrot 설정 스키마와 현재값 제공
- [ ] 플랫폼에서 설정 검색·분류·현재값·기본값 표시
- [ ] 단일 설정 변경 요청 및 장치 측 허용 목록 검증
- [ ] 적용 성공·실패 결과와 감사 기록
- [ ] 중복·만료·범위 초과 명령 거부 검사

완료 조건:

- 플랫폼 변경이 기존 Params 의미를 보존한다.
- 잘못된 명령은 장치 상태를 바꾸지 않는다.

## M5 — 플랫폼 UX 통합

- [ ] 기존 대시보드 내 CarrotLink 정보 구조 통합
- [ ] 모바일·태블릿·PC, 가로·세로 반응형 검증
- [ ] 장치 전환, 검색, 변경 상태 및 오류 복구 UX
- [ ] 접근성 및 저대역폭 동작 확인

완료 조건:

- 지원 뷰포트에서 주요 흐름에 가로 넘침이나 막힌 조작이 없다.
- 설정 결과와 실패 원인이 명확히 표시된다.

## M6 — 안전한 시험 배포

- [ ] Comma 배포 전 백업 및 롤백 절차 검증
- [ ] 사용자 승인 후 offroad 상태에서 시험 설치
- [ ] 페어링·조회·단일 설정 변경 실장치 검증
- [ ] 레거시 Carrot Web 및 운행 기능 회귀 확인
- [ ] 플랫폼 장애와 네트워크 단절 시 fail-safe 확인

완료 조건:

- 레거시 Carrot Web이 계속 동작한다.
- CarrotLink 장애가 CarrotPilot 운행 기능에 영향을 주지 않는다.
- 검증 결과와 남은 위험을 문서에 기록한다.

## 현재 작업 경계

**M2 로컬 페어링 수직 슬라이스를 진행 중**이다. 플랫폼 pairing API와 `/pair` 승인 화면, Comma client와 tici/mici QR dialog 연결까지 구현했다. 다음 단계는 Linux/device UI build 검증과 공개 개발 환경의 실제 claim E2E다. 실제 Comma 배포는 사용자 승인 전까지 하지 않는다.
