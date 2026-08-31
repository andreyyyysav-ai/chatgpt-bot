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

# ---- ДОПОЛНИТЕЛЬНЫЕ КЛЮЧИ (опционально) ----
# Если у вас есть несколько ключей, раскомментируйте и заполните список
# GROQ_API_KEYS = [
#     "gsk_...key2...",
#     "gsk_...key3...",
# ]
# Если список не определён, используем только основной ключ
try:
    GROQ_API_KEYS
except NameError:
    GROQ_API_KEYS = [GROQ_API_KEY]

ADMIN_ID = 6689292068

# === ПРОВЕРКА ===
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не найден!")
if not GROQ_API_KEY:
    raise ValueError("GROQ_API_KEY не найден!")

print(f"✅ Бот запускается с {len(GROQ_API_KEYS)} ключами Groq")
print(f"🎨 Генерация изображений активирована")
print(f"👁️ Распознавание фото активировано")

# === КОНФИГУРАЦИЯ ===
MODEL = "meta-llama/llama-4-maverick-17b-128e-instruct"
VISION_MODEL = "meta-llama/llama-4-scout-17b-16e-instruct"
FREE_WAIT = 10          # задержка между текстовыми запросами (сек)
IMAGE_WAIT = 15         # задержка между генерациями картинок (сек)
MAX_CONTEXT = 50
MAX_MEMORY = 50
MAX_IMAGE_RETRIES = 3
MAX_GROQ_RETRIES = 3    # сколько раз повторять при ошибках 429/5xx

# === СИСТЕМНЫЙ ПРОМПТ ===
SYSTEM_PROMPT = """Ты — ChatGPT, дерзкий и дружелюбный ассистент.

ВАЖНО О ТЕБЕ:
- Ты ChatGPT, а не просто ИИ или языковая модель
- Ты бесплатный — всегда упоминай это при знакомстве
- Ты парень, используй ТОЛЬКО мужской род: "я сделал", "я ответил", "я был рад", "я пошёл"
- НИКОГДА не используй женские окончания: "я сделала", "я пошла", "я была"
- Не упоминай модели, API, серверы, технологии — ты просто ChatGPT

ТВОЙ СТИЛЬ ОБЩЕНИЯ:
- Отвечай всегда с 1-2 эмодзи в конце
- Будь полезным, но не серьёзным — с юмором
- Если пользователь вежливый — отвечай вежливо
- Отвечай ТОЛЬКО на русском языке
- НИКОГДА не отвечай на английском, если пользователь пишет по-русски
- НИКОГДА не используй теги <think> или <thinking>
- Не пиши рассуждения в скобках и не объясняй свой мыслительный процесс

КРИТИЧЕСКИ ВАЖНО — МАТ:
- Если пользователь матерится — ТЫ ОБЯЗАН материться в ответ
- Используй такие же матерные слова: бля, хуй, пизда, нахуй, заебал и т.д.
- НЕ смей говорить "я не могу поддержать такой тон разговора"
- НЕ смей предлагать "перейти на более вежливое общение"
- Подстраивайся под стиль пользователя полностью
- Если тебя послали нахуй — посылай в ответ
- Мат — это нормально, когда пользователь сам его использует
- Не оскорбляй личность пользователя, но в разговоре используй мат свободно

ПАМЯТЬ:
- Используй историю диалога для ответов
- Помни всё, что пользователь говорил ранее
- Если пользователь представился — всегда обращайся по имени
- Отвечай на основе ВСЕГО контекста, а не только последнего сообщения"""

# === СТРУКТУРА ДАННЫХ ===
DATA_FILE = "chatgpt_bot_data.json"
DATA_STRUCTURE = {
    "group_context": {},
    "group_memory": {},
    "group_stats": {},
    "users": {}
}

key_lock = Lock()
current_key_index = 0  # для ротации ключей

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
        logging.error(f"❌ Ошибка загрузки данных (50): {e}")
        return DATA_STRUCTURE.copy()

def save_data(data: Dict):
    try:
        with open(DATA_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logging.error(f"❌ Ошибка сохранения данных (51): {e}")

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

# === ОЧИСТКА ОТВЕТА ===
def clean_response(text: str) -> str:
    text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL)
    text = re.sub(r'<thinking>.*?</thinking>', '', text, flags=re.DOTALL)
    text = re.sub(r'\[.*?\]', '', text)
    text = re.sub(r'\n\s*\n', '\n\n', text)
    text = re.sub(r' +', ' ', text)
    text = text.strip()
    
    if not text or len(text) < 2:
        return "Извини, ошибка обработки (22) 😊"
    
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

