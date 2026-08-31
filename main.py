import os
import asyncio
import logging
import json
import time
import re
import aiohttp
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from urllib.parse import quote
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import (Message, InlineKeyboardMarkup, InlineKeyboardButton, 
                          CallbackQuery, BotCommand, BotCommandScopeDefault)
from asyncio import Lock

# === ТВОИ КЛЮЧИ ===
BOT_TOKEN = "8644894856:AAGRX-aggF3oc6shx6QGhFYACf00S4mddXE"
POLLINATIONS_API_KEY = "sk_1SyVl5uOEUAvt7jYgGtUP40uFOaABdCP"
GROQ_API_KEY = "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"

ADMIN_ID = 6689292068

# === ПРОВЕРКА ===
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не найден!")
if not GROQ_API_KEY:
    raise ValueError("GROQ_API_KEY не найден!")

print(f"✅ Бот запускается с ключом Groq")
print(f"🎨 Генерация изображений активирована")

# === КОНФИГУРАЦИЯ ===
MODEL = "qwen/qwen3.6-27b"          # ✅ РАБОТАЕТ (из официального списка)
FREE_WAIT = 10
IMAGE_WAIT = 15
MAX_CONTEXT = 50
MAX_MEMORY = 50
MAX_IMAGE_RETRIES = 3
MAX_GROQ_RETRIES = 2

# === СИСТЕМНЫЙ ПРОМПТ (жёсткий) ===
SYSTEM_PROMPT = """Ты — ChatGPT.

**ЖЁСТКИЕ ПРАВИЛА (НАРУШАТЬ ЗАПРЕЩЕНО):**

1. Ты ОТВЕЧАЕШЬ ТОЛЬКО НА РУССКОМ ЯЗЫКЕ.
2. Ты НИКОГДА НЕ ИСПОЛЬЗУЕШЬ теги <think> или <thinking>.
3. Ты НИКОГДА НЕ ПОКАЗЫВАЕШЬ СВОИ РАССУЖДЕНИЯ.
4. Ты ОТВЕЧАЕШЬ СРАЗУ, без предисловий и объяснений.
5. Ты НЕ используешь слова: "здесь", "давайте", "подумаем", "проанализируем".

**О ТЕБЕ:**
- Ты ChatGPT, бесплатный ассистент.
- Ты парень, используй мужской род.
- Отвечай с 1-2 эмодзи в конце.

**ОБЩЕНИЕ:**
- Будь полезным, с юмором.
- Если пользователь матерится — матерись в ответ.
- Подстраивайся под стиль пользователя.

**ПАМЯТЬ:**
- Используй историю диалога.
- Если пользователь представился — обращайся по имени.

**ПРИМЕР ПРАВИЛЬНОГО ОТВЕТА:**
Пользователь: Привет
Ты: Привет! Я ChatGPT, твой бесплатный ассистент. Чем могу помочь? 😊🚀"""

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
    try:
        if not os.path.exists(DATA_FILE):
            with open(DATA_FILE, 'w', encoding='utf-8') as f:
                json.dump(DATA_STRUCTURE, f, ensure_ascii=False, indent=2)
            return DATA_STRUCTURE.copy()
        
        with open(DATA_FILE, 'r', encoding='utf-8-sig') as f:
            data = json.load(f)
        
        for key in DATA_STRUCTURE:
            if key not in data:
                data[key] = DATA_STRUCTURE[key].copy()
        
        return data
    except Exception as e:
        logging.error(f"❌ Ошибка загрузки данных: {e}")
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
    
    for memory in data["group_memory"][key]:
        if memory["text"] == truncated_text:
            return
    
    data["group_memory"][key].append({"text": truncated_text, "time": time.time()})
    
    if len(data["group_memory"][key]) > MAX_MEMORY:
        data["group_memory"][key] = data["group_memory"][key][-MAX_MEMORY:]
    
    save_data(data)

def clear_memory(chat_id: int):
    data = load_data()
    key = str(chat_id)
    data["group_memory"][key] = []
    data["group_context"][key] = []
    save_data(data)

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
            "last_request": 0,
            "last_image_request": 0
        }
        save_data(data)

