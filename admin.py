### Файл ADMIN.PY ###
# -*- coding: utf-8 -*-
"""
Модуль admin.py – административная панель управления.

Содержит:
- open_admin_dashboard – развёртывание интерактивного дашборда настроек в ЛС.
- process_admin_inline_callbacks – маршрутизация callback-запросов с префиксом 'adm_'.
- save_input_parameter_handler – сохранение изменённых параметров (заголовок, маркер, разделитель).
- process_install_command – генерация deep-link ссылки для установки панели в группе.
- admin_add_user_handler, admin_select_tag_user, admin_set_tag_handler – управление пользователями и тегами.

Все текстовые строки берутся из STRINGS.
"""

import telebot  # Импортируем pyTelegramBotAPI
from telebot import types  # Подключаем типы разметки интерфейсов
import database  # Подключаем слой данных SQLite3
import time # ИНЪЕКЦИЯ: Исправляем падение парсера времени на Amvera
import uuid  # ИНЪЕКЦИЯ: Добавляем импорт модуля для генерации токенов установки
import config  # ИНЪЕКЦИЯ: Добавляем импорт файла конфигурации проекта
from logger_config import logger
from user_notifier import UserNotifier

def open_admin_dashboard(message: telebot.types.Message, param: str, bot: telebot.TeleBot) -> None:
    """
    Развертывание интерактивного монолитного дашборда настроек администратора в ЛС (п. 5.3).
    Парсит входящий параметр, определяет целевую группу и выводит панель конфигурации.
    """
    user_id = message.from_user.id  # Идентификатор администратора
    import strings as _strings
    # ТОЧЕЧНАЯ ПРАВКА: Интеграция одноразовых UUID-токенов шлюза (п. 3.2 Манифеста) и защита от падения парсера
    
    
    # Если param не начинается с "adm_", значит это токен шлюза (UUID)
    if not param.startswith("adm_"):
        
        gateway_data = database.pop_gateway_token(param)  # Однократно извлекаем и удаляем токен из СУБД
        if not gateway_data:
            raise Exception(_strings.STRINGS.get("gateway_error_missing", "Ошибка шлюза"))
        param = f"adm_{gateway_data[0]}_{gateway_data[1]}"
        
        
        
        
    # Извлекаем параметры чата из deep-link строки (формат: adm_CHATID_THREADID)
    parts = param.split("_")    # Безопасно дробим уже гарантированно канонизированную строку параметров
    if len(parts) < 3:        
        raise Exception(_strings.STRINGS.get("gateway_error_nan", "Неверный параметр"))
        
    try:
        chat_id = int(parts[1])
        thread_id = int(parts[2])
    except ValueError:
        raise Exception(_strings.STRINGS.get("gateway_error_nan", "Неверный параметр"))

    # Подтягиваем текущие рабочие параметры разметки отчетов из БД
    settings = database.get_group_settings(chat_id, thread_id)
    
    # Формируем заголовок панели с текущими метаданными (п. 5.3)
    
    header_text = _strings.STRINGS.get("pm_admin_header", "").format(chat_id, thread_id)
    body_text = (
        f"{_strings.STRINGS.get('settings_title', '')}\n\n"
        f"📝 Заголовок: `{settings['headline']}`\n"
        f"✏️ Маркер строки: `{settings['line_mark']}`\n"
        f"🔗 Разделитель имен: `{settings['separator']}`"
    )
    
        # Создаём объект инлайн-клавиатуры с двумя кнопками в ряду
    markup = types.InlineKeyboardMarkup(row_width=2)
    # Первый ряд: кнопки редактирования заголовка, маркера, разделителя
    markup.add(
        types.InlineKeyboardButton(text=_strings.STRINGS.get("btn_edit_head_admin"), callback_data=f"adm_ed_headline_{chat_id}_{thread_id}"),
        types.InlineKeyboardButton(text=_strings.STRINGS.get("btn_edit_mark_admin"), callback_data=f"adm_ed_line_mark_{chat_id}_{thread_id}"),
        types.InlineKeyboardButton(text=_strings.STRINGS.get("btn_edit_sep_admin"), callback_data=f"adm_ed_separator_{chat_id}_{thread_id}")
    )
    # Второй ряд: кнопки добавления пользователя и тега
    markup.add(
        types.InlineKeyboardButton(text=_strings.STRINGS.get("btn_add_user_admin"), callback_data=f"adm_add_user_{chat_id}_{thread_id}"),
        types.InlineKeyboardButton(text=_strings.STRINGS.get("btn_add_tag_admin"), callback_data=f"adm_add_tag_{chat_id}_{thread_id}")
    )
     # Получить текущее состояние флага для этой группы из БД
    current_flag = database.get_intercept_flag(chat_id, thread_id)
    flag_text = "Вкл" if current_flag else "Выкл"
    
    
    # Третий ряд: кнопка переключения перехвата команд
    markup.add(
        types.InlineKeyboardButton(
            text=_strings.STRINGS.get("btn_intercept_toggle").format(flag_text),
            callback_data=f"adm_toggle_intercept_{chat_id}_{thread_id}"
        )
    )
    # Четвёртый ряд: кнопки перезагрузки строк, удаления бота и сброса контекста
    markup.add(
        types.InlineKeyboardButton(text=_strings.STRINGS.get("btn_reload_admin"), callback_data=f"adm_reload_{chat_id}_{thread_id}"),
        types.InlineKeyboardButton(text=_strings.STRINGS.get("btn_uninstall_admin"), callback_data=f"adm_uninstall_{chat_id}_{thread_id}"),
        types.InlineKeyboardButton(text=_strings.STRINGS.get("btn_abort_admin"), callback_data=f"adm_abort_{chat_id}_{thread_id}")
    )
    
    markup.add(
        types.InlineKeyboardButton(text="♻️ Restart Bot", callback_data=f"adm_restart_{chat_id}_{thread_id}")
    )
    
    bot.send_message(chat_id=user_id, text=header_text + body_text, reply_markup=markup, parse_mode="Markdown")
    
        
        
