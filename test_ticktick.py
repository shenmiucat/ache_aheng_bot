import http.server
import json
import socketserver
import urllib.parse
import requests

CLIENT_ID = "d6X1J7hZtu0WzBh8Rx"
CLIENT_SECRET = "PBDidmn65X3gaI5Z9EHI4bG1x7MM7O1n"
REDIRECT_URI = "http://localhost:8080/callback"
PORT = 8080

auth_code = None


class CallbackHandler(http.server.SimpleHTTPRequestHandler):

  def do_GET(self):
    global auth_code
    parsed = urllib.parse.urlparse(self.path)
    if parsed.path == "/callback":
      query = urllib.parse.parse_qs(parsed.query)
      if "code" in query:
        auth_code = query["code"][0]
        self.send_response(200)
        self.send_header("Content-type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(
            "<h1>授權成功！請回到終端機查看。</h1>".encode("utf-8")
        )
      else:
        self.send_response(400)
        self.end_headers()
    else:
      self.send_response(404)
      self.end_headers()


# 正確的 TickTick OAuth 授權網址（注意是 ticktick.com，不是 api.ticktick.com）
auth_url = (
    f"https://ticktick.com/oauth/authorize?"
    f"client_id={CLIENT_ID}&"
    f"redirect_uri={urllib.parse.quote(REDIRECT_URI)}&"
    f"response_type=code&"
    f"scope=tasks:read"
)

# 把這段換掉原本的 print 網址
with open("login_url.txt", "w", encoding="utf-8") as f:
  f.write(auth_url)

print("=" * 60)
print("網址已經幫妳存到同資料夾的 login_url.txt 了！")
print("直接打開那個文字檔，複製裡面的網址去 Chrome 貼上就行。")
print("=" * 60)

# 本地接聽 Callback
with socketserver.TCPServer(("", PORT), CallbackHandler) as httpd:
  print("正在等待授權回傳 (Callback)...")
  while not auth_code:
    httpd.handle_request()

print(f"\n[成功取得授權碼 Code]: {auth_code}")

# 用 Code 換取 Access Token
token_url = "https://ticktick.com/oauth/token"
token_data = {
    "client_id": CLIENT_ID,
    "client_secret": CLIENT_SECRET,
    "code": auth_code,
    "grant_type": "authorization_code",
    "scope": "tasks:read",
    "redirect_uri": REDIRECT_URI,
}

res = requests.post(token_url, data=token_data)
if res.status_code != 200:
  print("取得 Token 失敗：", res.text)
  exit()

token_info = res.json()
access_token = token_info.get("access_token")
print(f"[成功取得 Access Token]: {access_token[:10]}******")

with open("ticktick_token.json", "w") as f:
  json.dump(token_info, f)

# 抓取待辦清單
headers = {"Authorization": f"Bearer {access_token}"}
print("\n正在向 TickTick 抓取清單...")

project_res = requests.get(
    "https://api.ticktick.com/open/v1/project", headers=headers
)
if project_res.status_code == 200:
  projects = project_res.json()
  print(f"找到 {len(projects)} 個清單！")
  for proj in projects:
    proj_id = proj["id"]
    proj_name = proj["name"]
    data_res = requests.get(
        f"https://api.ticktick.com/open/v1/project/{proj_id}/data",
        headers=headers,
    )
    if data_res.status_code == 200:
      tasks = data_res.json().get("tasks", [])
      if tasks:
        print(f"\n📁 【{proj_name}】")
        for t in tasks:
          due = t.get("dueDate", "未設定時間")
          print(f"  - 任務: {t.get('title')} | 截止/時間: {due}")
else:
  print("讀取清單失敗：", project_res.text)