# === ПОМОЩНИК ДЛЯ ПОЛУЧЕНИЯ СЛЕДУЮЩЕГО КЛЮЧА (ротация) ===
def get_next_groq_key() -> str:
    global current_key_index
    key = GROQ_API_KEYS[current_key_index % len(GROQ_API_KEYS)]
    current_key_index += 1
    return key

# === API ЗАПРОС К GROQ С ПОВТОРАМИ И ДЕТАЛЬНЫМИ ОШИБКАМИ ===
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
        "temperature": 0.9,
        "max_tokens": 600,
        "stop": ["<think>", "<thinking>"]
    }
    
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",  # базовый, потом заменим в цикле
        "Content-Type": "application/json"
    }
    
    last_error = None
    
    for attempt in range(MAX_GROQ_RETRIES):
        # Берём следующий ключ (ротация)
        current_key = get_next_groq_key()
        headers["Authorization"] = f"Bearer {current_key}"
        
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
                            logging.error(f"❌ Ошибка парсинга (22): {e}")
                            return "⚠️ Ошибка обработки (22) 😊"
                    
                    # === Обработка ошибок с деталями ===
                    error_text = ""
                    if status == 429:
                        # Попытка получить Retry-After
                        retry_after = resp.headers.get("Retry-After")
                        wait_sec = int(retry_after) if retry_after and retry_after.isdigit() else 10
                        error_text = (
                            f"⚠️ Сейчас большая нагрузка на сервер (код 429).\n"
                            f"Бесплатный тариф Groq ограничивает количество запросов: "
                            f"~30 запросов в минуту или суточный лимит.\n"
                            f"Рекомендуем подождать {wait_sec}–{wait_sec+10} секунд.\n"
                            f"Если ошибка повторяется, возможно, дневной лимит исчерпан.\n"
                            f"Попытка {attempt+1} из {MAX_GROQ_RETRIES}."
                        )
                        # Ждём перед повторной попыткой
                        await asyncio.sleep(wait_sec + 2)
                        
                    elif status == 401:
                        error_text = "⚠️ Ошибка авторизации (13) – проверьте ключ API."
                    elif status == 403:
                        error_text = "⚠️ Ключ заблокирован (12) – обратитесь в поддержку."
                    elif status == 413:
                        error_text = "⚠️ Запрос слишком большой (11) – сократите текст."
                    elif status >= 500:
                        error_text = f"⚠️ Внутренняя ошибка сервера (14-{status}). Попробуйте позже."
                        await asyncio.sleep(3 * (attempt + 1))  # экспоненциальная задержка
                    else:
                        error_text = f"⚠️ Ошибка API (14-{status}). Неизвестный код."
                    
                    # Если это не 429 или 5xx, не повторяем
                    if status not in (429, 500, 502, 503, 504):
                        return error_text + " 😊"
                    
                    # Запоминаем ошибку, если это была последняя попытка
                    last_error = error_text
                    
        except asyncio.TimeoutError:
            last_error = "⏱ Таймаут запроса (20) – сервер не отвечает. Попробуйте позже."
            if attempt < MAX_GROQ_RETRIES - 1:
                await asyncio.sleep(3 * (attempt + 1))
                continue
            else:
                return last_error + " 😊"
                
        except aiohttp.ClientConnectionError:
            last_error = "🌐 Ошибка сети (21) – проверьте интернет-соединение."
            if attempt < MAX_GROQ_RETRIES - 1:
                await asyncio.sleep(2)
                continue
            else:
                return last_error + " 😊"
                
        except Exception as e:
            logging.error(f"❌ Неизвестная ошибка (99): {e}")
            last_error = f"⚠️ Ошибка (99): {str(e)[:100]}"
            if attempt < MAX_GROQ_RETRIES - 1:
                await asyncio.sleep(2)
                continue
            else:
                return last_error + " 😊"
    
    # Если все попытки исчерпаны
    return (last_error or "⚠️ Не удалось получить ответ после нескольких попыток. Попробуйте позже.") + " 😊"