def process_admin_inline_callbacks(call: telebot.types.CallbackQuery, bot: telebot.TeleBot) -> None:
    """
    Маршрутизатор входящих инлайн-кликов панели настроек администратора.
    Обеспечивает переход в режим текстового ожидания ввода (Next Step Handler).
    """
    global STRINGS
    user_id = call.from_user.id  # Идентификатор кликнувшего админа
    data_parts = call.data.split("_")  # Дробим callback-строку
    
    # Очищаем нажатие кнопки в интерфейсе Telegram
    bot.answer_callback_query(callback_query_id=call.id)
    import strings as _strings
    
    # Кейс изменения текстовых параметров: adm_ed_[ПОЛЕ]_[CHAT]_[THREAD]
    if data_parts[1] == "ed":
        field = data_parts[2]
        chat_id = int(data_parts[3])
        thread_id = int(data_parts[4])
        
        # Отправляем сервисное сообщение-запрос ввода
        prompt_msg = bot.send_message(
            chat_id=user_id, 
            text=_strings.STRINGS.get("admin_edit_title", "").format(field), 
            parse_mode="Markdown"
        )
        
        # Регистрируем перехват следующего текстового сообщения от этого админа
        bot.register_next_step_handler(
            prompt_msg, 
            save_input_parameter_handler, 
            bot=bot, 
            field=field, 
            chat_id=chat_id, 
            thread_id=thread_id
        )
        
        # ... существующий код для data_parts[1] == "ed" ...

    # Кейс добавления пользователя (временно заглушка)
    if len(data_parts) > 1 and data_parts[1] == "add_user":
        chat_id = int(data_parts[2])
        thread_id = int(data_parts[3])        
        #msg = bot.send_message(user_id, "Введите числовой ID пользователя для добавления в группу:")
        # Отправляем запрос на ввод ID через унифицированный сервисный шлюз (п. 0.2.2)
        # Сохраняем объект сообщения для регистрации следующего шага
        msg = CanonicalUnifier.send_service_delivery_gate(
            bot=bot,
            chat_id=user_id,
            text_key="admin_enter_user_id",   
            is_temporary=False                # Сообщение должно оставаться до ответа пользователя
        )
        bot.register_next_step_handler(msg, admin_add_user_handler, bot=bot, chat_id=chat_id, thread_id=thread_id)
        return
        # Показываем всплывающее сообщение о том, что функция в разработке
        bot.answer_callback_query(call.id, text="Функция в разработке", show_alert=True)
        return

    # Кейс добавления тега (временно заглушка)
    if len(data_parts) > 1 and data_parts[1] == "add_tag":
        chat_id = int(data_parts[2])
        thread_id = int(data_parts[3])
        # Получить список участников группы
        members = database.get_group_members(chat_id, thread_id)
        if not members:
            bot.send_message(user_id, "В группе нет участников (кроме админов).")
            return
        markup = types.InlineKeyboardMarkup(row_width=1)
        for uid in members:
            name = database.resolve_user_display_name(uid)
            markup.add(types.InlineKeyboardButton(text=name, callback_data=f"adm_seltag_{uid}_{chat_id}_{thread_id}"))
        bot.send_message(user_id, "Выберите пользователя для назначения тега:", reply_markup=markup)
        return
        bot.answer_callback_query(call.id, text="Функция в разработке", show_alert=True)
        return

    # Кейс переключения глобального флага перехвата команд
    if len(data_parts) > 1 and data_parts[1] == "toggle_intercept":
        chat_id = int(data_parts[2])
        thread_id = int(data_parts[3])
        # Получить текущее значение
        current = database.get_intercept_flag(chat_id, thread_id)
        # Инвертировать и сохранить в БД
        database.set_intercept_flag(chat_id, thread_id, not current)
        # Перерисовать дашборд
        open_admin_dashboard(call.message, f"adm_{chat_id}_{thread_id}", bot)
        return

    # Кейс перезагрузки строк (reload)
    if len(data_parts) > 1 and data_parts[1] == "reload":
        try:
            # Перезагружаем модуль STRINGS.py и обновляем глобальную переменную STRINGS
            import importlib, strings
            importlib.reload(strings)
            
            STRINGS = _strings.STRINGS.STRINGS
            # Показываем временный алерт об успехе
            UserNotifier.notify('admin_settings_button',
                                _strings.STRINGS.get("reload_success"),
                                callback=call, bot=bot)
            # Перерисовываем дашборд, чтобы использовать обновлённые строки
            open_admin_dashboard(call.message, f"adm_{chat_id}_{thread_id}", bot)
        except Exception as e:
            # В случае ошибки выводим алерт с текстом исключения
            UserNotifier.notify('admin_settings_button',
                                _strings.STRINGS.get("reload_fail").format(str(e)),
                                callback=call, bot=bot)
        return

    # Кейс запроса на удаление бота из группы (uninstall)
    if len(data_parts) > 1 and data_parts[1] == "uninstall":
        # Вызываем унифицированное диалоговое окно подтверждения
        from unifier import CanonicalUnifier
        CanonicalUnifier.send_dialog_confirm_gate(
            bot=bot, chat_id=user_id, text_key="uninstall_confirm",
            yes_callback=f"adm_confirm_uninstall_{chat_id}_{thread_id}",
            no_callback=f"adm_cancel_uninstall_{chat_id}_{thread_id}"
        )
        return

    # Кейс сброса контекста администрирования (abort)
    if len(data_parts) > 1 and data_parts[1] == "abort":
        # Импортируем глобальный словарь admin_contexts из main.py
        from main import admin_contexts
        if user_id in admin_contexts:
            del admin_contexts[user_id]  # Удаляем запись о контексте
        # Показываем всплывающее уведомление
        # Используем бесшумный алерт с текстом из STRINGS (п. 0.2.3)
        import strings as _strings
        UserNotifier.notify('admin_settings_button',
                            _strings.STRINGS.get("admin_context_reset"),
                            callback=call, bot=bot)
        # Удаляем само сообщение дашборда настроек
        try:
            bot.delete_message(user_id, call.message.message_id)
        except Exception:
            pass
        return    
        
    if len(data_parts) > 1 and data_parts[1] == "restart":
        chat_id = int(data_parts[2])
        thread_id = int(data_parts[3])
        # Отправить диалог подтверждения (используем унификатор)
        from unifier import CanonicalUnifier
        CanonicalUnifier.send_dialog_confirm_gate(
            bot=bot, chat_id=user_id, text_key="restart_confirm",
            yes_callback=f"sys_confirm_restart",  # тот же, что и для команды
            no_callback=f"sys_deny_restart"
        )
        return




