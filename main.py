### Файл MAIN.PY
# -*- coding: utf-8 -*-
"""
Модуль main.py – точка входа и глобальный маршрутизатор Telegram-бота.

Инициализирует объект bot, регистрирует все хэндлеры сообщений и callback-запросов.
Управляет глобальными хранилищами:
- user_sessions – активные сессии конструктора отчётов (user_id -> dict).
- admin_contexts – активные контексты администрирования (user_id -> (chat_id, thread_id)).

Запускает отказоустойчивый цикл пуллинга start_bot_engine().
"""

import telebot  # Импортируем официальную библиотеку pyTelegramBotAPI
import config   # Подключаем конфигурационную матрицу параметров и таймингов

import router   # Подключаем модуль изоляции и первичного распределения входящих сессий
import wizard   # Подключаем изолированный интерактивный движок пошаговой сборки отчетов
import admin    # Подключаем административный модуль управления параметрами группы
from strings import STRINGS  # Импортируем централизованную текстовую матрицу интерфейсов
import database
import time     # Локальный импорт модуля времени для обеспечения изоляции сбойного блока
import sys
import os
os.environ['HTTP_PROXY'] = ''
os.environ['HTTPS_PROXY'] = ''
import importlib
import group_actions
from dashboard_manager import DashboardManager
from ping_updater import PingUpdater
from logger_config import logger
from user_notifier import UserNotifier

## Импортируем requests для настройки сессии
#import requests
#
## Создаём сессию с игнорированием системных прокси
#_requests_session = requests.Session()
#_requests_session.trust_env = False          # не читать переменные окружения HTTP_PROXY
#_requests_session.proxies = {}               # явно убираем любые прокси
#
## Передаём сессию в TeleBot
#bot = telebot.TeleBot(config.TOKEN, threaded=True, requests_session=_requests_session)


_dashboard_manager = None
_ping_updater = None

# Инициализируем корневой многопоточный объект управления Telegram-ботом 

try:
    # Пытаемся импортировать локальный токен
    import token_local
    bot = telebot.TeleBot(token_local.TOKEN, threaded=True)
    logger.debug(f"Бот запущен с локальным токеном.")
except ModuleNotFoundError:
    # Если локального файла нет, берем серверный токен
    import token
    bot = telebot.TeleBot(token.TOKEN, threaded=True)
    logger.debug(f"Бот запущен с серверным токеном.")

# Монолитное ОЗУ-хранилище сессий активных конструкторов отчетов. Ключ: user_id, Значение: dict структуры данных
user_sessions = {}
# ОЗУ-хранилище активного контекста администрирования (chat_id, thread_id) для команд в ЛС
admin_contexts = {}  # Ключ: user_id (администратора), значение: кортеж (chat_id, thread_id)



#
#
#
# =========================================================================
# ОБРАБОТЧИК ТЕКСТОВЫХ КОМАНД В ГЛАВНОМ ДАШБОРДЕ
# =========================================================================
@bot.message_handler(func=lambda msg: msg.chat.type in ["group", "supergroup"], 
                     commands=["install", "uninstall", "settings", "reload", "mini_games", "help", 
                               "edit_head", "edit_mark", "edit_sep", "add_user", "add_tag"])
def public_group_commands_intercept_handler(message: telebot.types.Message) -> None:
    """
    Глобальный перехватчик любых системных команд в публичных пространствах.
    Обеспечивает Принцип полной тишины (1.1.1): мгновенно удаляет триггерное сообщение 
    и выводит ошибки контекста или прав доступа исключительно через тихие, бесшумные всплывающие плашки.
    """
    user_id = message.from_user.id          # Числовой идентификатор автора сообщения
    chat_id = message.chat.id              # Числовой идентификатор публичной группы
    thread_id = message.message_thread_id if message.message_thread_id else 0 
     

    # Шаг 1: Обеспечение стерильности чата — мгновенное и тихое удаление сообщения пользователя
    try:
        bot.delete_message(chat_id=chat_id, message_id=message.message_id)
    except Exception as delete_error:
        logger.debug(f"⚠️ [Стерильность]: Не удалось удалить команду {command} в группе {chat_id}: {delete_error}")
    
    database.update_last_activity(chat_id, thread_id)
    
        
    # Извлекаем команду
    # Извлекаем ПЕРВОЕ слово сообщения как строку и переводим в нижний регистр
    first_word = message.text.split()[0] if message.text else ""
    # Отрезаем юзернейм бота, если команда была отправлена как /install@bot_name
    command = first_word.split("@")[0].lower()
    
    # Логируем перехват вызова в консоль сервера для аудита безопасности
    logger.debug(f"🛑 [Группа]: Команда {command} от юзера {user_id} в чате {chat_id} перехвачена и заглушена.")


    # Проверка прав
    is_admin = group_actions.check_admin_rights(bot, chat_id, user_id)


    # --- Обработка команд по Манифесту (п. 2.2) ---

    # /help – доступна всем, отправляем в ЛС справку
    if command == "/help":
        group_actions.handle_help_request(bot, user_id)
        return

    # /mini_games – для всех: временное сообщение в ЛС о недоступности
    if command == "/mini_games":
        import strings as _strings
        alert_text = group_actions.build_access_denied_alert(user_id, command)
        UserNotifier.notify('group_text_command', alert_text,
                            bot=bot, chat_id=chat_id, thread_id=thread_id,
                            dashboard_manager=_dashboard_manager)
        return

    # Остальные команды требуют прав администратора
    if not is_admin:
        import strings as _strings
        alert_text = group_actions.build_access_denied_alert(user_id, command)
        UserNotifier.notify('group_text_command', alert_text,
                            bot=bot, chat_id=chat_id, thread_id=thread_id,
                            dashboard_manager=_dashboard_manager)
        return

    # Администраторские команды
    if command == "/install":
        admin.process_install_command(message=message, bot=bot)
        # Обновляем дашборд через менеджер
        if _dashboard_manager:
            _dashboard_manager.create_or_update(chat_id, thread_id)
        return

    if command == "/reload":
        import strings as _strings
        success = group_actions.reload_bot_strings_and_layout(_dashboard_manager)
        if success:
            UserNotifier.notify('group_text_command',
                                _strings.STRINGS.get("reload_success"),
                                bot=bot, chat_id=user_id)
        else:
            UserNotifier.notify('group_text_command',
                                _strings.STRINGS.get("reload_err").format("неизвестная ошибка"),
                                bot=bot, chat_id=user_id)
        return

    if command == "/uninstall":
        group_actions.handle_uninstall_request(bot, user_id, chat_id, thread_id)
        return

    if command == "/settings":
        group_actions.handle_settings_request(bot, user_id, chat_id, thread_id)
        return

    # Команды редактирования (/edit_head, /edit_mark и т.д.) – игнорируем в группе
    # (можно просто удалить сообщение, что уже сделано)
