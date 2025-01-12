import logging
import colorlog # type: ignore

logger = logging.getLogger("bot_logger")
logger.setLevel(logging.DEBUG)

file_handler = logging.FileHandler('discord_bot/discord_bot.log')
file_handler.setLevel(logging.DEBUG)
file_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
file_handler.setFormatter(file_formatter)

console_handler = logging.StreamHandler()
console_handler.setLevel(logging.DEBUG)

color_formatter = colorlog.ColoredFormatter(
    "%(asctime)s - %(log_color)s%(levelname)s%(reset)s - %(message)s",
    datefmt='%Y-%m-%d %H:%M:%S',
    log_colors={
        'DEBUG': 'light_purple',
        'INFO': 'cyan',
        'WARNING': 'yellow',
        'ERROR': 'red',
        'CRITICAL': 'bold_red',
    },
    reset=True
)

console_handler.setFormatter(color_formatter)

logger.addHandler(file_handler)
logger.addHandler(console_handler)