def save_input_parameter_handler(message: telebot.types.Message, bot: telebot.TeleBot, field: str, chat_id: int, thread_id: int) -> None:
    """
    Текстовый шлюз-перехватчик. Принимает введенную админом строку, сохраняет в БД 
    и высылает подтверждение, завершая сессию редактирования.
    """
    user_id = message.from_user.id  # Идентификатор админа
    new_value = message.text  # Введенный текст параметра
    
    # Записываем изменения в базу данных SQLite3 через потокобезопасный слой
    database.save_group_setting(chat_id, thread_id, field, new_value)
    # Обновить дашборд в группе через менеджер
    from main import _dashboard_manager
    if _dashboard_manager:
        _dashboard_manager.refresh(chat_id, thread_id)
    # Отправляем сообщение об успешном сохранении по протоколу    
    UserNotifier.notify('admin_settings_button',
                        _strings.STRINGS.get("admin_save_ok"),
                        bot=bot, chat_id=user_id)
    # Генерируем искусственный параметр для авто-возврата админа в обновленный дашборд
    return_param = f"adm_{chat_id}_{thread_id}"
    open_admin_dashboard(message, return_param, bot)

def process_install_command(message: telebot.types.Message, bot: telebot.TeleBot) -> None:
    
    from main import _dashboard_manager
        
    # Извлекаем уникальный числовой идентификатор текущей группы или супергруппы Telegram
    chat_id = message.chat.id
    # Проверяем наличие идентификатора темы (топика) внутри форума, иначе присваиваем ноль
    thread_id = message.message_thread_id if message.message_thread_id else 0
    
    # Генерируем одноразовый UUID-токен шлюза через базу данных для защиты перехода в ЛС
    # Заменить строку: gateway_token = database.create_gateway_token(chat_id=chat_id, thread_id=thread_id)
    gateway_token = str(uuid.uuid4())[:8] # ИНЪЕКЦИЯ: Генерируем 8-значный хэш на стороне административного модуля
    database.create_gateway_token(secure_token=gateway_token, chat_id=chat_id, thread_id=thread_id) # ИНЪЕКЦИЯ: Пишем в БД по новой сигнатуре
    # Запрашиваем у серверов Telegram API актуальный системный юзернейм нашего бота
    bot_username = bot.get_me().username
    
    import strings as _strings
    # Сохраняем настройки группы в БД (дефолтные значения)
    def_headline = _strings.STRINGS.get("default_headline", "Отчет по дежурству")
    def_line_mark = _strings.STRINGS.get("default_line_mark", "⚪️")
    def_separator = _strings.STRINGS.get("default_separator", ", ")
    # Временно сохраняем dashboard_msg_id = 0, т.к. менеджер сам создаст сообщение
    database.save_installation(
        chat_id=chat_id,
        thread_id=thread_id,
        dashboard_msg_id=0,   # временный ID, будет обновлён менеджером
        headline=def_headline,
        line_mark=def_line_mark,
        separator=def_separator
    )
    
    # Вызываем менеджер для создания/обновления дашборда
    if _dashboard_manager:
        _dashboard_manager.create_or_update(chat_id, thread_id)
    
    logger.debug(f"✅ [Монтаж]: Дашборд инициализирован в чате {chat_id} | Топик {thread_id}")



