# Файл DATABASE.PY
# -*- coding: utf-8 -*-
"""
Модуль database.py – слой доступа к данным SQLite3.

Содержит:
- Класс DatabaseConnectionContext для потокобезопасных транзакций.
- Функции инициализации таблиц (init_db).
- CRUD операции для настроек групп (group_settings), шлюзов (report_gateways),
  пользователей (users), участников групп (group_members).
- Функции синхронизации участников с Telegram API.

Все функции используют контекстный менеджер для автоматического открытия,
коммита и закрытия соединения. Блокировка db_lock гарантирует целостность
данных при многопоточном пуллинге.
"""

import sqlite3  # Импортируем стандартный легковесный модуль для работы с БД SQLite3
import threading  # Импортируем потоки для реализации потокобезопасного локального хранилища (Lock)
import os
import uuid # Добавляем стандартную библиотеку UUID для работы крипто-токенов

from logger_config import logger

# Создаем объект блокировки для предотвращения конфликтов одновременной записи из разных потоков пуллинга
db_lock = threading.Lock()
class DatabaseConnectionContext: # Объявляем класс контекстного менеджера для потокобезопасного управления сессиями СУБД
    """ Инфраструктурный класс для автоматического открытия, коммита и закрытия соединений SQLite3 по SOLID. """
    def __init__(self, db_path, lock): # Конструктор класса инициализирует пути к физическому файлу и глобальный мьютекс
        self.db_path = db_path # Сохраняем абсолютный путь к файлу базы данных внутри экземпляра класса
        self.lock = lock # Привязываем объект потоковой блокировки threading.Lock для предотвращения Race Condition
        self.conn = None # Инициализируем внутреннюю переменную удержания активного соединения как пустую

    def __enter__(self): # Магический метод инициализации контекста при входе в операторную конструкцию with
        self.lock.acquire() # Жестко блокируем текущий поток пуллинга для обеспечения монопольного доступа к диску
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False) # Открываем физическое соединение с файлом SQLite3
        return self.conn.cursor() # Создаем и возвращаем активный курсор для выполнения SQL-команд в бизнес-логике

    def __exit__(self, exc_type, exc_val, exc_tb): # Магический метод автоматического закрытия контекста при выходе из with
        if self.conn: # Проверяем, было ли успешно инициализировано физическое соединение с базой данных
            if not exc_type: # Если внутри защищенного блока with не произошло аварийных Runtime-исключений
                self.conn.commit() # Вызываем фиксацию транзакции для персистентной записи изменений на диск Amvera
            self.conn.close() # Безопасно закрываем дескриптор соединения, освобождая ресурсы операционной системы
        self.lock.release() # Разблокируем мьютекс, открывая доступ к транзакциям для других параллельных потоков





# --- ПРАВИЛЬНАЯ НАСТРОЙКА АБСОЛЮТНЫХ ПУТЕЙ ДЛЯ AMVERA ---

# Отыскиваем гарантированную системную папку персистентного хранилища Amvera в корне контейнера
# Если папка /data отсутствует (например, при локальном тестировании на ПК), откатываемся на папку проекта
if os.path.exists("/data"):
    # Используем строго выделенный Amvera внешний диск для постоянного хранения файлов
    DB_FOLDER = "/data"
else:
    # Локальная отладка: создаем папку data прямо в корневой директории скрипта
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    DB_FOLDER = os.path.join(BASE_DIR, "data")

# Формируем финальный, абсолютно точный путь к файлу базы данных проекта
DB_PATH = os.path.join(DB_FOLDER, "hivolt_drm.db")

# Автоматически создаем папку для базы данных только там, где у процесса гарантированно есть права
os.makedirs(DB_FOLDER, exist_ok=True)

# Стерилизуем лог: собираем уведомление о синхронизации пути к БД методом чистого строкового сложения без f-строк
logger.debug("[LOG/DB]: Синхронизация файловой системы... Путь к БД установлен: " + str(DB_PATH))
        
