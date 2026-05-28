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
암호화된 HLS 스트림은 `ffmpeg` 경로를 우선 사용하며, 일부 native HLS 처리에 필요한 `pycryptodomex`도 함께 설치됩니다.

## 사용법

브라우저로 페이지를 열고 스트림 후보를 감지한 뒤 가장 그럴듯한 본편을 다운로드합니다.

```bash
video-dl "https://example.com/watch/123"
```

후보만 확인:

```bash
video-dl "https://example.com/watch/123" -l
```

브라우저를 직접 보면서 로그인, 쿠키 동의, 재생 버튼 클릭이 필요한 페이지 처리:

```bash
video-dl "https://example.com/watch/123" --headed -s 45
```

헤드리스에서도 기본적으로 데스크톱 크롬 User-Agent, 한국어 locale, timezone, 일반 브라우저 헤더를 넣습니다. 특정 사이트가 그래도 차단하면 `--headed`로 확인하거나 `--user-agent`로 직접 값을 지정할 수 있습니다.

재생 버튼 클릭 시 뜨는 악성 광고 팝업은 기본으로 닫고, 명확한 광고 네트워크 요청은 차단합니다. 새 탭에서 실제 영상이 열리는 경우를 위해 광고처럼 보이지 않는 새 탭은 유지하며, 스트림 감지는 열린 모든 탭에서 수행합니다. 영상 사이트가 정상 동작하는 데 팝업 차단이 방해될 때만 끌 수 있습니다.

```bash
video-dl "https://example.com/watch/123" --allow-popups
```

광고로 보이는 후보까지 포함해서 두 번째 후보 다운로드:

```bash
video-dl "https://example.com/watch/123" --include-ads -c 2
```

5초짜리 클립처럼 짧은 영상이 광고로 제외될 때:

```bash
video-dl "https://example.com/watch/123" --allow-short
```

`yt-dlp`의 일반 페이지 추출기로 바로 다운로드:

```bash
video-dl "https://example.com/watch/123" -m ytdlp
```

브라우저 감지 모드는 점수가 높은 후보부터 다운로드를 시도합니다. 선택한 후보가 404 등으로 실패하면 필터링된 다음 후보를 이어서 시도하고, 모든 후보가 실패한 경우에만 실패로 처리합니다.

기본 `auto` 모드는 브라우저 감지에서 스트림 후보를 하나도 찾지 못한 경우 `yt-dlp` 페이지 추출로 fallback합니다. 이 fallback을 완전히 끄려면:

```bash
video-dl "https://example.com/watch/123" --no-fallback
```

Tor/SOCKS 프록시를 통해 브라우저 감지와 다운로드를 실행하려면 `.proxyinfo`를 만들고 `-p`, `--use-proxy`를 붙입니다.

```bash
cp .proxyinfo.example .proxyinfo
video-dl "https://example.com/watch/123" -p
video-dl -i sites.txt -j 3 -p
```

기본 `.proxyinfo` 예시는 로컬 Tor 프록시 `socks5://127.0.0.1:9050`과 ControlPort `127.0.0.1:9051`을 사용합니다.
Docker에서 Tor를 띄운다면 9050은 SOCKS 프록시, 9051은 IP 변경 요청용 ControlPort로 열면 됩니다.

```text
PROXY_URL=socks5://127.0.0.1:9050
TOR_CONTROL_HOST=127.0.0.1
TOR_CONTROL_PORT=9051
TOR_CONTROL_PASSWORD=plain-control-password
TOR_NEWNYM_DELAY=10
TOR_ROTATION_RETRIES=1
TOR_ROTATE_ON_STATUS=403,429,500,502,503,504
PROXY_IP_CHECK_URL=https://api.ipify.org
PROXY_KILL_SWITCH=true
```

