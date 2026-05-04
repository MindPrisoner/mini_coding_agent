import time
from openai import OpenAI, APITimeoutError

from config import API_KEY, BASE_URL, MODEL_NAME


client = OpenAI(
    api_key=API_KEY,
    base_url=BASE_URL,
)


def call_llm(
    system_prompt: str,
    user_prompt: str,
    temperature: float = 0.1,
    timeout: int = 60,
    max_retries: int = 3,
) -> str:
    last_error = None

    for attempt in range(1, max_retries + 1):
        try:
            response = client.chat.completions.create(
                model=MODEL_NAME,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=temperature,
                timeout=timeout,
            )
            content = response.choices[0].message.content
            if not content or not content.strip():
                raise ValueError("模型返回空内容")
            return content.strip()

        except APITimeoutError as e:
            last_error = e
            if attempt < max_retries:
                sleep_seconds = attempt * 2
                print(f"[WARN] 模型调用超时，第 {attempt} 次失败，{sleep_seconds}s 后重试...")
                time.sleep(sleep_seconds)

        except Exception as e:
            last_error = e
            break

    raise RuntimeError(f"模型调用失败: {type(last_error).__name__}: {last_error}") from last_error
