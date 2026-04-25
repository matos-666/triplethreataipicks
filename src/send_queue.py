"""Envia picks não enviadas da fila. Corre a cada 10 minutos via GitHub Actions.

Envia todas as picks pendentes de uma vez (batch=50) para compensar quando o
GitHub Actions schedule atrasa/falha, garantindo que as picks saem antes dos jogos.
"""
import logging
import sys
from src import telegram_bot

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

sent = telegram_bot.send_next_queued(batch=50)
print("sent=true" if sent else "sent=false (queue empty or no picks today)")
