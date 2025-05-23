"""
Utility functions for LLM API calls (OpenAI GPT and Perplexity)
"""
import os
import requests
import openai
from tenacity import retry, stop_after_attempt, wait_fixed, retry_if_exception_type

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
PERPLEXITY_API_KEY = os.environ.get("PERPLEXITY_API_KEY", "")

openai.api_key = OPENAI_API_KEY

@retry(
    stop=stop_after_attempt(2),
    wait=wait_fixed(2),
    retry=retry_if_exception_type(openai.OpenAIError),
    reraise=True,
)
def gpt_chat(messages, model="gpt-4o", temperature=0.7, max_tokens=1024):
    resp = openai.chat.completions.create(
        model=model,
        temperature=temperature,
        messages=messages,
        max_tokens=max_tokens,
    )
    return resp.choices[0].message.content

def query_perplexity(prompt, timeout=40):
    hdr = {
        "Authorization": f"Bearer {PERPLEXITY_API_KEY}",
        "Content-Type": "application/json",
    }
    resp = requests.post(
        "https://api.perplexity.ai/chat/completions",
        headers=hdr,
        json={
            "model": "sonar",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.3,
        },
        timeout=timeout,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]
