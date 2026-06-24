### Файл PING_UPDATER.PY ###
# -*- coding: utf-8 -*-
"""
Модуль ping_updater.py – фоновое обновление виджета tech_bar в дашборде.
Модуль ping_updater.py – фоновое обновление виджета tech_bar в дашборде.

Реализован с единым циклом, периодически опрашивающим БД и обновляющим
техническую строку для всех установленных групп.
"""

import threading
import time
import random
import config
import database
from logger_config import logger
from tech_bar_formatter import TechBarFormatter

class PingUpdater:
    """
    Планировщик периодического обновления времени в дашборде.
    Использует единый цикл, который раз в N секунд проверяет все группы
    из БД и обновляет tech_bar для тех, у кого наступило время.
    """
    def __init__(self, bot, dashboard_manager):
        """
        Инициализация пингера.

        Аргументы:
            bot: экземпляр TeleBot (не используется напрямую, но может понадобиться в будущем).
            dashboard_manager: экземпляр DashboardManager для вызова update_widget.
        """
        self.bot = bot                       # Сохраняем ссылку на бота (на будущее)
        self.dm = dashboard_manager          # Сохраняем менеджер дашборда
        self.running = False                 # Флаг работы цикла
        self.thread = None                   # Поток, в котором выполняется цикл
        self.last_update_time = {}           # Словарь {(chat_id, thread_id): timestamp} — время последнего обновления tech_bar

        # Интервал между проверками (в секундах) – можно вынести в config
        self.check_interval = 10             # Будем проверять каждые 10 секунд

    def _calculate_interval(self, last_activity: int, current_time: int) -> int:
        """
        Вычисляет интервал обновления для группы на основе её последней активности.

        Аргументы:
            last_activity (int): Unix-время последней активности группы (из БД).
            current_time (int): текущее Unix-время.

        Возвращает:
            int: интервал в секундах (короткий для активных, длинный для неактивных).
        """
        # Определяем, активна ли группа (активность в течение ACTIVITY_THRESHOLD секунд)
        is_active = (current_time - last_activity) <= config.ACTIVITY_THRESHOLD

        # Для активных групп – короткий интервал, для неактивных – длинный
        if is_active:
            # Для активных также можно варьировать, но пока вернём фиксированный короткий
            return random.randint(config.PING_ACTIVE_MIN, config.PING_ACTIVE_MAX)
        else:
            return random.randint(config.PING_INACTIVE_MIN, config.PING_INACTIVE_MAX)

    def _update_loop(self):
        """
        Основной цикл обновления. Выполняется в отдельном потоке.
        """
        logger.debug("[PingUpdater] Цикл обновления запущен")

        while self.running:
            try:
                # Шаг 1: Получаем актуальный список групп из БД
                with database.DatabaseConnectionContext(database.DB_PATH, database.db_lock) as cursor:
                    # Выбираем все группы, у которых есть хотя бы запись в group_settings
                    cursor.execute("""
                        SELECT chat_id, thread_id, last_activity
                        FROM group_settings
                    """)
                    rows = cursor.fetchall()   # список кортежей (chat_id, thread_id, last_activity)

                # Если групп нет – просто ждём следующий цикл
                if not rows:
                    logger.debug("[PingUpdater] Нет установленных групп, ожидание...")
                    time.sleep(self.check_interval)
                    continue

                # Текущее время (один раз за цикл)
                current_time = int(time.time())

                # Для каждой группы проверяем, нужно ли обновлять tech_bar
                for chat_id, thread_id, last_activity in rows:
                    # Если last_activity None – считаем, что группа неактивна (0)
                    if last_activity is None:
                        last_activity = 0

                    # Вычисляем интервал для этой группы
                    interval = self._calculate_interval(last_activity, current_time)

                    # Получаем время последнего обновления для этой группы (или 0, если не было)
                    key = (chat_id, thread_id)
                    last_upd = self.last_update_time.get(key, 0)

                    # Если прошло достаточно времени – обновляем виджет
                    if current_time - last_upd >= interval:
                        # Формируем актуальную строку tech_bar через централизованный форматтер
                        tech_text = TechBarFormatter.get_content()

                        # Вызываем метод обновления виджета у менеджера дашборда
                        success = self.dm.update_widget(chat_id, thread_id, "tech_bar", tech_text)

                        if success:
                            # Запоминаем время успешного обновления
                            self.last_update_time[key] = current_time
                            # Двигаем дашборд вниз, если нужно
                            self.dm.ensure_dashboard_at_bottom(chat_id, thread_id)
                            logger.debug(f"[PingUpdater] tech_bar обновлён для чата {chat_id}, топик {thread_id}")
                        else:
                            # Если виджет не найден (возможно, группа ещё не создала дашборд) – не обновляем время
                            logger.debug(f"[PingUpdater] Не удалось обновить tech_bar для чата {chat_id} (виджет отсутствует)")

                # Небольшая пауза перед следующим циклом
                time.sleep(self.check_interval)

            except Exception as e:
                # Обрабатываем любые ошибки, чтобы цикл не прерывался
                logger.debug(f"[PingUpdater] Ошибка в цикле обновления: {e}")
                time.sleep(self.check_interval)   # Подождать и повторить

        logger.debug("[PingUpdater] Цикл обновления остановлен")

    def start(self):
        """
        Запускает фоновый поток с циклом обновления.
        """
        if self.running:
            logger.debug("[PingUpdater] Уже запущен")
            return

        self.running = True
        self.thread = threading.Thread(target=self._update_loop, daemon=True)
        self.thread.start()
        logger.debug("[PingUpdater] Поток обновления запущен")

    def stop(self):
        """
        Останавливает фоновый поток (устанавливает флаг, чтобы цикл завершился).
        """
        if not self.running:
            return

        self.running = False
        # Ждём завершения потока (необязательно, но для чистоты)
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=2.0)
            logger.debug("[PingUpdater] Поток остановлен")
        else:
            logger.debug("[PingUpdater] Остановка выполнена")

        
        
        
        
        
        
        
        
        
        
###






