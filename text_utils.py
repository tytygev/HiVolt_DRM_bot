### file TEXT_UTILS.PY
# -*- coding: utf-8 -*-
"""
Модуль text_utils.py – утилиты форматирования текста.

Содержит класс TextFormatter со статическими методами для получения
строк фиксированной ширины, используемых в дашбордах и интерфейсах.
"""

import config
from logger_config import logger


class TextFormatter:
    """
    Форматирование текста с учётом моноширинного шрифта.
    Все размеры задаются в символах.
    """

    @staticmethod
    def format_fixed_width_alert(user_name: str, command: str) -> str:
        import strings as _strings
        template = _strings.STRINGS.get("alert_widget_template",
                               "⛔️ {user} пытался выполнить {cmd}.")
        padding_char = _strings.STRINGS.get("alert_padding_char", " ")
        max_width = config.ALERT_WIDGET_WIDTH

        # Фиксированная часть строки, не зависящая от имени
        fixed_part = template.format(user="", cmd=command)
        min_user_width = 10   # минимум символов для имени (7 букв + "…")

        # Если даже с минимальным именем строка не влезает – обрезаем команду
        if len(fixed_part) + min_user_width > max_width:
            available_cmd = max_width - min_user_width
            if available_cmd > 1:
                cmd_display = command[:available_cmd - 1] + "…"
            else:
                cmd_display = "…"
            fixed_part = template.format(user="", cmd=cmd_display)

        # Теперь готовим имя
        if len(user_name) <= max_width - len(fixed_part):
            # Имя помещается полностью
            plain_text = template.format(user=user_name, cmd=command)
        else:
            # Имя слишком длинное – обрезаем до доступного места
            available_user = max_width - len(fixed_part)
            if available_user < min_user_width:
                # На всякий случай (уже должно быть обработано выше)
                available_user = min_user_width
            # Оставляем место под многоточие
            truncated_user = user_name[:available_user - 1] + "…"
            plain_text = template.format(user=truncated_user, cmd=command)

        # Дополняем до фиксированной ширины справа
        if len(plain_text) < max_width:
            plain_text += padding_char * (max_width - len(plain_text))

        return "<code>" + plain_text + "</code>"
        
        
        
### end of file