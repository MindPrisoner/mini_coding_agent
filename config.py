import os
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("DASHSCOPE_API_KEY") or os.getenv("API_KEY")
BASE_URL = os.getenv("BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
MODEL_NAME = os.getenv("MODEL_NAME", "qwen3.5-flash")

if not API_KEY:
    raise ValueError("缺少 API_KEY，请设置系统环境变量 DASHSCOPE_API_KEY 或在 .env 中配置 API_KEY")