#============================================================================
#============================================================================
#============================================================================





@bot.message_handler(commands=["restart"])
def private_restart_command_handler(message: telebot.types.Message) -> None:
    """Запрос подтверждения перезапуска бота."""
    user_id = message.from_user.id
    try:
        bot.delete_message(user_id, message.message_id)
    except:
        pass
    # Проверяем права (только администраторы групп могут перезагружать? Но проще разрешить всем, кто знает команду)
    # По Манифесту, /restart доступна админам? Лучше ограничить: проверим, есть ли у пользователя админ-контекст или он админ хоть где-то?
    # Упростим: запрос подтверждения без проверки прав (но кнопка будет только в админ-дашборде, так что команда остаётся служебной)
    from unifier import CanonicalUnifier
    CanonicalUnifier.send_dialog_confirm_gate(
        bot=bot, chat_id=user_id, text_key="restart_confirm",
        yes_callback="sys_confirm_restart", no_callback="sys_deny_restart"
    )




# =========================================================================
# СИНХРОНИЗАЦИЯ УЧАСТНИКОВ ГРУППЫ (СОБЫТИЯ ВХОДА/ВЫХОДА)
# =========================================================================

@bot.message_handler(func=lambda msg: msg.chat.type in ["group", "supergroup"] and 
                     (msg.new_chat_members or msg.left_chat_member))
def group_members_change_handler(message: telebot.types.Message) -> None:  
    """
    Автоматическая синхронизация таблиц users и group_members при добавлении 
    или удалении участников в группе/топике (Манифест п. 6.3).
    """
    chat_id = message.chat.id
    thread_id = message.message_thread_id or 0
    # Запускаем синхронизацию в отдельном потоке, чтобы не блокировать удаление сообщения
    import threading
    threading.Thread(
        target=database.sync_group_users,
        args=(bot, chat_id, thread_id),
        daemon=True
    ).start()
    # Обновляем метку активности группы
    database.update_last_activity(chat_id, thread_id)
    # Удаляем служебное сообщение о входе/выходе (Политика стерильности 1.1.1)
    try:
        bot.delete_message(chat_id=chat_id, message_id=message.message_id)
    except Exception:
        pass
    # Логируем событие (без f-строк)
    log_msg = "[LOG/MAIN]: Запущена синхронизация участников для чата " + str(chat_id)
    logger.debug(log_msg)



