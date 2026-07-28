from collections import defaultdict
import json
import os
import random
import re
import time
from bs4 import BeautifulSoup
# 브라우저 TLS Handshake 흉내를 위해 curl_cffi 사용
from curl_cffi import requests

# ---------------------------------------------------------
# 설정 (GitHub Secrets에 등록되어 있으면 우선 적용, 없으면 기본 URL 사용)
# ---------------------------------------------------------
GAS_WEBAPP_URL = os.environ.get(
    "GAS_WEBAPP_URL",
    "https://script.google.com/macros/s/AKfycbz2peaf7ClpvR1bKJ6GLL0wKpX0xzNZZ7MqkZfttkgTE_I6DCVM03kLq9dbeqcc3-RYzQ/exec",
)

# 웹사이트 접근 블락을 방지하기 위한 HTTP 헤더 설정
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        " (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
}


def fetch_target_list():
  """GAS로부터 대학대전 시트의 스폰주소_SSU 대상 목록 추출"""
  try:
    response = requests.get(
        GAS_WEBAPP_URL,
        params={"action": "getSsuSponTargetList"},
        timeout=15,
        impersonate="chrome120",
    )
    if response.status_code == 200:
      data = response.json()
      if isinstance(data, str):
        data = json.loads(data)
      return data if isinstance(data, list) else []
    else:
      print(f"대상 목록 로드 실패 (HTTP {response.status_code})")
      return []
  except Exception as e:
    print(f"GAS 통신 오류: {e}")
    return []


def parse_spon_table(soup):
  """수집한 HTML 내 tr 정보들을 월 단위(YYYY-MM)별로 요약 계산 (스폰수, 승, 패, 승률 포함)"""
  raw_months = defaultdict(
      lambda: {
          "total_match": 0,
          "total_win": 0,
          "total_lose": 0,
          "tribe": {
              "Z": {"match": 0, "win": 0, "lose": 0},
              "P": {"match": 0, "win": 0, "lose": 0},
              "T": {"match": 0, "win": 0, "lose": 0},
          },
          "mini_match": 0,
          "mini_win": 0,
          "mini_lose": 0,
      }
  )

  rows = soup.select("tbody tr")
  if not rows:
    return {}

  for row in rows:
    tds = row.find_all("td")
    if len(tds) < 6:
      continue

    # 1. 날짜 추출 (예: 2026-07-26)
    date_text = tds[0].get_text(strip=True)
    if not re.match(r"^\d{4}-\d{2}-\d{2}", date_text):
      continue

    # 2025년 1월 이후 데이터만 필터링
    year = int(date_text[:4])
    if year < 2025:
      continue

    month_key = f"{year}-{int(date_text[5:7]):02d}"

    # 2. 상대방 및 종족 추출 (예: 유즈(Z))
    enemy_text = tds[1].get_text(strip=True)
    tribe_match = re.search(r"\((T|P|Z)\)", enemy_text, re.IGNORECASE)
    tribe = tribe_match.group(1).upper() if tribe_match else None

    # 3. 승패 데이터
    result_text = tds[3].get_text(strip=True)
    is_win = result_text == "승"

    # 4. 대전종류 데이터
    match_type_text = tds[5].get_text(strip=True)
    is_mini = ("미니대전" in match_type_text) or (
        "미니대학대전" in match_type_text
    )

    # --- 집계 처리 ---
    m_data = raw_months[month_key]

    # 전체 통계
    m_data["total_match"] += 1
    if is_win:
      m_data["total_win"] += 1
    else:
      m_data["total_lose"] += 1

    # 종족별 통계
    if tribe in m_data["tribe"]:
      m_data["tribe"][tribe]["match"] += 1
      if is_win:
        m_data["tribe"][tribe]["win"] += 1
      else:
        m_data["tribe"][tribe]["lose"] += 1

    # 미니대전 통계
    if is_mini:
      m_data["mini_match"] += 1
      if is_win:
        m_data["mini_win"] += 1
      else:
        m_data["mini_lose"] += 1

  # 데이터가 아예 없는 경우 빈 객체 반환
  if not raw_months:
    return {}

  # 포맷 정형화 (스폰수, 승, 패, 승률 계산) - 날짜 오름차순 정렬
  formatted_result = {}
  for month_key, data in sorted(raw_months.items()):
    total_m = data["total_match"]
    total_w = data["total_win"]
    total_l = data["total_lose"]

    mini_m = data["mini_match"]
    mini_w = data["mini_win"]
    mini_l = data["mini_lose"]

    formatted_result[month_key] = {
        "스폰수": total_m,
        "승": total_w,
        "패": total_l,
        "승률": round((total_w / total_m * 100), 1) if total_m > 0 else 0.0,
        "종족별": {
            t: {
                "스폰수": data["tribe"][t]["match"],
                "승": data["tribe"][t]["win"],
                "패": data["tribe"][t]["lose"],
                "승률": (
                    round(
                        (
                            data["tribe"][t]["win"]
                            / data["tribe"][t]["match"]
                            * 100
                        ),
                        1,
                    )
                    if data["tribe"][t]["match"] > 0
                    else 0.0
                ),
            }
            for t in ["Z", "P", "T"]
        },
        "미니대전": {
            "스폰수": mini_m,
            "승": mini_w,
            "패": mini_l,
            "승률": (
                round((mini_w / mini_m * 100), 1) if mini_m > 0 else 0.0
            ),
        },
    }

  return formatted_result


