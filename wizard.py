# Файл WIZARD.PY
# -*- coding: utf-8 -*-
"""
Модуль wizard.py – пошаговый конструктор отчётов.

Содержит:
- generate_report_link – создание одноразовой deep-link ссылки.
- start_report_wizard_engine – инициализация сессии и запуск Шага 1.
- render_wizard_dashboard_step – отрисовка текущего шага (1,2,3).
- handle_wizard_inline_callbacks – обработка нажатий кнопок (префикс 'w1_').
- logic_text_capture_step_3 – приём текстовых строк на Шаге 3.
- WizardKeyboardBuilder – класс для генерации инлайн-клавиатур (рефакторинг).

Сессии пользователей хранятся в словаре user_sessions (передаётся из main.py).
"""

import time  # Импортируем модуль времени для контроля задержек автоудаления окон
import uuid  # Импортируем генератор UUID для создания уникальных токенов коротких ссылок
import telebot  # Импортируем pyTelegramBotAPI
from telebot import types  # Подключаем типы интерфейсов Telegram
import config  # Подключаем глобальную конфигурацию (тайминги, флаги перехвата)
import database  # Подключаем базу данных для сборки отчетов и имен участников
from logger_config import logger
from unifier import CanonicalUnifier  # Подключаем DRY-унификатор окон подтверждения
from user_notifier import UserNotifier


# =====================================================================
# Класс для генерации клавиатур конструктора (рефакторинг, этап 3)
# =====================================================================

class WizardKeyboardBuilder:
    """
    Построитель инлайн-клавиатур для каждого шага мастера отчётов.
    Изолирует логику формирования кнопок от рендеринга.
    """

    @staticmethod
    def build_step1_keyboard(selected_users: list, members: list) -> types.InlineKeyboardMarkup:
        """
        Строит клавиатуру для Шага 1 (выбор участников).

        Аргументы:
            selected_users (list): список ID выбранных пользователей.
            members (list): список ID всех участников группы.

        Возвращает:
            InlineKeyboardMarkup: клавиатура с чекбоксами и кнопками Далее/Отмена.
        """
        markup = types.InlineKeyboardMarkup(row_width=2)
        # Кнопки участников
        import strings as _strings
        for uid in members:
            name = database.resolve_user_display_name(uid)
            marker = _strings.STRINGS.get("mark_checked") if uid in selected_users else _strings.STRINGS.get("mark_unchecked")
            markup.add(types.InlineKeyboardButton(text=f"{marker} {name}", callback_data=f"w1_tog_{uid}"))
        # Кнопки управления
        btn_next = types.InlineKeyboardButton(
            text=_strings.STRINGS.get("btn_next_text", "").format(len(selected_users)),
            callback_data="w1_next_1"
        )
        btn_abort = types.InlineKeyboardButton(text=_strings.STRINGS.get("btn_abort_action"), callback_data="w1_req_abort")
        markup.add(btn_next, btn_abort)
        return markup

    @staticmethod
    def build_step2_keyboard(has_photos: bool) -> types.InlineKeyboardMarkup:
        """
        Строит клавиатуру для Шага 2 (фотографии).

        Аргументы:
            has_photos (bool): есть ли уже загруженные фото.

        Возвращает:
            InlineKeyboardMarkup: клавиатура с кнопками Пропустить/Далее и Отмена.
        """
        markup = types.InlineKeyboardMarkup(row_width=2)
        import strings as _strings
        if has_photos:
            btn_action = types.InlineKeyboardButton(text=_strings.STRINGS.get("btn_next_photo"), callback_data="w1_next_2")
        else:
            btn_action = types.InlineKeyboardButton(text=_strings.STRINGS.get("btn_skip_photo"), callback_data="w1_next_2")
        btn_abort = types.InlineKeyboardButton(text=_strings.STRINGS.get("btn_abort_action"), callback_data="w1_req_abort")
        markup.add(btn_action, btn_abort)
        return markup

    @staticmethod
    def build_step3_keyboard() -> types.InlineKeyboardMarkup:
        """
        Строит клавиатуру для Шага 3 (ввод текста).

        Возвращает:
            InlineKeyboardMarkup: клавиатура с кнопками Опубликовать, Стереть строку, Отмена.
        """
        import strings as _strings
        markup = types.InlineKeyboardMarkup(row_width=2)
        btn_publish = types.InlineKeyboardButton(text=_strings.STRINGS.get("btn_publish"), callback_data="w1_publish")
        btn_erase = types.InlineKeyboardButton(text=_strings.STRINGS.get("btn_delete_last"), callback_data="w1_erase_line")
        btn_abort = types.InlineKeyboardButton(text=_strings.STRINGS.get("btn_abort_action"), callback_data="w1_req_abort")
        markup.add(btn_publish)
        markup.add(btn_erase, btn_abort)
        return markup


