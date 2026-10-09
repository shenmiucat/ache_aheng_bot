import base64
import datetime
import json
import os
import random
import socketserver
import sys
import threading
import time
from http.server import SimpleHTTPRequestHandler

import requests
import urllib3

# 強制 Log 即時刷新
if hasattr(sys.stdout, "reconfigure"):
  sys.stdout.reconfigure(line_buffering=True)

os.environ["TZ"] = "Asia/Taipei"
if hasattr(time, "tzset"):
  time.tzset()

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


# ================= 讓 Render 順利偵測 Port 的虛擬伺服器 =================
def run_dummy_server():
  port = int(os.environ.get("PORT", 10000))

  class QuietHandler(SimpleHTTPRequestHandler):

    def log_message(self, format, *args):
      pass

  try:
    with socketserver.TCPServer(("", port), QuietHandler) as httpd:
      print(f"🌐 [Render 連線監聽] 已成功綁定 Port {port}")
      httpd.serve_forever()
  except Exception as e:
    print(f"⚠️ 虛擬 Port 伺服器啟動失敗: {e}")


# ================= 基礎設定 (全數改從安全環境變數讀取) =================
API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()

# 從 Render Environment 讀取 Telegram Token & Gist 設定
TG_BOT_TOKEN_ACHE = os.environ.get("TG_BOT_TOKEN_ACHE", "").strip()
TG_BOT_TOKEN_AHENG = os.environ.get("TG_BOT_TOKEN_AHENG", "").strip()
GIST_ID = os.environ.get("GIST_ID", "").strip()
GIST_TOKEN = os.environ.get("GIST_TOKEN", "").strip()
MY_CHAT_ID = 8773051890

tg_base_ache = "https://api.telegram.org/bot" + TG_BOT_TOKEN_ACHE
TG_SEND_URL_ACHE = tg_base_ache + "/sendMessage"
TG_UPDATES_URL_ACHE = tg_base_ache + "/getUpdates"

tg_base_aheng = "https://api.telegram.org/bot" + TG_BOT_TOKEN_AHENG
TG_SEND_URL_AHENG = tg_base_aheng + "/sendMessage"
TG_UPDATES_URL_AHENG = tg_base_aheng + "/getUpdates"

# 遵照模型設定
CANDIDATE_MODELS = [
    "gemini-3.5-flash-lite",
]

session = requests.Session()

# ================= 全局記憶體資料結構 =================
notes = []
chat_history = []
aheng_history = []
group_history = []


# ================= GitHub Gist 直連同步機制 =================
def fetch_gist_data():
  """直接從 Gist 抓取最新資料並更新記憶體，若失敗則維持現有記憶體"""
  global notes, chat_history, aheng_history, group_history
  if not GIST_ID or not GIST_TOKEN:
    print("⚠️ 未設定 GIST_ID 或 GIST_TOKEN，使用純記憶體模式。")
    return
  url = f"https://api.github.com/gists/{GIST_ID}"
  headers = {
      "Authorization": f"Bearer {GIST_TOKEN}",
      "Accept": "application/vnd.github+json",
  }
  try:
    res = session.get(url, headers=headers, timeout=10)
    if res.status_code == 200:
      files = res.json().get("files", {})
      if "bot_data.json" in files:
        raw_content = files["bot_data.json"].get("content", "{}")
        all_data = json.loads(raw_content)

        notes = all_data.get("notes", [])
        chat_history = all_data.get("ache_private", [])
        aheng_history = all_data.get("aheng_private", [])
        group_history = all_data.get("group", [])
  except Exception as e:
    print(f"⚠️ 從 Gist 讀取資料失敗: {e}")


def push_gist_data():
  """將目前記憶體中的最新資料同步寫入 Gist"""
  if not GIST_ID or not GIST_TOKEN:
    return
  url = f"https://api.github.com/gists/{GIST_ID}"
  headers = {
      "Authorization": f"Bearer {GIST_TOKEN}",
      "Accept": "application/vnd.github+json",
  }

  payload = {
      "files": {
          "bot_data.json": {
              "content": json.dumps(
                  {
                      "notes": notes,
                      "ache_private": chat_history,
                      "aheng_private": aheng_history,
                      "group": group_history,
                  },
                  ensure_ascii=False,
                  indent=2,
              )
          }
      }
  }
  try:
    res = session.patch(url, headers=headers, json=payload, timeout=10)
    if res.status_code == 200:
      print("☁️ [Gist 同步成功] 最新記憶與歷史已更新至 GitHub！")
    else:
      print(f"⚠️ 寫入 Gist 失敗，HTTP 狀態碼: {res.status_code}")
  except Exception as e:
    print(f"⚠️ 備份至 Gist 失敗: {e}")


