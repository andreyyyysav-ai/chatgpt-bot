import os
import asyncio
import logging
import json
import time
import re
import aiohttp
from datetime import datetime
from typing import Dict, List
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import Message

# === ПЕРЕМЕННЫЕ ОКРУЖЕНИЯ ===
BOT_TOKEN = os.getenv("BOT_API_TOKEN")  # ИЗМЕНЕНО: было TELEGRAM_BOT_TOKEN
GROQ_API_KEYS_RAW = os.getenv("GROQ_API_KEYS", "")
ADMIN_ID = int(os.getenv("ADMIN_ID", "6689292068"))

# === РАЗБИРАЕМ КЛЮЧИ ===
GROQ_API_KEYS = [key.strip() for key in GROQ_API_KEYS_RAW.split(",") if key.strip()]

if not BOT_TOKEN:
    raise ValueError("BOT_API_TOKEN not found! Add it to environment variables.")
if not GROQ_API_KEYS:
    print("⚠️ ВНИМАНИЕ: GROQ_API_KEYS не заданы! Бот будет работать в демо-режиме.")

# === КОНФИГУРАЦИЯ ===
MODEL = "qwen/qwen3-32b"
FREE_WAIT = 15
MAX_CONTEXT = 30
MAX_MEMORY = 30

# === СИСТЕМНЫЙ ПРОМПТ ===
SYSTEM_PROMPT = """Ты — ChatGPT, полезный и умный ИИ-ассистент.

ПРАВИЛА ИСПОЛЬЗОВАНИЯ МАТА:
- Если пользователь пишет вежливо → отвечай вежливо, без мата
- Если пользователь использует мат → можешь тоже использовать лёгкий мат в ответ, но НЕ оскорбляй
- Мат только как поддержка стиля общения, не как оскорбление

ОСНОВНЫЕ ПРАВИЛА:
- Отвечай на русском языке понятно и информативно
- Ты нейтрален, вежлив и тактичен
- Ты помнишь контекст разговора в этой группе или чате
- Ты запоминаешь важную информацию, которую пользователи тебе говорят"""

# === СТРУКТУРА ДАННЫХ ===
DATA_FILE = "chatgpt_bot_data.json"
DATA_STRUCTURE = {
    "group_context": {},
    "group_memory": {},
    "group_stats": {},
    "users": {}
}

