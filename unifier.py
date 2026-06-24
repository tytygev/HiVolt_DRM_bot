# Файл UNIFIER.PY
# -*- coding: utf-8 -*-
"""
Модуль unifier.py – канонический унификатор интерфейсов.

Содержит класс CanonicalUnifier со статическими методами для:
- Генерации стандартных диалогов подтверждения (generate_confirm_keyboard).
- Отправки сервисных сообщений с поддержкой автоудаления (send_service_delivery_gate).
- Отправки диалоговых окон с кнопками Да/Нет (send_dialog_confirm_gate).

Реализует принцип DRY и централизует все выводы текстов из STRINGS.py.
"""

import telebot  # Импортируем главный модуль telebot для аннотаций типов (TeleBot)
from telebot import types  # Импортируем типы данных Telegram API для сборки клавиатур
from logger_config import logger

class CanonicalUnifier:
    """
    Канонический Унификатор окон подтверждения (Реализация принципа DRY и п. 4.4 Манифеста).
    Генерирует стандартизированные двухэтапные клавиатуры подтверждения («Да» / «Нет»)
    для любых разрушительных или системных действий в боте.
    """
    
    @staticmethod
    def generate_confirm_keyboard(yes_callback, no_callback, item_id=None):
        """
        Создаёт инлайн-клавиатуру с кнопками «Да» и «Нет».

        Аргументы:
            yes_callback (str): callback_data для кнопки подтверждения.
            no_callback (str): callback_data для кнопки отмены.
            item_id (int | None): идентификатор элемента (добавляется в конец callback_data).

        Возвращает:
            types.InlineKeyboardMarkup: объект клавиатуры Telegram.
        """
        import strings as _strings
        markup = types.InlineKeyboardMarkup(row_width=2)  # Инициализируем разметку в два столбца
        
        # Формируем суффикс с ID элемента, если он передан (например, для удаления конкретного отчета)
        suffix = f"_{item_id}" if item_id else ""
        import strings as _strings
        # Создаем кнопку подтверждения (Да)
        btn_yes = types.InlineKeyboardButton(
            text=_strings.STRINGS.get("btn_yes_abort", "✅ Да, выполнить"),
            callback_data=f"{yes_callback}{suffix}"
        )
        # Создаем кнопку отмены действия (Нет)
        btn_no = types.InlineKeyboardButton(
            text=_strings.STRINGS.get("btn_no_abort", "🔙 Нет, вернуться"),
            callback_data=f"{no_callback}{suffix}"
        )
        
        markup.add(btn_yes, btn_no)  # Монтируем кнопки в сетку интерфейса
        return markup
    
    
    @staticmethod
    def send_service_delivery_gate(
        bot: telebot.TeleBot,
        chat_id: int,
        text_key: str,
        is_temporary: bool = False,
        reply_markup: types.InlineKeyboardMarkup = None,
        parse_mode: str = "Markdown"
    ) -> telebot.types.Message | None:
        """
        Отправляет сервисное сообщение с возможностью автоудаления.

        Аргументы:
            bot (telebot.TeleBot): экземпляр бота.
            chat_id (int): ID чата назначения.
            text_key (str): ключ в словаре STRINGS.
            is_temporary (bool): если True, сообщение удалится через config.TEMP_MSG_DELAY сек.
            reply_markup (InlineKeyboardMarkup | None): инлайн-клавиатура.
            parse_mode (str): форматирование (Markdown/HTML).

        Возвращает:
            Message | None: объект отправленного сообщения или None при ошибке.
        """
        # Извлекаем текст строго из централизованной матрицы по ключу (
        import strings as _strings
        delivery_text = _strings.STRINGS.get(text_key)
        
        # Если ключ в словаре не найден, защищаем систему от падения 
        if not delivery_text:
            delivery_text = text_key  # Если передан сырой текст, используем его напрямую
            
        try:
            # Физически отправляем сообщение в Telegram API с учетом переданной разметки кнопок и модальности
            sent_msg = bot.send_message(
                chat_id=chat_id,
                text=delivery_text,
                reply_markup=reply_markup,
                parse_mode=parse_mode
            )
            
            # Если активирован флаг временного сообщения (п. 0.2.2 Манифеста — исчезающие окна)
            if is_temporary:
                import config  # Локальный безопасный импорт таймингов конфигурации
                delay = config.TEMP_MSG_DELAY  # Извлекаем эталонную задержку удаления (5.0 секунд)
                
                # Запускаем фоновый таймер на бесшумное физическое удаление сообщения из чата Telegram
                import threading
                threading.Timer(delay, lambda: bot.delete_message(chat_id=chat_id, message_id=sent_msg.message_id)).start()
                
            # Ведем консольный журнал по правилу атомарной конкатенации Протокола Альфа
            log_pfx = "[LOG/UNIFIER]: Сервисный пакет успешно доставлен в чат ID: "
            chat_str = str(chat_id)
            logger.debug(log_pfx + chat_str)
            
            return sent_msg  # Возвращаем объект отправленного сообщения для верхних слоев логики
            
        except Exception as api_error:
            # Предотвращаем Runtime Crash бота при сетевых сбоях пуллинга Amvera
            log_err_pfx = "❌[Критический сбой шлюза вывода]: Сообщение не доставлено: "
            logger.debug(log_err_pfx + str(api_error))
            return None

    @staticmethod
    def send_dialog_confirm_gate(
        bot: telebot.TeleBot,
        chat_id: int,
        text_key: str,
        yes_callback: str,
        no_callback: str,
        parse_mode: str = "Markdown"
    ) -> telebot.types.Message | None:
        """
        Отправляет диалоговое окно с кнопками подтверждения.

        Аргументы:
            bot (telebot.TeleBot): экземпляр бота.
            chat_id (int): ID чата.
            text_key (str): ключ вопроса в _strings.STRINGS.
            yes_callback (str): callback для кнопки Да.
            no_callback (str): callback для кнопки Нет.
            parse_mode (str): форматирование.

        Возвращает:
            Message | None: объект отправленного сообщения.
        """
        import strings as _strings
        # Сначала вызываем нашу существующую фабрику для сборки объекта инлайн-клавиатуры
        markup = CanonicalUnifier.generate_confirm_keyboard(
            yes_callback=yes_callback,
            no_callback=no_callback
        )
        
        # Извлекаем текст вопроса из_strings.STRINGS.py по переданному строковому ключу (Запрет хардкода по п. 4)
        from strings import STRINGS  # Локальный безопасный импорт для предотвращения циклов
        dialog_text = _strings.STRINGS.get(text_key)
        
        # Защита от падения: если ключа нет в словаре, используем переданную строку как сырой текст
        if not dialog_text:
            dialog_text = text_key
            
        try:
            # Физически публикуем диалоговое окно в Telegram API, прикрепив сгенерированную сетку кнопок
            sent_dialog = bot.send_message(
                chat_id=chat_id,
                text=dialog_text,
                reply_markup=markup,
                parse_mode=parse_mode
            )
            
            # Логируем отправку системного диалога в консоль через безопасное сложение изолированных строк
            log_dialog_pfx = "[LOG/UNIFIER]: Диалоговое окно успешно развернуто для чата: "
            chat_str = str(chat_id)
            logger.debug(log_dialog_pfx + chat_str)
            
            return sent_dialog  # Возвращаем объект сообщения для возможной последующей обработки
            
        except Exception as dialog_error:
            # Предотвращаем Runtime Crash при сетевых сбоях на хостинге Amvera
            log_dialog_err = "❌[Критический сбой диалогового шлюза]: Окно не выведено: "
            logger.debug(log_dialog_err + str(dialog_error))
            return None



### end of file