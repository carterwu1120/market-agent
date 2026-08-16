"""One-time Gmail OAuth2 authorization flow.

Run once to generate gmail_token.json:
    python scripts/gmail_auth.py

Prerequisites:
  1. Google Cloud Console → APIs & Services → Credentials
  2. 建立 OAuth 2.0 用戶端 ID（桌面應用程式類型）
  3. 下載 JSON → 存為專案根目錄的 gmail_credentials.json
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.config import settings

_SCOPES = [
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.compose",
]


def main():
    from google_auth_oauthlib.flow import InstalledAppFlow

    creds_path = Path(settings.gmail_credentials_file)
    token_path = Path(settings.gmail_token_file)

    if not creds_path.exists():
        print(f"❌ 找不到 {creds_path}")
        print("請至 Google Cloud Console 下載 OAuth 2.0 用戶端憑證 JSON，存為 gmail_credentials.json")
        sys.exit(1)

    flow = InstalledAppFlow.from_client_secrets_file(str(creds_path), _SCOPES)
    creds = flow.run_local_server(port=0)
    token_path.write_text(creds.to_json())
    print(f"✅ 授權完成，token 已存至 {token_path}")


if __name__ == "__main__":
    main()
