### файл logger_config.py
# -*- coding: utf-8 -*-
"""
Модуль logger_config.py – централизованная настройка логирования.

Настраивает корневой логгер с выводом в stdout (консоль) без буферизации,
что гарантирует видимость сообщений на хостинге Amvera.
Формат сообщения: дата-время - уровень - сообщение.
Уровень детализации задаётся в config.py через LOG_LEVEL (по умолчанию DEBUG).
"""

import logging
import sys
import config


def setup_logger():
    """Создаёт и возвращает настроенный логгер с именем 'HiVolt_DRM_bot'."""
    logger = logging.getLogger('HiVolt_DRM_bot')
    logger.setLevel(getattr(logging, config.LOG_LEVEL, logging.DEBUG))

    # Обработчик для stdout (консоль Amvera)
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(logging.DEBUG)   # обработчик принимает всё, фильтрация на уровне логгера

    # Формат: время - сообщение (без имени логгера, чтобы не загромождать)
    formatter = logging.Formatter('%(asctime)s - %(message)s', datefmt='%H:%M:%S')
    handler.setFormatter(formatter)

    logger.addHandler(handler)
    return logger


# Глобальный объект логгера, используемый всем проектом
logger = setup_logger()


### end of file