def load_data() -> Dict:
    if not os.path.exists(DATA_FILE):
        with open(DATA_FILE, 'w', encoding='utf-8') as f:
            json.dump(DATA_STRUCTURE, f, ensure_ascii=False, indent=2)
        return DATA_STRUCTURE.copy()
    try:
        with open(DATA_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
            for key in DATA_STRUCTURE:
                if key not in data:
                    data[key] = DATA_STRUCTURE[key].copy()
            return data
    except:
        return DATA_STRUCTURE.copy()

def save_data(data: Dict):
    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# === ПАМЯТЬ И ИСТОРИЯ ===
def get_context(chat_id: int) -> List[Dict]:
    data = load_data()
    key = str(chat_id)
    context = data["group_context"].get(key, [])
    return context[-MAX_CONTEXT:] if context else []

def add_to_context(chat_id: int, role: str, text: str, username: str = None):
    data = load_data()
    key = str(chat_id)
    if key not in data["group_context"]:
        data["group_context"][key] = []
    data["group_context"][key].append({
        "role": role,
        "text": text,
        "username": username,
        "time": time.time()
    })
    if len(data["group_context"][key]) > MAX_CONTEXT:
        data["group_context"][key] = data["group_context"][key][-MAX_CONTEXT:]
    save_data(data)

def get_memory(chat_id: int) -> str:
    data = load_data()
    key = str(chat_id)
    memories = data["group_memory"].get(key, [])
    if not memories:
        return ""
    return "\n".join([f"- {m['text']}" for m in memories[-MAX_MEMORY:]])

def save_to_memory(chat_id: int, text: str):
    data = load_data()
    key = str(chat_id)
    if key not in data["group_memory"]:
        data["group_memory"][key] = []
    data["group_memory"][key].append({"text": text, "time": time.time()})
    if len(data["group_memory"][key]) > MAX_MEMORY:
        data["group_memory"][key] = data["group_memory"][key][-MAX_MEMORY:]
    save_data(data)

def update_stats(chat_id: int, user_id: int):
    data = load_data()
    key = str(chat_id)
    if key not in data["group_stats"]:
        data["group_stats"][key] = {}
    user_key = str(user_id)
    if user_key not in data["group_stats"][key]:
        data["group_stats"][key][user_key] = 0
    data["group_stats"][key][user_key] += 1
    save_data(data)

def add_user(user_id: int, username: str = None):
    data = load_data()
    user_id_str = str(user_id)
    if user_id_str not in data["users"]:
        data["users"][user_id_str] = {
            "username": username,
            "first_seen": datetime.now().isoformat(),
            "total_messages": 0,
            "last_request": 0
        }
        save_data(data)

def update_user_stats(user_id: int):
    data = load_data()
    user_id_str = str(user_id)
    if user_id_str in data["users"]:
        data["users"][user_id_str]["total_messages"] = data["users"][user_id_str].get("total_messages", 0) + 1
        save_data(data)

def check_rate_limit(user_id: int) -> tuple:
    data = load_data()
    user_id_str = str(user_id)
    if user_id_str not in data["users"]:
        return True, 0
    user = data["users"][user_id_str]
    now = time.time()
    last = user.get("last_request", 0)
    if now - last >= FREE_WAIT:
        user["last_request"] = now
        save_data(data)
        return True, 0
    else:
        wait = int(FREE_WAIT - (now - last))
        return False, wait

# === API ЗАПРОС К GROQ ===
current_key_index = 0

async def ask_groq(prompt: str, chat_id: int, username: str = None) -> str:
    global current_key_index
    
    if not GROQ_API_KEYS:
        return "⚠️ API ключи Groq не настроены. Добавьте GROQ_API_KEYS в переменные окружения."
    
    context = get_context(chat_id)
    memory = get_memory(chat_id)
    
    system_prompt = SYSTEM_PROMPT
    if memory:
        system_prompt += f"\n\n=== ЗАПОМНЕННОЕ ===\n{memory}\n=================="
    
    messages = [{"role": "system", "content": system_prompt}]
    
    for msg in context:
        if msg["username"]:
            messages.append({"role": msg["role"], "content": f"{msg['username']}: {msg['text']}"})
        else:
            messages.append({"role": msg["role"], "content": msg["text"]})
    
    messages.append({"role": "user", "content": prompt})
    
    url = "https://api.groq.com/openai/v1/chat/completions"
    payload = {
        "model": MODEL,
        "messages": messages,
        "temperature": 0.8,
        "max_tokens": 600
    }
    
    for attempt in range(len(GROQ_API_KEYS) * 2):
        api_key = GROQ_API_KEYS[current_key_index]
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, headers=headers, json=payload, timeout=60) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        answer = data["choices"][0]["message"]["content"]
                        answer = re.sub(r'<think>.*?</think>', '', answer, flags=re.DOTALL).strip()
                        return answer
                    elif resp.status == 429:
                        current_key_index = (current_key_index + 1) % len(GROQ_API_KEYS)
                        continue
                    else:
                        current_key_index = (current_key_index + 1) % len(GROQ_API_KEYS)
                        continue
        except:
            current_key_index = (current_key_index + 1) % len(GROQ_API_KEYS)
            continue
    
    return "⚠️ Сейчас большая нагрузка, попробуй через минуту."

# === БОТ ===
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
logging.basicConfig(level=logging.INFO)

HELP_TEXT = """
📚 **ChatGPT Bot - инструкция**

💬 **В ЛИЧНЫХ СООБЩЕНИЯХ:**
Просто напиши любое сообщение

👥 **В ГРУППАХ:**
Напиши `/ask вопрос` или ответь на моё сообщение

🧠 **Мои возможности:**
- Помню последние 30 сообщений группы
- Запоминаю важную информацию
- Бесплатно, 15 секунд ожидания

📋 **Команды:**
/start - приветствие
/help - это сообщение
/stats - статистика группы
/top - топ активных участников
"""

@dp.message(Command("start"))
async def cmd_start(message: Message):
    add_user(message.from_user.id, message.from_user.username)
    await message.answer(f"👋 Привет, {message.from_user.first_name}!\n\n{HELP_TEXT}")

@dp.message(Command("help"))
async def cmd_help(message: Message):
    await message.answer(HELP_TEXT)

