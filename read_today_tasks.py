import datetime
import json
import requests

TOKEN_FILE = "ticktick_token.json"

# 1. 讀取先前存好的 Token
try:
  with open(TOKEN_FILE, "r", encoding="utf-8") as f:
    token_data = json.load(f)
    access_token = token_data.get("access_token")
except FileNotFoundError:
  print("找不到 token 檔案，請確認 ticktick_token.json 是否在同個資料夾！")
  input("\n按 Enter 結束...")
  exit()

headers = {"Authorization": f"Bearer {access_token}"}

print("=" * 50)
print("正在從 TickTick 抓取妳的清單與待辦事項...")
print("=" * 50)

# 2. 抓取所有清單
res = requests.get("https://api.ticktick.com/open/v1/project", headers=headers)
if res.status_code != 200:
  print(f"抓取失敗 (HTTP {res.status_code})：{res.text}")
  input("\n按 Enter 結束...")
  exit()

projects = res.json()
total_tasks = 0

for proj in projects:
  proj_id = proj["id"]
  proj_name = proj["name"]

  # 抓取該清單內的詳細資料
  data_res = requests.get(
      f"https://api.ticktick.com/open/v1/project/{proj_id}/data",
      headers=headers,
  )
  if data_res.status_code == 200:
    tasks = data_res.json().get("tasks", [])
    if tasks:
      print(f"\n📂 【{proj_name}】")
      for t in tasks:
        title = t.get("title")
        due = t.get("dueDate")
        # 簡單整理時間格式
        due_str = due if due else "無設定時間"
        print(f"  • {title}  (時間: {due_str})")
        total_tasks += 1

print("\n" + "=" * 50)
print(f"讀取完成！共抓到 {total_tasks} 個未完成事項。")
print("=" * 50)

input("\n按 Enter 鍵關閉視窗...")