`TOR_CONTROL_PASSWORD`에는 `TOR_HashedControlPassword` 값이 아니라 ControlPort 인증에 쓰는 평문 비밀번호를 넣어야 합니다. 다운로드 중 403, 429, 5xx 계열 에러가 발생하면 Tor에 `SIGNAL NEWNYM`을 요청하고 `TOR_NEWNYM_DELAY`초 기다린 뒤 같은 다운로드를 다시 시도합니다.
프록시를 쓰면 시작 시점의 현재 프록시 IP를 로그에 남기고, IP 변경 후에도 새 IP를 다시 확인합니다. IP 확인 주소는 `PROXY_IP_CHECK_URL`로 바꿀 수 있습니다.
`PROXY_KILL_SWITCH=true`이면 프록시 IP 확인에 실패했을 때 브라우저나 다운로드를 시작하지 않고 즉시 종료합니다.
병렬 다운로드 중 여러 작업이 동시에 차단되더라도 IP 변경 요청은 하나씩 순서대로 처리합니다.

출력 디렉토리 지정:

```bash
video-dl "https://example.com/watch/123" -o videos
```

출력 템플릿 지정:

```bash
video-dl "https://example.com/watch/123" --output-template "videos/%(title)s.%(ext)s"
```

브라우저 감지 모드의 기본 파일명은 페이지 제목을 우선 사용합니다. 페이지 제목을 찾지 못하면 `yt-dlp`가 추출한 제목인 `%(title).200B.%(ext)s` 형식으로 저장합니다. 직접 템플릿을 지정하려면 `--output-template`을 사용하면 됩니다.

여러 URL을 파일에서 읽어 다운로드:

```bash
video-dl --input-file sites.txt
```

`sites.txt`는 한 줄에 하나의 URL을 넣습니다. 빈 줄과 `#`로 시작하는 주석 줄은 무시합니다.

```text
https://example.com/watch/123
https://example.com/watch/456
# https://example.com/watch/skip
https://example.com/watch/789
```

동시에 3개까지 병렬 처리:

```bash
video-dl -i sites.txt -j 3
```

배치 모드는 기본적으로 `htop`처럼 터미널 전체 화면에서 고정된 표 형태로 진행률을 표시합니다. 예전처럼 일반 tqdm 진행바를 쓰려면 `--no-dashboard`를 붙이면 됩니다.

```bash
video-dl -i sites.txt -j 10 --no-dashboard
```

HLS/DASH 스트림은 내부적으로 작은 조각(fragment) 여러 개로 나뉩니다. `-F`, `--fragment-parallel`은 한 영상 안에서 이 조각을 몇 개씩 동시에 받을지 정합니다. 기본값은 4입니다.

```bash
video-dl -i sites.txt -j 3 -F 8
```

`-j`는 동시에 처리할 URL 개수이고, `-F`는 각 다운로드 내부의 조각 동시 다운로드 수입니다. 너무 크게 잡으면 사이트가 차단하거나 서버/네트워크가 불안정해질 수 있으니 보통 4~8부터 시도하는 편이 좋습니다.

실패한 URL만 재시도합니다. 기본값은 실패 후 3번 재시도이며, `--retries`로 조정할 수 있습니다.

```bash
video-dl -i sites.txt -j 3 -r 5
```

`Ctrl+C`를 누르면 새 작업과 재시도를 중단하고 진행바를 정리한 뒤 종료합니다.
끝까지 실패한 URL이 있으면 마지막에 번호와 URL 목록을 출력합니다.
마지막 요약에는 실제 다운로드한 개수, 이미 있어서 스킵한 개수, 실패한 개수를 함께 출력합니다.

배치 모드에서는 전체 작업 수와 각 다운로드의 진행률을 MB 단위로 출력합니다. 전체 파일 크기를 알 수 없는 스트림은 진행된 MB와 속도를 계속 갱신합니다.
다운로드 중에는 진행바만 갱신하고, 재시도/실패 같은 로그는 진행바가 정리된 뒤 마지막에 모아서 출력합니다.

```text
video-dl dashboard
batch 1/3 (33%)

slot  job          state        progress                       size             speed
1     1/3 try 1    downloading  ███████████░░░░░░░░░░░░░░░░░ 42% 84.0/200.0 MB  3.1 MB/s
```