# Регистрируем хэндлер сообщений для групповых пространств с фильтрацией роботов по ТЗ
@bot.message_handler(
    func=lambda msg: msg.chat.type in ["group", "supergroup"] and not msg.from_user.is_bot
)
def public_group_dashboard_refresh_handler(message): # Объявляем функцию диспетчеризации сдвига пульта
    
    """ 
    
    Переносит дашборд на самый низ топика при активности пользователей. 
    
    Игнорирует команды (начинающиеся с '/'), так как они будут удалены.
    
    """
    """
    
    Текущий код public_group_dashboard_refresh_handler всё ещё нарушает SRP, так как он занимается и удалением, и созданием дашборда. Это уже обсуждалось. В идеале всю логику перемещения дашборда должен брать на себя DashboardManager, а хэндлер должен только уведомлять менеджер о необходимости обновления. Но для текущей задачи это не критично.
    
    """
    
    # ===== ДИАГНОСТИКА: проверяем, вызывается ли хэндлер =====
    logger.debug(f"[LOG/MAIN] Вызван public_group_dashboard_refresh_handler для чата {message.chat.id}")
    # ============================================================
    
    chat_id = message.chat.id # Извлекаем уникальный числовой идентификатор текущего чата группы
    thread_id = message.message_thread_id or 0 # Определяем ID топика супергруппы или зануляем его
    
    # Это нужно для корректной работы пинга (интервал обновления tech_bar)
    database.update_last_activity(chat_id, thread_id) #обновление активности
    
    
    # Игнорируем команды – они будут удалены, перемещение не требуется
    # Пропускаем только текстовые команды — они будут удалены
    if message.content_type == 'text' and message.text and message.text.startswith('/'):
        return
        
    """
    Команды мгновенно удаляются, их message_id не должен учитываться. Без этой проверки дашборд дёргается при каждой команде. Изменение точечное, не затрагивает остальную логику.
    """
    
    # Уведомляем менеджер о новом сообщении (для возможного перемещения при пинге)
    if _dashboard_manager:
        _dashboard_manager.notify_user_message(chat_id, thread_id, message.message_id)
        
    # Получаем настройки группы (чтобы узнать ID текущего дашборда)
    settings = database.get_group_settings(chat_id, thread_id) # Запрашиваем конфигурацию пульта из СУБД SQLite
    
    if not settings:
        logger.debug(f"[LOG/MAIN] Настройки для чата {chat_id} не найдены, пропускаем")
        return
    
    if not settings.get("dashboard_msg_id"):
        logger.debug(f"[LOG/MAIN] Нет dashboard_msg_id для чата {chat_id}, пропускаем")
        return

    # Сравниваем ID сообщения пользователя и ID дашборда
    if message.message_id > settings["dashboard_msg_id"]:
        logger.debug(f"[LOG/MAIN] Условие сработало: {message.message_id} > {settings['dashboard_msg_id']}")
        
        # Вызываем атомарное перемещение через менеджер
        if _dashboard_manager:
            logger.debug("[LOG/MAIN] Перед вызовом move_dashboard")
            _dashboard_manager.move_dashboard(chat_id, thread_id)
            logger.debug("[LOG/MAIN] После вызова move_dashboard")
            logger.debug(f"[LOG/MAIN] Дашборд перемещён вниз для чата {chat_id}")
        else:
            logger.debug("[LOG/MAIN] КРИТИЧЕСКАЯ ОШИБКА: _dashboard_manager не инициализирован")
    else:
        logger.debug(f"[LOG/MAIN] Условие НЕ сработало: {message.message_id} <= {settings['dashboard_msg_id']}")
    
@bot.message_handler(
    content_types=['photo', 'document', 'audio', 'video', 'voice', 'sticker', 'video_note'],
    func=lambda msg: msg.chat.type in ["group", "supergroup"] and not msg.from_user.is_bot
)
def group_media_refresh_handler(message):
    """
    Перемещает дашборд вниз при появлении любого нетекстового сообщения (медиа).
    Выделен в отдельный обработчик для гарантированной реакции на фото/файлы.
    """
    chat_id = message.chat.id
    thread_id = message.message_thread_id or 0
    database.update_last_activity(chat_id, thread_id)

    # Уведомляем менеджер о новом сообщении
    if _dashboard_manager:
        _dashboard_manager.notify_user_message(chat_id, thread_id, message.message_id)

    # Проверяем, нужно ли переместить дашборд
    settings = database.get_group_settings(chat_id, thread_id)
    if not settings or not settings.get("dashboard_msg_id"):
        return

    if message.message_id > settings["dashboard_msg_id"]:
        if _dashboard_manager:
            _dashboard_manager.move_dashboard(chat_id, thread_id)
            logger.debug(f"[LOG/MAIN] Дашборд перемещён вниз после медиа в чате {chat_id}")

# =========================================================================
# ПРИВАТНОЕ ПРОСТРАНСТВО (ЛИЧНЫЕ СООБЩЕНИЯ / ЛС)
# Точка входа шлюза роутера и распределение входящих пакетов обновлений
# =========================================================================

@bot.message_handler(commands=["start"])
def main_start_handler(message: telebot.types.Message) -> None:
    """
    Единственный глобальный перехватчик стартовых команд в ЛС.
    Ответственность: Принять объект сообщения и передать его в чистый изолированный роутер.
    """
    router.process_start_routing(
        message=message,
        bot=bot,
        user_sessions=user_sessions,
        launch_wizard_func=run_wizard_from_router,
        open_admin_func=run_admin_from_router
    )


def run_wizard_from_router(message: telebot.types.Message, target_chat_id: int, target_thread_id: int) -> None:
    """
    Изолированный мост-перенаправитель для развертывания конструктора отчетов.
    Вызывается роутером после успешной валидации параметров короткой сессионной ссылки.
    """
    wizard.start_report_wizard_engine(
        message=message,
        target_chat_id=target_chat_id,
        target_thread_id=target_thread_id,
        bot=bot,
        user_sessions=user_sessions
    )


