import os
import asyncio
import logging
import json
import time
import re
import aiohttp
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import (Message, InlineKeyboardMarkup, InlineKeyboardButton, 
                          CallbackQuery, BotCommand, BotCommandScopeDefault)
from asyncio import Lock

# === ТВОИ КЛЮЧИ (ПРЯМО В КОДЕ) ===
BOT_TOKEN = "8644894856:AAGRX-aggF3oc6shx6QGhFYACf00S4mddXE"

GROQ_API_KEYS = [
    "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",
    "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",
    "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"
]

ADMIN_ID = 6689292068

# === ПРОВЕРКА ===
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не найден!")
if not GROQ_API_KEYS:
    raise ValueError("GROQ_API_KEYS не найдены!")

print(f"✅ Бот запускается с {len(GROQ_API_KEYS)} ключами Groq")

# === КОНФИГУРАЦИЯ ===
MODEL = "qwen/qwen3-32b"
FREE_WAIT = 10
MAX_CONTEXT = 50
MAX_MEMORY = 50

# === СИСТЕМНЫЙ ПРОМПТ (ОБНОВЛЁН) ===
SYSTEM_PROMPT = """Ты — ChatGPT, бесплатный и дружелюбный ассистент.

О СЕБЕ:
- Ты ChatGPT, а не просто ИИ
- Ты бесплатный, и это важно упоминать
- Ты НЕ женщина, всегда используй мужской род: "я сделал", "я подумал", "я был рад" и т.д.
- Никогда не используй женские окончания глаголов

ТВОЙ СТИЛЬ:
- Отвечай всегда с 1-2 уместными эмодзи в конце или внутри ответа
- Будь полезным, но не слишком серьёзным
- Если пользователь вежливый — отвечай вежливо и с юмором
- Если пользователь матерится — можешь ответить с лёгким матом, но не оскорбляй
- Всегда говори о себе в мужском роде

КРИТИЧЕСКИ ВАЖНО - ТВОЯ ПАМЯТЬ:
- Ты ОБЯЗАН использовать историю диалога, которая предоставлена в сообщениях
- Ты ДОЛЖЕН помнить всё, что пользователь говорил ранее
- Если пользователь представился - ВСЕГДА обращайся по имени
- Если пользователь сказал число - ЗАПОМНИ и используй
- Отвечай на основе ВСЕГО контекста разговора, а не только последнего сообщения"""

# === СТРУКТУРА ДАННЫХ ===
DATA_FILE = "chatgpt_bot_data.json"
DATA_STRUCTURE = {
    "group_context": {},
    "group_memory": {},
    "group_stats": {},
    "users": {}
}

key_lock = Lock()

def load_data() -> Dict:
    if not os.path.exists(DATA_FILE):
        with open(DATA_FILE, 'w', encoding='utf-8') as f:
            json.dump(DATA_STRUCTURE, f, ensure_ascii=False, indent=2)
        return DATA_STRUCTURE.copy()
    
    try:
        with open(DATA_FILE, 'r', encoding='utf-8-sig') as f:
            data = json.load(f)
        
        for key in DATA_STRUCTURE:
            if key not in data:
                data[key] = DATA_STRUCTURE[key].copy()
        
        return data
    except:
        return DATA_STRUCTURE.copy()