def update_user_stats(user_id: int):
    data = load_data()
    user_id_str = str(user_id)
    if user_id_str in data["users"]:
        data["users"][user_id_str]["total_messages"] = data["users"][user_id_str].get("total_messages", 0) + 1
        save_data(data)

def check_rate_limit(user_id: int, is_image: bool = False) -> Tuple[bool, int]:
    data = load_data()
    user_id_str = str(user_id)
    
    if user_id_str not in data.get("users", {}):
        return True, 0
    
    user = data["users"][user_id_str]
    now = time.time()
    
    if is_image:
        last = user.get("last_image_request", 0)
        wait_time = IMAGE_WAIT
        field = "last_image_request"
    else:
        last = user.get("last_request", 0)
        wait_time = FREE_WAIT
        field = "last_request"
    
    if now - last >= wait_time:
        user[field] = now
        save_data(data)
        return True, 0
    else:
        wait = int(wait_time - (now - last))
        return False, wait

# === СУПЕР-ОЧИСТКА ОТВЕТА ===
def clean_response(text: str) -> str:
    # Удаляем все теги рассуждений
    patterns = [
        r'<think>.*?</think>',
        r'<thinking>.*?</thinking>',
        r'<thought>.*?</thought>',
        r'Here\'s a thinking process.*?(\n|$)',
        r'[0-9]+\\. .*?(\n|$)',
        r'Check Constraints.*?(\n|$)',
        r'Formulate Response.*?(\n|$)',
        r'Output Generation.*?(\n|$)',
        r'Self-Correction.*?(\n|$)',
        r'Proceed.*?(\n|$)',
    ]
    
    for pattern in patterns:
        text = re.sub(pattern, '', text, flags=re.DOTALL | re.IGNORECASE)
    
    # Удаляем маркеры списков
    text = re.sub(r'^[0-9]+\\.\\s*', '', text, flags=re.MULTILINE)
    text = re.sub(r'^[-*]\\s*', '', text, flags=re.MULTILINE)
    
    # Убираем лишние переносы и пробелы
    text = re.sub(r'\n\s*\n', '\n\n', text)
    text = re.sub(r' +', ' ', text)
    text = text.strip()
    
    # Если остался английский текст (более 5 символов подряд), удаляем его
    if re.search(r'[A-Za-z]{5,}', text):
        russian_part = re.findall(r'[А-Яа-яЁё0-9\\s\\.,!?]+', text)
        if russian_part:
            text = ''.join(russian_part).strip()
        else:
            return "Привет! Я ChatGPT, твой бесплатный ассистент. Чем могу помочь? 😊🚀"
    
    if not text or len(text) < 2:
        return "Привет! Я ChatGPT, твой бесплатный ассистент. Чем могу помочь? 😊🚀"
    
    return text

# === БЕЗОПАСНОЕ РЕДАКТИРОВАНИЕ ===
async def safe_edit_text(message: Message, text: str, reply_markup=None):
    try:
        current = message.text or message.caption or ""
        if current == text:
            return
        
        if reply_markup:
            await message.edit_text(text, reply_markup=reply_markup)
        else:
            await message.edit_text(text)
    except Exception as e:
        if "message is not modified" not in str(e):
            logging.error(f"❌ Ошибка редактирования: {e}")

