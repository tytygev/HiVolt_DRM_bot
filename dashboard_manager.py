### Файл DASHBOARD_MANAGER.PY ###
# -*- coding: utf-8 -*-
"""
Модуль dashboard_manager.py – менеджер состояния главного дашборда в группах.

Содержит класс DashboardManager, который:
- Хранит в ОЗУ для каждой группы список виджетов (greeting, tech_bar, warning, ...).
- Управляет таймерами виджетов (для автоматического удаления).
- Отрисовывает дашборд, собирая текст из виджетов.
- Не зависит от БД (получает настройки через колбэк settings_provider).
- Не управляет пингом (обновление tech_bar вызывается извне).

Текущая архитектура DashboardManager всё ещё нарушает SRP – он знает о токенах шлюза и генерирует их в _build_keyboard. Это должно быть вынесено в отдельный сервис (например, LinkGenerator).
"""

import threading
import time
from typing import Callable, Optional, Dict, List, Any, Tuple

import telebot
from telebot import types
import config
import importlib   # Для динамической перезагрузки модуля main_dashboard_layout

from tech_bar_formatter import TechBarFormatter
import main_dashboard_layout as m_dash_layout
from telebot import apihelper   # если ещё не импортирован
from logger_config import logger
import database

class DashboardManager:
    """
    Менеджер состояния дашборда.
    Для каждой группы (chat_id, thread_id) хранит:
        message_id: int
        widgets: list[dict]  # каждый виджет: {type, content, priority, ttl, timer}
        keyboard_markup: InlineKeyboardMarkup
    """
    def __init__(self,
                 bot: telebot.TeleBot,
                 settings_provider: Callable[[int, int], Optional[dict]],
                 update_msg_id_callback: Callable[[int, int, int], None]):
        """
        Инициализация менеджера.

        Аргументы:
            bot: экземпляр TeleBot.
            settings_provider: функция, возвращающая настройки группы (headline, line_mark, separator, ...)
                               или None, если группа не установлена.
            update_msg_id_callback: функция для сохранения нового message_id в БД.
        """
        self.bot = bot
        self.get_settings = settings_provider
        self.update_msg_id = update_msg_id_callback
        # Сохраняем ссылку на конфигурацию дашборда (можно будет перезагрузить)
        self.layout = m_dash_layout
        self._states: Dict[Tuple[int, int], dict] = {}   # ключ -> состояние
        #RLock (рекурсивная блокировка) позволяет одному потоку повторно входить в критические секции, что предотвращает deadlock при вложенных вызовах create_or_update -> _render -> create_or_update внутри того же потока.
        self._lock = threading.RLock()                    # блокировка для потокобезопасности
        
        self._last_user_msg_id: Dict[Tuple[int, int], int] = {}  # ключ -> message_id последнего сообщения пользователя
        
    # ---- Вспомогательные методы ----

    def _get_state(self, chat_id: int, thread_id: int) -> Optional[dict]:
        """Возвращает состояние группы или None, если его нет."""
        return self._states.get((chat_id, thread_id))

    def _ensure_state(self, chat_id: int, thread_id: int) -> dict:
        """
        Возвращает состояние группы, создавая пустое, если его нет.
        Используется только когда гарантировано, что дашборд существует.
        """
        key = (chat_id, thread_id)
        if key not in self._states:
            # Пустое состояние без виджетов, но с возможностью добавить их позже
            self._states[key] = {
                "message_id": None,
                "widgets": [],
                "keyboard_markup": None
            }
        return self._states[key]

    def _build_keyboard(self, chat_id: int, thread_id: int) -> types.InlineKeyboardMarkup:
        """
        Генерирует клавиатуру для главного дашборда на основе конфигурации
        из main_dashboard_layout.BUTTONS.
        Поддерживает типы кнопок: 'callback', 'url', 'special' (create_report).
        """
        markup = types.InlineKeyboardMarkup(row_width=2)

        for row_def in self.layout.BUTTONS:
            buttons_in_row = []
            for btn_def in row_def:
                import strings as _strings
                btn_text = _strings.STRINGS.get(btn_def["text_key"], btn_def["text_key"])
                btn_type = btn_def["type"]

                if btn_type == "callback":
                    # Подставляем chat_id и thread_id в шаблон
                    cb_data = btn_def["callback_data_template"].format(
                        chat_id=chat_id, thread_id=thread_id
                    )
                    btn = types.InlineKeyboardButton(text=btn_text, callback_data=cb_data)

                elif btn_type == "url":
                    url = btn_def["url_template"].format(
                        chat_id=chat_id, thread_id=thread_id
                    )
                    btn = types.InlineKeyboardButton(text=btn_text, url=url)

                elif btn_type == "special":
                    # Особое действие: создание отчёта
                    if btn_def.get("action") == "create_report":
                        import uuid
                        import database
                        token = str(uuid.uuid4())[:8]
                        database.create_gateway_token(token, chat_id, thread_id)
                        bot_username = self.bot.get_me().username
                        report_link = f"https://t.me/{bot_username}?start={token}"
                        btn = types.InlineKeyboardButton(text=btn_text, url=report_link)
                    else:
                        logger.debug(f"[DashboardManager] Неизвестное специальное действие: {btn_def['action']}")
                        continue
                else:
                    logger.debug(f"[DashboardManager] Неизвестный тип кнопки: {btn_type}")
                    continue

                buttons_in_row.append(btn)

            # Добавляем целый ряд кнопок (одну или несколько)
            if buttons_in_row:
                markup.add(*buttons_in_row)

        return markup

    def _render(self, chat_id: int, thread_id: int) -> None:
        """
        Пересобирает текст из всех виджетов и обновляет сообщение дашборда.
        Вызывается при любом изменении виджетов (добавление, удаление, обновление).
        Использует _build_content и _send_or_update.
        """
        
        
        # Захватываем блокировку (теперь RLock, безопасно для вложенных вызовов)
        with self._lock:
            state = self._get_state(chat_id, thread_id)
            if not state:
                # Если состояния нет – ничего не делаем
                return
            
            # Получаем актуальный текст и клавиатуру через общий строитель
            text, markup = self._build_content(chat_id, thread_id)
            
            # Получаем текущий message_id из состояния
            current_msg_id = state.get("message_id")
            
            # Вызываем универсальный метод отправки/редактирования
            new_msg_id = self._send_or_update(
                chat_id=chat_id,
                thread_id=thread_id,
                text=text,
                markup=markup,
                edit_message_id=current_msg_id
            )
            
            # Если ID изменился (было редактирование, но не удалось, и отправили новое) – обновляем состояние и БД
            if new_msg_id != current_msg_id:
                state["message_id"] = new_msg_id
                self.update_msg_id(chat_id, thread_id, new_msg_id)
                logger.debug(f"[DashboardManager] Обновлён ID дашборда на {new_msg_id} для чата {chat_id}")
            

    # ---- Публичные методы ----

    def init_from_db(self, groups: List[Tuple[int, int, int]]) -> None:
        """
        Инициализирует состояния для групп, уже установленных в БД.
        Вызывается при старте бота.

        Аргументы:
            groups: список кортежей (chat_id, thread_id, dashboard_msg_id)
        """
        with self._lock:
            for chat_id, thread_id, msg_id in groups:
                key = (chat_id, thread_id)
                if key not in self._states:
                    self._states[key] = {
                        "message_id": msg_id,
                        "widgets": [],
                        "keyboard_markup": None
                    }
            logger.debug(f"[DashboardManager] Инициализировано состояний: {len(self._states)}")
        # После создания состояний сразу наполняем виджетами и отображаем дашборд.
        # Это необходимо после перезапуска бота с существующей БД, чтобы виджеты
        # (особенно tech_bar) были доступны для PingUpdater и чтобы move_dashboard
        # не пытался отправить пустое сообщение.
        for chat_id, thread_id, msg_id in groups:
            self.create_or_update(chat_id, thread_id)
            # После восстановления дашборда перемещаем его в самый низ,
            # чтобы он не остался над сообщениями, появившимися за время простоя бота
            self.move_dashboard(chat_id, thread_id)
            
    def create_or_update(self, chat_id: int, thread_id: int) -> None:
        """
        Полностью пересоздаёт дашборд: сбрасывает все виджеты, генерирует стандартный набор
        (greeting, tech_bar), а затем вызывает _render для отображения.
        """
        
        # Получаем настройки группы через провайдер (для проверки, что группа установлена)
        settings = self.get_settings(chat_id, thread_id)
        if not settings:
            # Группа не установлена – нечего делать
            return

        with self._lock:
            # Берём или создаём состояние
            state = self._ensure_state(chat_id, thread_id)

            # Отменяем все текущие таймеры виджетов
            for w in state["widgets"]:
                if w.get("timer"):
                    try:
                        w["timer"].cancel()
                    except:
                        pass
            # Очищаем список виджетов
            state["widgets"] = []

            # Создаём виджеты согласно конфигурации из main_dashboard_layout
            for wdef in self.layout.WIDGETS:
                # Определяем содержимое виджета
                if wdef["text_key"] is None:
                    # Особый случай: динамический контент (tech_bar)
                    if wdef["type"] == "tech_bar":
                        content = TechBarFormatter.get_content()
                    elif wdef["type"] == "alert_placeholder":
                        # Заполнитель: по умолчанию берём текст из STRINGS по ключу placeholder_text,
                        # а если ключ не указан – используем сам placeholder_text как текст
                        placeholder_key = wdef.get("placeholder_text", " ")
                        import strings as _strings
                        content = _strings.STRINGS.get(placeholder_key, placeholder_key)
                    else:
                        # Неизвестный динамический тип – пропускаем
                        logger.debug(f"[DashboardManager] Неизвестный динамический виджет: {wdef['type']}")
                        continue
                else:
                    import strings as _strings
                    content = _strings.STRINGS.get(wdef["text_key"], wdef["text_key"])

                # Приоритет из конфига или значение по умолчанию
                priority = wdef.get("priority", 100)
                logger.debug(f"[DEBUG] Виджет {wdef['type']}: content = {repr(content)}")
                state["widgets"].append({
                    "type": wdef["type"],
                    "content": content,                   
                    "priority": priority,
                    "ttl": None,
                    "timer": None
                })


            
            # Клавиатуру пересоздадим при первом вызове _build_content (она вызовет _build_keyboard)
            state["keyboard_markup"] = None   # сбросим, чтобы сгенерировать заново
            
            # Теперь вызываем _render, который отправит или отредактирует сообщение
            # _render сам захватит блокировку, но поскольку мы уже внутри _lock, используем RLock – безопасно
            self._render(chat_id, thread_id)

        logger.debug(f"[DashboardManager] Дашборд создан/обновлён для чата {chat_id}")
            
            
            
           

    def add_widget(self, chat_id: int, thread_id: int,
                   widget_type: str, content: str,
                   priority: int = None, ttl: int = None) -> None:
        """
        Добавляет новый виджет или заменяет существующий с таким же type.
        Если ttl задан, запускает таймер на автоматическое удаление виджета.
        """
        if priority is None:
            # Берём приоритет из конфига по типу виджета, иначе 100
            priority = getattr(config, f"WIDGET_PRIORITY_{widget_type.upper()}", 100)

        with self._lock:
            state = self._get_state(chat_id, thread_id)
            if not state:
                # Дашборд ещё не создан – создаём (это может быть предупреждение при отсутствии дашборда)
                self.create_or_update(chat_id, thread_id)
                state = self._get_state(chat_id, thread_id)
                if not state:
                    return

            # Ищем виджет с таким же типом
            for w in state["widgets"]:
                if w["type"] == widget_type:
                    # Удаляем старый таймер, если есть
                    if w.get("timer"):
                        try:
                            w["timer"].cancel()
                        except:
                            pass
                    state["widgets"].remove(w)
                    break

            # Создаём новый виджет
            timer = None
            if ttl is not None:
                # Таймер на удаление виджета
                timer = threading.Timer(
                    ttl,
                    self.remove_widget,
                    args=(chat_id, thread_id, widget_type)
                )
                timer.daemon = True
                timer.start()

            new_widget = {
                "type": widget_type,
                "content": content,
                "priority": priority,
                "ttl": ttl,
                "timer": timer
            }
            state["widgets"].append(new_widget)

            # Перерисовываем дашборд
            self._render(chat_id, thread_id)

    def update_widget(self, chat_id: int, thread_id: int,
                      widget_type: str, new_content: str) -> bool:
        """
        Обновляет содержимое существующего виджета.
        Возвращает True, если виджет найден и обновлён.
        """
        logger.debug(f"[DashboardManager] Поиск виджета '{widget_type}' для чата {chat_id}")
        with self._lock:
            state = self._get_state(chat_id, thread_id)
            if not state:
                logger.debug(f"[DashboardManager] Состояние для чата {chat_id} не найдено")
                return False
            for w in state["widgets"]:
                if w["type"] == widget_type:
                    if w["content"] != new_content:
                        w["content"] = new_content
                        logger.debug(f"[DashboardManager] Виджет '{widget_type}' найден, обновляем контент")
                        self._render(chat_id, thread_id)
                    else:
                        logger.debug(f"[DashboardManager] Виджет '{widget_type}' уже содержит актуальный контент, пропускаем")
                    return True
            logger.debug(f"[DashboardManager] Виджет '{widget_type}' НЕ НАЙДЕН в списке виджетов")
            
            # Распечатаем список виджетов для отладки
            types_list = [w["type"] for w in state["widgets"]]
            logger.debug(f"[DashboardManager] Доступные типы: {types_list}")
            
        return False

    def remove_widget(self, chat_id: int, thread_id: int, widget_type: str) -> None:
        """Удаляет виджет по типу, отменяет его таймер."""
        with self._lock:
            state = self._get_state(chat_id, thread_id)
            if not state:
                return
            for w in state["widgets"]:
                if w["type"] == widget_type:
                    if w.get("timer"):
                        try:
                            w["timer"].cancel()
                        except:
                            pass
                    state["widgets"].remove(w)
                    self._render(chat_id, thread_id)
                    break

    def refresh(self, chat_id: int, thread_id: int) -> None:
        """
        Принудительно обновляет дашборд, удаляя все временные виджеты
        (оставляет только greeting и tech_bar) и пересоздавая стандартные.
        Вызывается после изменения настроек или команды /reload.
        """
        with self._lock:
            state = self._get_state(chat_id, thread_id)
            if not state:
                return
            
            
            # Удаляем все виджеты, у которых is_permanent=False (в соответствии с layout)
            permanent_types = {wdef["type"] for wdef in self.layout.WIDGETS if wdef.get("is_permanent", False)}
            to_remove = [w for w in state["widgets"] if w["type"] not in permanent_types]
            for w in to_remove:
                if w.get("timer"):
                    try:
                        w["timer"].cancel()
                    except:
                        pass
                state["widgets"].remove(w)
            # Обновляем tech_bar свежим временем
            tech_text = TechBarFormatter.get_content()
            
            # ДИАГНОСТИКА: какие виджеты есть перед сбросом placeholder
            widget_types = [w["type"] for w in state["widgets"]]
            logger.debug(f"[DEBUG refresh] Виджеты до сброса: {widget_types}")
            
             # Сбрасываем alert_placeholder на заполнитель из макета
            for w in state["widgets"]:
                if w["type"] == "alert_placeholder":
                    # Находим описание виджета в layout, чтобы взять актуальный placeholder_text
                    import strings as _strings
                    
                    placeholder = " "
                    for wdef in self.layout.WIDGETS:
                        if wdef["type"] == "alert_placeholder":
                            ph_key = wdef.get("placeholder_text", " ")
                            import strings as _strings
                            placeholder = _strings.STRINGS.get(ph_key, ph_key)
                            break
                    logger.debug(f"[DEBUG refresh] Текущий content alert_placeholder: {w['content']}")
                    logger.debug(f"[DEBUG refresh] Новый placeholder из layout: {placeholder}")
                    w["content"] = placeholder
                    if w.get("timer"):
                        try:
                            w["timer"].cancel()
                        except:
                            pass
                    w["timer"] = None
                    break
            
            for w in state["widgets"]:
                if w["type"] == "tech_bar":
                    w["content"] = tech_text
                    break
            
            
            self._render(chat_id, thread_id)


    def show_alert(self, chat_id: int, thread_id: int, text: str, duration: int) -> None:
        """
        Показывает временное предупреждение на дашборде, используя виджет alert_placeholder.
        Текст появляется в зарезервированной строке, не меняя размер дашборда.
        Через duration секунд автоматически убирается.
        """
        with self._lock:
            state = self._get_state(chat_id, thread_id)
            if not state:
                return
            # Ищем виджет alert_placeholder
            for w in state["widgets"]:
                if w["type"] == "alert_placeholder":
                    # Отменяем предыдущий таймер, если был
                    if w.get("timer"):
                        try:
                            w["timer"].cancel()
                        except:
                            pass
                    
                    # Меняем содержимое на предупреждение
                    w["content"] = text
                    
                    # Запускаем таймер на очистку
                    def clear_alert():
                        # Получаем исходный заполнитель из конфигурации
                        placeholder_key = " "
                        for wdef in self.layout.WIDGETS:
                            if wdef["type"] == "alert_placeholder":
                                placeholder_key = wdef.get("placeholder_text", " ")
                                break
                        # Через duration секунд вернуть заполнитель
                        import strings as _strings
                        w["content"] = _strings.STRINGS.get(placeholder_key, placeholder_key)
                        # Перерисовать дашборд
                        self._render(chat_id, thread_id)
                    timer = threading.Timer(duration, clear_alert)
                    timer.daemon = True
                    timer.start()
                    w["timer"] = timer
                    # Обновляем дашборд
                    self._render(chat_id, thread_id)
                    break


    def move_dashboard(self, chat_id: int, thread_id: int) -> None:
        """
        Перемещает дашборд вниз топика:
        - Сначала обновляет виджет tech_bar (актуальное время).
        - Отправляет новое сообщение с текущим содержимым (внизу).
        - Удаляет старое сообщение.
        Использует _build_content и _send_or_update для избежания дублирования.
        Используется при появлении нового сообщения в группе.
        """
        
        
        logger.debug("[DashboardManager] move_dashboard вызван")
        
        with self._lock:
            state = self._get_state(chat_id, thread_id)
            if not state:
                # Если состояния нет – создаём заново
                self.create_or_update(chat_id, thread_id)
                return
            # Сохраняем ID старого сообщения
            old_msg_id = state.get("message_id")
            
            
            # Актуализируем время в tech_bar прямо сейчас (причина активности)
            # Ищем виджет tech_bar в состоянии и обновляем его content БЕЗ вызова _render.
            # Это точечное изменение в ОЗУ, чтобы при последующей отправке нового сообщения
            # дашборда в нём сразу отражалось время данного взаимодействия.
            for widget in state["widgets"]:
                if widget["type"] == "tech_bar":
                    widget["content"] = TechBarFormatter.get_content()
                    break
            
            
            # Получаем текущий текст и клавиатуру через общий строитель
            text, markup = self._build_content(chat_id, thread_id)
            
            # Отправляем НОВОЕ сообщение (не редактируем старое)
            new_msg_id = self._send_or_update(
                chat_id=chat_id,
                thread_id=thread_id,
                text=text,
                markup=markup,
                edit_message_id=None   # None гарантирует отправку нового сообщения
            )
            
            # Обновляем состояние и БД
            state["message_id"] = new_msg_id
            self.update_msg_id(chat_id, thread_id, new_msg_id)
            logger.debug(f"[DashboardManager] Новое сообщение дашборда отправлено (ID {new_msg_id}) при перемещении")

            # Удаляем старое сообщение (если оно существует и отличается от нового)
            if old_msg_id and old_msg_id != new_msg_id:
                try:
                    self.bot.delete_message(chat_id=chat_id, message_id=old_msg_id)
                    logger.debug(f"[DashboardManager] Старое сообщение {old_msg_id} удалено после перемещения")
                except Exception as e:
                    logger.debug(f"[DashboardManager] Не удалось удалить старое сообщение: {e}")
           


    def remove(self, chat_id: int, thread_id: int) -> None:
        """
        Полностью удаляет дашборд (сообщение) и состояние группы.
        Вызывается при /uninstall.
        """
        with self._lock:
            state = self._get_state(chat_id, thread_id)
            if state:
                # Отменяем все таймеры виджетов
                for w in state["widgets"]:
                    if w.get("timer"):
                        try:
                            w["timer"].cancel()
                        except:
                            pass
                # Удаляем сообщение
                if state.get("message_id"):
                    try:
                        self.bot.delete_message(chat_id, state["message_id"])
                    except Exception as e:
                        logger.debug(f"[DashboardManager] Не удалось удалить дашборд {chat_id}: {e}")
                # Удаляем состояние
                key = (chat_id, thread_id)
                if key in self._states:
                    del self._states[key]
                logger.debug(f"[DashboardManager] Дашборд удалён для чата {chat_id}")
                
                
                
                
        # ---- НОВЫЕ МЕТОДЫ ДЛЯ РЕФАКТОРИНГА (SOLID, DRY) ----

    def _build_content(self, chat_id: int, thread_id: int) -> tuple:
        """
        Строит полный текст и клавиатуру для дашборда на основе текущих виджетов.
        Возвращает кортеж (text, markup).
        Используется в _render и move_dashboard для исключения дублирования кода.
        """
        # Получаем состояние группы
        state = self._get_state(chat_id, thread_id)
        if not state:
            # Если состояния нет – возвращаем пустой текст и None (вызовет ошибку, но это обработается выше)
            return "", None

        # Сортируем виджеты по приоритету (меньше – выше)
        widgets = state["widgets"]
        sorted_widgets = sorted(widgets, key=lambda w: w["priority"])
        # Склеиваем содержимое виджетов с двумя переводами строк между ними
        text_parts = [w["content"] for w in sorted_widgets]
        full_text = "\n\n".join(text_parts)

        # Генерируем клавиатуру (если её нет в состоянии – создаём)
        markup = state.get("keyboard_markup")
        if markup is None:
            markup = self._build_keyboard(chat_id, thread_id)
            state["keyboard_markup"] = markup

        return full_text, markup

    def _send_or_update(self, chat_id: int, thread_id: int, text: str, markup: types.InlineKeyboardMarkup, edit_message_id: int = None) -> int:
        """
        Отправляет новое сообщение или редактирует существующее.
        Возвращает ID итогового сообщения (нового или отредактированного).
        Если edit_message_id передан – пытается отредактировать, иначе отправляет новое.
        В случае ошибки редактирования – отправляет новое сообщение.
        """
        # Определяем параметр топика для отправки (None для основного чата)
        message_thread_param = thread_id if thread_id != 0 else None

        if edit_message_id is not None:
            # Пытаемся отредактировать существующее сообщение
            try:
                self.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=edit_message_id,
                    text=text,
                    reply_markup=markup,
                    parse_mode="HTML"
                    #disable_notification=True      # Без звука
                    # message_thread_id НЕ передаём – при редактировании он не нужен
                )
                # Если редактирование успешно – возвращаем тот же ID
                return edit_message_id
            except telebot.apihelper.ApiTelegramException as e:
                error_msg = str(e)
                # Если сообщение не изменилось – не создаём новое, возвращаем старый ID
                
                if "message thread not found" in error_msg or "chat not found" in error_msg:
                    logger.warning("Топик/чат не найден при редактировании %s: %s. Автоудаление дашборда.", chat_id, error_msg)
                    self.remove(chat_id, thread_id)
                    database.delete_group_settings(chat_id, thread_id)
                    return edit_message_id
                
                if "message is not modified" in error_msg:
                    logger.debug(f"[DashboardManager] Сообщение дашборда {edit_message_id} не изменилось, пропускаем")
                    return edit_message_id
                logger.debug(f"[DashboardManager] Ошибка редактирования {chat_id}: {e}")
            except Exception as e:
                # Если редактирование не удалось (например, сообщение удалено) – логируем и переходим к отправке нового
                logger.debug(f"[DashboardManager] Ошибка редактирования {chat_id}: {e}")
                # Продолжаем выполнение – отправим новое сообщение


        # Если редактирование не удалось – удаляем старое сообщение, чтобы не плодить дубли
        if edit_message_id is not None:
            try:
                self.bot.delete_message(chat_id=chat_id, message_id=edit_message_id)
                logger.debug(f"[DashboardManager] Удалено старое сообщение {edit_message_id} после неудачного редактирования")
            except Exception as del_e:
                logger.debug(f"[DashboardManager] Не удалось удалить старое сообщение {edit_message_id}: {del_e}")
                

        # Отправляем новое сообщение
        try:
            new_msg = self.bot.send_message(
                chat_id=chat_id,
                text=text,
                reply_markup=markup,
                parse_mode="HTML",
                message_thread_id=message_thread_param,
                disable_notification=True      # Без звука
            )
            return new_msg.message_id
        except telebot.apihelper.ApiTelegramException as e:
            error_msg = str(e)
            if "message thread not found" in error_msg or "chat not found" in error_msg:
                logger.warning("Топик/чат не найден при отправке в %s: %s. Автоудаление дашборда.", chat_id, error_msg)
                self.remove(chat_id, thread_id)
                database.delete_group_settings(chat_id, thread_id)
                return 0
            else:
                logger.error("Ошибка отправки нового сообщения в %s: %s", chat_id, e)
            # Возвращаем старый ID (или 0), чтобы не потерять состояние
            return edit_message_id if edit_message_id else 0
        
    def reload_layout(self) -> None:
        """
        Перезагружает модуль main_dashboard_layout и обновляет все дашборды.
        Вызывается при команде /reload, чтобы не требовалась полная перезагрузка бота.
        """
        # Динамически перезагружаем модуль конфигурации дашборда
        import main_dashboard_layout
        importlib.reload(main_dashboard_layout)
        # Обновляем ссылку в экземпляре менеджера на свежий модуль
        self.layout = main_dashboard_layout

        # Заново создаём виджеты и перерисовываем дашборд для всех групп
        with self._lock:
            for (chat_id, thread_id) in list(self._states.keys()):
                # refresh удалит временные виджеты, обновит tech_bar и сохранит постоянные
                self.refresh(chat_id, thread_id)

        logger.debug("[DashboardManager] Layout перезагружен и дашборды обновлены")
        
    def notify_user_message(self, chat_id: int, thread_id: int, message_id: int) -> None:
        """Сообщает менеджеру о новом сообщении от пользователя в группе."""
        key = (chat_id, thread_id)
        with self._lock:
            self._last_user_msg_id[key] = message_id
    
    def ensure_dashboard_at_bottom(self, chat_id: int, thread_id: int) -> None:
        """
        Проверяет, не появилось ли в чате более новое сообщение.
        Если да — перемещает дашборд вниз.
        """
        key = (chat_id, thread_id)
        last_user_msg = self._last_user_msg_id.get(key)
        if not last_user_msg:
            return
        state = self._get_state(chat_id, thread_id)
        if not state or not state.get("message_id"):
            return
        if last_user_msg > state["message_id"]:
            self.move_dashboard(chat_id, thread_id)
        
        
        
        
        
        
        
        
        
        
        
        
        
### end of file






