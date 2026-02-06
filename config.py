import os
from dotenv import load_dotenv
from typing import List

load_dotenv()

TOKEN = os.getenv("TOKEN")

# Парсим ADMIN_IDS из строки в список int
ADMIN_IDS_STR = os.getenv("ADMIN_IDS", "")
ADMIN_IDS: List[int] = [int(x.strip()) for x in ADMIN_IDS_STR.split(",") if x.strip()]
print(ADMIN_IDS)

WELCOME_PHOTO_PATH = "png/logo.jpg"  
    