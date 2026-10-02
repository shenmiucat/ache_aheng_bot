import os
import requests

API_KEY = "AQ.Ab8RN6IcLO6vD0Jx-0oq77ivtI6vkOOuXhO3lTcVkG3js2WfXA"
url = f"https://generativelanguage.googleapis.com/v1beta/models?key={API_KEY}"
res = requests.get(url)

if res.status_code == 200:
  models = res.json().get("models", [])
  valid_models = [
      m["name"].replace("models/", "")
      for m in models
      if "generateContent" in m.get("supportedGenerationMethods", [])
  ]
  print("目前真正可用的模型：")
  for m in valid_models:
    print(f"- {m}")
else:
  print(f"查詢失敗: {res.status_code}, {res.text}")
