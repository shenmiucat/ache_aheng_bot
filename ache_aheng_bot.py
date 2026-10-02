import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer


# 專門給 Render 通訊埠檢查用的虛擬網頁服務
class DummyHandler(BaseHTTPRequestHandler):

  def do_GET(self):
    self.send_response(200)
    self.end_headers()
    self.wfile.write(b"Bot is running!")


def run_dummy_server():
  port = int(os.environ.get("PORT", 10000))
  server = HTTPServer(("0.0.0.0", port), DummyHandler)
  server.serve_forever()


# 在背景啟動虛擬網頁，讓 Render 能抓到 Port
threading.Thread(target=run_dummy_server, daemon=True).start()

import datetime
import json
import os
import random
import threading
import time
import requests
import urllib3

# 強制設定整個環境時區為台北時間 (UTC+8)
os.environ["TZ"] = "Asia/Taipei"
if hasattr(time, "tzset"):
  time.tzset()

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ================= 基礎設定 =================
API_KEY = "AQ.Ab8RN6IcLO6vD0Jx-0oq77ivtI6vkOOuXhO3lTcVkG3js2WfXA"
TG_BOT_TOKEN_ACHE = "8832956921:AAElTmndvj0Alcl0usafLI2qNwTFNmcmlgA"
TG_BOT_TOKEN_AHENG = "8856056737:AAG5QndXLI0J4ilsYPVjZUB6ysJAn49_30Y"
MY_CHAT_ID = 8773051890  # 預設私聊綁定對象

# Telegram 網址
tg_base_ache = "https://api.telegram.org/bot" + TG_BOT_TOKEN_ACHE
TG_SEND_URL_ACHE = tg_base_ache + "/sendMessage"
TG_UPDATES_URL_ACHE = tg_base_ache + "/getUpdates"

tg_base_aheng = "https://api.telegram.org/bot" + TG_BOT_TOKEN_AHENG
TG_SEND_URL_AHENG = tg_base_aheng + "/sendMessage"
TG_UPDATES_URL_AHENG = tg_base_aheng + "/getUpdates"

# 候選模型清單（按優先順序嘗試，加入不同配額池的 Lite）
CANDIDATE_MODELS = [
    "gemini-3.5-flash-lite",
]

session = requests.Session()


