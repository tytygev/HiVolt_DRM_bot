### file USER_NOTIFIER.PY
# -*- coding: utf-8 -*-
"""
Модуль user_notifier.py – единый диспетчер уведомлений.

Класс UserNotifier получает категорию взаимодействия, текст и контекст,
и в зависимости от правил NOTIFICATION_RULES (config.py) выбирает
стратегию доставки: бесшумная плашка, виджет дашборда,
временное сообщение в ЛС, модальное окно или полное молчание.
"""
from logger_config import logger          # Логирование событий
import config                             # Конфигурация (правила и тайминги)
from unifier import CanonicalUnifier      # Универсальный отправщик сообщений


class UserNotifier:
    """
    Диспечер уведомлений, привязанный к категориям интерфейса.
    Все методы статические, состояние не хранит.
    """
    @staticmethod
    def notify(category: str, text: str, callback=None, bot=None,
               chat_id: int = None, thread_id: int = None,
               dashboard_manager=None) -> None:
        """
        Показать уведомление для заданной категории.

        Параметры:
        category – ключ категории из NOTIFICATION_RULES (например, 'main_dashboard_button')
        text     – готовая строка для отображения (уже отформатированная, из STRINGS)
        callback – объект CallbackQuery (если ответ на нажатие кнопки)
        bot      – экземпляр TeleBot
        chat_id  – идентификатор чата (для виджета или ЛС)
        thread_id – идентификатор топика (для виджета)
        dashboard_manager – менеджер дашборда (для стратегии dashboard_widget)
        """
        # ----- 1. Определяем стратегию -----
        # Загружаем правила из конфига. Если категория не найдена, используем безопасную стратегию.
        rules = getattr(config, 'NOTIFICATION_RULES', {})
        strategy = rules.get(category, 'callback_alert')   # по умолчанию плашка

        # Если нет callback, а стратегия требует его – заменяем на отправку в ЛС
        if callback is None and strategy in ('callback_alert', 'modal'):
            logger.debug("UserNotifier: callback отсутствует, меняем '%s' на 'private_message'", strategy)
            strategy = 'private_message'

        # Логируем решение
        logger.debug("UserNotifier: категория='%s', стратегия='%s', текст='%.40s'", category, strategy, text)

        # ----- 2. Выполняем стратегию -----
        if strategy == 'callback_alert':
            # Бесшумная плашка (п. 0.2.3 Манифеста)
            if callback:
                try:
                    bot.answer_callback_query(callback.id, text=text, show_alert=False)
                except Exception as e:
                    logger.error("Ошибка при показе плашки: %s", e)

        elif strategy == 'dashboard_widget':
            # Временный виджет на дашборде (alert_placeholder)
            if dashboard_manager and chat_id is not None:
                dashboard_manager.show_alert(chat_id, thread_id or 0, text, config.TEMP_MSG_DELAY)
            else:
                # Если менеджера или chat_id нет, откатываемся на личное сообщение
                logger.warning("UserNotifier: недостаточно данных для dashboard_widget, шлём в ЛС")
                CanonicalUnifier.send_service_delivery_gate(
                    bot=bot, chat_id=chat_id,
                    text_key=text,       # передаём как ключ – если не найдёт, покажет сам текст
                    is_temporary=True
                )

        elif strategy == 'private_message':
            # Временное сообщение в ЛС (п. 0.2.2)
            CanonicalUnifier.send_service_delivery_gate(
                bot=bot, chat_id=chat_id,
                text_key=text,
                is_temporary=True
            )

        elif strategy == 'modal':
            # Модальное окно с кнопкой (п. 0.2.4) – только при callback
            if callback:
                bot.answer_callback_query(callback.id, text=text, show_alert=True)
            else:
                CanonicalUnifier.send_service_delivery_gate(
                    bot=bot, chat_id=chat_id,
                    text_key=text,
                    is_temporary=True
                )

        elif strategy == 'silent':
            # Ничего не показываем
            pass

        else:
            logger.error("UserNotifier: неизвестная стратегия '%s'", strategy)
            

### end of file