def crawl_spon_data(target_item, current_idx, total_count):
  """curl_cffi로 Chrome TLS 브라우저 위장 수집 및 파싱"""
  url = target_item["sponUrl"]
  streamer_name = target_item.get("streamerName", "알 수 없음")

  print(
      f"[{current_idx}/{total_count}] [{streamer_name}] 데이터 요청 중:"
      f" {url}"
  )

  max_retries = 2
  for attempt in range(1, max_retries + 1):
    try:
      # ★ impersonate="chrome120"을 통해 브라우저 암호화 서명 위장
      res = requests.get(
          url, headers=HEADERS, timeout=15, impersonate="chrome120"
      )

      if res.status_code != 200:
        print(f" -> 접속 실패 (HTTP Status: {res.status_code})")
      else:
        soup = BeautifulSoup(res.text, "html.parser")

        rows = soup.select("tbody tr")
        if not rows:
          print(" -> 테이블 구조(tbody tr)를 찾지 못함 - 건너뜁니다.")
          return False, {}

        parsed_json_data = parse_spon_table(soup)

        if not parsed_json_data:
          print(
              " -> 최근 전적 데이터가 없거나 파싱에 실패함 - 건너뜁니다."
          )
          return False, {}
        else:
          print(
              f" -> 성공: {len(parsed_json_data)}개 월별 데이터 파싱완료"
          )
          return True, parsed_json_data

    except Exception as e:
      print(f" -> [시도 {attempt}/{max_retries}] 요청 중 지연/오류 발생: {e}")

    if attempt < max_retries:
      time.sleep(3)

  print(
      f" -> [{streamer_name}] 최종 수집 실패 - 건너뜁니다 (기존 데이터"
      " 유지)"
  )
  return False, {}


def send_payload_to_gas(payload):
  """결과 데이터를 GAS로 전송"""
  headers = {"Content-Type": "application/json"}
  body = {"action": "updateSsuSponData", "payload": payload}
  try:
    response = requests.post(
        GAS_WEBAPP_URL,
        data=json.dumps(body),
        headers=headers,
        timeout=15,
        impersonate="chrome120",
    )
    print(" -> GAS 전송 결과:", response.text)
  except Exception as e:
    print(f" -> GAS 전송 실패: {e}")


def main():
  targets = fetch_target_list()
  if not targets:
    print("크롤링할 대상이 없거나 목록을 가져오지 못했습니다.")
    return

  total_count = len(targets)
  print(f"총 {total_count}명의 대상 스트리머 수집 시작.\n")

  payload = []
  consecutive_failures = 0  # 연속 실패 감지 카운터

  for idx, target in enumerate(targets, 1):
    is_success, spon_data = crawl_spon_data(target, idx, total_count)

    if is_success:
      consecutive_failures = 0  # 성공 시 카운터 초기화
      payload.append({
          "rowNum": target["rowNum"],
          "sponData": spon_data,
          "success": True,
      })
    else:
      consecutive_failures += 1
      print(
          f" -> [{target.get('streamerName')}] 업데이트 대상에서 제외 (기존 데이터"
          " 유지)"
      )

    # ★ 연속 3회 실패 시 서버 방화벽 쿨다운을 위해 60초간 일시 정지
    if consecutive_failures >= 3:
      print(
          "\n[경고] 연속 3회 타임아웃 발생 (IP 차단 가능성). 방화벽 쿨다운을"
          " 위해 60초간 대기합니다...\n"
      )
      time.sleep(60)
      consecutive_failures = 0

    # 10개 단위 묶음 전송
    if len(payload) >= 10:
      send_payload_to_gas(payload)
      payload.clear()

    if idx < total_count:
      # ★ 서버 차단을 최소화하기 위해 대기 시간을 4.0초 ~ 7.0초 사이로 상향 조정
      sleep_time = random.uniform(4.0, 7.0)
      time.sleep(sleep_time)

  # 남은 데이터 전송
  if payload:
    send_payload_to_gas(payload)

  print("\n모든 크롤링 및 구글 시트 반영 작업이 완료되었습니다.")


if __name__ == "__main__":
  main()