# ================= 輔助文字清理與格式化 =================
def format_private_msg(raw_text, char_name="阿澈"):
  """私聊專用：將 💭 內心話轉為黑條遮罩防雷，清理無關標籤"""
  clean = "\n".join([
      line
      for line in raw_text.split("\n")
      if not line.strip().startswith("📝 記住：")
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
  """群聊專用：徹底截斷 💭 內心話，不向公開場合暴露"""
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


# ================= 記憶檔案管理 =================
def load_memory():
  base_dir = os.path.dirname(os.path.abspath(__file__))
  mem_path = os.path.join(base_dir, "memory.json")
  if not os.path.exists(mem_path):
    return []
  try:
    with open(mem_path, "r", encoding="utf-8") as f:
      data = json.load(f)
      return data.get("notes", [])
  except Exception:
    return []


def update_memory(raw_text):
  base_dir = os.path.dirname(os.path.abspath(__file__))
  mem_path = os.path.join(base_dir, "memory.json")
  notes = load_memory()
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
    elif line_str.startswith("🗑️ 忘記："):
      item = line_str.replace("🗑️ 忘記：", "").strip()
      before_len = len(notes)
      notes = [n for n in notes if item not in n]
      if len(notes) != before_len:
        changed = True
        print(f"🧹 [記憶清除] {item}")

  if changed:
    try:
      with open(mem_path, "w", encoding="utf-8") as f:
        json.dump({"notes": notes}, f, ensure_ascii=False, indent=2)
    except Exception as e:
      print(f"❌ 記憶存檔失敗: {e}")


# ================= 對話歷史持久化 (三本帳分流) =================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
HISTORY_FILE_ACHE = os.path.join(BASE_DIR, "chat_history.json")
HISTORY_FILE_AHENG = os.path.join(BASE_DIR, "aheng_history.json")
GROUP_HISTORY_FILE = os.path.join(BASE_DIR, "group_history.json")


def load_chat_history(file_type="ache_private"):
  if file_type == "group":
    target_file = GROUP_HISTORY_FILE
  elif file_type == "aheng_private":
    target_file = HISTORY_FILE_AHENG
  else:
    target_file = HISTORY_FILE_ACHE

  if not os.path.exists(target_file):
    return []
  try:
    with open(target_file, "r", encoding="utf-8") as f:
      return json.load(f)
  except Exception:
    return []


chat_history = load_chat_history("ache_private")
aheng_history = load_chat_history("aheng_private")
group_history = load_chat_history("group")


def save_chat_history(role, text, file_type="ache_private"):
  global chat_history, aheng_history, group_history
  weekdays = ["週一", "週二", "週三", "週四", "週五", "週六", "週日"]
  now = datetime.datetime.now()
  time_tag = f"[{now.strftime('%Y-%m-%d')} {weekdays[now.weekday()]} {now.strftime('%H:%M')}]"

  if file_type == "group":
    target_list = group_history
    target_file = GROUP_HISTORY_FILE
  elif file_type == "aheng_private":
    target_list = aheng_history
    target_file = HISTORY_FILE_AHENG
  else:
    target_list = chat_history
    target_file = HISTORY_FILE_ACHE

  target_list.append((role, f"{time_tag} {text}"))

  try:
    with open(target_file, "w", encoding="utf-8") as f:
      json.dump(target_list, f, ensure_ascii=False, indent=2)
  except Exception as e:
    print(f"❌ 對話歷史存檔失敗: {e}")


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


# ================= 讀取 TickTick 待辦 (限阿澈) =================
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


# ================= 雙大腦呼叫 (阿澈 & 阿珩) =================
def call_ai_brain(
    character="ache",
    user_input=None,
    is_auto=False,
    is_group=False,
    extra_context="",
):
  global chat_history, aheng_history, group_history
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

  # 讀取記憶與待辦（專屬於阿澈）
  notes = load_memory()
  mem_text = "\n".join([f"- {n}" for n in notes]) if notes else "（暫無特殊備忘）"
  memory_str = (
      f"\n【你目前腦子裡記住的事】:\n{mem_text}\n" if character == "ache" else ""
  )
  tasks_text = f"{get_ticktick_summary()}\n" if character == "ache" else ""
  # 每次呼叫大腦時，在終端機印出角色當前看到的待辦
  print(f"\n📋 [{char_name} 讀取到的待辦清單]:\n{tasks_text.strip()}")

  # 歷史紀錄組裝：不管是群聊還是私聊，都把「該角色的私聊」與「公共群聊」合併並按時間排序
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

  headers = {"Content-Type": "application/json", "x-goog-api-key": API_KEY}
  payload = {
      "systemInstruction": {"parts": [{"text": system_prompt}]},
      "contents": [{"parts": [{"text": prompt}]}],
  }

  for model_name in CANDIDATE_MODELS:
    # 把 key= 直接帶在網址後面
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={API_KEY}"
    try:
      res = session.post(
          url, headers=headers, json=payload, verify=False, timeout=12
      )
      if res.status_code == 200:
        return (
            res.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
        )
      elif res.status_code == 429:
        print(
            f"⚠️ [{char_name}] 模型 {model_name} 達到頻率限制 (429)，冷卻 2 秒並切換備援..."
        )
        time.sleep(2)
      else:
        print(
            f"⚠️ [{char_name}] 模型 {model_name} 呼叫失敗 ({res.status_code})，切換備援..."
        )
    except Exception as e:
      print(f"⚠️ [{char_name}] 模型 {model_name} 連線異常: {e}，切換備援...")

  return (
      f"（{char_name}在旁邊喝茶發呆，沒聽清）"
      if is_group
      else f"（{char_name}若有所思地看著茶杯，沒聽清妳說什麼）"
  )


# ================= 發送 TG 訊息 =================
def send_tg_message(text, target_chat_id=MY_CHAT_ID, sender="ache"):
  send_url = TG_SEND_URL_ACHE if sender == "ache" else TG_SEND_URL_AHENG
  sender_name = "阿澈" if sender == "ache" else "阿珩"
  payload = {"chat_id": target_chat_id, "text": text, "parse_mode": "HTML"}
  try:
    r = session.post(send_url, json=payload, verify=False, timeout=5)
    if r.status_code != 200:
      payload.pop("parse_mode", None)
      session.post(send_url, json=payload, verify=False, timeout=5)
  except Exception as e:
    print(f"  --> [{sender_name}] Telegram 連線超時: {e}")


# ================= 背景隨機主動發話任務 (阿澈專屬) =================
def random_tick_loop():
  while True:
    wait_seconds = random.randint(3600, 7200)
    print(
        f"⏳ [計時開始] 阿澈預計等待 {wait_seconds} 秒 ({wait_seconds//60}"
        " 分鐘) 後主動發話..."
    )
    time.sleep(wait_seconds)
    now_hour = datetime.datetime.now().hour
    if 6 <= now_hour < 22:
      msg = call_ai_brain("ache", is_auto=True, is_group=False)
      # 只要阿澈回覆 [SILENCE] 或空白，就默默略過不發訊息
      if "[SILENCE]" in msg or not msg.strip():
        continue

      update_memory(msg)
      formatted_msg = format_private_msg(msg, char_name="阿澈")

      # 歷史紀錄保留乾淨純文本（不帶 HTML 標籤）
      pure_text = msg.split("💭")[0].replace("💬", "").strip()
      save_chat_history("阿澈", pure_text, file_type="ache_private")

      send_tg_message(formatted_msg, target_chat_id=MY_CHAT_ID, sender="ache")
      print(f"\n[主動觸發] 阿澈私聊發送：\n{formatted_msg}\n")


# ================= 專屬監聽：阿珩私聊視窗 =================
def aheng_listener_loop():
  last_update_id = 0
  while True:
    try:
      params = {"offset": last_update_id + 1, "timeout": 20}
      res = session.get(
          TG_UPDATES_URL_AHENG, params=params, verify=False, timeout=25
      )
      if res.status_code == 200:
        data = res.json()
        for update in data.get("result", []):
          last_update_id = update["update_id"]
          msg_obj = update.get("message")
          if not msg_obj:
            continue
          if msg_obj.get("from", {}).get("is_bot", False):
            continue

          chat_obj = msg_obj.get("chat", {})
          chat_id = chat_obj.get("id")
          chat_type = chat_obj.get("type", "private")
          user_text = msg_obj.get("text", "").strip()

          if not user_text or user_text == "/start":
            continue

          if chat_type == "private" and chat_id == MY_CHAT_ID:
            print(f"\n[收到阿渺私聊阿珩] {user_text}")
            save_chat_history("阿渺", user_text, file_type="aheng_private")

            reply = call_ai_brain(
                "aheng", user_input=user_text, is_auto=False, is_group=False
            )
            # 阿珩私聊加上黑條防雷遮罩！
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
            print(f"[阿珩私聊回覆]\n{formatted_reply}\n")

    except Exception as e:
      time.sleep(2)


# ================= 主迴圈：阿澈私聊 & 群組相聲邏輯 =================
def main():
  print("\n雙人模式已啟動（阿澈 + 阿珩待命中，皆支援私聊與群聊）...")
  # 後台顯示待辦
  print(f"{get_ticktick_summary()}\n")
  print("現在可以打開手機 Telegram 互動了！(按 Ctrl+C 結束)\n")

  threading.Thread(target=random_tick_loop, daemon=True).start()
  threading.Thread(target=aheng_listener_loop, daemon=True).start()

  last_update_id = 0
  while True:
    try:
      params = {"offset": last_update_id + 1, "timeout": 20}
      res = session.get(
          TG_UPDATES_URL_ACHE, params=params, verify=False, timeout=25
      )
      if res.status_code == 200:
        data = res.json()
        for update in data.get("result", []):
          up_id = update["update_id"]  # 抓出這次的 update_id
          last_update_id = update["update_id"]
          msg_obj = update.get("message")
          if not msg_obj:
            continue

          if msg_obj.get("from", {}).get("is_bot", False):
            continue

          chat_obj = msg_obj.get("chat", {})
          chat_id = chat_obj.get("id")
          chat_type = chat_obj.get("type", "private")
          is_group = chat_type in ["group", "supergroup"]
          user_text = msg_obj.get("text", "").strip()

          if not user_text or user_text == "/start":
            continue

          # ---------------- 模式 A：私聊阿澈 ----------------
          if not is_group and chat_id == MY_CHAT_ID:
            save_chat_history("阿渺", user_text, file_type="ache_private")
            print(f"\n[收到阿渺私聊阿澈 | UpdateID: {up_id}] {user_text}")

            reply = call_ai_brain(
                "ache", user_input=user_text, is_auto=False, is_group=False
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
            print(f"[阿澈回覆]\n{formatted_reply}\n")
            continue

          # ---------------- 模式 B：群組聊天 ----------------
          if is_group:
            save_chat_history("阿渺", user_text, file_type="group")
            print(f"\n[收到群組訊息] {user_text}")

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

            # 隨機或指定第一棒
            if is_tag_aheng and not is_tag_ache:
              first_speaker = "aheng"
            elif is_tag_ache and not is_tag_aheng:
              first_speaker = "ache"
            else:
              first_speaker = random.choice(["ache", "aheng"])

            first_name = "阿珩" if first_speaker == "aheng" else "阿澈"

            reply_first = call_ai_brain(
                first_speaker, user_input=user_text, is_group=True
            )
            # 群聊徹底切除內心話，只發明面言論
            clean_first = format_group_msg(reply_first)

            save_chat_history(first_name, clean_first, file_type="group")
            send_tg_message(
                clean_first, target_chat_id=chat_id, sender=first_speaker
            )
            print(f"[{first_name}首發回覆]\n{clean_first}\n")

            last_speaker = first_speaker
            last_reply = clean_first

            # 接話吐槽迴圈（改為 1 輪，節約配額避免連環爆發）
            for round_idx in range(1):
              delay = random.randint(3, 5)
              time.sleep(delay)

              if last_speaker == "ache":
                next_speaker = "aheng"
                prompt_msg = (
                    f"【現場情況】阿渺剛才說：『{user_text}』\n"
                    f"阿澈剛才說：『{last_reply}』\n"
                    "請以阿珩身分直接回答，嚴禁輸出內心話。"
                    "若覺得對話可以告一段落，請在句尾加上 [END]。"
                )
              else:
                next_speaker = "ache"
                prompt_msg = (
                    f"【現場情況】阿渺剛才說：『{user_text}』\n"
                    f"阿珩剛才說：『{last_reply}』\n"
                    "請以阿澈身分直接回答，嚴禁輸出內心話。"
                    "若不想再搭理阿珩或對話結束，請在句尾加上 [END]。"
                )

              reply_text = call_ai_brain(
                  next_speaker, user_input=prompt_msg, is_group=True
              )
              is_end = "[END]" in reply_text
              clean_text = format_group_msg(reply_text)

              speaker_name = "阿珩" if next_speaker == "aheng" else "阿澈"
              save_chat_history(speaker_name, clean_text, file_type="group")
              send_tg_message(
                  clean_text, target_chat_id=chat_id, sender=next_speaker
              )
              print(f"[{speaker_name}接話回覆]\n{clean_text}\n")

              if is_end:
                break

    except Exception as e:
      print(f"⚠️ 主迴圈異常: {e}")
      time.sleep(2)


if __name__ == "__main__":
  main()
