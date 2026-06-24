# Файл ROUTER.PY
# -*- coding: utf-8 -*-
# Поддержка UTF-8 кодировки для логов и системных уведомлений

import database  # Подключаем модуль прямого взаимодействия с SQLite3 базой данных
from user_notifier import UserNotifier
from logger_config import logger

def process_start_routing(message, bot, user_sessions, launch_wizard_func, open_admin_func):
    """
    Центральный изолированный дешифратор стартовых deep-link ссылок (п. 3.2 Манифеста).
    Разделяет потоки юзеров на запуск конструктора, админку или выдает ошибку шлюза.
    """
    import strings as _strings
    user_id = message.from_user.id  # Извлекаем ID пользователя
    text_parts = message.text.split()  # Дробим входящий текст по пробелам
    
    # Если команда /start передана без параметров (Простое нажатие кнопки Start в ЛС)
    if len(text_parts) == 1:
        # ТОЧЕЧНАЯ ПРАВКА СТРОКИ 21: Перевод уведомления простого старта на универсальный шлюз unifier.py
        UserNotifier.notify('private_text_command',
                    _strings.STRINGS.get("private_only"),
                    bot=bot, chat_id=user_id)
        return
        
    # Извлекаем зашифрованный payload-параметр из ссылки вида t.me/bot?start=XXXXX
    payload = text_parts[1]
    
    # Кейс А: Переход по ссылке управления администратора (Префикс 'adm')
    if payload.startswith("adm"):
        logger.debug(f"📥 [Роутер]: Админ {user_id} зашел через шлюз настроек с параметром {payload}")
        # ---- СОХРАНЕНИЕ КОНТЕКСТА АДМИНИСТРИРОВАНИЯ ----
        # Извлекаем chat_id и thread_id из payload (формат adm_CHATID_THREADID или adm_...)
        parts = payload.split("_")
        if len(parts) >= 3:
            try:
                chat_id = int(parts[1])      # Второй фрагмент — идентификатор чата
                thread_id = int(parts[2])    # Третий фрагмент — идентификатор топика
                # Импортируем глобальный словарь admin_contexts из main
                from main import admin_contexts
                admin_contexts[user_id] = (chat_id, thread_id)  # Запоминаем контекст для этого админа
                logger.debug(f"[LOG/ROUTER]: Контекст администрирования сохранён для {user_id}: чат {chat_id}, топик {thread_id}")
            except ValueError:
                pass  # Если не удалось преобразовать в числа — игнорируем
        # ---- Конец сохранения контекста ----
        open_admin_func(message, payload)
        return
        
    # Кейс Б: Переход по одноразовой короткой ссылке конструктора отчетов
    # Валидация на структуру ключа: токен шлюза должен быть строго строковым хэшем
    logger.debug(f"📥 [Роутер]: Юзер {user_id} пытается активировать токен конструктора: {payload}")
    
    # Пытаемся извлечь данные топика и группы из БД с одновременным его уничтожением (Одноразовость п. 3.2)
    gateway_data = database.pop_gateway_token(payload)
    
    if not gateway_data:
        # Нарушение п. 3.2 Манифеста — токен не существует или уже был активирован ранее
        # ТОЧЕЧНАЯ ПРАВКА №2: Перевод сообщения об ошибке токена на исчезающий ворнинг шлюза
        UserNotifier.notify('private_text_command',
                            _strings.STRINGS.get("gateway_error_missing"),
                            bot=bot, chat_id=user_id)       
        logger.debug(f"❌ [Роутер]: Отказ шлюза для {user_id}. Токен {payload} недействителен.")
        return
        
    # Успешная валидация: извлекаем целевые ID группы и топика (thread)
    target_chat_id, target_thread_id = gateway_data
    
    # Проверяем, нет ли у юзера уже активной запущенной сессии сборки (Защита от наслоения окон)
    if user_id in user_sessions:
        # ТОЧЕЧНАЯ ПРАВКА СТРОКИ 58: Перевод предупреждения о наслоении сессий на исчезающий ворнинг
        UserNotifier.notify('private_text_command',
                            _strings.STRINGS.get("wizard_already_active"),
                            bot=bot, chat_id=user_id)
        return
        
    # Передаем управление в изолированный интерактивный мастер-конструктор wizard.py
    launch_wizard_func(message, target_chat_id, target_thread_id)
