def generate_report_link(chat_id: int, thread_id: int, bot: telebot.TeleBot) -> str:
    """
    Генерирует уникальную защищенную короткую deep-link ссылку для безопасного 
    перехода пользователей из группы в ЛС бота для сборки отчета (п. 3.2 Манифеста).
    """
    token = str(uuid.uuid4())[:8]  # Создаем укороченный уникальный хэш-токен сессии
    database.create_gateway_token(token, chat_id, thread_id)  # Прописываем токен в шлюз БД
    #return f"https://t.me{(types.User().username or 'bot')}?start={token}"  # Возвращаем готовую ссылку
    bot_info = bot.get_me()  # Запрашиваем актуальные метаданные у Telegram API (работает на любом токене)
    # Собираем адрес по кусочкам через сложение строк, чтобы гарантировать наличие разделителей
    prefix = "https://t.me"
    slash = "/"
    bot_name = bot_info.username
    param = "?start="
    
    return prefix + slash + bot_name + param + token

def start_report_wizard_engine(message: telebot.types.Message, target_chat_id: int, target_thread_id: int, bot: telebot.TeleBot, user_sessions: dict) -> None:
    """
    Инициализатор интерактивного движка пошагового Конструктора отчетов в ЛС.
    Формирует пустую ОЗУ-структуру сессии и разворачивает интерфейс Шага 1 (п. 5.2).
    """
    user_id = message.from_user.id  # Идентификатор собирающего пользователя
    # Синхронизировать список участников группы (актуально)
    database.sync_group_users(bot, target_chat_id, target_thread_id)
    # Инициализируем чистую структуру сессии в оперативной памяти (ОЗУ) бота
    user_sessions[user_id] = {
        "chat_id": target_chat_id,        # Целевая группа публикации
        "thread_id": target_thread_id,    # Целевой топик публикации
        "step": 1,                        # Текущий активный шаг мастера (Шаг 1: Кто участвует)
        "selected_users": [],             # Список ID выбранных участников смены
        "strings": [],                    # Массив введенных текстовых строк отчета
        "photos": [],                     # Массив файловых ID загруженных фотографий
        "last_dash_id": None              # ID текущего сообщения-дашборда для его обновления/удаления
        #УДАЛЁН – теперь не требуется, так как хэндлер сам проверяет существование сессии
        #"last_prompt_msg": None   # ← Добавить: сюда будем сохранять сообщение-промпт для очистки хэндлера
    }
    
    # Разворачиваем графический дашборд Шага 1
    render_wizard_dashboard_step(user_id, bot, user_sessions)

def render_wizard_dashboard_step(user_id: int, bot: telebot.TeleBot, user_sessions: dict) -> None:
    """
    Отрисовывает текущий дашборд конструктора в ЛС пользователя.
    Использует WizardKeyboardBuilder для генерации клавиатур.

    Аргументы:
        user_id (int): ID пользователя.
        bot (telebot.TeleBot): экземпляр бота.
        user_sessions (dict): словарь сессий.
    """
    session = user_sessions.get(user_id)
    if not session:
        return

    step = session["step"]
    markup = None
    text = ""
    import strings as _strings
    # Шаг 1: выбор участников
    if step == 1:
        text = _strings.STRINGS.get("step_1", "").format(config.VERSION)
        members = database.get_group_members(session["chat_id"], session["thread_id"])
        markup = WizardKeyboardBuilder.build_step1_keyboard(session["selected_users"], members)
    
    # Шаг 2: фотографии
    elif step == 2:
        photo_count = len(session["photos"])
        if photo_count == 0:
            text = _strings.STRINGS.get("step_2", "")
        else:
            text = _strings.STRINGS.get("step_2_with_photos", "").format(photo_count)
        markup = WizardKeyboardBuilder.build_step2_keyboard(photo_count > 0)

    # Шаг 3: ввод текста
    elif step == 3:
        grp_set = database.get_group_settings(session["chat_id"], session["thread_id"])
        preview = f"{grp_set['headline']}\n"
        if not session["strings"]:
            preview += _strings.STRINGS.get("preview_empty", "")
        else:
            for s in session["strings"]:
                preview += f"{grp_set['line_mark']} {s}\n"
        text = f"{_strings.STRINGS.get('step_3', '')}\n\n{_strings.STRINGS.get('preview_title', '')}{preview}"
        markup = WizardKeyboardBuilder.build_step3_keyboard()

    # Отправка или редактирование сообщения
    if session.get("last_dash_id"):
        try:
            bot.edit_message_text(chat_id=user_id, message_id=session["last_dash_id"], text=text, reply_markup=markup, parse_mode="Markdown")
            return
        except Exception:
            pass
    msg = bot.send_message(chat_id=user_id, text=text, reply_markup=markup, parse_mode="Markdown")
    session["last_dash_id"] = msg.message_id