def run_admin_from_router(message: telebot.types.Message, param: str) -> None:
    """
    Изолированный мост-перенаправитель для развертывания пульта настроек администратора.
    Передает исполнение в выделенный модуль admin.py.
    """
    admin.open_admin_dashboard(message=message, param=param, bot=bot)


# =========================================================================
# ОБРАБОТЧИКИ СОБЫТИЙ ИНЛАЙН-КНОПОК (CALLBACK QUERIES)
# =========================================================================

@bot.callback_query_handler(func=lambda call: call.data.startswith("adm_"))
def main_admin_callback_handler(call: telebot.types.CallbackQuery) -> None:
    """
    Перехватчик инлайн-кликов пульта управления Администратора.
    Маршрутизирует событие по префиксу 'adm_' в изолированный программный слой admin.py.
    """
    admin.process_admin_inline_callbacks(call=call, bot=bot)


@bot.callback_query_handler(func=lambda call: call.data.startswith("w1_"))



def main_wizard_callback_handler(call: telebot.types.CallbackQuery) -> None:
    
    wizard.handle_wizard_inline_callbacks(
        call=call,
        bot=bot,
        user_sessions=user_sessions
    )


# =========================================================================
# СИСТЕМНЫЙ ДВИЖОК ЗАПУСКА И МОНИТОРИНГА РЕПЛИК
# =========================================================================

def start_bot_engine():
    """
    Отказоустойчивый сетевой супервизор.
    Обеспечивает непрерывное соединение с серверами Telegram API.
    Защищает приложение от критических падений при перезагрузках или сетевых сбоях платформы Amvera.
    """
    
    global _dashboard_manager, _ping_updater    
    logger.debug(f"🚀 [Главный]: Запуск бесконечного цикла пуллинга для версии {config.VERSION}...")

    # Провайдеры для менеджера
    def settings_provider(chat_id, thread_id):
        return database.get_group_settings(chat_id, thread_id)

    def update_msg_id_callback(chat_id, thread_id, new_msg_id):
        # обновить dashboard_msg_id в БД
        with database.DatabaseConnectionContext(database.DB_PATH, database.db_lock) as cur:
            cur.execute("UPDATE group_settings SET dashboard_msg_id = ? WHERE chat_id = ? AND thread_id = ?",
                        (new_msg_id, chat_id, thread_id))

    # ---- СОЗДАНИЕ МЕНЕДЖЕРА (один раз) ----
    # Раньше здесь был код, который создавал _dashboard_manager дважды: до цикла и внутри цикла.
    # Это приводило к утечке памяти и конфликту версий. Теперь создаём только один экземпляр.


    _dashboard_manager = DashboardManager(bot, settings_provider, update_msg_id_callback)

    # Загружаем существующие группы из БД
    groups = database.get_all_installed_groups()   # функция из database.py
    _dashboard_manager.init_from_db(groups)
    
    # Создаём менеджер дашборда
    #dashboard_manager = DashboardManager(bot, settings_provider, update_msg_id_callback)   # <-- ЭТА СТРОКА ДОЛЖНА БЫТЬ ВЫШЕ
    
    _ping_updater = PingUpdater(bot, _dashboard_manager)
    _ping_updater.start()

    # Сохраняем в глобальные переменные, чтобы другие функции (например, /reload) могли их использовать
    #_dashboard_manager = DashboardManager(bot, settings_provider, update_msg_id_callback)
    #_ping_updater = PingUpdater(bot, _dashboard_manager)
    
    
    
    # ---- БЕСКОНЕЧНЫЙ ЦИКЛ ПУЛЛИНГА ----
    
    while True:
        try:
            # Принудительно удаляем вебхук и сбрасываем предыдущий polling-сеанс
            bot.remove_webhook()          # Отключаем вебхук (если был)
            time.sleep(1)                 # Даём время API обработать сброс
            
            
            
            # ===== НИЖЕСЛЕДУЮЩИЙ КОД БЫЛ УДАЛЁН (ЗАКОММЕНТИРОВАН), ПОТОМУ ЧТО ОН ПОВТОРЯЛ СОЗДАНИЕ МЕНЕДЖЕРА И ПИНГЕРА =====
            # Это были лишние строки, которые создавали второй экземпляр менеджера и пингера внутри цикла.
            # Они приводили к тому, что после перезапуска (при ошибке) создавались новые объекты,
            # а старые оставались в памяти, и глобальные переменные _dashboard_manager и _ping_updater
            # переприсваивались, но старые таймеры продолжали работать, вызывая конфликты.
            # Теперь мы создаём менеджер и пингер только один раз, до цикла.
            
            """
            # Функции-провайдеры для DashboardManager
            def settings_provider(chat_id, thread_id):
                return database.get_group_settings(chat_id, thread_id)
        
            def update_msg_id_callback(chat_id, thread_id, new_msg_id):
                with database.DatabaseConnectionContext(database.DB_PATH, database.db_lock) as cur:
                    cur.execute("UPDATE group_settings SET dashboard_msg_id = ? WHERE chat_id = ? AND thread_id = ?",
                        (new_msg_id, chat_id, thread_id))

        
            # СОЗДАЁМ ЭКЗЕМПЛЯР МЕНЕДЖЕРА (локальная переменная dashboard_manager)
            dashboard_manager = DashboardManager(bot, settings_provider, update_msg_id_callback)
        
            # Загружаем группы из БД
            groups = database.get_all_installed_groups()
            dashboard_manager.init_from_db(groups)
        
            # Запускаем пингер
            ping_updater = PingUpdater(bot, _dashboard_manager)
            ping_updater.start()
            
            # СОХРАНЯЕМ В ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ (с подчёркиванием)
            
            _dashboard_manager = dashboard_manager
            _ping_updater = ping_updater
            """
            
            
            
            # Активируемinfinity polling с жестко заданными таймаутами удержания сокетов
            bot.infinity_polling(
                timeout=30,
                long_polling_timeout=30,
                allowed_updates=["message", "callback_query"],  # Запрашиваем только разрешенные типы апдейтов
                skip_pending=True          # Пропускаем старые обновления (важно!)
            )
        except Exception as polling_error:
            # Логируем детальное описание сетевого сбоя в консоль сервера Amvera
            logger.debug(f"⚠️ [Критический сбой пуллинга]: {polling_error}. Автоматический перезапуск через 5 секунд...")
            
            # Останавливаем пингер, чтобы он не мешал при перезапуске
            if _ping_updater:
                _ping_updater.stop()
                
            time.sleep(5)  # Пауза перед инициированием нового сетевого соединения
            # Перезапускаем пингер (создаём заново, так как предыдущий был остановлен)
            _ping_updater = PingUpdater(bot, _dashboard_manager)
            _ping_updater.start()