def admin_add_user_handler(message: telebot.types.Message, bot: telebot.TeleBot, chat_id: int, thread_id: int) -> None:
    user_id_admin = message.from_user.id
    try:
        new_uid = int(message.text.strip())
    except:
        # Выводим временное предупреждение через шлюз (автоудаление через TEMP_MSG_DELAY)
        UserNotifier.notify('admin_settings_button',
                            _strings.STRINGS.get("admin_invalid_user_id"),
                            bot=bot, chat_id=user_id_admin)
        return
    # Проверить, существует ли пользователь в Telegram и не админ ли
    try:
        member = bot.get_chat_member(chat_id, new_uid)
        if member.status in ("creator", "administrator") or member.user.is_bot:
            UserNotifier.notify('admin_settings_button',
                                _strings.STRINGS.get("admin_cannot_add_admin_bot"),
                                bot=bot, chat_id=user_id_admin)
            return
        # Добавить в users
        database.register_or_update_user(new_uid, member.user.username or "", None)
        # Добавить в group_members
        with database.DatabaseConnectionContext(database.DB_PATH, database.db_lock) as cursor:
            cursor.execute("INSERT OR IGNORE INTO group_members (chat_id, thread_id, user_id) VALUES (?, ?, ?)",
                           (chat_id, thread_id, new_uid))
        UserNotifier.notify('admin_settings_button',
                            _strings.STRINGS.get("admin_user_added"),
                            bot=bot, chat_id=user_id_admin)
        # Обновить дашборд настроек
        open_admin_dashboard(message, f"adm_{chat_id}_{thread_id}", bot)
    except Exception as e:
        # Используем форматирование через .format() для избежания f-строк (п. 8 Протокола)
        import strings as _strings
        error_text = _strings.STRINGS.get("admin_add_user_error").format(str(e))
        # Отправляем через шлюз как временное сообщение
        UserNotifier.notify('admin_settings_button',
                            error_text,
                            bot=bot, chat_id=user_id_admin)