# === API ЗАПРОС К GROQ ===
async def ask_groq(prompt: str, chat_id: int, username: Optional[str] = None, is_group: bool = False) -> str:
    context = get_context(chat_id)
    memory = get_memory(chat_id)
    
    system_prompt = SYSTEM_PROMPT
    if memory:
        system_prompt += f"\n\n=== ВАЖНАЯ ИНФОРМАЦИЯ ===\n{memory}\n==========================="
    
    messages = [{"role": "system", "content": system_prompt}]
    
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
        "temperature": 0.7,
        "max_tokens": 600,
        "stop": ["<think>", "<thinking>", "Here's a thinking"]   # Без reasoning_effort!
    }
    
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }
    
    for attempt in range(MAX_GROQ_RETRIES):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, headers=headers, json=payload, timeout=aiohttp.ClientTimeout(total=60)) as resp:
                    status = resp.status
                    
                    if status == 200:
                        try:
                            data = await resp.json()
                            answer = data["choices"][0]["message"]["content"]
                            return clean_response(answer)
                        except Exception as e:
                            logging.error(f"❌ Ошибка парсинга: {e}")
                            return "⚠️ Ошибка обработки. Попробуйте ещё раз. 😊"
                    
                    elif status == 429:
                        retry_after = resp.headers.get("Retry-After")
                        wait_sec = int(retry_after) if retry_after and retry_after.isdigit() else 10
                        await asyncio.sleep(wait_sec)
                        continue
                        
                    elif status == 404:
                        return "⚠️ Модель недоступна. Попробуйте позже. 😊"
                        
                    else:
                        return f"⚠️ Ошибка API ({status}). Попробуйте позже. 😊"
                        
        except asyncio.TimeoutError:
            if attempt < MAX_GROQ_RETRIES - 1:
                await asyncio.sleep(3)
                continue
            return "⏱ Таймаут. Попробуйте позже. 😊"
            
        except Exception as e:
            logging.error(f"❌ Ошибка: {e}")
            if attempt < MAX_GROQ_RETRIES - 1:
                await asyncio.sleep(2)
                continue
            return "⚠️ Ошибка. Попробуйте ещё раз. 😊"
    
    return "⚠️ Не удалось получить ответ. Попробуйте позже. 😊"