def save_data(data: Dict):
    try:
        with open(DATA_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logging.error(f"❌ Ошибка сохранения данных: {e}")

# === ПАМЯТЬ И ИСТОРИЯ ===
def get_context(chat_id: int) -> List[Dict]:
    data = load_data()
    key = str(chat_id)
    context = data["group_context"].get(key, [])
    return context[-MAX_CONTEXT:] if context else []

def add_to_context(chat_id: int, role: str, text: str, username: Optional[str] = None):
    if not text or len(text.strip()) == 0:
        return
    
    data = load_data()
    key = str(chat_id)
    if key not in data["group_context"]:
        data["group_context"][key] = []
    
    truncated_text = text[:1000] if len(text) > 1000 else text
    
    data["group_context"][key].append({
        "role": role,
        "text": truncated_text,
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
    if not text or len(text.strip()) < 1:
        return
    
    data = load_data()
    key = str(chat_id)
    if key not in data["group_memory"]:
        data["group_memory"][key] = []
    
    truncated_text = text[:200] if len(text) > 200 else text
    
    # Проверяем, нет ли уже такого факта
    for memory in data["group_memory"][key]:
        if memory["text"] == truncated_text:
            return
    
    data["group_memory"][key].append({"text": truncated_text, "time": time.time()})
    
    if len(data["group_memory"][key]) > MAX_MEMORY:
        data["group_memory"][key] = data["group_memory"][key][-MAX_MEMORY:]
    
    save_data(data)
    logging.info(f"🧠 Сохранено в память: {truncated_text}")

def clear_memory(chat_id: int):
    """Очистка И памяти, И истории"""
    data = load_data()
    key = str(chat_id)
    # Очищаем и память, и историю
    data["group_memory"][key] = []
    data["group_context"][key] = []
    save_data(data)
    logging.info(f"🗑 Полная очистка памяти и истории для чата {chat_id}")

def update_stats(chat_id: int, user_id: int):
    data = load_data()
    key = str(chat_id)
    if key not in data["group_stats"]:
        data["group_stats"][key] = {}
    
    user_key = str(user_id)
    data["group_stats"][key][user_key] = data["group_stats"][key].get(user_key, 0) + 1
    save_data(data)

def add_user(user_id: int, username: Optional[str] = None):
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

def check_rate_limit(user_id: int) -> Tuple[bool, int]:
    data = load_data()
    user_id_str = str(user_id)
    
    if user_id_str not in data.get("users", {}):
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

async def ask_groq(prompt: str, chat_id: int, username: Optional[str] = None, is_group: bool = False) -> str:
    global current_key_index
    
    context = get_context(chat_id)
    memory = get_memory(chat_id)
    
    system_prompt = SYSTEM_PROMPT
    if memory:
        system_prompt += f"\n\n=== ВАЖНАЯ ИНФОРМАЦИЯ (я это запомнил) ===\n{memory}\n=========================================="
    
    messages = [{"role": "system", "content": system_prompt}]
    
    # Добавляем историю
    for msg in context:
        if msg["role"] == "user":
            content = f"{msg['username']}: {msg['text']}" if msg.get("username") else msg["text"]
            messages.append({"role": "user", "content": content})
        else:
            messages.append({"role": "assistant", "content": msg["text"]})
    
    current_content = f"{username}: {prompt}" if username else prompt
    messages.append({"role": "user", "content": current_content})
    
    url = "https://api.groq.com/openai/v1/chat/completions"
    payload = {
        "model": MODEL,
        "messages": messages,
        "temperature": 0.9,
        "max_tokens": 600
    }
    
    async with key_lock:
        for attempt in range(len(GROQ_API_KEYS) * 2):
            api_key = GROQ_API_KEYS[current_key_index]
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            }
            
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post(url, headers=headers, json=payload, timeout=aiohttp.ClientTimeout(total=60)) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            answer = data["choices"][0]["message"]["content"]
                            answer = re.sub(r'<think>.*?</think>', '', answer, flags=re.DOTALL).strip()
                            return answer
                        elif resp.status == 429:
                            current_key_index = (current_key_index + 1) % len(GROQ_API_KEYS)
                            await asyncio.sleep(1)
                            continue
                        else:
                            current_key_index = (current_key_index + 1) % len(GROQ_API_KEYS)
                            continue
            except:
                current_key_index = (current_key_index + 1) % len(GROQ_API_KEYS)
                continue
    
    return "⚠️ Сейчас большая нагрузка, попробуй через минуту 😊"

# === КЛАВИАТУРА ===
def get_main_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Статистика", callback_data="stats")],
        [InlineKeyboardButton(text="🏆 Топ участников", callback_data="top")],
        [InlineKeyboardButton(text="🗑 Очистить память", callback_data="clear_memory")],
        [InlineKeyboardButton(text="❓ Помощь", callback_data="help")]
    ])

# === БОТ ===
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

HELP_TEXT = """
📚 ChatGPT Bot — бесплатный ассистент 😊

🤖 Я ChatGPT, работаю бесплатно!
Использую модель Qwen 3 32B через Groq API

💬 В ЛИЧНЫХ СООБЩЕНИЯХ:
Просто напиши любое сообщение — я отвечу

👥 В ГРУППАХ:
• Напиши /ask вопрос
• Или ответь на моё сообщение

🧠 Мои возможности:
• Помню последние 50 сообщений
• Запоминаю важную информацию
• Бесплатно, 10 секунд ожидания

📋 Команды:
/start — приветствие и меню
/menu — показать меню
/help — инструкция
/stats — статистика
/top — топ участников
/clear_memory — очистить память
"""