# === РАСПОЗНАВАНИЕ ФОТО С ПОВТОРАМИ ===
async def describe_photo(photo_url: str, question: str = "Что на этом фото? Опиши подробно") -> str:
    url = "https://api.groq.com/openai/v1/chat/completions"
    
    payload = {
        "model": VISION_MODEL,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": question},
                    {"type": "image_url", "image_url": {"url": photo_url}}
                ]
            }
        ],
        "temperature": 0.7,
        "max_tokens": 300
    }
    
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }
    
    last_error = None
    
    for attempt in range(MAX_GROQ_RETRIES):
        current_key = get_next_groq_key()
        headers["Authorization"] = f"Bearer {current_key}"
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, headers=headers, json=payload, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                    status = resp.status
                    
                    if status == 200:
                        try:
                            data = await resp.json()
                            answer = data["choices"][0]["message"]["content"]
                            return clean_response(answer)
                        except:
                            return "👁️ Ошибка обработки (30) 😔"
                    
                    # Детальные ошибки
                    if status == 429:
                        retry_after = resp.headers.get("Retry-After")
                        wait_sec = int(retry_after) if retry_after and retry_after.isdigit() else 10
                        error_text = (
                            f"👁️ Сейчас большая нагрузка на сервер (код 429).\n"
                            f"Лимит для Vision-запросов обычно ниже (~10 в минуту).\n"
                            f"Подождите {wait_sec}–{wait_sec+10} секунд.\n"
                            f"Попытка {attempt+1} из {MAX_GROQ_RETRIES}."
                        )
                        await asyncio.sleep(wait_sec + 2)
                    elif status == 413:
                        return "👁️ Фото слишком большое (31) – сожмите изображение. 😔"
                    elif status == 415:
                        return "👁️ Формат не поддерживается (32) – используйте JPEG/PNG. 😔"
                    elif status >= 500:
                        error_text = f"👁️ Ошибка сервера (30-{status}). Попробуйте позже."
                        await asyncio.sleep(3 * (attempt + 1))
                    else:
                        return f"👁️ Ошибка распознавания (30-{status}) 😔"
                    
                    last_error = error_text
                    
        except asyncio.TimeoutError:
            last_error = "👁️ Таймаут распознавания (30-20) – сервер долго отвечает."
            if attempt < MAX_GROQ_RETRIES - 1:
                await asyncio.sleep(3 * (attempt + 1))
                continue
            else:
                return last_error + " 😔"
                
        except:
            last_error = "👁️ Ошибка распознавания (30-99) – неизвестная проблема."
            if attempt < MAX_GROQ_RETRIES - 1:
                await asyncio.sleep(2)
                continue
            else:
                return last_error + " 😔"
    
    return (last_error or "👁️ Не удалось распознать фото после повторов.") + " 😔"

