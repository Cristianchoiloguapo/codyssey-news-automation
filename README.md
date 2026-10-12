# Project B | AI 뉴스 요약 자동화 워크플로우

RSS에서 AI 관련 뉴스를 읽고, 본문을 추출한 후 OpenAI로 **3줄 이내 요약**하여 Notion에 매일 자동 저장하는 팀 프로젝트입니다.

**상태:** 기본 구현 준비 완료 / 실서비스 API 연결 및 실측 결과 입력 필요. 실제 테스트 전까지 완료했다고 주장하지 않습니다.

## 1. 개발 도구

- Python 3.11, VS Code
- feedparser(RSS), requests + trafilatura(본문 추출)
- OpenAI API (Responses API, `gpt-4.1-mini`)
- Notion API (버전 `2025-09-03`)
- GitHub Actions (매일 09:07 Asia/Seoul 스케줄 + 수동 실행)

## 2. 흐름

스케줄 → RSS 수집 → AI 주제 키워드 필터링 → 중복 검사 → 본문 추출 → AI 3줄 요약 → Notion 자동 저장 → 로그 확인

구조와 예외 처리: [`docs/WORKFLOW.md`](docs/WORKFLOW.md)

## 3. Notion DB 설정

Notion에서 새로운 데이터베이스를 만든 뒤 속성을 **아래 이름·타입 그대로** 작성하세요.

| 속성 이름 | Notion 타입 | 용도 |
|---|---|---|
| `제목` | 제목(Title) | 기사 제목 (기본 `이름` 속성을 `제목`으로 변경) |
| `요약` | 텍스트(Text) | OpenAI 요약 결과 |
| `원문 링크` | URL | 원문 주소 / 중복 검사 |
| `발행일시` | 날짜(Date) | RSS 원문 게시일 |

Notion 개발자 사이트의 내부 연동(Integration)을 생성해 **Read content / Insert content** 권한을 주고 해당 DB 페이지에 연결을 추가하세요. Notion API의 최신 구조에서는 데이터베이스(Database) ID와 **데이터 소스(Data source) ID가 서로 다릅니다**. 이 코드는 `NOTION_DATA_SOURCE_ID`를 사용합니다.

## 4. 로컬 실행

Python 3.11 이상 설치 후 VS Code 터미널에서(우선 빠른 실행 기준):

```bash
python -m pip install -r requirements.txt
```

프로젝트별 패키지를 분리하고 싶다면 가상환경(venv)을 만들어 VS Code 인터프리터를 해당 가상환경으로 선택하면 됩니다.

먼저 키 없이 RSS/본문 테스트:

```bash
python news_summary.py --dry-run
```

`.env.example`을 복사하여 `.env`로 저장하고 실제 값을 채우세요. `.env` 파일을 GitHub에 올리지 마세요.

```bash
python news_summary.py --check
python news_summary.py --run
python -m unittest discover -s tests -v
```

`--run` 명령은 실제 OpenAI API 비용이 발생할 수 있으며, 조건이 맞는 뉴스 1건을 Notion에 저장합니다.

## 5. 자동 실행 설정

1. GitHub에 이 폴더 전체를 업로드합니다(반드시 `.github/workflows/daily-news.yml` 파일 포함).
2. GitHub 저장소 → Settings → Secrets and variables → Actions → New repository secret:
   - `OPENAI_API_KEY`
   - `NOTION_TOKEN`
   - `NOTION_DATA_SOURCE_ID`
3. 저장소 Actions → Daily AI News to Notion → Run workflow로 수동 실행합니다.
4. Notion DB에 1개가 저장되는지 확인합니다.
5. 이후 GitHub Actions에서 매일 한국시간 09:07 스케줄을 실행합니다(보장된 초 단위 정시 시작은 아님).

*주의:* GitHub Actions의 예약 워크플로는 기본 브랜치에 있는 경우에 실행됩니다. 장기 비활성 공개 저장소에서는 예약이 중단될 수 있으므로 주기적으로 로그를 확인합니다.

## 6. 제출 증빙

- 자동화 구조: [`docs/WORKFLOW.md`](docs/WORKFLOW.md)
- [ ] `docs/evidence/`에 GitHub Actions 성공 실행 내역 스크린샷 추가
- [ ] `docs/evidence/`에 Notion DB 저장 결과 스크린샷 추가
- [ ] `docs/evidence/`에 자동화 구조/설정 스크린샷 추가
- [ ] 실제 노션 DB 링크: [입력]
- [ ] 성공 실행 링크: [입력]
- 팀원 역할 및 오류처리: [`docs/WORK_LOG_TEMPLATE.md`](docs/WORK_LOG_TEMPLATE.md)
- 체크리스트: [`docs/SUBMISSION_CHECKLIST.md`](docs/SUBMISSION_CHECKLIST.md)

## 7. 팀 구성 및 시행착오

팀별 역할은 **실제로 진행된 일만** `docs/WORK_LOG_TEMPLATE.md`에 기록합니다. 해결한 오류, 주제 키워드 선택 근거, 예외 처리 테스트 결과를 추가한 뒤 제출합니다.

## 8. 참고

- GitHub Actions Schedule: https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows
- Notion Query Data Source: https://developers.notion.com/reference/query-a-data-source
- OpenAI Responses API: https://developers.openai.com/api/reference/python/resources/responses/methods/create
