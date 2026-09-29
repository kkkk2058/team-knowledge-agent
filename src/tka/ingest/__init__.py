"""수집: 소스 레포 가져오기 → 파일 고르기 → (4단계) 정규화 → 청킹.

    uv run python -m tka.ingest fetch   소스 레포를 캐시에 받아 기준 커밋으로 맞춘다
    uv run python -m tka.ingest files   포함 규칙으로 고른 파일 목록과 규칙별 개수

소스 문서를 이 레포에 복사하지 않는다. 캐시(.cache/)는 커밋하지 않는다.
"""