# 初始化時先從 Gist 拉取一次資料
fetch_gist_data()


# ================= 輔助文字清理與格式化 =================
def format_private_msg(raw_text, char_name="阿澈"):
  clean = "\n".join([
      line
      for line in raw_text.split("\n")
      if not line.strip().startswith("📝 記住：")
      and not line.strip().startswith("🗑 忘記：")
      and not line.strip().startswith("🗑️ 忘記：")
  ])

  if "💭" in clean:
    parts = clean.split("💭", 1)
    speech = (
        parts[0]
        .replace("💬", "")
        .replace(f"[{char_name}]", "")
        .replace("[阿澈]", "")
        .replace("[阿珩]", "")
        .strip()
    )
    thought = parts[1].strip()
    if speech:
      return f"{speech}\n<blockquote><tg-spoiler>💭 {thought}</tg-spoiler></blockquote>"
    else:
      return f"<blockquote><tg-spoiler>💭 {thought}</tg-spoiler></blockquote>"

  return (
      clean.replace("💬", "")
      .replace(f"[{char_name}]", "")
      .replace("[阿澈]", "")
      .replace("[阿珩]", "")
      .strip()
  )


def format_group_msg(raw_text):
  text = raw_text.replace("[END]", "")
  if "💭" in text:
    text = text.split("💭")[0]
  return (
      text.replace("💬 [阿澈]", "")
      .replace("💬 [阿珩]", "")
      .replace("💬", "")
      .replace("[阿澈]", "")
      .replace("[阿珩]", "")
      .strip()
  )


# ================= Telegram 圖片下載與 Base64 轉換 =================
def get_tg_file_b64(file_id, token):
  """抓取 Telegram 圖片並轉為 Base64 字串"""
  try:
    res = session.get(
        f"https://api.telegram.org/bot{token}/getFile?file_id={file_id}",
        timeout=5,
    ).json()
    if not res.get("ok"):
      return None
    file_path = res["result"]["file_path"]

    img_res = session.get(
        f"https://api.telegram.org/file/bot{token}/{file_path}", timeout=10
    )
    if img_res.status_code == 200:
      return base64.b64encode(img_res.content).decode("utf-8")
  except Exception as e:
    print(f"⚠️ 下載圖片失敗: {e}")
  return None


# ================= 記憶與備忘管理 (直連 Gist) =================
def load_memory():
  """直接從 Gist 讀取最新的備忘清單"""
  fetch_gist_data()
  return notes


def update_memory(raw_text):
  """依據指令更新備忘並同步回 Gist"""
  global notes
  fetch_gist_data()  # 更新前先抓取最新狀況，避免複寫
  changed = False

  weekdays = ["週一", "週二", "週三", "週四", "週五", "週六", "週日"]
  now = datetime.datetime.now()
  timestamp = f"[{now.strftime('%Y-%m-%d')} {weekdays[now.weekday()]} {now.strftime('%H:%M')}]"

  for line in raw_text.split("\n"):
    line_str = line.strip()
    if line_str.startswith("📝 記住："):
      item = line_str.replace("📝 記住：", "").strip()
      if item:
        entry = f"{timestamp} {item}"
        if not any(item in n for n in notes):
          notes.append(entry)
          changed = True
          print(f"🧠 [記憶新增] {entry}")
    elif line_str.startswith("🗑️ 忘記：") or line_str.startswith("🗑 忘記："):
      item = (
          line_str.replace("🗑️ 忘記：", "").replace("🗑 忘記：", "").strip()
      )
      before_len = len(notes)
      notes = [n for n in notes if item not in n]
      if len(notes) != before_len:
        changed = True
        print(f"🧹 [記憶清除] {item}")

  if changed:
    push_gist_data()