def handle_wizard_inline_callbacks(call: telebot.types.CallbackQuery, bot: telebot.TeleBot, user_sessions: dict) -> None:
    """
    Глобальный обработчик инлайн-событий конструктора (префикс 'w1_').
    Обеспечивает переключение шагов, триггеры чекбоксов и вызов Канонического Унификатора.
    """
    user_id = call.from_user.id  # Кто нажал на кнопку
    session = user_sessions.get(user_id)  # Извлекаем ОЗУ-сессию
    import strings as _strings
    if not session:
        UserNotifier.notify('report_wizard_button',
                            _strings.STRINGS.get("gateway_error_missing"),
                            callback=call, bot=bot)
        return
        # При клике на любую кнопку на Шаге 3 не нужно очищать хэндлер, так как
    # logic_text_capture_step_3 при следующем вызове проверит сессию и шаг и корректно выйдет.
    # Оставляем только лог для отладки (опционально).
    if session.get("step") == 3:
        logger.debug(f"[LOG/FSM]: Пользователь {user_id} нажал кнопку на Шаге 3 (хэндлер остаётся, но безопасен).")
    
    data = call.data
    bot.answer_callback_query(callback_query_id=call.id)  # Гасим часики анимации кнопки в Telegram

    # --- ТРИГГЕР ЧЕКБОКСА: Клик по участнику (w1_tog_[ID]) ---
    if data.startswith("w1_tog_"):
        target_uid = int(data.split("_")[2])
        if target_uid in session["selected_users"]:
            session["selected_users"].remove(target_uid)  # Выключаем чекбокс
        else:
            session["selected_users"].append(target_uid)  # Включаем чекбокс
        render_wizard_dashboard_step(user_id, bot, user_sessions)  # Мгновенно перерисовываем экран
        return

    # --- ПЕРЕХОД: Шаг 1 -> Шаг 2 (Клик 'w1_next_1') ---
    if data == "w1_next_1":
        if not session["selected_users"]:
            # Нарушение бизнес-логики: нельзя создать отчет без людей. Шлем тихий алерт
            UserNotifier.notify('report_wizard_button',
                                _strings.STRINGS.get("alert_select_users"),
                                callback=call, bot=bot)
            return
        session["step"] = 2  # Переводим статус сессии на Шаг 2
        render_wizard_dashboard_step(user_id, bot, user_sessions)
        return

    # --- ПЕРЕХОД: Шаг 2 -> Шаг 3 (Клик 'w1_next_2') ---
    if data == "w1_next_2":
        session["step"] = 3  # Переводим статус на Шаг 3 (Ввод текста)
        render_wizard_dashboard_step(user_id, bot, user_sessions)
        
        # Активируем Next Step Handler для непрерывного поглощения входящего текста от юзера в ЛС
        # ТОЧЕЧНАЯ ПРАВКА: Перевод подсказки Шага 3 на универсальный шлюз вывода unifier.py (Принцип DRY)
        from unifier import CanonicalUnifier
        msg_prompt = CanonicalUnifier.send_service_delivery_gate(
            bot=bot,
            chat_id=user_id,
            text_key="step_1_continue",  # Ключ строки продолжения ввода из STRINGS.py
            is_temporary=False           # Подсказка должна оставаться на экране во время ввода текста
        )
         # Регистрируем хэндлер на это сообщение. 
        # Хэндлер сам проверит, существует ли сессия и нужный шаг, поэтому не нужно сохранять msg_prompt.
        bot.register_next_step_handler(msg_prompt, logic_text_capture_step_3, bot=bot, user_sessions=user_sessions)
        
        return

    # --- ОЧИСТКА: Стереть последнюю введенную строку в отчете (w1_erase_line) ---
    if data == "w1_erase_line":
        if session["strings"]:
            session["strings"].pop()  # Выбрасываем последний элемент из массива строк отчета
        render_wizard_dashboard_step(user_id, bot, user_sessions)
        return
        # --- ОПУБЛИКОВАТЬ ОТЧЕТ (w1_publish) ---
    
    if data == "w1_publish":
        try:
            # Получаем настройки оформления отчёта для целевой группы
            grp_set = database.get_group_settings(session["chat_id"], session["thread_id"])
            if not grp_set:
                raise Exception("Группа не настроена")  # Если настроек нет — ошибка
            # Начинаем собирать текст отчёта
            import strings as _strings
            report_text = _strings.STRINGS.get("report_header", "").format(grp_set['headline'])
            # Формируем список имён выбранных участников
            user_names = []
            for uid in session["selected_users"]:
                name = database.resolve_user_display_name(uid)  # Получаем отображаемое имя по приоритету
                user_names.append(name)
            # Добавляем строку со списком участников, разделённых заданным разделителем\
            import strings as _strings
            report_text += _strings.STRINGS.get("report_user_list", "").format(grp_set['separator'].join(user_names))
            # Добавляем все текстовые строки отчёта, каждую с маркером
            for line in session["strings"]:
                import strings as _strings
                report_text += _strings.STRINGS.get("report_line", "").format(grp_set['line_mark'], line)
            # Определяем ID топика (если 0, то отправляем в основной чат)
            target_thread = session["thread_id"] if session["thread_id"] != 0 else None
            # Если есть фотографии, отправляем их медиагруппой
            if session["photos"]:
                # Создаём список объектов InputMediaPhoto из file_id
                media_group = [types.InputMediaPhoto(media=photo) for photo in session["photos"]]
                bot.send_media_group(chat_id=session["chat_id"], message_thread_id=target_thread, media=media_group)
            # Отправляем текстовое сообщение с отчётом
            bot.send_message(chat_id=session["chat_id"], message_thread_id=target_thread, text=report_text, parse_mode="HTML")
            
            
            
            
            # --- Очистка ЛС после успешной публикации ---
            # Удаляем все сообщения пользователя (фото, текстовые вводы)
            for msg_id in session.get("user_message_ids", []):
                try:
                    bot.delete_message(user_id, msg_id)
                except Exception:
                    pass
            # Удаляем сам дашборд конструктора
            if session.get("last_dash_id"):
                try:
                    bot.delete_message(user_id, session["last_dash_id"])
                except Exception:
                    pass
             # Удаляем сессию из ОЗУ
            del user_sessions[user_id]
            # Очистка хэндлера не требуется, так как сессия удалена, а функция logic_text_capture_step_3 
            # при следующем вызове выйдет по проверке session
            
            
            # Отправляем подтверждение об успехе (временное сообщение)
            UserNotifier.notify('report_wizard_button',
                                _strings.STRINGS.get("publish_success"),
                                bot=bot, chat_id=user_id)
        except Exception as e:
            # В случае любой ошибки выводим временное сообщение с её текстом
            UserNotifier.notify('report_wizard_button',
                                _strings.STRINGS.get("publish_fail").format(str(e)),
                                bot=bot, chat_id=user_id)
        return

    # --- ВЫЗОВ УНИФИКАТОРА: Запрос прерывания конструктора (w1_req_abort) ---
    if data == "w1_req_abort":
        # Генерируем DRY-клавиатуру двухэтапного подтверждения через наш выделенный класс (п. 4.4)
        # ТОЧЕЧНАЯ ПРАВКА: Перевод генерации диалога отмены на универсальный шлюз send_dialog_confirm_gate
        from unifier import CanonicalUnifier
        CanonicalUnifier.send_dialog_confirm_gate(
            bot=bot,
            chat_id=user_id,
            text_key="msg_abort_confirm",  # Строковый ключ вопроса из STRINGS.py
            yes_callback="w1_confirm_abort",  # Куда слать сигнал при согласии
            no_callback="w1_deny_abort"       # Куда слать сигнал при отказе
        )
        return

    # --- УНИФИКАТОР: Подтверждение полной отмены (w1_confirm_abort) ---
    if data == "w1_confirm_abort":
        # Полностью зачищаем ОЗУ-сессию пользователя, уничтожая все введенные им данные
        if user_id in user_sessions:
        
            # Удаляем все сообщения пользователя, сохранённые в сессии
            for msg_id in user_sessions[user_id].get("user_message_ids", []):
                try:
                    bot.delete_message(user_id, msg_id)
                except Exception:
                    pass
            
            
            # Больше не нужно очищать хэндлер, так как функция logic_text_capture_step_3 сама проверит сессию
            ## Очищаем активный хэндлер ввода, если он был
            #if user_sessions[user_id].get("last_prompt_msg"):
            #    try:
            #        bot.clear_step_handler(user_sessions[user_id]["last_prompt_msg"])
            #    except Exception:
            #        pass
            
            
            # Удаляем сам дашборд конструктора
            if user_sessions[user_id].get("last_dash_id"):
                try:
                    bot.delete_message(user_id, user_sessions[user_id]["last_dash_id"])
                except Exception:
                    pass
            del user_sessions[user_id]
            #bot.clear_step_handler_by_chat_id(chat_id=user_id)
            # В текущей версии telebot метода clear_step_handler_by_chat_id нет, 
            # поэтому оставляем заглушку. Хэндлеры сами отвалятся при получении следующего сообщения.
            try:
                # Пытаемся очистить через штатный метод, если он есть
                bot.clear_step_handler(call.message)
            except Exception:
                pass
            logger.debug(f"[LOG/FSM]: Перехватчик ввода для {user_id} принудительно сброшен при отмене мастера.")
        UserNotifier.notify('report_wizard_button',
                            _strings.STRINGS.get("msg_wizard_aborted"),
                            bot=bot, chat_id=user_id)
        return