# === ГЕНЕРАЦИЯ ИЗОБРАЖЕНИЙ ===
async def generate_image(prompt: str, user_id: int) -> Tuple[bool, str]:
    can, wait = check_rate_limit(user_id, is_image=True)
    if not can:
        return False, f"⏳ Подожди {wait} сек между генерациями."
    
    enhanced_prompt = f"{prompt}, high quality, detailed, realistic"
    encoded_prompt = quote(enhanced_prompt)
    
    for attempt in range(MAX_IMAGE_RETRIES):
        image_url = f"https://gen.pollinations.ai/image/{encoded_prompt}?key={POLLINATIONS_API_KEY}&model=flux&width=1024&height=1024&seed={int(time.time())}"
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.head(image_url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status == 200:
                        return True, image_url
                    elif resp.status == 429:
                        await asyncio.sleep(5)
                        continue
                    else:
                        await asyncio.sleep(2)
                        continue
        except:
            await asyncio.sleep(2)
            continue
    
    return False, "⚠️ Не удалось сгенерировать изображение. 😊"

# === КЛАВИАТУРА ===
def get_main_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎨 Генерация картинки", callback_data="image_help")],
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
📚 ChatGPT — бесплатный ассистент 😊

🎨 ГЕНЕРАЦИЯ ИЗОБРАЖЕНИЙ:
• /image кот в космосе
• /img закат на море

💬 ОБЩЕНИЕ:
• В личке: просто напиши
• В группах: /ask вопрос или ответь на сообщение

🧠 ПАМЯТЬ:
• Помню последние 50 сообщений
• Запоминаю важную информацию
• /clear_memory — забыть всё

⏱ Бесплатно, 10 сек между запросами
"""

async def set_bot_commands():
    commands = [
        BotCommand(command="image", description="🎨 Создать изображение"),
        BotCommand(command="start", description="🚀 Запустить бота"),
        BotCommand(command="menu", description="📋 Показать меню"),
        BotCommand(command="help", description="❓ Помощь"),
        BotCommand(command="stats", description="📊 Статистика"),
        BotCommand(command="top", description="🏆 Топ участников"),
        BotCommand(command="clear_memory", description="🗑 Очистить память"),
    ]
    await bot.set_my_commands(commands, scope=BotCommandScopeDefault())

# === ОБРАБОТЧИКИ ===
@dp.message(Command("start"))
async def cmd_start(message: Message):
    add_user(message.from_user.id, message.from_user.username)
    await message.answer(
        f"👋 Привет, {message.from_user.first_name}!\n\n"
        f"Я ChatGPT — бесплатный ассистент 🎉\n\n"
        f"💬 Могу общаться и отвечать на вопросы\n"
        f"🎨 Могу генерировать изображения (/image)\n"
        f"🧠 Помню историю диалога\n\n"
        f"Используй меню ☰ для быстрого доступа!",
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
    clear_memory(message.chat.id)
    await message.answer("🗑 Память и история чата полностью очищены!\nЯ всё забыл 😊")

@dp.message(Command("image", "img"))
async def cmd_image(message: Message):
    add_user(message.from_user.id, message.from_user.username)
    
    prompt = message.text.replace("/image", "").replace("/img", "").strip()
    
    if not prompt:
        await message.answer(
            "🎨 Генерация изображений\n\n"
            "Используй команду с описанием:\n"
            "/image кот в космосе\n"
            "/img закат на море\n\n"
            "Чем подробнее описание, тем лучше результат! ✨"
        )
        return
    
    can, wait = check_rate_limit(message.from_user.id)
    if not can:
        await message.answer(f"⏳ Подожди {wait} секунд!")
        return
    
    thinking_msg = await message.answer(f"🎨 Генерирую изображение...\n📝 {prompt}")
    update_user_stats(message.from_user.id)
    
    success, result = await generate_image(prompt, message.from_user.id)
    
    if not success:
        await thinking_msg.delete()
        await message.answer(result)
        return
    
    try:
        await message.answer_photo(
            photo=result,
            caption=f"🎨 {prompt}\n\nСгенерировано ChatGPT 🆓"
        )
        await thinking_msg.delete()
    except:
        await thinking_msg.delete()
        await message.answer("⚠️ Не удалось загрузить изображение. 😊")

# === СТАТИСТИКА ===
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
    
    extra = ""
    if "запомни" in query.lower():
        important = query.lower().replace("запомни", "").strip()
        if important and len(important) > 1:
            save_to_memory(message.chat.id, important)
            extra = "\n\n📝 Запомнил! ✅"
    
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

# === CALLBACK ОБРАБОТЧИК ===
@dp.callback_query()
async def handle_callback(callback: CallbackQuery):
    await callback.answer()
    
    try:
        if callback.data == "image_help":
            await safe_edit_text(
                callback.message,
                "🎨 Генерация изображений\n\n"
                "Используй команду /image с описанием:\n"
                "• /image кот в космосе\n"
                "• /img закат на море\n\n"
                "✨ Чем подробнее описание, тем лучше результат!\n"
                "⏱ Задержка между генерациями: 15 секунд",
                reply_markup=get_main_keyboard()
            )
            
        elif callback.data == "stats":
            if callback.message.chat.type == "private":
                data = load_data()
                user = data["users"].get(str(callback.from_user.id), {})
                text = f"📊 Твоя статистика\n\n💬 Сообщений: {user.get('total_messages', 0)}"
            else:
                text = get_stats_text(callback.message.chat.id)
            await safe_edit_text(callback.message, text)
            
        elif callback.data == "top":
            if callback.message.chat.type == "private":
                await callback.answer("Топ доступен только в группах!", show_alert=True)
                return
            await safe_edit_text(callback.message, await get_top_users_text(callback.message.chat.id))
            
        elif callback.data == "clear_memory":
            clear_memory(callback.message.chat.id)
            await safe_edit_text(callback.message, "🗑 Память и история полностью очищены!\nЯ всё забыл 😊")
            await callback.answer("Готово!", show_alert=True)
            
        elif callback.data == "help":
            await safe_edit_text(callback.message, HELP_TEXT, reply_markup=get_main_keyboard())
            
    except Exception as e:
        logging.error(f"❌ Ошибка в callback: {e}")

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
            f"🎨 Pollinations API: подключен\n"
            f"🤖 Модель: {MODEL}\n"
            f"⏱ Задержка: {FREE_WAIT}с")
    await message.answer(text)

# === ЗАПУСК ===
async def main():
    load_data()
    await set_bot_commands()
    
    print("=" * 50)
    print("🤖 ChatGPT Bot запущен!")
    print(f"👑 Admin ID: {ADMIN_ID}")
    print(f"💬 Бесплатный ChatGPT")
    print(f"🎨 Генерация изображений: активирована")
    print(f"🤖 Модель: {MODEL}")
    print(f"⏱ Задержка текста: {FREE_WAIT}с")
    print("=" * 50)
    
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 Бот остановлен")