# ================= 對話歷史持久化 (直連 Gist) =================
def save_chat_history(role, text, file_type="ache_private"):
  """將訊息存入記憶體並直接同步更新至 Gist"""
  global chat_history, aheng_history, group_history

  fetch_gist_data()  # 寫入前先更新最新紀錄，避免多端覆寫

  weekdays = ["週一", "週二", "週三", "週四", "週五", "週六", "週日"]
  now = datetime.datetime.now()
  time_tag = f"[{now.strftime('%Y-%m-%d')} {weekdays[now.weekday()]} {now.strftime('%H:%M')}]"

  if file_type == "group":
    group_history.append((role, f"{time_tag} {text}"))
  elif file_type == "aheng_private":
    aheng_history.append((role, f"{time_tag} {text}"))
  else:
    chat_history.append((role, f"{time_tag} {text}"))

  push_gist_data()


# ================= 載入人設 =================
def load_all_prompts(filename="prompt.txt"):
  base_dir = os.path.dirname(os.path.abspath(__file__))
  prompt_file = os.path.join(base_dir, filename)

  default_ache = "你是阿澈。"
  default_aheng = "你是阿珩。"

  if not os.path.exists(prompt_file):
    return default_ache, default_aheng

  content = ""
  for enc in ["utf-8-sig", "utf-8", "big5", "cp950"]:
    try:
      with open(prompt_file, "r", encoding=enc) as f:
        content = f.read().strip()
        if content:
          print(f"✅ 成功載入 {filename} 設定！(編碼: {enc})")
          break
    except Exception:
      continue

  if not content:
    return default_ache, default_aheng

  try:
    common_text = ""
    ache_text = ""
    aheng_text = ""

    if "=== COMMON ===" in content:
      common_text = content.split("=== COMMON ===")[1].split("===")[0].strip()
    if "=== ACHE ===" in content:
      ache_text = content.split("=== ACHE ===")[1].split("===")[0].strip()
    if "=== AHENG ===" in content:
      aheng_text = content.split("=== AHENG ===")[1].split("===")[0].strip()

    final_ache = (
        f"{common_text}\n\n{ache_text}".strip()
        if ache_text
        else (content if not aheng_text else default_ache)
    )
    final_aheng = (
        f"{common_text}\n\n{aheng_text}".strip()
        if aheng_text
        else default_aheng
    )

    return final_ache, final_aheng
  except Exception as e:
    print(f"⚠️ 解析 {filename} 區塊時出錯: {e}")
    return default_ache, default_aheng


SYSTEM_INSTRUCTION_ACHE, SYSTEM_INSTRUCTION_AHENG = load_all_prompts(
    "prompt.txt"
)


# ================= 讀取 TickTick 待辦 =================
def get_ticktick_summary():
  base_dir = os.path.dirname(os.path.abspath(__file__))
  token_path = os.path.join(base_dir, "ticktick_token.json")

  if not os.path.exists(token_path):
    return "（目前無法讀取待辦清單：找不到 token 檔案）"

  try:
    with open(token_path, "r", encoding="utf-8") as f:
      access_token = json.load(f).get("access_token")
    if not access_token:
      return "（待辦清單讀取異常：token 為空）"
  except Exception:
    return "（待辦清單讀取異常）"

  headers = {"Authorization": "Bearer " + access_token}
  proj_url = "https://api.ticktick.com/open/v1/project"

  try:
    res = session.get(proj_url, headers=headers, verify=False, timeout=3)
    if res.status_code != 200:
      return f"（待辦清單無法連線，錯誤碼: {res.status_code}）"
    projects = res.json()
  except Exception:
    return "（待辦清單連線超時）"

  today_date = datetime.date.today()
  today_tasks = []

  for proj in projects:
    try:
      d_url = f"https://api.ticktick.com/open/v1/project/{proj['id']}/data"
      data_res = session.get(d_url, headers=headers, verify=False, timeout=2)
      if data_res.status_code == 200:
        for t in data_res.json().get("tasks", []):
          due = t.get("dueDate")
          if due:
            try:
              utc_dt = datetime.datetime.strptime(
                  due[:19], "%Y-%m-%dT%H:%M:%S"
              )
              local_dt = utc_dt + datetime.timedelta(hours=8)
              if local_dt.date() == today_date:
                today_tasks.append(
                    f"[{proj['name']}] {t.get('title')}"
                    f" ({local_dt.strftime('%H:%M')})"
                )
            except Exception:
              pass
    except Exception:
      continue

  if today_tasks:
    return "【阿渺今天的待辦】:\n" + "\n".join(today_tasks)
  return "【阿渺今天的待辦】: 今天無特定排程待辦。"


