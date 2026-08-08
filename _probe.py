"""Probe: fetch raw HTML via requests; check for form markup."""
import requests
import re

URL = "https://v.wjx.cn/vm/PpzftGB.aspx"
headers = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
}
r = requests.get(URL, headers=headers, timeout=30)
t = r.text
print("status", r.status_code, "len", len(t))
print("has divQuestion:", 'id="divQuestion"' in t)
print("has ui-field-contain:", "ui-field-contain" in t)
print("topic count:", len(re.findall(r"topic=['" + '"' + r"]\d+", t)))
m = re.search(r"activityId\s*=\s*DecodeId\((\d+)\)", t)
print("activityId raw:", m.group(1) if m else None)
i = t.find("ui-field-contain")
print("first field slice:")
print(t[i - 80:i + 420] if i > 0 else "NOT FOUND")