# Объявляем функцию первичной инициализации структуры таблиц базы данных
def init_db():
    """
    Инициализация базы данных, включение WAL-режима для высокой производительности 
    и создание всех необходимых таблиц с индексами, если они отсутствуют.
    """
    # Запускаем защищенный контекст управления соединением, передавая путь к файлу и мьютекс блокировки
    with DatabaseConnectionContext(DB_PATH, db_lock) as cursor:         
        # Включаем WAL (Write-Ahead Logging) режим для обеспечения параллельного чтения и безопасной записи
        cursor.execute("PRAGMA journal_mode=WAL;")
        
        # Таблица настроек групп/топиков (префиксы, заголовки, разделители)
                # Выполняем SQL-запрос создания таблицы настроек групп, если она ещё не существует
        import strings as _strings
                
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS group_settings (
            chat_id INTEGER,                      -- Уникальный идентификатор чата Telegram (группы)
            thread_id INTEGER,                    -- Идентификатор топика (0, если группа без топиков)
            headline TEXT DEFAULT '{}',           -- Заголовок отчёта (настраивается админом)
            line_mark TEXT DEFAULT '{}',          -- Символ маркера строки в отчёте (например, "•")
            separator TEXT DEFAULT '{}',          -- Разделитель имён участников в списке
            dashboard_msg_id INTEGER,             -- ID сообщения с дашбордом в группе (для его обновления/удаления)
            PRIMARY KEY (chat_id, thread_id)      -- Составной первичный ключ (чат + топик)
        );
        -- Подставляем значения по умолчанию из централизованного словаря STRINGS
        """.format(_strings.STRINGS.get("default_headline"), _strings.STRINGS.get("default_line_mark"), _strings.STRINGS.get("default_separator")))
        
        # Транзакция выполнит создание DDL структуры настроек групп при её отсутствии
        
        
        
        
        
        # Таблица шлюза коротких сессионных ссылок для конструктора (п. 3.2 Манифеста)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS report_gateways (
                gateway_token TEXT PRIMARY KEY,
                chat_id INTEGER,
                thread_id INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """) # Транзакция сформирует таблицу для безопасного одноразового гашения токенов
        
        
        # Таблица базы данных пользователей и их кастомных тегов/имен (Политика имён п. 0.4)
        # Таблица глобальных пользователей (без привязки к группе)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                display_name TEXT
            );
        """)# Транзакция создаст таблицу пользователей для отображения кастомных имен в отчетах
        
        # Таблица связи пользователь-группа (many-to-many)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS group_members (
                chat_id INTEGER,
                thread_id INTEGER,
                user_id INTEGER,
                PRIMARY KEY (chat_id, thread_id, user_id)
            );
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_group_members_user ON group_members(user_id);")
        
        # Добавить колонку intercept_commands в group_settings, если её нет
        cursor.execute("PRAGMA table_info(group_settings);")
        columns = [col[1] for col in cursor.fetchall()]
        if 'intercept_commands' not in columns:
            cursor.execute("ALTER TABLE group_settings ADD COLUMN intercept_commands INTEGER DEFAULT 0;")
        
        # Добавляем колонку last_activity, если её нет
        if 'last_activity' not in columns:
            cursor.execute("ALTER TABLE group_settings ADD COLUMN last_activity INTEGER DEFAULT 0;")
            logger.debug("[LOG/DB]: Миграция: добавлена колонка last_activity")
        

def get_group_settings(chat_id, thread_id) -> dict: # Объявляем функцию извлечения параметров конфигурации топика
    """
    Извлекает настройки группы/топика из таблицы group_settings.

    Аргументы:
        chat_id (int): идентификатор чата Telegram.
        thread_id (int): идентификатор топика (0, если не форум).

    Возвращает:
        dict: словарь с ключами 'headline', 'line_mark', 'separator', 'dashboard_msg_id'.
              Если настройки не найдены, возвращает None.
    """
    
    # Атомарно открываем безопасное соединение в режиме чтения-записи с блокировкой потоков
    with DatabaseConnectionContext(DB_PATH, db_lock) as cursor:
        # Выполняем селективный выбор параметров по составному ключу уникальной инсталляции
        cursor.execute("""
            SELECT headline, line_mark, separator, dashboard_msg_id 
            FROM group_settings 
            WHERE chat_id = ? AND thread_id = ?
        """, (int(chat_id), int(thread_id) if thread_id else 0)) # Изолируем аргументы от инъекций через плейсхолдеры
        
        # Извлекаем одну результирующую строку из буфера ответов базы данных SQLite3
        row = cursor.fetchone()
        
        # Проверяем, были ли найдены физические параметры конфигурации для запрашиваемой темы
        if row:
            # Превращаем кортеж ответа в семантически понятный ассоциативный словарь по SOLID
            return {"headline": row[0], "line_mark": row[1], "separator": row[2], "dashboard_msg_id": row[3]}
        
        # Возвращаем пустой объект, если инсталляция для данного топика не производилась
        return None


# Найти функцию def save_group_setting и заменить её тело целиком:

def save_group_setting(chat_id, thread_id, key, value): # Объявляем функцию точечного обновления одного параметра группы
    """ Безопасно обновляет конкретное поле конфигурации топика в СУБД по SOLID через контекстный менеджер. """
    # Запускаем защищенный контекст управления соединением СУБД с автоматическим коммитом на Amvera
    with DatabaseConnectionContext(DB_PATH, db_lock) as cursor:
        # Проверяем, является ли изменяемый ключ конфигурации текстовым заголовком отчета
        if key == "headline":
            # Выполняем параметризованное обновление поля headline по составному первичному ключу группы
            cursor.execute("UPDATE group_settings SET headline = ? WHERE chat_id = ? AND thread_id = ?", (str(value), int(chat_id), int(thread_id) if thread_id else 0))
        # Проверяем, отвечает ли изменяемый параметр за маркер начала строки
        elif key == "line_mark":
            # Выполняем параметризованное обновление поля line_mark для указанной темы
            cursor.execute("UPDATE group_settings SET line_mark = ? WHERE chat_id = ? AND thread_id = ?", (str(value), int(chat_id), int(thread_id) if thread_id else 0))
        # Проверяем, является ли целевой ключ разделителем имен пользователей в отчете
        elif key == "separator":
            # Выполняем параметризованное обновление поля separator в таблице настроек
            cursor.execute("UPDATE group_settings SET separator = ? WHERE chat_id = ? AND thread_id = ?", (str(value), int(chat_id), int(thread_id) if thread_id else 0))


def resolve_user_display_name(user_id: int, username: str = None, display_name: str = None) -> str:
    """
    Возвращает отображаемое имя пользователя по приоритету:
    1) кастомный тег (display_name),
    2) username с символом '@',
    3) числовой user_id.

    Если переданы username или display_name, выполняет UPSERT в таблицу users.

    Аргументы:
        user_id (int): Telegram ID пользователя.
        username (str | None): системное имя пользователя.
        display_name (str | None): кастомный тег (имя).

    Возвращает:
        str: строка для отображения.
    """
    # Открываем контекст соединения с блокировкой потока
    with DatabaseConnectionContext(DB_PATH, db_lock) as cursor:
        # Если переданы новые данные для обновления (при вызове из админки или при регистрации)
        if display_name or username:
            # Выполняем операцию UPSERT: INSERT OR REPLACE с обновлением только переданных полей
            cursor.execute("""
                INSERT INTO users (user_id, username, display_name)
                VALUES (?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    username = COALESCE(excluded.username, users.username),
                    display_name = COALESCE(excluded.display_name, users.display_name)
            """, (int(user_id), username, display_name))
        # Запрашиваем актуальные данные о пользователе из базы
        cursor.execute("SELECT display_name, username FROM users WHERE user_id = ?", (int(user_id),))
        # Получаем одну строку результата
        row = cursor.fetchone()
        # Если запись существует
        if row:
            # Извлекаем кастомное имя и логин из кортежа
            disp, usr = row
            # Приоритет 1: кастомный тег (display_name)
            if disp:
                return str(disp)  # Возвращаем его как строку
            # Приоритет 2: системный username (с символом @ в начале)
            if usr:
                return "@" + str(usr)  # Добавляем @ перед именем
        # Приоритет 3: числовой идентификатор пользователя
        return str(user_id)


def register_or_update_user(user_id, username, custom_tag=None):
    """
    Регистрирует или обновляет данные пользователя в таблице users.
    Использует потокобезопасный контекстный менеджер DatabaseConnectionContext.
    
    Аргументы:
        user_id (int): Telegram ID пользователя.
        username (str): системное имя пользователя (может быть пустым).
        custom_tag (str | None): кастомный тег (отображаемое имя).
    """
    # Открываем контекст соединения с автоматической блокировкой и фиксацией
    with DatabaseConnectionContext(DB_PATH, db_lock) as cursor:
        # Проверяем, существует ли уже пользователь в таблице
        cursor.execute("SELECT display_name FROM users WHERE user_id = ?", (user_id,))
        row = cursor.fetchone()  # Получаем одну строку результата
        
        if row:
            # Пользователь найден — выполняем обновление существующей записи
            if custom_tag is not None:
                # Обновляем и username, и display_name (тег)
                cursor.execute(
                    "UPDATE users SET username = ?, display_name = ? WHERE user_id = ?",
                    (username, custom_tag, user_id)
                )
            else:
                # Обновляем только username, оставляя display_name без изменений
                cursor.execute(
                    "UPDATE users SET username = ? WHERE user_id = ?",
                    (username, user_id)
                )
        else:
            # Пользователь не найден — вставляем новую запись
            tag_val = custom_tag if custom_tag else ""  # Если тег не передан, подставляем пустую строку
            cursor.execute(
                "INSERT INTO users (user_id, username, display_name) VALUES (?, ?, ?)",
                (user_id, username, tag_val)
            )
    # При выходе из блока with автоматически выполняется commit (если нет исключений)
    # и закрывается соединение, блокировка снимается

def create_gateway_token(secure_token: str, chat_id: int, thread_id: int) -> str:
    """ Регистрирует короткий UUID-токен шлюза в базе данных через контекстный менеджер. """
    # Атомарно открываем изолированную сессию транзакции СУБД с автоматическим коммитом при выходе
    with DatabaseConnectionContext(DB_PATH, db_lock) as cursor:
        # Выполняем безопасный параметризованный запрос вставки токена и метаданных группы в СУБД
        cursor.execute("""
        INSERT INTO report_gateways (gateway_token, chat_id, thread_id) 
        VALUES (?, ?, ?)
        """, (str(secure_token), int(chat_id), int(thread_id))) # Передаем строго стерилизованные типы данных в кортеже
        
        # Стерилизуем лог вывода: атомарно собираем системное сообщение через конкатенацию строк по Правилу №8
    logger.debug("[LOG/DB]: Токен шлюза " + str(secure_token) + " успешно зафиксирован в базе для чата " + str(chat_id))
    # Возвращаем исходный зарегистрированный строковый токен шлюза вызывающему модулю
    return str(secure_token)


def pop_gateway_token(token: str): # Объявляем функцию однократного гашения короткого UUID-токена шлюза
    """ Считывает метаданные группы по токену и мгновенно уничтожает его в СУБД через контекстный менеджер. """
    # Запускаем защищенный контекст управления транзакциями базы данных с мьютексной блокировкой потоков
    with DatabaseConnectionContext(DB_PATH, db_lock) as cursor:
        # Выполняем выборку идентификаторов чата и топика, привязанных к уникальному строковому токену
        cursor.execute("SELECT chat_id, thread_id FROM report_gateways WHERE gateway_token = ?", (str(token),))
        # Извлекаем результат запроса в виде кортежа метаданных из временного буфера данных
        row = cursor.fetchone()
        
        # Проверяем, был ли переданный токен успешно найден в персистентной таблице шлюзов
        if row:
            # Мгновенно и безвозвратно удаляем использованный токен из СУБД для обеспечения одноразовости по ТЗ
            cursor.execute("DELETE FROM report_gateways WHERE gateway_token = ?", (str(token),))
            # Возвращаем кортеж с идентификаторами чата и топика вызывающему административному модулю
            return row
            logger.debug(f"[LOG/DB]: Ресурсы подключения для токена успешно освобождены.")  # Вывод отладочного лога по Протоколу Альфа
        return row # Возвращает кортеж (chat_id, thread_id) или None
        

def save_installation(chat_id, thread_id, dashboard_msg_id, headline, line_mark, separator): # Объявляем функцию сохранения монтажа пульта
    """ Сохраняет или перезаписывает метаданные развернутого дашборда темы через контекстный менеджер. """
    # Запускаем защищенный контекст управления соединением СУБД с автоматической фиксацией на диске
    with DatabaseConnectionContext(DB_PATH, db_lock) as cursor:
        # Выполняем операцию слияния данных INSERT OR REPLACE для атомарного обновления параметров топика
        cursor.execute("""
        INSERT INTO group_settings (chat_id, thread_id, dashboard_msg_id, headline, line_mark, separator)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(chat_id, thread_id) DO UPDATE SET
            dashboard_msg_id = excluded.dashboard_msg_id,
            headline = excluded.headline,
            line_mark = excluded.line_mark,
            separator = excluded.separator
        """, (int(chat_id), int(thread_id) if thread_id else 0, int(dashboard_msg_id), str(headline), str(line_mark), str(separator)))
    
    # Стерилизуем лог: собираем техническое уведомление методом чистого строкового сложения без f-строк
    logger.debug("[LOG/DB]: Настройки инсталляции чата " + str(chat_id) + " (Дашборд ID: " + str(dashboard_msg_id) + ") сохранены.")

def delete_group_settings(chat_id, thread_id):
    """Удаляет запись о группе из таблицы настроек."""
    # Открываем контекст соединения с блокировкой
    with DatabaseConnectionContext(DB_PATH, db_lock) as cursor:
        # Выполняем DELETE по составному первичному ключу (chat_id, thread_id)
        cursor.execute("DELETE FROM group_settings WHERE chat_id = ? AND thread_id = ?",
                       (int(chat_id), int(thread_id) if thread_id else 0))
        # Логируем действие в консоль
        logger.debug("[LOG/DB]: Удалены настройки для чата " + str(chat_id) + " топик " + str(thread_id))        
       

# =====================================================================
# ДОБАВЛЯЕМ НЕДОСТАЮЩИЕ ФУНКЦИИ (вызываются из admin.py и wizard.py)
# =====================================================================

def get_intercept_flag(chat_id, thread_id):
    """
    Возвращает флаг перехвата команд для указанной группы/топика.
    Если настройка отсутствует в БД, возвращает значение из config.ALLOW_COMMAND_INTERCEPT.
    """
    # Открываем контекст базы данных с блокировкой (потокобезопасно)
    with DatabaseConnectionContext(DB_PATH, db_lock) as cursor:
        # Выполняем SELECT, чтобы получить значение поля intercept_commands
        cursor.execute("SELECT intercept_commands FROM group_settings WHERE chat_id = ? AND thread_id = ?",
                       (chat_id, thread_id))
        row = cursor.fetchone()  # Получаем одну строку результата (или None)
        # Если строка существует и значение не NULL, преобразуем в bool (0/1 -> True/False)
        if row and row[0] is not None:
            return bool(row[0])
    # Если настройки для этой группы нет, используем глобальный флаг из config.py
    import config
    return config.ALLOW_COMMAND_INTERCEPT


def set_intercept_flag(chat_id, thread_id, value):
    """
    Устанавливает флаг перехвата команд для группы/топика.
    value: True или False (в БД сохранится 1 или 0)
    """
    with DatabaseConnectionContext(DB_PATH, db_lock) as cursor:
        # Используем INSERT OR REPLACE – если запись существует, обновляем, иначе создаём
        cursor.execute("""
            INSERT INTO group_settings (chat_id, thread_id, intercept_commands)
            VALUES (?, ?, ?)
            ON CONFLICT(chat_id, thread_id) DO UPDATE SET intercept_commands = excluded.intercept_commands
        """, (chat_id, thread_id, 1 if value else 0))
        # Логируем действие в консоль (без f-строк, чтобы избежать проблем с кодировкой)
        logger.debug("[LOG/DB]: Флаг перехвата для группы " + str(chat_id) + " установлен в " + str(value))


def delete_group_members(chat_id, thread_id):
    """
    Удаляет всех участников группы из таблицы group_members.
    Вызывается при деинсталляции бота из группы.
    """
    with DatabaseConnectionContext(DB_PATH, db_lock) as cursor:
        # Удаляем все строки, где chat_id и thread_id совпадают
        cursor.execute("DELETE FROM group_members WHERE chat_id = ? AND thread_id = ?",
                       (chat_id, thread_id))
        logger.debug("[LOG/DB]: Удалены участники группы " + str(chat_id) + " топик " + str(thread_id))

       
def update_dashboard_msg_id(chat_id, thread_id, new_msg_id):
    """Обновляет ID сообщения дашборда в группе."""
    with DatabaseConnectionContext(DB_PATH, db_lock) as cursor:
        # Обновляем поле dashboard_msg_id для указанной группы/топика
        cursor.execute("""
            UPDATE group_settings SET dashboard_msg_id = ?
            WHERE chat_id = ? AND thread_id = ?
        """, (int(new_msg_id), int(chat_id), int(thread_id) if thread_id else 0))
        # Логируем изменение
        logger.debug(f"[LOG/DB]: Дашборд ID обновлён на {new_msg_id} для чата {chat_id}")        


def sync_group_users(bot, chat_id, thread_id):
    """
    Синхронизирует пользователей группы, получая список администраторов
    и обновляя таблицу users. Администраторы исключаются из group_members.
    Обычные участники добавляются только через события входа/выхода или при
    запуске мастера отчётов (из-за ограничения Bot API).
    """
    from logger_config import logger
    try:
        admins = bot.get_chat_administrators(chat_id)
        admin_ids = set()
        with DatabaseConnectionContext(DB_PATH, db_lock) as cursor:
            for member in admins:
                user = member.user
                uid = user.id
                username = user.username or ""
                admin_ids.add(uid)
                # UPSERT в users (обновляем username, display_name не трогаем)
                cursor.execute("""
                    INSERT INTO users (user_id, username, display_name)
                    VALUES (?, ?, COALESCE((SELECT display_name FROM users WHERE user_id = ?), NULL))
                    ON CONFLICT(user_id) DO UPDATE SET
                        username = excluded.username
                """, (uid, username, uid))
            # Удаляем всех администраторов из group_members (они не должны быть в списке выбора)
            cursor.execute("DELETE FROM group_members WHERE chat_id = ? AND thread_id = ? AND user_id IN ({})".format(','.join('?'*len(admin_ids))),
                           [chat_id, thread_id] + list(admin_ids))
            logger.info("Синхронизировано %d администраторов для чата %s", len(admin_ids), chat_id)
        return True
    except Exception as e:
        logger.error("Ошибка синхронизации через администраторов: %s", e)
        return False




def get_group_members(chat_id, thread_id):
    """Возвращает список user_id всех участников группы (из group_members)."""
    with DatabaseConnectionContext(DB_PATH, db_lock) as cursor:
        cursor.execute("SELECT user_id FROM group_members WHERE chat_id = ? AND thread_id = ?", (chat_id, thread_id))
        rows = cursor.fetchall()
        return [row[0] for row in rows]



def get_all_installed_groups():
    """
    Возвращает список кортежей (chat_id, thread_id, dashboard_msg_id)
    для всех групп, где бот установлен (есть запись в group_settings).
    Используется для инициализации менеджера дашборда при старте.
    """
    with DatabaseConnectionContext(DB_PATH, db_lock) as cursor:
        cursor.execute("SELECT chat_id, thread_id, dashboard_msg_id FROM group_settings")
        return cursor.fetchall()

def update_last_activity(chat_id, thread_id):
    """Обновляет время последней активности группы (Unix timestamp)."""
    import time
    now = int(time.time())
    with DatabaseConnectionContext(DB_PATH, db_lock) as cursor:
        cursor.execute("""
            UPDATE group_settings 
            SET last_activity = ?
            WHERE chat_id = ? AND thread_id = ?
        """, (now, chat_id, thread_id))   #Первый ? получает now (текущее время).
                                            #Второй ? получает chat_id.
                                                #Третий ? получает thread_id.

# Запускаем инициализацию таблиц при первом импорте модуля
init_db()




