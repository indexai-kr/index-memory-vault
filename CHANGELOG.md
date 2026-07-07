# Changelog

## 0.2.3

Add: `search_memory` FTS 한국어 다중어 질의 폴백 추가 — G3 파일럿 발견 결함 대응.
빈 결과 시 서버가 자동으로 (1) 핵심 토큰 축약 prefix FTS → (2) 토큰별 OR LIKE
부분일치로 폴백하고, 응답에 `retrieval_path`(fts | fts_reduced | like | none)를
기록합니다. 없는 내용을 지어내지 않는 원칙은 유지(폴백 실패 시 빈 결과 반환).

## 0.2.1

Fix: Claude Desktop MSIX 빌드의 누락된 config 경로를 자동 감지합니다.
`Packages\*\LocalCache\Roaming\Claude`의 모든 일치 패키지에 병합 기입합니다.