@bot.message_handler(content_types=['photo'], func=lambda msg: msg.chat.type == "private")
def private_photo_intercept_handler(message: telebot.types.Message) -> None: # ИНЪЕКЦИЯ: Жесткий перехватчик медиапотока на Шаге 2 (п. 4.2 Манифеста)
    user_id = message.from_user.id
    if user_id in user_sessions and user_sessions[user_id].get("step") == 2:
        photo_file_id = message.photo[-1].file_id # Извлекаем максимальное качество снимка
        user_sessions[user_id]["photos"].append(photo_file_id) # Складываем file_id в ОЗУ-массив сессии
        # Сохранить ID сообщения пользователя для последующей очистки
        if "user_message_ids" not in user_sessions[user_id]:
            user_sessions[user_id]["user_message_ids"] = []
        user_sessions[user_id]["user_message_ids"].append(message.message_id)
        logger.debug(f"[LOG/MAIN]: Фото {photo_file_id} добавлено в сессию {user_id}.") # Лог по Протоколу Альфа
        wizard.render_wizard_dashboard_step(user_id, bot, user_sessions) # ИСКЛЮЧЕНИЕ: перерисовываем дашборд, НЕ удаляя фото юзера
        return
    try:
        bot.delete_message(chat_id=message.chat.id, message_id=message.message_id) # Стерилизация мусорных фото вне Шага 2
    except Exception: pass

# ТОЧЕЧНАЯ ПРАВКА: Чистильщик текстового флуда в ЛС с делегированием вывода в wizard.py (SOLID)
@bot.message_handler(func=lambda msg: msg.chat.type == "private")
def private_text_cleaner_intercept_handler(message: telebot.types.Message) -> None:
    """
    Перехватывает любой неконтекстный текст в ЛС, мгновенно стирает его 
    и запрашивает у слоя wizard.py рендеринг временного ворнинга.
    """
    user_id = message.from_user.id
    user_session = user_sessions.get(user_id, {})
    
    # Если пользователь находится на Шаге 3 — отдаем управление в wizard.logic_text_capture_step_3
    if user_session.get("step") == 3:
        return
        
    # Мгновенно и бесшумно уничтожаем входящее мусорное сообщение, обеспечивая стерильность чата
    try:
        bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
    except Exception:
        pass
        
    # Вызываем единую универсальную функцию-шлюз вывода для отправки исчезающего предупреждения
    
    import strings as _strings
    UserNotifier.notify('private_text_command',
                        _strings.STRINGS.get("err_no_admin_context"),
                        bot=bot, chat_id=message.chat.id)

@bot.message_handler(commands=["abort"])  # Реагируем на команду /abort только в ЛС
def private_abort_command_handler(message: telebot.types.Message) -> None:
    """Обработка /abort в ЛС – двухэтапное подтверждение отмены."""
    user_id = message.from_user.id  # Получаем ID пользователя, вызвавшего команду
    # Удаляем сообщение с командой из чата (политика стерильности)
    try:
        bot.delete_message(chat_id=user_id, message_id=message.message_id)
    except Exception:  # Если не удалось удалить (нет прав, сообщение уже удалено) — игнорируем
        pass
    # Проверяем, есть ли активная сессия конструктора отчётов
    if user_id in user_sessions:
        # Вызываем универсальное диалоговое окно подтверждения через CanonicalUnifier
        CanonicalUnifier.send_dialog_confirm_gate(
            bot=bot,                      # Передаём экземпляр бота
            chat_id=user_id,              # Отправляем в ЛС пользователя
            text_key="abort_confirm_dialog",  # Ключ текста вопроса из STRINGS.py
            yes_callback="adm_confirm_abort_cmd",  # Callback при согласии
            no_callback="adm_deny_abort_cmd"      # Callback при отказе
        )
    else:
        # Если активной сессии нет — выводим временное сообщение об ошибке
        import strings as _strings
        UserNotifier.notify('private_text_command',
                            _strings.STRINGS.get("cmd_abort_no_session"),
                            bot=bot, chat_id=user_id)