# ================= 呼叫 Gemini 大腦 =================
def call_ai_brain(
    character="ache",
    user_input=None,
    is_auto=False,
    is_group=False,
    extra_context="",
    image_b64=None,
):
  # 呼叫大腦時先從 Gist 抓取最新紀錄
  fetch_gist_data()

  current_key = os.environ.get("GEMINI_API_KEY", "").strip()
  if not current_key:
    return f"⚠️ [{character}] 呼叫失敗：Render 環境變數未讀取到 GEMINI_API_KEY"

  weekdays = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
  now = datetime.datetime.now()
  current_time = f"{now.strftime('%Y-%m-%d %H:%M')} ({weekdays[now.weekday()]})"
  mode_str = "【群聊模式】" if is_group else "【私聊模式】"
  system_prompt = (
      SYSTEM_INSTRUCTION_ACHE
      if character == "ache"
      else SYSTEM_INSTRUCTION_AHENG
  )
  char_name = "阿澈" if character == "ache" else "阿珩"

  mem_text = "\n".join([f"- {n}" for n in notes]) if notes else "（暫無特殊備忘）"
  memory_str = (
      f"\n【你目前腦子裡記住的事】:\n{mem_text}\n" if character == "ache" else ""
  )
  tasks_text = f"{get_ticktick_summary()}\n" if character == "ache" else ""

  target_private = chat_history if character == "ache" else aheng_history
  tagged_private = [(f"{role}(私聊)", text) for role, text in target_private]
  tagged_group = [(f"{role}(群聊)", text) for role, text in group_history]

  combined_history = tagged_private + tagged_group
  combined_history.sort(key=lambda item: item[1])

  history_str = "\n".join(
      [f"{role}: {text}" for role, text in combined_history[-500:]]
  )

  if is_auto:
    prompt = (
        f"{mode_str}\n現在時間是：{current_time}\n{tasks_text}{memory_str}"
        f"最近對話紀錄：\n{history_str}\n"
        "這是你隨機想主動敲她一下。"
    )
  else:
    context_str = f"\n【現場情況】\n{extra_context}\n" if extra_context else ""
    prompt = (
        f"{mode_str}\n現在時間是：{current_time}\n{tasks_text}{memory_str}{context_str}"
        f"最近對話紀錄：\n{history_str}\n對方發言：{user_input}\n請以{char_name}的身分回覆："
    )

  # 打包 Parts（若有圖片則一併加入）
  parts = [{"text": prompt}]
  if image_b64:
    parts.append({"inlineData": {"mimeType": "image/jpeg", "data": image_b64}})

  headers = {"Content-Type": "application/json", "x-goog-api-key": current_key}
  payload = {
      "systemInstruction": {"parts": [{"text": system_prompt}]},
      "contents": [{"parts": parts}],
  }

  last_error = "所有連線嘗試皆失敗"
  for model_name in CANDIDATE_MODELS:
    urls = [
        f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={current_key}",
        f"https://aiplatform.googleapis.com/v1beta1/publishers/google/models/{model_name}:generateContent?key={current_key}",
    ]

    for url in urls:
      try:
        res = session.post(
            url, headers=headers, json=payload, verify=False, timeout=30
        )
        if res.status_code == 200:
          return (
              res.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
          )
        elif res.status_code == 429:
          last_error = "HTTP 429 (達到頻率限制)"
          time.sleep(2)
        else:
          last_error = f"HTTP {res.status_code} - {res.text[:80]}"
      except Exception as e:
        last_error = f"連線異常: {e}"

  return f"⚠️ [{char_name} 呼叫失敗] 原因：{last_error}"