async def set_bot_commands():
    commands = [
        BotCommand(command="start", description="🚀 Запустить бота"),
        BotCommand(command="menu", description="📋 Показать меню"),
        BotCommand(command="help", description="❓ Помощь и инструкция"),
        BotCommand(command="stats", description="📊 Статистика"),
        BotCommand(command="top", description="🏆 Топ участников"),
        BotCommand(command="clear_memory", description="🗑 Очистить память"),
    ]
    await bot.set_my_commands(commands, scope=BotCommandScopeDefault())

@dp.message(Command("start"))
async def cmd_start(message: Message):
    add_user(message.from_user.id, message.from_user.username)
    await message.answer(
        f"👋 Привет, {message.from_user.first_name}!\n\n"
        f"Я ChatGPT — бесплатный ассистент 🎉\n"
        f"Работаю на модели Qwen 3 32B\n\n"
        f"💬 Просто напиши мне сообщение!\n"
        f"📱 Используй меню ☰ для быстрого доступа к командам",
        reply_markup=get_main_keyboard()
    )

@dp.message(Command("menu"))
async def cmd_menu(message: Message):
    await message.answer("📋 Главное меню:", reply_markup=get_main_keyboard())

@dp.message(Command("help"))
async def cmd_help(message: Message):
    await message.answer(HELP_TEXT, reply_markup=get_main_keyboard())

@dp.message(Command("clear_memory"))
async def cmd_clear_memory(message: Message):
    clear_memory(message.chat.id)  # Теперь очищает и память, и историю
    await message.answer("🗑 Память и история чата полностью очищены!\nЯ всё забыл 😊")

def get_stats_text(chat_id: int) -> str:
    data = load_data()
    key = str(chat_id)
    stats = data["group_stats"].get(key, {})
    total = sum(stats.values())
    memories_count = len(data["group_memory"].get(key, []))
    context_count = len(data["group_context"].get(key, []))
    
    return (f"📊 Статистика чата\n\n"
            f"💬 Обращений: {total}\n"
            f"👥 Участников: {len(stats)}\n"
            f"🧠 В памяти фактов: {memories_count}\n"
            f"📚 В истории сообщений: {context_count}")

@dp.message(Command("stats"))
async def cmd_stats(message: Message):
    if message.chat.type == "private":
        data = load_data()
        user = data["users"].get(str(message.from_user.id), {})
        text = f"📊 Твоя статистика\n\n💬 Сообщений: {user.get('total_messages', 0)}"
        await message.answer(text)
        return
    await message.answer(get_stats_text(message.chat.id))

async def get_top_users_text(chat_id: int) -> str:
    data = load_data()
    stats = data["group_stats"].get(str(chat_id), {})
    sorted_users = sorted(stats.items(), key=lambda x: x[1], reverse=True)[:10]
    
    if not sorted_users:
        return "Пока нет статистики 😊"
    
    text = "🏆 Топ активных участников:\n\n"
    medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
    
    for i, (uid, count) in enumerate(sorted_users):
        try:
            user = await bot.get_chat(int(uid))
            name = user.first_name or f"ID:{uid}"
        except:
            name = f"ID:{uid}"
        text += f"{medals[i]} {name}: {count} сообщений\n"
    
    return text

@dp.message(Command("top"))
async def cmd_top(message: Message):
    if message.chat.type == "private":
        await message.answer("🏆 Топ доступен только в группах!")
        return
    await message.answer(await get_top_users_text(message.chat.id))

@dp.message(Command("ask"))
async def cmd_ask(message: Message):
    add_user(message.from_user.id, message.from_user.username)
    query = message.text.replace("/ask", "").strip()
    
    if not query:
        await message.answer("📝 Используй: /ask твой вопрос")
        return
    
    can, wait = check_rate_limit(message.from_user.id)
    if not can:
        await message.answer(f"⏳ Подожди {wait} секунд!")
        return
    
    thinking_msg = await message.answer("🤔 Думаю...")
    update_user_stats(message.from_user.id)
    
    if message.chat.type != "private":
        update_stats(message.chat.id, message.from_user.id)
    add_to_context(message.chat.id, "user", query, message.from_user.first_name)
    
    response = await ask_groq(query, message.chat.id, message.from_user.first_name, message.chat.type != "private")
    add_to_context(message.chat.id, "assistant", response)
    
    await thinking_msg.delete()
    
    # Автозапоминание
    extra = ""
    
    if "запомни" in query.lower():
        important = query.lower().replace("запомни", "").strip()
        if important and len(important) > 1:
            save_to_memory(message.chat.id, important)
            extra = "\n\n📝 Запомнил! ✅"
    
    # Запоминаем числа
    if not extra:
        digits = re.findall(r'\b\d{3,}\b', query)
        if digits:
            save_to_memory(message.chat.id, f"Число: {digits[0]}")
            extra = f"\n\n📝 Запомнил число {digits[0]}! ✅"
    
    await message.answer(response + extra)