# Данная функция НЕ использует декоратор, она вызывается вручную из main.py
# через admin.process_admin_inline_callbacks (который уже обрабатывает префикс "adm_seltag_")
def admin_select_tag_user(call):
    user_id_admin = call.from_user.id
    parts = call.data.split("_")
    target_uid = int(parts[2])
    chat_id = int(parts[3])
    thread_id = int(parts[4])
    bot.answer_callback_query(call.id)
    msg = CanonicalUnifier.send_service_delivery_gate(
        bot=bot,
        chat_id=user_id_admin,
        text_key="admin_enter_new_tag",
        is_temporary=False
    )
    bot.register_next_step_handler(msg, admin_set_tag_handler, bot=bot, target_uid=target_uid, chat_id=chat_id, thread_id=thread_id)

def admin_set_tag_handler(message: telebot.types.Message, bot: telebot.TeleBot, target_uid: int, chat_id: int, thread_id: int) -> None:
    user_id_admin = message.from_user.id
    from main import _dashboard_manager
    new_tag = message.text.strip()
    if not new_tag:
        UserNotifier.notify('admin_settings_button',
                            _strings.STRINGS.get("admin_tag_empty"),
                            bot=bot, chat_id=user_id_admin)
        return
    # Обновить display_name в users
    with database.DatabaseConnectionContext(database.DB_PATH, database.db_lock) as cursor:
        cursor.execute("UPDATE users SET display_name = ? WHERE user_id = ?", (new_tag, target_uid))
    
    # Обновить групповой дашборд (чтобы отобразилось новое имя пользователя)
    if _dashboard_manager:
        _dashboard_manager.refresh(chat_id, thread_id)
    import strings as _strings
    # Формируем текст через .format() без f-строк
    success_text = _strings.STRINGS.get("admin_tag_set").format(target_uid, new_tag)
    UserNotifier.notify('admin_settings_button',
                        success_text,
                        bot=bot, chat_id=user_id_admin)
    
    
    
    # Вернуться в дашборд настроек
    open_admin_dashboard(message, f"adm_{chat_id}_{thread_id}", bot)
    
    
    
    
    
    
    
    



### end of file ###

    