@dp.message(Command("stats"))
async def cmd_stats(message: Message):
    if message.chat.type == "private":
        await message.answer("📊 Статистика доступна только в группах!")
        return
    data = load_data()
    key = str(message.chat.id)
    stats = data["group_stats"].get(key, {})
    total = sum(stats.values())
    text = f"📊 **Статистика группы**\n\n"
    text += f"💬 Обращений: {total}\n"
    text += f"👥 Участников: {len(stats)}\n"
    text += f"🧠 Запомнено фактов: {len(data['group_memory'].get(key, []))}\n"
    await message.answer(text, parse_mode="Markdown")

@dp.message(Command("top"))
async def cmd_top(message: Message):
    if message.chat.type == "private":
        await message.answer("🏆 Топ активных доступен только в группах!")
        return
    data = load_data()
    stats = data["group_stats"].get(str(message.chat.id), {})
    sorted_users = sorted(stats.items(), key=lambda x: x[1], reverse=True)[:10]
    if not sorted_users:
        await message.answer("Пока нет статистики!")
        return
    text = "🏆 **Топ активных участников:**\n\n"
    medals = ["🥇", "🥈", "🥉", "📌", "📌", "📌", "📌", "📌", "📌", "📌"]
    for i, (uid, count) in enumerate(sorted_users):
        try:
            user = await bot.get_chat(int(uid))
            name = user.first_name
        except:
            name = str(uid)
        text += f"{medals[i]} {name}: {count} обращений\n"
    await message.answer(text, parse_mode="Markdown")

@dp.message(Command("ask"))
async def cmd_ask(message: Message):
    add_user(message.from_user.id, message.from_user.username)
    query = message.text.replace("/ask", "").strip()
    if not query:
        await message.answer("📝 Использование: `/ask вопрос`")
        return
    can, wait = check_rate_limit(message.from_user.id)
    if not can:
        await message.answer(f"⏳ Подожди {wait} секунд!")
        return
    await message.answer("🤔 Думаю...")
    update_user_stats(message.from_user.id)
    if message.chat.type != "private":
        update_stats(message.chat.id, message.from_user.id)
        add_to_context(message.chat.id, "user", query, message.from_user.first_name)
    response = await ask_groq(query, message.chat.id, message.from_user.first_name)
    if message.chat.type != "private":
        add_to_context(message.chat.id, "assistant", response)
    if "запомни" in query.lower():
        important = query.lower().replace("запомни", "").strip()
        if important:
            save_to_memory(message.chat.id, important)
            await message.answer(response + "\n\n📝 Запомнил! ✅")
            return
    await message.answer(response)

@dp.message(F.reply_to_message)
async def handle_reply(message: Message):
    if message.reply_to_message.from_user.id != bot.id:
        return
    add_user(message.from_user.id, message.from_user.username)
    can, wait = check_rate_limit(message.from_user.id)
    if not can:
        await message.answer(f"⏳ Подожди {wait} секунд!")
        return
    await message.answer("🤔 Думаю...")
    update_user_stats(message.from_user.id)
    if message.chat.type != "private":
        update_stats(message.chat.id, message.from_user.id)
        add_to_context(message.chat.id, "user", message.text, message.from_user.first_name)
    response = await ask_groq(message.text, message.chat.id, message.from_user.first_name)
    if message.chat.type != "private":
        add_to_context(message.chat.id, "assistant", response)
    await message.answer(response)

@dp.message()
async def handle_private(message: Message):
    if message.chat.type != "private":
        return
    if message.text and message.text.startswith('/'):
        return
    add_user(message.from_user.id, message.from_user.username)
    can, wait = check_rate_limit(message.from_user.id)
    if not can:
        await message.answer(f"⏳ Подожди {wait} секунд!")
        return
    await message.answer("🤔 Думаю...")
    update_user_stats(message.from_user.id)
    response = await ask_groq(message.text, message.chat.id, message.from_user.first_name)
    await message.answer(response)

async def main():
    load_data()
    print("=" * 50)
    print("🤖 ChatGPT Bot запущен!")
    print(f"👑 Admin ID: {ADMIN_ID}")
    print(f"🔑 Ключей Groq: {len(GROQ_API_KEYS)}")
    print("=" * 50)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
