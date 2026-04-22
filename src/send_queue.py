"""Envia a próxima pick não enviada da fila. Corre a cada 15 minutos via GitHub Actions."""
import logging
import sys
from src import telegram_bot

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

sent = telegram_bot.send_next_queued()
print("sent=true" if sent else "sent=false (queue empty or no picks today)")
