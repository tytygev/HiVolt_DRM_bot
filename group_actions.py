### file GROUP_ACTIONS.PY ###
# -*- coding: utf-8 -*-
"""
Модуль group_actions.py – действия, вызываемые из группы/топика.

Содержит вспомогательные функции для обработки команд и callback'ов
главного дашборда. Избавляет от дублирования логики в main.py.

Все тексты берутся из STRINGS, для вывода используются CanonicalUnifier
и бесшумные answer_callback_query (согласно Манифесту, п. 0.2.3).
"""

import importlib
import strings
import main_dashboard_layout
from logger_config import logger
from unifier import CanonicalUnifier
import database
import admin  # для open_admin_dashboard
from text_utils import TextFormatter

def reload_bot_strings_and_layout(dashboard_manager):
    """
    Перезагружает STRINGS.py и main_dashboard_layout.py,
    обновляет дашборды через dashboard_manager.reload_layout().
    Возвращает True при успехе, иначе False.
    """
    try:
        importlib.reload(strings)
        importlib.reload(main_dashboard_layout)
        import tech_bar_formatter
        importlib.reload(tech_bar_formatter)
        if dashboard_manager:
            dashboard_manager.reload_layout()
        return True
    except Exception as e:
        logger.debug(f"[GroupActions] Ошибка перезагрузки: {e}")
        return False


def check_admin_rights(bot, chat_id, user_id):
    """
    Проверяет, является ли пользователь администратором в чате.
    Возвращает bool.
    """
    try:
        member = bot.get_chat_member(chat_id, user_id)
        return member.status in ["creator", "administrator"]
    except Exception:
        return False


def handle_uninstall_request(bot, user_id, chat_id, thread_id):
    """
    Отправляет в ЛС диалоговое окно подтверждения удаления панели.
    Параметры: bot, user_id админа, chat_id, thread_id.
    """
    # Формируем callback'и для подтверждения/отмены с правильными ID
    yes_cb = f"adm_confirm_uninstall_{chat_id}_{thread_id}"
    no_cb = f"adm_cancel_uninstall_{chat_id}_{thread_id}"
    result = CanonicalUnifier.send_dialog_confirm_gate(
        bot=bot,
        chat_id=user_id,
        text_key="uninstall_confirm",
        yes_callback=yes_cb,
        no_callback=no_cb
    )
    return result is not None   # True, если сообщение отправлено

def handle_settings_request(bot, user_id, chat_id, thread_id):
    """
    Открывает дашборд настроек в ЛС администратора.
    """
    # Сохраняем контекст администрирования
    from main import admin_contexts
    admin_contexts[user_id] = (chat_id, thread_id)
    # Формируем параметр для open_admin_dashboard
    param = f"adm_{chat_id}_{thread_id}"
    # Имитируем объект message, чтобы open_admin_dashboard могла работать
    # (она ожидает message.from_user.id, но использует только user_id)
    class FakeMsg:
        from_user = type('obj', (object,), {'id': user_id})()
    admin.open_admin_dashboard(FakeMsg(), param, bot)


#def handle_help_request(bot, user_id):
#    """
#    Отправляет справочное сообщение в ЛС.
#    """
#    CanonicalUnifier.send_service_delivery_gate(
#        bot=bot,
#        chat_id=user_id,
#        text_key="help_text",
#        is_temporary=False,
#        parse_mode="HTML"
#    )



    
    
def build_access_denied_alert(user_id, command):
    """
    Формирует текст предупреждения о запрете доступа фиксированной ширины.
    Использует универсальный форматтер TextFormatter.
    Использует resolve_user_display_name для получения имени.
    """
    display = database.resolve_user_display_name(user_id)
    return TextFormatter.format_fixed_width_alert(display, command)
    
    
### end of file