@bot.message_handler(commands=["reload"])  # Реагируем на /reload в ЛС
def private_reload_command_handler(message: telebot.types.Message) -> None:
    """Перезагрузка строк из STRINGS.py (без перезапуска бота)."""
    user_id = message.from_user.id
    # Удаляем служебное сообщение с командой
    try:
        bot.delete_message(chat_id=user_id, message_id=message.message_id)
    except Exception:
        pass
    try:
        # Динамически перезагружаем модуль STRINGS.py
        import importlib, strings  # Импортируем модуль для перезагрузки
        importlib.reload(strings)    # Выполняем перезагрузку
        # Обновляем глобальную переменную STRINGS (которая используется во всём боте)
        globals()['STRINGS'] = _strings.STRINGS
        # Обновить все дашборды через менеджер
        # Перезагрузка layout и обновление всех дашбордов через менеджер
        if _dashboard_manager:
            _dashboard_manager.reload_layout()
            
        # Отправляем сообщение об успехе
        import strings as _strings
        UserNotifier.notify('private_text_command',
                            _strings.STRINGS.get("reload_success"),
                            bot=bot, chat_id=user_id)
    except Exception as e:
        # В случае ошибки выводим сообщение с текстом исключения
        import strings as _strings
        UserNotifier.notify('private_text_command',
                            _strings.STRINGS.get("reload_fail").format(str(e)),
                            bot=bot, chat_id=user_id)

@bot.message_handler(commands=["mini_games"])  # Реагируем на /mini_games в ЛС
def private_mini_games_handler(message: telebot.types.Message) -> None:
    """Заглушка для команды /mini_games – выводит всплывающий алерт."""
    import strings as _strings
    user_id = message.from_user.id
    # Удаляем команду из чата
    try:
        bot.delete_message(chat_id=user_id, message_id=message.message_id)
    except Exception:
        pass
    # Отправляем бесшумное всплывающее окно (answer_callback_query, но без callback)
    # Используем show_alert=True для модального окна
    import strings as _strings
    UserNotifier.notify('private_text_command',
                        _strings.STRINGS.get("mini_games_alert"),
                        bot=bot, chat_id=user_id)

@bot.message_handler(commands=["edit_head", "edit_mark", "edit_sep", "add_user", "add_tag"])
def private_admin_edit_commands(message: telebot.types.Message) -> None:
    """Обработка прямых команд редактирования в ЛС. Требуют активного контекста администрирования."""
    import strings as _strings
    user_id = message.from_user.id
    command = message.text.split()[0].lower()  # Извлекаем команду без аргументов
    # Удаляем сообщение с командой
    try:
        bot.delete_message(chat_id=user_id, message_id=message.message_id)
    except Exception:
        pass
    # Проверяем, есть ли у пользователя активный контекст администрирования
    if user_id not in admin_contexts:
        # Если нет — выводим временное предупреждение
        import strings as _strings
        UserNotifier.notify('private_text_command',
                            _strings.STRINGS.get("no_admin_context"),
                            bot=bot, chat_id=user_id)
        return
    # Извлекаем сохранённые идентификаторы группы и топика
    chat_id, thread_id = admin_contexts[user_id]
    # Определяем, какое поле настройки нужно редактировать
    if command == "/edit_head":
        field = "headline"          # Поле заголовка отчёта
        prompt_key = "enter_new_value"
    elif command == "/edit_mark":
        field = "line_mark"         # Поле маркера строки
        prompt_key = "enter_new_value"
    elif command == "/edit_sep":
        field = "separator"         # Поле разделителя имён
        prompt_key = "enter_new_value"
    else:
        # Команды /add_user и /add_tag пока не реализованы, выводим заглушку
        import strings as _strings
        UserNotifier.notify('private_text_command',
                            _strings.STRINGS.get("not_implemented"),
                            bot=bot, chat_id=user_id)
        return
    # Отправляем сообщение с запросом нового значения
    msg = bot.send_message(user_id, _strings.STRINGS.get(prompt_key, "").format(field))
    # Регистрируем следующий шаг — функцию-обработчик введённого текста
    bot.register_next_step_handler(msg, admin_edit_value_handler, bot=bot, field=field, chat_id=chat_id, thread_id=thread_id)

