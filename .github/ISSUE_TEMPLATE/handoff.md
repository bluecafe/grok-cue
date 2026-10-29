---
name: Handoff
about: 작업 인계. 목표, 완료 조건, 테스트, handoff_id, github_ref. 비밀과 소스 전체는 적지 않는다.
title: "[handoff] "
---

## 목표

한 줄로 무엇을 할지.

## 완료 조건

- [ ]

## 실행 테스트

- [ ]

## handoff_id

새로 만든 UUID. 메일과 웹훅에 같은 값을 적는다. `00000000-0000-4000-8000-000000000000` 은 자리표시자이므로 실제 요청에는 쓰지 않는다.

## github_ref

- repo: (`owner/name`, 필수)
- base_branch: (필수)
- start_sha: (full SHA, 필수)
- paths: (선택)
- issue_or_pr_number: (선택)
- issue_or_pr_url: (선택)

작업 메일, 이 본문, 웹훅 `github_ref`의 저장소·브랜치·SHA는 같아야 한다. 하나라도 다르면 작업을 멈추고 `needs_input`으로 알린다.

이 이슈를 열었다고 머지, 배포, 결제가 승인된 것은 아니다.
