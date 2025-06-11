import logging
import asyncio
import os
import sqlite3
from datetime import datetime
from aiogram import Bot, Dispatcher, types, F
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.filters import Command
from aiogram.types import (
    InlineKeyboardMarkup, 
    InlineKeyboardButton, 
    KeyboardButton, 
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    FSInputFile
)
from dotenv import load_dotenv

# Загрузка переменных окружения из .env файла
load_dotenv()

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Инициализация бота и диспетчера
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise Exception("BOT_TOKEN не найден в .env файле")

# ID администраторов, которые будут получать уведомления о новых заказах
ADMIN_IDS = [int(id) for id in os.getenv("ADMIN_IDS", "").split(",") if id]
if not ADMIN_IDS:
    logger.warning("ADMIN_IDS не найдены в .env файле. Уведомления не будут отправляться")

# Инициализация бота и диспетчера
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# Пути к файлам
DB_PATH = "coffee_shop.db"
IMAGES_DIR = "images"

# Создаем директорию для изображений, если она не существует
os.makedirs(IMAGES_DIR, exist_ok=True)

# Класс для работы с базой данных
class Database:
    def __init__(self, db_file):
        self.conn = sqlite3.connect(db_file)
        self.cursor = self.conn.cursor()
        
    def setup(self):
        # Создание необходимых таблиц, если они не существуют
        self.cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            full_name TEXT,
            phone TEXT,
            registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        ''')
        
        self.cursor.execute('''
        CREATE TABLE IF NOT EXISTS categories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            description TEXT,
            image_path TEXT
        )
        ''')
        
        self.cursor.execute('''
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category_id INTEGER,
            name TEXT NOT NULL,
            description TEXT,
            price REAL NOT NULL,
            image_path TEXT,
            available BOOLEAN DEFAULT 1,
            FOREIGN KEY (category_id) REFERENCES categories (id)
        )
        ''')
        
        self.cursor.execute('''
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            status TEXT NOT NULL,
            total_price REAL NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            payment_method TEXT,
            pickup_time TEXT,
            FOREIGN KEY (user_id) REFERENCES users (user_id)
        )
        ''')
        
        self.cursor.execute('''
        CREATE TABLE IF NOT EXISTS order_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER,
            product_id INTEGER,
            quantity INTEGER NOT NULL,
            price REAL NOT NULL,
            FOREIGN KEY (order_id) REFERENCES orders (id),
            FOREIGN KEY (product_id) REFERENCES products (id)
        )
        ''')
        
        self.cursor.execute('''
        CREATE TABLE IF NOT EXISTS reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            text TEXT NOT NULL,
            rating INTEGER NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (user_id)
        )
        ''')
        
        # Добавляем базовые категории, если их нет
        self.cursor.execute("SELECT COUNT(*) FROM categories")
        if self.cursor.fetchone()[0] == 0:
            categories = [
                ("Кофе", "Различные виды кофе", "coffee.jpg"),
                ("Напитки", "Разнообразные чайные напитки", "tea.jpg"),
                ("Еда", "Сладости и выпечка", "desserts.jpg"),
        
            ]
            self.cursor.executemany("INSERT INTO categories (name, description, image_path) VALUES (?, ?, ?)", categories)
            
            # Добавляем базовые продукты
            products = [
                (1, "Американо 200мл", "Классический черный кофе", 200, "americano.jpg", 1),
                (1, "Капучино 200мл", "Кофе с молочной пенкой", 230, "cappuccino.jpg", 1),
                (1, "Латте 300мл", "Кофе с молоком", 260, "latte.jpg", 1),
                (2, "Милкшейк Шоколад", "Освежающий зеленый чай", 300, "green_tea.jpg", 1),
                (2, "Милкшейк Шоколад", "Крепкий черный чай", 300, "black_tea.jpg", 1),
                (3, "Пончик Шоколад", "Нежный десерт с творожной начинкой", 180, "cheesecake.jpg", 1),
                (3, "Круассан Классический", "Хрустящая выпечка", 180, "croissant.jpg", 1),
                (4, "Сэндвич с курицей", "Сэндвич с куриным филе", 180, "chicken_sandwich.jpg", 1)
            ]
            self.cursor.executemany("INSERT INTO products (category_id, name, description, price, image_path, available) VALUES (?, ?, ?, ?, ?, ?)", products)
            
        self.conn.commit()
            
    def register_user(self, user_id, username, full_name, phone=None):
        self.cursor.execute(
            "INSERT OR REPLACE INTO users (user_id, username, full_name, phone) VALUES (?, ?, ?, ?)",
            (user_id, username, full_name, phone)
        )
        self.conn.commit()
        
    def get_user(self, user_id):
        self.cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
        return self.cursor.fetchone()
        
    def get_categories(self):
        self.cursor.execute("SELECT id, name, description, image_path FROM categories")
        return self.cursor.fetchall()
        
    def get_products_by_category(self, category_id):
        self.cursor.execute(
            "SELECT id, name, description, price, image_path FROM products WHERE category_id = ? AND available = 1",
            (category_id,)
        )
        return self.cursor.fetchall()
        
    def get_product_by_id(self, product_id):
        self.cursor.execute(
            "SELECT id, name, description, price, image_path FROM products WHERE id = ?",
            (product_id,)
        )
        return self.cursor.fetchone()
        
    def create_order(self, user_id, cart, payment_method, pickup_time):
        total_price = sum(item['price'] * item['quantity'] for item in cart)
        
        # Создаем заказ
        self.cursor.execute(
            "INSERT INTO orders (user_id, status, total_price, payment_method, pickup_time) VALUES (?, ?, ?, ?, ?)",
            (user_id, "новый", total_price, payment_method, pickup_time)
        )
        order_id = self.cursor.lastrowid
        
        # Добавляем позиции заказа
        for item in cart:
            self.cursor.execute(
                "INSERT INTO order_items (order_id, product_id, quantity, price) VALUES (?, ?, ?, ?)",
                (order_id, item['product_id'], item['quantity'], item['price'])
            )
            
        self.conn.commit()
        return order_id
        
    def get_order_details(self, order_id):
        # Получаем основную информацию о заказе
        self.cursor.execute(
            "SELECT id, user_id, status, total_price, created_at, payment_method, pickup_time FROM orders WHERE id = ?",
            (order_id,)
        )
        order = self.cursor.fetchone()
        
        if not order:
            return None
            
        # Получаем позиции заказа
        self.cursor.execute(
            """
            SELECT oi.product_id, p.name, oi.quantity, oi.price 
            FROM order_items oi 
            JOIN products p ON oi.product_id = p.id 
            WHERE oi.order_id = ?
            """,
            (order_id,)
        )
        items = self.cursor.fetchall()
        
        return {
            "id": order[0],
            "user_id": order[1],
            "status": order[2],
            "total_price": order[3],
            "created_at": order[4],
            "payment_method": order[5],
            "pickup_time": order[6],
            "items": items
        }
        
    def add_review(self, user_id, text, rating):
        self.cursor.execute(
            "INSERT INTO reviews (user_id, text, rating) VALUES (?, ?, ?)",
            (user_id, text, rating)
        )
        self.conn.commit()
        
    def close(self):
        self.conn.close()

# Инициализация базы данных
db = Database(DB_PATH)
db.setup()

# Состояния для FSM
class OrderStates(StatesGroup):
    choosing_category = State()
    choosing_product = State()
    adding_to_cart = State()
    viewing_cart = State()
    checkout = State()
    payment_method = State()
    pickup_time = State()
    confirming_order = State()

class FeedbackStates(StatesGroup):
    waiting_for_text = State()
    waiting_for_rating = State()

class ContactAdminStates(StatesGroup):
    waiting_for_message = State()

class RegistrationStates(StatesGroup):
    waiting_for_phone = State()

# Функции для клавиатур
def get_main_keyboard():
    """Создает основную клавиатуру с главным меню"""
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🍽 Меню")],
            [KeyboardButton(text="🛒 Корзина"), KeyboardButton(text="📝 Мои заказы")],
            [KeyboardButton(text="⭐ Оставить отзыв"), KeyboardButton(text="📨 Связаться с администратором")],
            [KeyboardButton(text="📍 Где нас найти"), KeyboardButton(text="📣 Наш канал")],
        ],
        resize_keyboard=True
    )
    return keyboard

def get_categories_keyboard(categories):
    """Создает клавиатуру со списком категорий"""
    buttons = []
    for category in categories:
        buttons.append([InlineKeyboardButton(text=category[1], callback_data=f"category:{category[0]}")])
    
    buttons.append([InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_products_keyboard(products, category_id):
    """Создает клавиатуру со списком продуктов в категории"""
    buttons = []
    for product in products:
        buttons.append([InlineKeyboardButton(text=f"{product[1]} - {product[3]} ₽", callback_data=f"product:{product[0]}")])
    
    buttons.append([InlineKeyboardButton(text="🔙 К категориям", callback_data="back_to_categories")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_product_keyboard(product_id, in_cart=False):
    """Создает клавиатуру для конкретного продукта"""
    buttons = [
        [
            InlineKeyboardButton(text="➖", callback_data=f"decrease:{product_id}"),
            InlineKeyboardButton(text="1", callback_data="quantity"),
            InlineKeyboardButton(text="➕", callback_data=f"increase:{product_id}")
        ],
        [InlineKeyboardButton(text="🛒 Добавить в корзину", callback_data=f"add_to_cart:{product_id}")]
    ]
    if in_cart:
        buttons.append([InlineKeyboardButton(text="🗑 Удалить из корзины", callback_data=f"remove_from_cart:{product_id}")])
    
    buttons.append([InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_products")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_cart_keyboard():
    """Создает клавиатуру для корзины"""
    buttons = [
        [InlineKeyboardButton(text="🧹 Очистить корзину", callback_data="clear_cart")],
        [InlineKeyboardButton(text="💳 Оформить заказ", callback_data="checkout")],
        [InlineKeyboardButton(text="🔙 Вернуться к меню", callback_data="back_to_menu")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_payment_method_keyboard():
    """Создает клавиатуру для выбора способа оплаты"""
    buttons = [
        [InlineKeyboardButton(text="💵 Наличными при получении", callback_data="payment:cash")],
        [InlineKeyboardButton(text="💳 Картой", callback_data="payment:card")],
        [InlineKeyboardButton(text="📱 СБП", callback_data="payment:sbp")],
        [InlineKeyboardButton(text="🔙 Назад к корзине", callback_data="back_to_cart")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_pickup_time_keyboard():
    """Создает клавиатуру для выбора времени получения"""
    current_hour = datetime.now().hour
    current_minute = datetime.now().minute
    
    # Округляем до ближайших 15 минут
    if current_minute < 10:
        next_minute = 10
    elif current_minute < 30:
        next_minute = 30
    elif current_minute < 40:
        next_minute = 40
    else:
        next_minute = 0
        current_hour = (current_hour + 1) % 24
        
    times = []
    for i in range(4):  # Предлагаем 4 временных слота
        hour = (current_hour + ((next_minute + i * 10) // 60)) % 24
        minute = (next_minute + i * 10) % 60
        time_str = f"{hour:02d}:{minute:02d}"
        times.append(time_str)
    
    buttons = []
    for time_str in times:
        buttons.append([InlineKeyboardButton(text=time_str, callback_data=f"time:{time_str}")])
    
    buttons.append([InlineKeyboardButton(text="🕒 Как можно скорее", callback_data="time:asap")])
    buttons.append([InlineKeyboardButton(text="🔙 Назад к выбору оплаты", callback_data="back_to_payment")])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_confirm_order_keyboard():
    """Создает клавиатуру для подтверждения заказа"""
    buttons = [
        [
            InlineKeyboardButton(text="✅ Подтвердить", callback_data="confirm_order"),
            InlineKeyboardButton(text="❌ Отменить", callback_data="cancel_order")
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_rating_keyboard():
    """Создает клавиатуру для выставления рейтинга"""
    buttons = []
    for rating in range(1, 6):
        buttons.append(InlineKeyboardButton(text="⭐" * rating, callback_data=f"rating:{rating}"))
    
    return InlineKeyboardMarkup(inline_keyboard=[buttons])

# Хэндлеры команд
@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    """Обработчик команды /start"""
    user_id = message.from_user.id
    username = message.from_user.username
    full_name = message.from_user.full_name
    
    # Регистрация пользователя в базе данных
    db.register_user(user_id, username, full_name)
    
    await state.clear()
    
    welcome_text = (
        f"👋 Привет, {full_name}!\n\n"
        "Добро пожаловать в бот нашей кофейни Playa!\n"
        "Здесь вы можете ознакомиться с нашим меню, сделать заказ, "
        "оставить отзыв или связаться с администратором.\n\n"
        "Выберите один из пунктов в меню ниже 👇"
    )
    
    await message.answer(welcome_text, reply_markup=get_main_keyboard())

@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    """Обработчик команды /help"""
    help_text = (
        "🆘 Помощь по использованию бота:\n\n"
        "🍽 <b>Меню</b> - просмотр меню и заказ продуктов\n"
        "🛒 <b>Корзина</b> - просмотр выбранных товаров\n"
        "📝 <b>Мои заказы</b> - история ваших заказов\n"
        "⭐ <b>Оставить отзыв</b> - оценить наше обслуживание\n"
        "📨 <b>Связаться с администратором</b> - задать вопрос\n"
        "📍 <b>Где нас найти</b> - адрес и карта проезда\n"
        "📣 <b>Наш канал</b> - последние новости и акции\n\n"
        "Команды:\n"
        "/start - начать взаимодействие с ботом\n"
        "/menu - открыть меню\n"
        "/cart - перейти к корзине\n"
        "/help - показать эту справку"
    )
    
    await message.answer(help_text, parse_mode="HTML")

@dp.message(Command("menu"))
async def cmd_menu(message: types.Message, state: FSMContext):
    """Обработчик команды /menu"""
    await show_categories(message, state)

@dp.message(Command("cart"))
async def cmd_cart(message: types.Message, state: FSMContext):
    """Обработчик команды /cart"""
    await show_cart(message, state)

# Обработчики сообщений
@dp.message(F.text == "🍽 Меню")
async def menu_button(message: types.Message, state: FSMContext):
    """Обработчик нажатия на кнопку Меню"""
    await show_categories(message, state)

@dp.message(F.text == "🛒 Корзина")
async def cart_button(message: types.Message, state: FSMContext):
    """Обработчик нажатия на кнопку Корзина"""
    await show_cart(message, state)

@dp.message(F.text == "📝 Мои заказы")
async def my_orders_button(message: types.Message, state: FSMContext):
    """Обработчик нажатия на кнопку Мои заказы"""
    # Здесь должна быть логика получения заказов пользователя из БД
    await message.answer(
        "В настоящее время история заказов не доступна. Пожалуйста, попробуйте позже.",
        reply_markup=get_main_keyboard()
    )

@dp.message(F.text == "⭐ Оставить отзыв")
async def feedback_button(message: types.Message, state: FSMContext):
    """Обработчик нажатия на кнопку Оставить отзыв"""
    await state.set_state(FeedbackStates.waiting_for_text)
    await message.answer(
        "Пожалуйста, напишите ваш отзыв о нашей кофейне. Нам очень важно ваше мнение!",
        reply_markup=ReplyKeyboardRemove()
    )

@dp.message(FeedbackStates.waiting_for_text)
async def process_feedback_text(message: types.Message, state: FSMContext):
    """Обработчик получения текста отзыва"""
    await state.update_data(feedback_text=message.text)
    await state.set_state(FeedbackStates.waiting_for_rating)
    await message.answer(
        "Спасибо за ваш отзыв! Теперь оцените наш сервис от 1 до 5 звезд:",
        reply_markup=get_rating_keyboard()
    )

@dp.callback_query(FeedbackStates.waiting_for_rating, F.data.startswith("rating:"))
async def process_feedback_rating(callback: types.CallbackQuery, state: FSMContext):
    """Обработчик выбора рейтинга"""
    rating = int(callback.data.split(":")[1])
    data = await state.get_data()
    feedback_text = data.get("feedback_text", "")
    
    # Сохраняем отзыв в базе данных
    db.add_review(callback.from_user.id, feedback_text, rating)
    
    await state.clear()
    
    # Отправляем уведомление администраторам
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(
                admin_id,
                f"📝 Новый отзыв от пользователя {callback.from_user.full_name}:\n\n"
                f"{feedback_text}\n\n"
                f"Оценка: {'⭐' * rating}"
            )
        except Exception as e:
            logger.error(f"Ошибка при отправке уведомления администратору {admin_id}: {e}")
    
    await callback.message.answer(
        "Спасибо за ваш отзыв! Мы ценим ваше мнение и постоянно работаем над улучшением нашего сервиса.",
        reply_markup=get_main_keyboard()
    )
    await callback.answer()

@dp.message(F.text == "📨 Связаться с администратором")
async def contact_admin_button(message: types.Message, state: FSMContext):
    """Обработчик нажатия на кнопку Связаться с администратором"""
    await state.set_state(ContactAdminStates.waiting_for_message)
    await message.answer(
        "Пожалуйста, напишите ваше сообщение для администратора кофейни.",
        reply_markup=ReplyKeyboardRemove()
    )

@dp.message(ContactAdminStates.waiting_for_message)
async def process_admin_message(message: types.Message, state: FSMContext):
    """Обработчик получения сообщения для администратора"""
    user_id = message.from_user.id
    user_fullname = message.from_user.full_name
    user_text = message.text
    
    # Отправляем сообщение администраторам
    for admin_id in ADMIN_IDS:
        try:
            admin_message = (
                f"📩 Сообщение от пользователя {user_fullname} (ID: {user_id}):\n\n"
                f"{user_text}"
            )
            await bot.send_message(admin_id, admin_message)
        except Exception as e:
            logger.error(f"Ошибка при отправке сообщения администратору {admin_id}: {e}")
    
    await state.clear()
    await message.answer(
        "Спасибо! Ваше сообщение было отправлено администратору кофейни. "
        "Мы свяжемся с вами, если потребуется дополнительная информация.",
        reply_markup=get_main_keyboard()
    )

@dp.message(F.text == "📍 Где нас найти")
async def location_button(message: types.Message):
    """Обработчик нажатия на кнопку Где нас найти"""
    location_text = (
        "🏠 <b>Playa Coffee Club</b>\n\n"
        "📍 Адрес: КП Спас-каменка, 30м слева за постом охраны\n"
        "⏱ Режим работы: будни с 10:00 до 20:00, выходные с 10:00 до 21:00\n\n"
        "📞 Телефон: "
    )
    
    location_keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🗺 Открыть на Яндекс.Картах", url="https://yandex.ru/maps")]
        ]
    )
    
    await message.answer(location_text, reply_markup=location_keyboard, parse_mode="HTML")

@dp.message(F.text == "📣 Наш канал")
async def channel_button(message: types.Message):
    """Обработчик нажатия на кнопку Наш канал"""
    channel_text = (
        "📣 <b>Наш официальный канал</b>\n\n"
        "Подписывайтесь, чтобы быть в курсе:\n"
        "• Новых акций и специальных предложений\n"
        "• Новинок меню\n"
        "• Мероприятий и событий\n"
        "• Изменений в графике работы"
    )
    
    channel_keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📱 Подписаться на канал", url="https://t.me/playacoffee")]
        ]
    )
    
    await message.answer(channel_text, reply_markup=channel_keyboard, parse_mode="HTML")

# Основные функции для работы с меню и заказами
async def show_categories(message: types.Union[types.Message, types.CallbackQuery], state: FSMContext):
    """Показывает список категорий"""
    if isinstance(message, types.CallbackQuery):
        message = message.message
    
    await state.set_state(OrderStates.choosing_category)
    categories = db.get_categories()
    
    text = "🍽 <b>Меню кофейни</b>\n\nВыберите категорию:"
    await message.answer(text, reply_markup=get_categories_keyboard(categories), parse_mode="HTML")

async def show_products(callback: types.CallbackQuery, state: FSMContext, category_id: int):
    """Показывает список продуктов в категории"""
    products = db.get_products_by_category(category_id)
    category_name = next((cat[1] for cat in db.get_categories() if cat[0] == category_id), "Категория")
    
    await state.update_data(current_category_id=category_id)
    await state.set_state(OrderStates.choosing_product)
    
    text = f"<b>{category_name}</b>\n\nВыберите продукт:"
    await callback.message.edit_text(
        text,
        reply_markup=get_products_keyboard(products, category_id),
        parse_mode="HTML"
    )

async def show_product_details(callback: types.CallbackQuery, state: FSMContext, product_id: int):
    """Показывает детали конкретного продукта"""
    product = db.get_product_by_id(product_id)
    if not product:
        await callback.answer("Продукт не найден")
        return
        
    product_id, name, description, price, image_path = product
    
    await state.update_data(current_product_id=product_id)
    await state.update_data(current_product_name=name)
    await state.update_data(current_product_price=price)
    await state.update_data(current_quantity=1)
    await state.set_state(OrderStates.adding_to_cart)
    
    # Проверяем наличие продукта в корзине
    data = await state.get_data()
    cart = data.get("cart", [])
    in_cart = any(item["product_id"] == product_id for item in cart)
    
    text = f"<b>{name}</b>\n\n{description}\n\nЦена: {price} ₽"
    
    # Если есть изображение, отправляем его
    if image_path and os.path.exists(os.path.join(IMAGES_DIR, image_path)):
        photo = FSInputFile(os.path.join(IMAGES_DIR, image_path))
        try:
            await callback.message.delete()  # Удаляем предыдущее сообщение
            await callback.message.answer_photo(
                photo=photo,
                caption=text,
                reply_markup=get_product_keyboard(product_id, in_cart),
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Ошибка при отправке изображения: {e}")
            await callback.message.edit_text(
                text,
                reply_markup=get_product_keyboard(product_id, in_cart),
                parse_mode="HTML"
            )
    else:
        await callback.message.edit_text(
            text,
            reply_markup=get_product_keyboard(product_id, in_cart),
            parse_mode="HTML"
        )

async def update_product_quantity(callback: types.CallbackQuery, state: FSMContext, change: int):
    """Обновляет количество продукта"""
    data = await state.get_data()
    current_quantity = data.get("current_quantity", 1)
    
    new_quantity = max(1, current_quantity + change)
    await state.update_data(current_quantity=new_quantity)
    
    # Обновляем кнопку с количеством
    current_markup = callback.message.reply_markup
    for row in current_markup.inline_keyboard:
        for button in row:
            if button.callback_data == "quantity":
                button.text = str(new_quantity)
    
    await callback.message.edit_reply_markup(reply_markup=current_markup)
    await callback.answer()

async def add_to_cart(callback: types.CallbackQuery, state: FSMContext):
    """Добавляет продукт в корзину"""
    data = await state.get_data()
    product_id = data.get("current_product_id")
    product_name = data.get("current_product_name")
    product_price = data.get("current_product_price")
    quantity = data.get("current_quantity", 1)
    
    cart = data.get("cart", [])
    
    # Проверяем, есть ли уже товар в корзине
    for item in cart:
        if item["product_id"] == product_id:
            item["quantity"] = quantity
            await state.update_data(cart=cart)
            await callback.answer(f"Количество {product_name} обновлено!")
            return
    
    # Добавляем новый товар
    cart.append({
        "product_id": product_id,
        "name": product_name,
        "price": product_price,
        "quantity": quantity
    })
    
    await state.update_data(cart=cart)
    await callback.answer(f"{product_name} добавлен в корзину!")
    
    # Обновляем клавиатуру, чтобы показать кнопку удаления
    current_markup = callback.message.reply_markup
    if not any("remove_from_cart" in button.callback_data for row in current_markup.inline_keyboard for button in row):
        remove_button = [InlineKeyboardButton(text="🗑 Удалить из корзины", callback_data=f"remove_from_cart:{product_id}")]
        current_markup.inline_keyboard.insert(-1, remove_button)
        await callback.message.edit_reply_markup(reply_markup=current_markup)

async def remove_from_cart(callback: types.CallbackQuery, state: FSMContext, product_id: int):
    """Удаляет продукт из корзины"""
    data = await state.get_data()
    cart = data.get("cart", [])
    
    # Находим и удаляем товар из корзины
    cart = [item for item in cart if item["product_id"] != product_id]
    
    await state.update_data(cart=cart)
    await callback.answer("Товар удален из корзины")
    
    # Обновляем клавиатуру, чтобы убрать кнопку удаления
    current_markup = callback.message.reply_markup
    current_markup.inline_keyboard = [row for row in current_markup.inline_keyboard if not any("remove_from_cart" in button.callback_data for button in row)]
    await callback.message.edit_reply_markup(reply_markup=current_markup)

async def show_cart(message: types.Union[types.Message, types.CallbackQuery], state: FSMContext):
    """Показывает содержимое корзины"""
    if isinstance(message, types.CallbackQuery):
        callback = message
        message = callback.message
        await callback.answer()
    
    data = await state.get_data()
    cart = data.get("cart", [])
    
    if not cart:
        await message.answer("Ваша корзина пуста. Выберите продукты в меню!", reply_markup=get_main_keyboard())
        return
    
    total_price = sum(item["price"] * item["quantity"] for item in cart)
    
    cart_text = "🛒 <b>Ваша корзина:</b>\n\n"
    for item in cart:
        cart_text += f"• {item['name']} x {item['quantity']} = {item['price'] * item['quantity']} ₽\n"
    
    cart_text += f"\n<b>Итого к оплате:</b> {total_price} ₽"
    
    await state.set_state(OrderStates.viewing_cart)
    
    try:
        if isinstance(message, types.CallbackQuery):
            await message.edit_text(cart_text, reply_markup=get_cart_keyboard(), parse_mode="HTML")
        else:
            await message.answer(cart_text, reply_markup=get_cart_keyboard(), parse_mode="HTML")
    except Exception as e:
        logger.error(f"Ошибка при отображении корзины: {e}")
        await message.answer(cart_text, reply_markup=get_cart_keyboard(), parse_mode="HTML")

async def checkout(callback: types.CallbackQuery, state: FSMContext):
    """Начинает процесс оформления заказа"""
    data = await state.get_data()
    cart = data.get("cart", [])
    
    if not cart:
        await callback.answer("Ваша корзина пуста")
        return
    
    await state.set_state(OrderStates.payment_method)
    await callback.message.edit_text(
        "Выберите способ оплаты:",
        reply_markup=get_payment_method_keyboard()
    )
    await callback.answer()

async def select_payment_method(callback: types.CallbackQuery, state: FSMContext):
    """Обрабатывает выбор способа оплаты"""
    payment_method = callback.data.split(":")[1]
    
    payment_methods = {
        "cash": "Наличными",
        "card": "Картой",
        "sbp": "СБП (Система быстрых платежей)"
    }
    
    await state.update_data(payment_method=payment_methods.get(payment_method, payment_method))
    await state.set_state(OrderStates.pickup_time)
    
    await callback.message.edit_text(
        "Выберите время получения заказа:",
        reply_markup=get_pickup_time_keyboard()
    )
    await callback.answer()

async def select_pickup_time(callback: types.CallbackQuery, state: FSMContext):
    """Обрабатывает выбор времени получения"""
    pickup_time = callback.data.split(":")[1]
    
    if pickup_time == "asap":
        pickup_time = "Как можно скорее"
    
    await state.update_data(pickup_time=pickup_time)
    await state.set_state(OrderStates.confirming_order)
    
    # Формируем сообщение с деталями заказа
    data = await state.get_data()
    cart = data.get("cart", [])
    payment_method = data.get("payment_method")
    
    total_price = sum(item["price"] * item["quantity"] for item in cart)
    
    order_text = "📋 <b>Подтверждение заказа:</b>\n\n"
    for item in cart:
        order_text += f"• {item['name']} x {item['quantity']} = {item['price'] * item['quantity']} ₽\n"
    
    order_text += f"\n<b>Итого к оплате:</b> {total_price} ₽\n"
    order_text += f"<b>Способ оплаты:</b> {payment_method}\n"
    order_text += f"<b>Время получения:</b> {pickup_time}\n\n"
    order_text += "Пожалуйста, проверьте детали заказа и подтвердите."
    
    await callback.message.edit_text(
        order_text,
        reply_markup=get_confirm_order_keyboard(),
        parse_mode="HTML"
    )
    await callback.answer()

async def confirm_order(callback: types.CallbackQuery, state: FSMContext):
    """Подтверждает и создает заказ"""
    data = await state.get_data()
    cart = data.get("cart", [])
    payment_method = data.get("payment_method")
    pickup_time = data.get("pickup_time")
    
    if not cart:
        await callback.answer("Ваша корзина пуста")
        return
    
    # Сохраняем заказ в базе данных
    user_id = callback.from_user.id
    order_id = db.create_order(user_id, cart, payment_method, pickup_time)
    
    # Получаем информацию о заказе
    order = db.get_order_details(order_id)
    
    # Формируем сообщение для пользователя
    confirmation_text = (
        f"✅ <b>Ваш заказ #{order_id} успешно принят!</b>\n\n"
        f"<b>Статус:</b> {order['status']}\n"
        f"<b>Сумма:</b> {order['total_price']} ₽\n"
        f"<b>Оплата:</b> {order['payment_method']}\n"
        f"<b>Время получения:</b> {order['pickup_time']}\n\n"
        f"Спасибо за заказ! Мы уведомим вас, когда он будет готов."
    )
    
    await callback.message.edit_text(confirmation_text, parse_mode="HTML")
    
    # Формируем сообщение для администраторов
    admin_text = (
        f"🆕 <b>Новый заказ #{order_id}</b>\n\n"
        f"<b>Клиент:</b> {callback.from_user.full_name} (@{callback.from_user.username})\n"
        f"<b>Сумма:</b> {order['total_price']} ₽\n"
        f"<b>Оплата:</b> {order['payment_method']}\n"
        f"<b>Время получения:</b> {order['pickup_time']}\n\n"
        f"<b>Заказ:</b>\n"
    )
    
    for item_id, item_name, quantity, price in order['items']:
        admin_text += f"• {item_name} x {quantity} = {price * quantity} ₽\n"
    
    # Отправляем уведомления администраторам
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, admin_text, parse_mode="HTML")
        except Exception as e:
            logger.error(f"Ошибка при отправке уведомления администратору {admin_id}: {e}")
    
    # Очищаем корзину
    await state.update_data(cart=[])
    await state.clear()
    
    # Отправляем сообщение с предложением вернуться в меню
    await callback.message.answer(
        "Вы можете вернуться в главное меню или сделать новый заказ.",
        reply_markup=get_main_keyboard()
    )
    await callback.answer()

async def cancel_order(callback: types.CallbackQuery, state: FSMContext):
    """Отменяет процесс оформления заказа"""
    await state.clear()
    await callback.message.edit_text("Заказ отменен. Вы можете вернуться в меню или создать новый заказ.")
    await callback.message.answer("Выберите действие:", reply_markup=get_main_keyboard())
    await callback.answer()

# Обработчики callback-запросов
@dp.callback_query(F.data == "back_to_main")
async def process_back_to_main(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.delete()
    await callback.message.answer("Главное меню:", reply_markup=get_main_keyboard())
    await callback.answer()

@dp.callback_query(F.data == "back_to_menu")
async def process_back_to_menu(callback: types.CallbackQuery, state: FSMContext):
    await show_categories(callback, state)
    await callback.answer()

@dp.callback_query(F.data == "back_to_categories")
async def process_back_to_categories(callback: types.CallbackQuery, state: FSMContext):
    await show_categories(callback, state)
    await callback.answer()

@dp.callback_query(F.data == "back_to_products")
async def process_back_to_products(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    category_id = data.get("current_category_id")
    if category_id:
        await show_products(callback, state, category_id)
    else:
        await show_categories(callback, state)
    await callback.answer()

@dp.callback_query(F.data == "back_to_cart")
async def process_back_to_cart(callback: types.CallbackQuery, state: FSMContext):
    await show_cart(callback, state)
    await callback.answer()

@dp.callback_query(F.data == "back_to_payment")
async def process_back_to_payment(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(OrderStates.payment_method)
    await callback.message.edit_text(
        "Выберите способ оплаты:",
        reply_markup=get_payment_method_keyboard()
    )
    await callback.answer()

@dp.callback_query(F.data.startswith("category:"))
async def process_category_selection(callback: types.CallbackQuery, state: FSMContext):
    category_id = int(callback.data.split(":")[1])
    await show_products(callback, state, category_id)
    await callback.answer()

@dp.callback_query(F.data.startswith("product:"))
async def process_product_selection(callback: types.CallbackQuery, state: FSMContext):
    product_id = int(callback.data.split(":")[1])
    await show_product_details(callback, state, product_id)
    await callback.answer()

@dp.callback_query(F.data.startswith("increase:"))
async def process_increase_quantity(callback: types.CallbackQuery, state: FSMContext):
    await update_product_quantity(callback, state, 1)

@dp.callback_query(F.data.startswith("decrease:"))
async def process_decrease_quantity(callback: types.CallbackQuery, state: FSMContext):
    await update_product_quantity(callback, state, -1)

@dp.callback_query(F.data.startswith("add_to_cart:"))
async def process_add_to_cart(callback: types.CallbackQuery, state: FSMContext):
    await add_to_cart(callback, state)

@dp.callback_query(F.data.startswith("remove_from_cart:"))
async def process_remove_from_cart(callback: types.CallbackQuery, state: FSMContext):
    product_id = int(callback.data.split(":")[1])
    await remove_from_cart(callback, state, product_id)

@dp.callback_query(F.data == "clear_cart")
async def process_clear_cart(callback: types.CallbackQuery, state: FSMContext):
    await state.update_data(cart=[])
    await callback.message.edit_text("Корзина очищена. Выберите товары из меню.")
    await callback.answer("Корзина очищена")

@dp.callback_query(F.data == "checkout")
async def process_checkout(callback: types.CallbackQuery, state: FSMContext):
    await checkout(callback, state)

@dp.callback_query(F.data.startswith("payment:"))
async def process_payment_selection(callback: types.CallbackQuery, state: FSMContext):
    await select_payment_method(callback, state)

@dp.callback_query(F.data.startswith("time:"))
async def process_time_selection(callback: types.CallbackQuery, state: FSMContext):
    await select_pickup_time(callback, state)

@dp.callback_query(F.data == "confirm_order")
async def process_order_confirmation(callback: types.CallbackQuery, state: FSMContext):
    await confirm_order(callback, state)

@dp.callback_query(F.data == "cancel_order")
async def process_order_cancellation(callback: types.CallbackQuery, state: FSMContext):
    await cancel_order(callback, state)

# Запуск бота
async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