def admin_edit_value_handler(message: telebot.types.Message, bot: telebot.TeleBot, field: str, chat_id: int, thread_id: int) -> None:
    """Принимает текст от администратора и сохраняет новое значение параметра в БД."""
    user_id = message.from_user.id
    new_value = message.text  # Получаем введённый текст
    # Удаляем сообщение пользователя с ответом (политика стерильности ЛС)
    try:
        bot.delete_message(user_id, message.message_id)
    except Exception:
        pass
    # Сохраняем новое значение в базу данных
    database.save_group_setting(chat_id, thread_id, field, new_value)
    # Отправляем временное подтверждение об успешном обновлении
    import strings as _strings
    # Формируем текст подтверждения
    msg = _strings.STRINGS.get("value_updated").format(field, new_value)
    UserNotifier.notify('private_text_command', msg, bot=bot, chat_id=user_id)
    # (Опционально) можно обновить дашборд настроек, но для простоты завершаем

@bot.callback_query_handler(func=lambda call: call.data in ["adm_confirm_abort_cmd", "adm_deny_abort_cmd"])
def abort_confirm_callback(call: telebot.types.CallbackQuery) -> None:
    """Обрабатывает нажатие кнопок в диалоге подтверждения отмены."""
    user_id = call.from_user.id
    # Закрываем всплывающее уведомление о нажатии (бесшумно)
    bot.answer_callback_query(call.id)
    if call.data == "adm_confirm_abort_cmd":  # Пользователь согласился отменить
        if user_id in user_sessions:
            # Получаем сессию конструктора
            session = user_sessions[user_id]
            # Удаляем сообщение дашборда, если оно есть
            if session.get("last_dash_id"):
                try:
                    bot.delete_message(user_id, session["last_dash_id"])
                except Exception:
                    pass
            # Уничтожаем сессию в ОЗУ
            del user_sessions[user_id]
            # Дополнительно сбросить контекст администрирования
            if user_id in admin_contexts:
                del admin_contexts[user_id]
        # Сбрасываем возможный ожидающий хэндлер ввода текста
        # Вместо bot.clear_step_handler_by_chat_id(user_id)
        try:
            # В некоторых версиях telebot есть метод clear_step_handler_by_chat_id, но официально его нет
            # Используем безопасную заглушку
            bot.clear_step_handler(message)  # Требуется объект message, но его нет в контексте callback
        except Exception:
            pass  # Лучше, чем AttributeError
        # Отправляем сообщение об успешной отмене
        import strings as _strings
        UserNotifier.notify('private_text_command',
                            _strings.STRINGS.get("abort_success"),
                            bot=bot, chat_id=user_id)
    else:
        # Пользователь отказался от отмены — выводим короткое уведомление
        import strings as _strings
        UserNotifier.notify('private_text_command',
                            _strings.STRINGS.get("action_cancelled"),
                            bot=bot, chat_id=user_id)


@bot.callback_query_handler(func=lambda call: call.data.startswith("adm_confirm_uninstall_"))
def confirm_uninstall_callback(call: telebot.types.CallbackQuery) -> None:
    """Выполняет полное удаление панели управления из группы после подтверждения."""
    import strings as _strings
    # Разбираем callback_data: adm_confirm_uninstall_CHATID_THREADID
    parts = call.data.split("_")
    # Извлекаем числовые идентификаторы
    chat_id = int(parts[3])
    thread_id = int(parts[4])
    user_id = call.from_user.id
    # ПОЛУЧАЕМ НАСТРОЙКИ ДО УДАЛЕНИЯ ИЗ БД
    # Получаем старые настройки, чтобы узнать ID сообщения дашборда в группе
    settings = database.get_group_settings(chat_id, thread_id)

    
    
    if settings and settings.get("dashboard_msg_id"):
        try:
            # Удаляем сам дашборд из группы/топика
            bot.delete_message(chat_id, settings["dashboard_msg_id"])
        except Exception:
            pass
            
    # Удаляем запись о группе из БД
    database.delete_group_settings(chat_id, thread_id)
    # Удаляем участников группы из таблицы group_members (новая функция)
    database.delete_group_members(chat_id, thread_id)
    # Отвечаем на callback, чтобы закрыть всплывающее окно
    # Выводим модальное окно с подтверждением удаления (п. 0.2.4)
    bot.answer_callback_query(
        call.id,
        text=_strings.STRINGS.get("uninstall_success_alert"),
        show_alert=True
    )
    # Удаляем диалоговое окно подтверждения (оно находится в ЛС админа)
    try:
        bot.delete_message(user_id, call.message.message_id)
    except Exception:
        pass






@bot.callback_query_handler(func=lambda call: call.data == "sys_confirm_restart")
def restart_confirm_callback(call: telebot.types.CallbackQuery) -> None:
    user_id = call.from_user.id
    # Бесшумный алерт о начале перезагрузки
    import strings as _strings
    bot.answer_callback_query(
        call.id,
        text=_strings.STRINGS.get("restart_in_progress"),
        show_alert=False
    )
    # Отправляем сообщение и удаляем через пару секунд
    # Отправляем сервисное сообщение через канонический шлюз
    msg = CanonicalUnifier.send_service_delivery_gate(
        bot=bot,
        chat_id=user_id,
        text_key="restart_message",
        is_temporary=False   # Не удаляем автоматически, т.к. бот всё равно перезагрузится
    )
    import threading, time
    def _restart():
        time.sleep(1)
        try:
            bot.delete_message(user_id, msg.message_id)
        except:
            pass
        # Полный перезапуск процесса
        os._exit(0)  # Завершаем процесс, хостинг перезапустит
    threading.Thread(target=_restart, daemon=True).start()

