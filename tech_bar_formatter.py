### file TECH_BAR_FORMATTER.PY ###

# -*- coding: utf-8 -*-
"""
Модуль tech_bar_formatter.py – форматирование содержимого виджета tech_bar.

Содержит класс TechBarFormatter со статическим методом get_content().
Централизует знание о формате технической строки дашборда (версия + время).
Используется DashboardManager и PingUpdater для устранения дублирования (DRY).
"""

import time
import config
from strings import STRINGS
from logger_config import logger

class TechBarFormatter:
    """
    Утилитарный класс для генерации строки tech_bar.
    Не имеет состояния, только статические методы.
    """

    @staticmethod
    def get_content() -> str:
        """
        Возвращает готовую строку для виджета tech_bar.

        Формирует строку на основе шаблона tech_bar из STRINGS,
        подставляя текущую версию из config.VERSION и текущее время
        в формате, заданном ключом ping_format.

        Возвращает:
            str: отформатированная строка, готовая для вставки в виджет.
        """
        import strings as _strings
        # Получаем шаблон технической строки из централизованного словаря
        template = _strings.STRINGS.get("tech_bar", "<code>version: {}. \nLast ping time: {}</code>")
        # Получаем формат времени
        time_format = _strings.STRINGS.get("ping_format", "%H:%M:%S")
        # Вычисляем текущее время по этому формату
        current_time = time.strftime(time_format)
        # Подставляем версию и время в шаблон методом .format() (без f-строк, 
        # чтобы избежать спецсимволов, требующих экранирования)
        return template.format(config.VERSION, current_time)
        
        
### end of file ###