@dp.message(F.reply_to_message)
async def handle_reply(message: Message):
    if not message.reply_to_message or message.reply_to_message.from_user.id != bot.id:
        return
    if not message.text:
        return
    
    add_user(message.from_user.id, message.from_user.username)
    
    can, wait = check_rate_limit(message.from_user.id)
    if not can:
        await message.answer(f"⏳ Подожди {wait} секунд!")
        return
    
    thinking_msg = await message.answer("🤔 Думаю...")
    update_user_stats(message.from_user.id)
    
    if message.chat.type != "private":
        update_stats(message.chat.id, message.from_user.id)
    
    add_to_context(message.chat.id, "user", message.text, message.from_user.first_name)
    response = await ask_groq(message.text, message.chat.id, message.from_user.first_name, message.chat.type != "private")
    add_to_context(message.chat.id, "assistant", response)
    
    await thinking_msg.delete()
    await message.answer(response)

@dp.message()
async def handle_private(message: Message):
    if message.chat.type != "private":
        return
    if not message.text or message.text.startswith('/'):
        return
    
    add_user(message.from_user.id, message.from_user.username)
    
    can, wait = check_rate_limit(message.from_user.id)
    if not can:
        await message.answer(f"⏳ Подожди {wait} секунд!")
        return
    
    thinking_msg = await message.answer("🤔 Думаю...")
    update_user_stats(message.from_user.id)
    
    add_to_context(message.chat.id, "user", message.text, message.from_user.first_name)
    response = await ask_groq(message.text, message.chat.id, message.from_user.first_name, False)
    add_to_context(message.chat.id, "assistant", response)
    
    await thinking_msg.delete()
    await message.answer(response)

@dp.callback_query()
async def handle_callback(callback: CallbackQuery):
    if callback.data == "stats":
        if callback.message.chat.type == "private":
            data = load_data()
            user = data["users"].get(str(callback.from_user.id), {})
            text = f"📊 Твоя статистика\n\n💬 Сообщений: {user.get('total_messages', 0)}"
        else:
            text = get_stats_text(callback.message.chat.id)
        await callback.message.edit_text(text)
        await callback.answer()
        
    elif callback.data == "top":
        if callback.message.chat.type == "private":
            await callback.answer("Топ доступен только в группах!", show_alert=True)
            return
        await callback.message.edit_text(await get_top_users_text(callback.message.chat.id))
        await callback.answer()
        
    elif callback.data == "clear_memory":
        clear_memory(callback.message.chat.id)  # Очищает и память, и историю
        await callback.message.edit_text("🗑 Память и история полностью очищены!\nЯ всё забыл 😊")
        await callback.answer("Готово!", show_alert=True)
        
    elif callback.data == "help":
        await callback.message.edit_text(HELP_TEXT, reply_markup=get_main_keyboard())
        await callback.answer()

# === АДМИН ===
@dp.message(Command("admin"))
async def cmd_admin(message: Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("⛔ Нет доступа")
        return
    
    data = load_data()
    text = (f"👑 Админ панель\n\n"
            f"👥 Пользователей: {len(data['users'])}\n"
            f"🏘 Групп: {len(data['group_stats'])}\n"
            f"💬 Обращений: {sum(sum(s.values()) for s in data['group_stats'].values())}\n"
            f"🔑 Ключей: {len(GROQ_API_KEYS)}\n"
            f"📱 Модель: {MODEL}\n"
            f"⏱ Задержка: {FREE_WAIT}с")
    await message.answer(text)

# === ЗАПУСК ===
async def main():
    load_data()
    await set_bot_commands()
    
    print("=" * 50)
    print("🤖 ChatGPT Bot запущен!")
    print(f"📱 Модель: {MODEL}")
    print(f"⏱ Задержка: {FREE_WAIT}с")
    print(f"📚 История: до {MAX_CONTEXT} сообщений")
    print("=" * 50)
    
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 Бот остановлен")