@bot.callback_query_handler(func=lambda call: call.data == "sys_deny_restart")
def restart_deny_callback(call: telebot.types.CallbackQuery) -> None:
    bot.answer_callback_query(call.id, text="Перезагрузка отменена", show_alert=True)
    try:
        bot.delete_message(call.from_user.id, call.message.message_id)
    except:
        pass


# ===================================
# ОБРАБОТЧИК КНОПОК ГЛАВНОГО ДАШБОРДА
# ===================================
@bot.callback_query_handler(func=lambda call: call.data.startswith("m_dash_"))
def main_dashboard_callback_handler(call: telebot.types.CallbackQuery) -> None:
    """
    О   Все кнопки главного дашборда (префикс 'm_dash_').
    Проверяет права администратора, выполняет действие,
    а результат отображает через UserNotifier.
    """
    
    # Локальный импорт для свежих текстов после reload
    import strings as _strings
    user_id = call.from_user.id
    # Разбираем callback_data: m_dash_<action>_<chat_id>_<thread_id>
    data_parts = call.data.split("_")
    if len(data_parts) < 4:
        UserNotifier.notify('main_dashboard_button',
                            _strings.STRINGS.get("gateway_error_nan",
                                                 "Неверный параметр"),
                            callback=call, bot=bot)
        return
    
    # Действие: reload/uninstall/settings/help/minigames
    action = data_parts[2]  # m_dash_<action>_chatid_threadid
    try:
        chat_id = int(data_parts[3])
        thread_id = int(data_parts[4])
    except ValueError:
        UserNotifier.notify('main_dashboard_button',
                            _strings.STRINGS.get("gateway_error_nan",
                                                 "Неверный параметр"),
                            callback=call, bot=bot)
        return

    # Проверяем права
    is_admin = group_actions.check_admin_rights(bot, chat_id, user_id)

    if not is_admin:
        import strings as _strings
        alert_text = group_actions.build_access_denied_alert(user_id, action)
        UserNotifier.notify('main_dashboard_button', alert_text, callback=call, bot=bot)
        return

    # Администратор – выполняем действие
    try:
        if action == "reload":
            # Перезагрузка строк и макета дашборда
            success = group_actions.reload_bot_strings_and_layout(_dashboard_manager)
            if success:
                UserNotifier.notify('main_dashboard_button',
                                    _strings.STRINGS.get("reload_success",
                                                         "Перезагружено"),
                                    callback=call, bot=bot)
            else:
                UserNotifier.notify('main_dashboard_button',
                                    _strings.STRINGS.get("reload_err",
                                                         "Ошибка перезагрузки"),
                                    callback=call, bot=bot)

        elif action == "uninstall":
            # Запрос на удаление панели
            if group_actions.handle_uninstall_request(bot, user_id, chat_id, thread_id):
                UserNotifier.notify('main_dashboard_button',
                                    _strings.STRINGS.get("uninstall_sent",
                                                         "Запрос отправлен в ЛС"),
                                    callback=call, bot=bot)
            else:
                UserNotifier.notify('main_dashboard_button',
                                    _strings.STRINGS.get("uninstall_fail",
                                                         "Не удалось отправить запрос"),
                                    callback=call, bot=bot)

        elif action == "settings":
            # Генерируем диплинк для принудительного перехода в ЛС с открытием настроек
            bot_username = bot.get_me().username
            url = f"https://t.me/{bot_username}?start=adm_{chat_id}_{thread_id}"
            bot.answer_callback_query(call.id, url=url)

        elif action == "help":
            # Отправка справки в ЛС
            group_actions.handle_help_request(bot, user_id)
            UserNotifier.notify('main_dashboard_button',
                                _strings.STRINGS.get("help_sent",
                                                     "Справка отправлена в ЛС"),
                                callback=call, bot=bot)

        elif action == "minigames":
            # Мини-игры (заглушка)
            UserNotifier.notify('main_dashboard_button',
                                _strings.STRINGS.get("mini_games_alert",
                                                     "Мини-игры недоступны"),
                                callback=call, bot=bot)

        else:
            UserNotifier.notify('main_dashboard_button',
                                _strings.STRINGS.get("unknown_action",
                                                     "Неизвестное действие"),
                                callback=call, bot=bot)

    except Exception as e:                             # Непредвиденная ошибка
        logger.error("Ошибка при выполнении '%s': %s", action, e)
        UserNotifier.notify('main_dashboard_button', str(e)[:200], callback=call, bot=bot)
#====================================
#====================================
#====================================



if __name__ == "__main__":
    # Запускаем корневой движок, поднимая бота в рабочее состояние
    start_bot_engine()   # Вызов уже объявленной выше функции


### end of file

