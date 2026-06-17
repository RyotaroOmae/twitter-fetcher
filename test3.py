import os, requests
token=os.environ["X_BEARER_TOKEN"]
r=requests.get(
  "https://api.x.com/2/users/by/username/X",
  headers={"Authorization": f"Bearer {token}"}
)
print("status:", r.status_code)
print(r.text[:300])