# ТОЧЕЧНАЯ ПРАВКА: Реализация упущенного пошагового поглотителя текста Шага 3 (Оживление конструктора)
def logic_text_capture_step_3(message: telebot.types.Message, bot: telebot.TeleBot, user_sessions: dict) -> None:
    """
    Перехватчик текстовых сообщений на Шаге 3 конструктора.
    Добавляет строку в отчёт, удаляет исходное сообщение пользователя и перерисовывает дашборд.
    Если сессии нет или шаг не 3 — просто игнорирует (без ошибок).
    """
    user_id = message.from_user.id                         # ID пользователя, отправившего текст
    session = user_sessions.get(user_id)                   # Получаем его сессию конструктора
    
    # ===== ГЛАВНАЯ ЗАЩИТА ОТ «ВИСЯЧИХ» ХЭНДЛЕРОВ =====
    # Если сессия отсутствует (пользователь уже вышел из мастера) ИЛИ это не Шаг 3 — выходим.
    # Это автоматически делает безопасным любой устаревший хэндлер.
    if not session or session.get("step") != 3:
        # Для поддержания чистоты чата удаляем сообщение пользователя, если оно есть
        try:
            bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
        except Exception:
            pass
        return
    
    # ---- Проверка перехвата команд (п. 4.3 Манифеста) ----
    intercept = database.get_intercept_flag(session["chat_id"], session["thread_id"])
    if intercept and message.text.startswith('/'):
        # Пользователь ввёл команду в режиме ввода текста — прерываем мастер
        # Удаляем сообщение с командой
        try:
            bot.delete_message(message.chat.id, message.message_id)
        except Exception:
            pass
        # Уничтожаем сессию
        if user_id in user_sessions:
            del user_sessions[user_id]
        # Выводим сообщение об отмене через унифицированный шлюз
        UserNotifier.notify('report_wizard_button',
                            _strings.STRINGS.get("abort_success"),
                            bot=bot, chat_id=user_id)
        return
    
    # ---- Основная логика добавления строки ----
    # Добавляем полученную строку в массив отчёта
    session["strings"].append(message.text)
    
    # Сохраняем ID сообщения пользователя для последующей очистки чата (п. 1.2.1)
    if "user_message_ids" not in session:
        session["user_message_ids"] = []
    session["user_message_ids"].append(message.message_id)
    
    # Стерилизуем чат: удаляем исходное сообщение пользователя (чтобы не засорять ЛС)
    try:
        bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
    except Exception as err:
        logger.debug("⚠️[Стерильность Конструктора]: Не удалось стереть строку: " + str(err))
    
    # Перерисовываем дашборд с обновлённым предпросмотром
    render_wizard_dashboard_step(user_id, bot, user_sessions)
    
    # ---- Повторная регистрация хэндлера для следующего ввода ----
    # Отправляем новое сообщение-приглашение для продолжения ввода
    from unifier import CanonicalUnifier
    new_prompt = CanonicalUnifier.send_service_delivery_gate(
        bot=bot,
        chat_id=user_id,
        text_key="step_1_continue",
        is_temporary=False,
        parse_mode="Markdown"
    )
    # Регистрируем следующий шаг (рекурсивно). Не сохраняем new_prompt в сессию, так как
    # при следующем вызове мы будем полагаться только на проверку session и step.
    bot.register_next_step_handler(new_prompt, logic_text_capture_step_3, bot=bot, user_sessions=user_sessions)
    
    # Логируем успешное добавление строки
    logger.debug("[LOG/WIZARD]: Строка успешно добавлена в ОЗУ для пользователя: " + str(user_id))






### end of file