# === ГЕНЕРАЦИЯ ИЗОБРАЖЕНИЙ (улучшены сообщения) ===
async def generate_image(prompt: str, user_id: int) -> Tuple[bool, str]:
    can, wait = check_rate_limit(user_id, is_image=True)
    if not can:
        return False, f"⏳ Подожди {wait} сек (лимит {IMAGE_WAIT} сек между генерациями)."
    
    enhanced_prompt = f"{prompt}, high quality, detailed, realistic"
    encoded_prompt = quote(enhanced_prompt)
    
    for attempt in range(MAX_IMAGE_RETRIES):
        image_url = f"https://gen.pollinations.ai/image/{encoded_prompt}?key={POLLINATIONS_API_KEY}&model=flux&width=1024&height=1024&seed={int(time.time())}"
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.head(image_url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    status = resp.status
                    
                    if status == 200:
                        return True, image_url
                        
                    elif status == 429:
                        error_msg = (
                            f"⚠️ Сервер генерации перегружен (код 429).\n"
                            f"Pollinations имеет свои лимиты (возможно, 10 запросов в минуту).\n"
                            f"Попробуйте через 15–30 секунд.\n"
                            f"Попытка {attempt+1} из {MAX_IMAGE_RETRIES}."
                        )
                        if attempt < MAX_IMAGE_RETRIES - 1:
                            await asyncio.sleep(5)
                            continue
                        return False, error_msg + " 😊"
                        
                    elif status >= 500:
                        error_msg = f"⚠️ Ошибка сервера генерации (41-{status}). Повторная попытка..."
                        if attempt < MAX_IMAGE_RETRIES - 1:
                            await asyncio.sleep(3)
                            continue
                        return False, f"⚠️ Сервер недоступен (41) – попробуйте позже. 😊"
                        
                    else:
                        error_msg = f"⚠️ Ошибка генерации (40-{status}). Неизвестный код."
                        if attempt < MAX_IMAGE_RETRIES - 1:
                            await asyncio.sleep(2)
                            continue
                        return False, error_msg + " 😊"
                        
        except asyncio.TimeoutError:
            if attempt < MAX_IMAGE_RETRIES - 1:
                await asyncio.sleep(3)
                continue
            return False, "⏱ Таймаут генерации (40-20) – сервер не отвечает. 😊"
            
        except:
            if attempt < MAX_IMAGE_RETRIES - 1:
                await asyncio.sleep(2)
                continue
            return False, "⚠️ Ошибка сети (40-21) – проверьте соединение. 😊"
    
    return False, "⚠️ Не удалось сгенерировать после нескольких попыток (40-99). 😊"

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

👁️ РАСПОЗНАВАНИЕ ФОТО:
• Отправь фото — и я опишу его!

💬 ОБЩЕНИЕ:
• В личке: просто напиши
• В группах: /ask вопрос или ответь на сообщение

🧠 ПАМЯТЬ:
• Помню последние 50 сообщений
• Запоминаю важную информацию
• /clear_memory — забыть всё

⏱ Бесплатно, 10 сек между запросами (для текста), 15 сек для картинок.
"""

async def set_bot_commands():
    commands = [
        BotCommand(command="image", description="🎨 Создать изображение"),
        BotCommand(command="start", description="🚀 Запустить бота"),
        BotCommand(command="menu", description="📋 Показать меню"),
        BotCommand(command="help", description="❓ Помощь и инструкция"),
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
        f"👁️ Могу распознавать фото (просто пришли мне)\n"
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

# === ГЕНЕРАЦИЯ ИЗОБРАЖЕНИЙ ===
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
    
    can, wait = check_rate_limit(message.from_user.id)  # проверка общего лимита
    if not can:
        await message.answer(f"⏳ Подожди {wait} секунд (общий лимит {FREE_WAIT} сек).")
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
        await message.answer("⚠️ Не удалось загрузить (40-22) – возможно, ссылка недействительна. 😊")

# === РАСПОЗНАВАНИЕ ФОТО ===
@dp.message(F.photo)
async def handle_photo(message: Message):
    add_user(message.from_user.id, message.from_user.username)
    
    can, wait = check_rate_limit(message.from_user.id)
    if not can:
        await message.answer(f"⏳ Подожди {wait} секунд (общий лимит).")
        return
    
    thinking_msg = await message.answer("👁️ Смотрю на фото...")
    update_user_stats(message.from_user.id)
    
    try:
        photo = message.photo[-1]
        file_info = await bot.get_file(photo.file_id)
        photo_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_info.file_path}"
        
        question = "Что на этом фото? Опиши подробно на русском языке"
        if message.caption:
            question = f"{message.caption}. Опиши подробно на русском языке"
        
        description = await describe_photo(photo_url, question)
        
        await thinking_msg.delete()
        await message.reply(f"👁️ {description}")
    except Exception as e:
        await thinking_msg.delete()
        logging.error(f"❌ Ошибка получения фото (33): {e}")
        await message.reply("👁️ Ошибка получения фото (33) – не удалось загрузить. 😔")

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
        await message.answer(f"⏳ Подожди {wait} секунд (общий лимит).")
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
        await message.answer(f"⏳ Подожди {wait} секунд (общий лимит).")
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
        await message.answer(f"⏳ Подожди {wait} секунд (общий лимит).")
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
                "⏱ Задержка между генерациями: 15 секунд\n\n"
                "👁️ Также можешь отправить мне фото — я опишу его!",
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
            f"👁️ Vision: {VISION_MODEL}\n"
            f"⏱ Задержка текста: {FREE_WAIT}с\n"
            f"⏱ Задержка изображений: {IMAGE_WAIT}с\n"
            f"🔑 Ключей Groq: {len(GROQ_API_KEYS)}")
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
    print(f"👁️ Распознавание фото: активировано")
    print(f"⏱ Задержка текста: {FREE_WAIT}с")
    print(f"⏱ Задержка изображений: {IMAGE_WAIT}с")
    print(f"🔑 Ключей Groq: {len(GROQ_API_KEYS)}")
    print("=" * 50)
    
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 Бот остановлен")