# ================= 發送 TG 訊息 =================
def send_tg_message(text, target_chat_id=MY_CHAT_ID, sender="ache"):
  token = (
      os.environ.get("TG_BOT_TOKEN_ACHE", "").strip()
      if sender == "ache"
      else os.environ.get("TG_BOT_TOKEN_AHENG", "").strip()
  )
  send_url = f"https://api.telegram.org/bot{token}/sendMessage"
  sender_name = "阿澈" if sender == "ache" else "阿珩"
  payload = {"chat_id": target_chat_id, "text": text, "parse_mode": "HTML"}
  try:
    r = session.post(send_url, json=payload, verify=False, timeout=5)
    if r.status_code != 200:
      payload.pop("parse_mode", None)
      session.post(send_url, json=payload, verify=False, timeout=5)
  except Exception as e:
    print(f"  --> [{sender_name}] Telegram 連線超時: {e}")


# ================= 背景隨機主動發話任務 =================
def random_tick_loop():
  while True:
    wait_seconds = random.randint(3600, 7200)
    time.sleep(wait_seconds)
    now_hour = datetime.datetime.now()
    if 6 <= now_hour.hour < 22:
      msg = call_ai_brain("ache", is_auto=True, is_group=False)
      if "[SILENCE]" in msg or not msg.strip() or "⚠️" in msg:
        continue

      update_memory(msg)
      formatted_msg = format_private_msg(msg, char_name="阿澈")
      pure_text = msg.split("💭")[0].replace("💬", "").strip()
      save_chat_history("阿澈", pure_text, file_type="ache_private")
      send_tg_message(formatted_msg, target_chat_id=MY_CHAT_ID, sender="ache")


# ================= 專屬監聽：阿珩私聊視窗 =================
def aheng_listener_loop():
  last_update_id = 0
  while True:
    token = os.environ.get("TG_BOT_TOKEN_AHENG", "").strip()
    if not token:
      time.sleep(5)
      continue

    updates_url = f"https://api.telegram.org/bot{token}/getUpdates"
    try:
      params = {"offset": last_update_id + 1, "timeout": 20}
      res = session.get(updates_url, params=params, verify=False, timeout=25)
      if res.status_code == 200:
        data = res.json()
        for update in data.get("result", []):
          last_update_id = update["update_id"]
          msg_obj = update.get("message")
          if not msg_obj or msg_obj.get("from", {}).get("is_bot", False):
            continue

          chat_obj = msg_obj.get("chat", {})
          chat_id = chat_obj.get("id")
          chat_type = chat_obj.get("type", "private")

          raw_text = msg_obj.get("text", "").strip()
          caption_text = msg_obj.get("caption", "").strip()
          photo_list = msg_obj.get("photo")
          image_b64 = None

          if photo_list:
            file_id = photo_list[-1]["file_id"]
            image_b64 = get_tg_file_b64(file_id, token)
            user_text = caption_text or "請看這張圖片並回覆我"
            history_text = (
                f"[傳送圖片] {caption_text}"
                if caption_text
                else "[傳送了一張圖片]"
            )
          else:
            user_text = raw_text
            history_text = raw_text

          if not user_text or user_text == "/start":
            continue

          if chat_type == "private" and chat_id == MY_CHAT_ID:
            save_chat_history("阿渺", history_text, file_type="aheng_private")
            reply = call_ai_brain(
                "aheng",
                user_input=user_text,
                is_auto=False,
                is_group=False,
                image_b64=image_b64,
            )
            formatted_reply = format_private_msg(reply, char_name="阿珩")
            pure_text = (
                reply.split("💭")[0]
                .replace("💬", "")
                .replace("[阿珩]", "")
                .strip()
            )
            save_chat_history("阿珩", pure_text, file_type="aheng_private")
            send_tg_message(
                formatted_reply, target_chat_id=chat_id, sender="aheng"
            )
    except Exception:
      time.sleep(2)


