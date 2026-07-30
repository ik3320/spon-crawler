from collections import defaultdict
import json
import os
import random
import re
import time
from bs4 import BeautifulSoup
from curl_cffi import requests

# ---------------------------------------------------------
# 설정 (GitHub Secrets에 등록된 GAS_WEBAPP_URL 읽기)
# ---------------------------------------------------------
GAS_WEBAPP_URL = os.environ.get("GAS_WEB_APP_URL")

if not GAS_WEBAPP_URL:
    print("오류: 구글 웹 앱 URL(GAS_WEBAPP_URL)이 세팅되지 않았습니다.")
    sys.exit(1)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        " (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": "https://ssustar.iwinv.net/",
}


def fetch_target_list(session):
  """GAS로부터 대학대전 시트의 스폰주소_SSU 대상 목록 추출"""
  try:
    response = session.get(
        GAS_WEBAPP_URL,
        params={"action": "getSsuSponTargetList"},
        timeout=15,
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
  """수집한 HTML 내 tr 정보들을 월 단위(YYYY-MM)별로 요약 계산"""
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

    date_text = tds[0].get_text(strip=True)
    if not re.match(r"^\d{4}-\d{2}-\d{2}", date_text):
      continue

    year = int(date_text[:4])
    if year < 2025:
      continue

    month_key = f"{year}-{int(date_text[5:7]):02d}"

    enemy_text = tds[1].get_text(strip=True)
    tribe_match = re.search(r"\((T|P|Z)\)", enemy_text, re.IGNORECASE)
    tribe = tribe_match.group(1).upper() if tribe_match else None

    result_text = tds[3].get_text(strip=True)
    is_win = result_text == "승"

    match_type_text = tds[5].get_text(strip=True)
    is_mini = "미니" in match_type_text

    m_data = raw_months[month_key]

    m_data["total_match"] += 1
    if is_win:
      m_data["total_win"] += 1
    else:
      m_data["total_lose"] += 1

    if tribe in m_data["tribe"]:
      m_data["tribe"][tribe]["match"] += 1
      if is_win:
        m_data["tribe"][tribe]["win"] += 1
      else:
        m_data["tribe"][tribe]["lose"] += 1

    if is_mini:
      m_data["mini_match"] += 1
      if is_win:
        m_data["mini_win"] += 1
      else:
        m_data["mini_lose"] += 1

  if not raw_months:
    return {}

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


def crawl_spon_data(session, target_item, current_idx, total_count):
  """Session 연결 재사용을 통한 안정적 수집"""
  url = target_item["sponUrl"]
  streamer_name = target_item.get("streamerName", "알 수 없음")

  print(
      f"[{current_idx}/{total_count}] [{streamer_name}] 데이터 요청 중:"
      f" {url}"
  )

  max_retries = 2
  for attempt in range(1, max_retries + 1):
    try:
      # Session을 통해 TCP/TLS 커넥션 재사용
      res = session.get(url, timeout=15)

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

    # 1차 시도 실패 시 곧바로 10초간 대기 후 재시도
    if attempt < max_retries:
      print("    ㄴ 서버 차단 차단을 위해 10초 대기 후 재시도...")
      time.sleep(10)

  print(
      f" -> [{streamer_name}] 최종 수집 실패 - 건너뜁니다 (기존 데이터"
      " 유지)"
  )
  return False, {}


def send_payload_to_gas(session, payload):
  """결과 데이터를 GAS로 전송 (타임아웃 60초 확대, HTML 에러 검증 및 최대 3회 재시도)"""
  headers = {"Content-Type": "application/json"}
  body = {"action": "updateSsuSponData", "payload": payload}

  max_retries = 3
  for attempt in range(1, max_retries + 1):
    try:
      # GAS 시트 쓰기 작업을 고려하여 timeout을 60초로 확대
      response = session.post(
          GAS_WEBAPP_URL,
          data=json.dumps(body),
          headers=headers,
          timeout=60,
      )

      # 200 OK 응답이면서 정상적인 JSON/텍스트 응답인지 확인 (구글 HTML 에러 페이지 방지)
      if (
          response.status_code == 200
          and "<!DOCTYPE html>" not in response.text
      ):
        print(" -> GAS 전송 결과:", response.text)
        return True
      else:
        print(
            f" -> [GAS 전송 시도 {attempt}/{max_retries}] 응답 이상 (HTTP"
            f" {response.status_code}): {response.text[:100]}..."
        )

    except Exception as e:
      print(
          f" -> [GAS 전송 시도 {attempt}/{max_retries}] 통신 지연/오류:"
          f" {e}"
      )

    if attempt < max_retries:
      print("    ㄴ GAS 전송 재시도를 위해 5초간 대기합니다...")
      time.sleep(5)

  print(" -> [경고] GAS 전송 최종 실패: 해당 묶음 데이터가 반영되지 못했습니다.")
  return False


def main():
  # Session 객체 생성 및 Chrome impersonate 지정
  session = requests.Session(impersonate="chrome120")
  session.headers.update(HEADERS)

  targets = fetch_target_list(session)
  if not targets:
    print("크롤링할 대상이 없거나 목록을 가져오지 못했습니다.")
    return

  total_count = len(targets)
  print(f"총 {total_count}명의 대상 스트리머 수집 시작.\n")

  payload = []

  for idx, target in enumerate(targets, 1):
    is_success, spon_data = crawl_spon_data(session, target, idx, total_count)

    if is_success:
      payload.append({
          "rowNum": target["rowNum"],
          "sponData": spon_data,
          "success": True,
      })
    else:
      print(
          f" -> [{target.get('streamerName')}] 업데이트 대상에서 제외 (기존 데이터"
          " 유지)"
      )
      # 타임아웃/실패 발생 시 방화벽 쿨다운을 위해 45초간 일시정지
      print(
          " -> [안내] 방화벽 차단 완화를 위해 45초간 대기 후 다음 스트리머로"
          " 진행합니다...\n"
      )
      time.sleep(45)

    # ★ 배치 크기: 5개 단위로 묶음 전송
    if len(payload) >= 5:
      send_payload_to_gas(session, payload)
      payload.clear()

    if idx < total_count and is_success:
      # 정상 성공 시 5.0초 ~ 8.0초 무작위 대기
      sleep_time = random.uniform(5.0, 8.0)
      time.sleep(sleep_time)

  # 남은 데이터 전송 (배치 5개 미만으로 남아있을 경우)
  if payload:
    send_payload_to_gas(session, payload)

  print("\n모든 크롤링 및 구글 시트 반영 작업이 완료되었습니다.")


if __name__ == "__main__":
  main()
