# video-downloader

브라우저에서 실제 페이지를 열고 재생을 시도한 뒤 네트워크 응답에서 HLS(`.m3u8`), DASH(`.mpd`), MP4/WebM 같은 비디오 스트림 후보를 찾아 다운로드하는 Python CLI입니다.

권한이 있는 콘텐츠를 개인적으로 저장하거나 테스트할 때 쓰는 도구입니다. DRM을 우회하지 않으며, 로그인/유료/저작권 콘텐츠는 해당 서비스 약관과 법적 권한 범위 안에서만 사용해야 합니다.

## 설치

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
python -m playwright install chromium
```

`yt-dlp`가 HLS/DASH 병합에 `ffmpeg`를 사용할 수 있으므로, 시스템에 `ffmpeg`가 있으면 결과가 더 안정적입니다.

## 사용법

브라우저로 페이지를 열고 스트림 후보를 감지한 뒤 가장 그럴듯한 본편을 다운로드합니다.

```bash
video-dl "https://example.com/watch/123"
```

후보만 확인:

```bash
video-dl "https://example.com/watch/123" --list-only
```

브라우저를 직접 보면서 로그인, 쿠키 동의, 재생 버튼 클릭이 필요한 페이지 처리:

```bash
video-dl "https://example.com/watch/123" --headed --play-seconds 45
```

광고로 보이는 후보까지 포함해서 두 번째 후보 다운로드:

```bash
video-dl "https://example.com/watch/123" --include-ads --candidate 2
```

`yt-dlp`의 일반 페이지 추출기로 바로 다운로드:

```bash
video-dl "https://example.com/watch/123" --mode ytdlp
```

출력 템플릿 지정:

```bash
video-dl "https://example.com/watch/123" -o "downloads/%(title)s.%(ext)s"
```

## 광고 스트림 구분 방식

광고 판별은 완벽한 보장이 아니라 휴리스틱입니다. 다음 신호를 조합합니다.

- `doubleclick`, `googleads`, `imasdk`, `vast`, `preroll`, `midroll` 같은 광고 도메인/경로/쿼리 키워드
- HLS/DASH manifest 안에서 파악되는 총 길이가 짧은 스트림
- 비디오 본편에서 자주 보이는 `master`, `playlist`, `vod`, `episode`, `movie` 같은 힌트
- 파일 크기와 콘텐츠 타입

기본값은 광고로 보이는 후보를 다운로드 대상에서 제외합니다. 사이트마다 스트림 이름이 다르므로 `--list-only`로 후보를 확인하고 `--candidate`를 조정하는 방식이 가장 안전합니다.
