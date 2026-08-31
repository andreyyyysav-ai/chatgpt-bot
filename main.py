import asyncio
import aiohttp
import json

# === ТВОЙ КЛЮЧ ===
GROQ_API_KEY = "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"

# === ВСЕ ВОЗМОЖНЫЕ МОДЕЛИ ДЛЯ ТЕСТА ===
MODELS_TO_TEST = [
    # Llama 4 (новые)
    "llama-4-maverick-17b-128e-instruct",
    "meta-llama/llama-4-maverick-17b-128e-instruct",
    "groq/meta-llama/llama-4-maverick-17b-128e-instruct",
    "llama-4-scout-17b-16e-instruct",
    "meta-llama/llama-4-scout-17b-16e-instruct",
    "groq/meta-llama/llama-4-scout-17b-16e-instruct",
    
    # Llama 3 (старые)
    "llama3-70b-8192",
    "llama3-8b-8192",
    "llama-3.1-70b-versatile",
    "llama-3.1-8b-instant",
    "llama-3.3-70b-versatile",
    "llama-3.2-11b-vision-preview",
    
    # Qwen
    "qwen/qwen3-32b",
    "qwen-32b",
    "groq/qwen/qwen3-32b",
    
    # GPT OSS
    "gpt-oss-20b",
    "gpt-oss-120b",
    "groq/gpt-oss-20b",
    "groq/gpt-oss-120b",
    
    # Другие
    "mixtral-8x7b-32768",
    "gemma2-9b-it",
    "deepseek-r1-distill-llama-70b",
]

async def test_model(model_name: str):
    """Тестирует одну модель"""
    url = "https://api.groq.com/openai/v1/chat/completions"
    payload = {
        "model": model_name,
        "messages": [{"role": "user", "content": "Привет! Ответь: ОК"}],
        "max_tokens": 5
    }
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=payload, timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return f"✅ {model_name}"
                else:
                    return f"❌ {model_name} -> {resp.status}"
    except asyncio.TimeoutError:
        return f"⏱️ {model_name} -> таймаут"
    except Exception as e:
        return f"⚠️ {model_name} -> {str(e)[:30]}"

async def get_official_models():
    """Получает список официальных моделей от Groq"""
    url = "https://api.groq.com/openai/v1/models"
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}"}
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    models = [m["id"] for m in data.get("data", []) if "embed" not in m["id"]]
                    return models
                else:
                    return [f"Ошибка: {resp.status}"]
    except Exception as e:
        return [f"Ошибка: {e}"]

async def main():
    print("=" * 60)
    print("🧪 ТЕСТИРОВАНИЕ МОДЕЛЕЙ GROQ")
    print("=" * 60)
    
    # Тестируем все модели
    results = []
    for model in MODELS_TO_TEST:
        result = await test_model(model)
        print(result)
        results.append(result)
        await asyncio.sleep(0.3)  # небольшая задержка
    
    print("\n" + "=" * 60)
    print("📋 ОФИЦИАЛЬНЫЕ МОДЕЛИ ОТ GROQ:")
    print("=" * 60)
    
    official = await get_official_models()
    for model in official:
        print(f"  - {model}")
    
    print("\n" + "=" * 60)
    print("✅ РАБОТАЮЩИЕ МОДЕЛИ:")
    print("=" * 60)
    working = [r for r in results if r.startswith("✅")]
    for w in working:
        print(w)
    
    print("\n" + "=" * 60)
    print("❌ НЕРАБОТАЮЩИЕ МОДЕЛИ:")
    print("=" * 60)
    failed = [r for r in results if not r.startswith("✅")]
    for f in failed:
        print(f)

if __name__ == "__main__":
    asyncio.run(main())