재생목록/목록 페이지에서 영상 페이지 링크만 추출:

```bash
video-dl "https://example.com/playlist/abc" -x
```

추출한 링크를 `sites.txt`로 저장한 뒤 배치 다운로드:

```bash
video-dl "https://example.com/playlist/abc" -x --links-output sites.txt
video-dl -i sites.txt -j 3
```

링크 추출은 `<a>` 주변에 썸네일 이미지가 있거나 URL/텍스트에 `watch`, `video`, `episode`, `lecture` 같은 힌트가 있는지 점수화합니다. 명확한 광고/팝업 URL은 제외합니다. 너무 적게 나오면 `--link-min-score 4`처럼 낮추고, 너무 많이 나오면 값을 높이면 됩니다.

## 자주 쓰는 옵션

| 옵션 | 설명 |
| --- | --- |
| `-o`, `--output-dir` | 다운로드 저장 디렉토리 |
| `-i`, `--input-file` | 한 줄에 하나씩 URL이 있는 파일 |
| `-j`, `--parallel` | 배치 다운로드 동시 실행 수 |
| `-F`, `--fragment-parallel` | HLS/DASH 조각 동시 다운로드 수 |
| `-r`, `--retries` | 실패 URL 재시도 횟수 |
| `--dashboard` | 배치 진행률을 전체 화면 표 형태로 표시. 기본값 |
| `--no-dashboard` | 일반 tqdm 진행바 사용 |
| `-l`, `--list-only` | 스트림 후보만 출력 |
| `-c`, `--candidate` | 다운로드할 후보 번호 |
| `-m`, `--mode` | `auto`, `browser`, `ytdlp` 중 선택 |
| `--no-fallback` | 브라우저 감지 실패 시 `yt-dlp` fallback 끄기 |
| `-p`, `--use-proxy` | `.proxyinfo` 프록시 설정 사용 |
| `--proxy-info` | 프록시 설정 파일 경로. 기본값 `.proxyinfo` |
| `-s`, `--play-seconds` | 브라우저 감지 대기 시간 |
| `-x`, `--extract-links` | 목록 페이지에서 영상 링크 추출 |
| `-q`, `--quiet` | 출력 줄이기 |
| `--allow-short` | 짧다는 이유만으로 제외된 스트림 허용 |
| `--allow-popups` | 새 탭/팝업 자동 차단 끄기 |
| `--output-template` | `yt-dlp` 출력 파일명 템플릿 직접 지정 |

## 구조

코드는 헥사고날 구조로 나뉩니다.

- `video_downloader/domain`: `StreamCandidate`, `LinkCandidate` 같은 모델과 광고/후보 점수화 규칙
- `video_downloader/ports`: 브라우저 감지, 다운로드, 진행 표시, URL 저장소 포트
- `video_downloader/application`: 단일 다운로드, 배치 다운로드, 링크 추출 유스케이스
- `video_downloader/adapters`: Playwright, yt-dlp, tqdm, 텍스트 파일 저장소 구현
- 기존 `sniffer.py`, `downloader.py`, `progress.py` 같은 모듈은 호환용 wrapper로 유지

## 광고 스트림 구분 방식

광고 판별은 완벽한 보장이 아니라 휴리스틱입니다. 다음 신호를 조합합니다.

- `doubleclick`, `googleads`, `imasdk`, `vast`, `preroll`, `midroll` 같은 광고 도메인/경로/쿼리 키워드
- HLS/DASH manifest 안에서 파악되는 총 길이가 짧은 스트림
- 비디오 본편에서 자주 보이는 `master`, `playlist`, `vod`, `episode`, `movie` 같은 힌트
- 파일 크기와 콘텐츠 타입

기본값은 광고로 보이는 후보를 다운로드 대상에서 제외합니다. 사이트마다 스트림 이름이 다르므로 `--list-only`로 후보를 확인하고 `--candidate`를 조정하는 방식이 가장 안전합니다.