# ================= 主迴圈：阿澈私聊 & 群組相聲邏輯 =================
def main():
  threading.Thread(target=run_dummy_server, daemon=True).start()
  print("\n雙人模式已啟動（阿澈 + 阿珩待命中）...")

  threading.Thread(target=random_tick_loop, daemon=True).start()
  threading.Thread(target=aheng_listener_loop, daemon=True).start()

  last_update_id = 0
  while True:
    token = os.environ.get("TG_BOT_TOKEN_ACHE", "").strip()
    if not token:
      time.sleep(5)
      continue

    updates_url = f"https://api.telegram.org/bot{token}/getUpdates"
    try:
      params = {"offset": last_update_id + 1, "timeout": 20}
      res = session.get(updates_url, params=params, verify=False, timeout=25)
      if res.status_code == 200:
        data = res.json()
        for update in data.get("result", []):
          up_id = update["update_id"]
          last_update_id = update["update_id"]
          msg_obj = update.get("message")
          if not msg_obj or msg_obj.get("from", {}).get("is_bot", False):
            continue

          chat_obj = msg_obj.get("chat", {})
          chat_id = chat_obj.get("id")
          chat_type = chat_obj.get("type", "private")
          is_group = chat_type in ["group", "supergroup"]

          raw_text = msg_obj.get("text", "").strip()
          caption_text = msg_obj.get("caption", "").strip()
          photo_list = msg_obj.get("photo")
          image_b64 = None

          if photo_list:
            file_id = photo_list[-1]["file_id"]
            image_b64 = get_tg_file_b64(file_id, token)
            user_text = caption_text or "請看這張圖片並回覆我"
            history_text = (
                f"[傳送圖片] {caption_text}"
                if caption_text
                else "[傳送了一張圖片]"
            )
          else:
            user_text = raw_text
            history_text = raw_text

          if not user_text or user_text == "/start":
            continue

          # 私聊阿澈
          if not is_group and chat_id == MY_CHAT_ID:
            save_chat_history("阿渺", history_text, file_type="ache_private")
            reply = call_ai_brain(
                "ache",
                user_input=user_text,
                is_auto=False,
                is_group=False,
                image_b64=image_b64,
            )
            update_memory(reply)
            formatted_reply = format_private_msg(reply, char_name="阿澈")
            pure_text = (
                reply.split("💭")[0]
                .replace("💬", "")
                .replace("[阿澈]", "")
                .strip()
            )
            save_chat_history("阿澈", pure_text, file_type="ache_private")
            send_tg_message(
                formatted_reply, target_chat_id=chat_id, sender="ache"
            )
            continue

          # 群組聊天
          if is_group:
            save_chat_history("阿渺", history_text, file_type="group")
            is_tag_aheng = (
                "@aheng" in user_text.lower()
                or user_text.startswith("阿珩")
                or user_text.startswith("珩")
            )
            is_tag_ache = (
                "@ache" in user_text.lower()
                or user_text.startswith("阿澈")
                or user_text.startswith("澈")
            )

            if is_tag_aheng and not is_tag_ache:
              first_speaker = "aheng"
            elif is_tag_ache and not is_tag_aheng:
              first_speaker = "ache"
            else:
              first_speaker = random.choice(["ache", "aheng"])

            first_name = "阿珩" if first_speaker == "aheng" else "阿澈"
            reply_first = call_ai_brain(
                first_speaker,
                user_input=user_text,
                is_group=True,
                image_b64=image_b64,
            )
            clean_first = format_group_msg(reply_first)

            save_chat_history(first_name, clean_first, file_type="group")
            send_tg_message(
                clean_first, target_chat_id=chat_id, sender=first_speaker
            )

            last_speaker = first_speaker
            last_reply = clean_first

            for round_idx in range(1):
              time.sleep(random.randint(3, 5))
              if last_speaker == "ache":
                next_speaker = "aheng"
                prompt_msg = (
                    f"【現場情況】阿渺剛才說：『{user_text}』\n"
                    f"阿澈剛才說：『{last_reply}』\n"
                    "請以阿珩身分直接回答，嚴禁輸出內心話。[END]"
                )
              else:
                next_speaker = "ache"
                prompt_msg = (
                    f"【現場情況】阿渺剛才說：『{user_text}』\n"
                    f"阿珩剛才說：『{last_reply}』\n"
                    "請以阿澈身分直接回答，嚴禁輸出內心話。[END]"
                )

              reply_text = call_ai_brain(
                  next_speaker, user_input=prompt_msg, is_group=True
              )
              clean_text = format_group_msg(reply_text)
              speaker_name = "阿珩" if next_speaker == "aheng" else "阿澈"
              save_chat_history(speaker_name, clean_text, file_type="group")
              send_tg_message(
                  clean_text, target_chat_id=chat_id, sender=next_speaker
              )
              break

    except Exception as e:
      time.sleep(2)


if __name__ == "__main__":
  main()
