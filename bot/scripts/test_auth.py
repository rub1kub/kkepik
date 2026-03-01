"""
Тестовый скрипт для авторизации WebMax сессии и чтения сообщений.
Запуск: cd bot && python -m max_bot.test_auth

При первом запуске:
  - Отправляет SMS-код на указанный телефон
  - Запрашивает код из терминала
  - Сохраняет сессию в kkep_max.db

При повторном запуске:
  - Использует сохранённую сессию (без SMS)
  - Выводит список чатов и последние сообщения
  - Слушает новые сообщения и логирует их
"""

import sys
import asyncio
import json
import os

# Кодировка для Windows-консоли
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.stderr.reconfigure(encoding='utf-8', errors='replace')

from webmax import WebMaxClient

# Настройки — задаются через .env или переменные окружения
PHONE = os.getenv("MAX_PHONE", "+7XXXXXXXXXX")
SESSION_NAME = "kkep_max"
TARGET_CHAT_ID = int(os.getenv("MAX_WATCH_CHAT_IDS", "0").split(",")[0].strip() or "0")


async def main():
    print(f"=== WebMax Test Auth ===")
    print(f"Телефон: {PHONE}")
    print(f"Сессия: {SESSION_NAME}")
    print(f"Целевой чат: {TARGET_CHAT_ID}")
    print()

    client = WebMaxClient(session_name=SESSION_NAME, phone=PHONE)

    @client.on_start()
    async def on_start():
        print("\n=== ПОДКЛЮЧЕНО ===")
        print(f"Авторизован как: {client.me}")
        print(f"Всего чатов: {len(client.chats)}")
        print()

        # Выводим все чаты
        print("--- Список чатов ---")
        target_found = False
        for cid, chat in client.chats.items():
            title = chat.title or f'Dialog'
            chat_type = chat.type or '?'
            members = chat.participants_count or '?'
            marker = ' <<<< ЦЕЛЕВОЙ' if cid == TARGET_CHAT_ID else ''
            print(f"  [{chat_type}] {title} (id={cid}, участников={members}){marker}")

            if cid == TARGET_CHAT_ID:
                target_found = True
                # Показываем последнее сообщение
                if chat.last_message:
                    lm = chat.last_message
                    print(f"    Последнее сообщение:")
                    print(f"      text: {lm.text!r}")
                    print(f"      attaches: {lm.attaches}")
                    print(f"      raw_data keys: {list(lm.raw_data.keys()) if lm.raw_data else 'N/A'}")
                    print(f"      raw_data: {json.dumps(lm.raw_data, ensure_ascii=False, indent=2)[:2000]}")

        if not target_found:
            print(f"\n  !!! Целевой чат {TARGET_CHAT_ID} НЕ НАЙДЕН в списке чатов !!!")
            print(f"  Возможно, неправильный ID или аккаунт не состоит в этом чате.")

        print("\n--- Слушаем новые сообщения (Ctrl+C для выхода) ---\n")

    @client.on_message()
    async def on_message(message):
        chat_id = message.chat_id
        text = message.text or ''
        sender = message.sender
        sender_name = f'{sender.firstname} {sender.lastname}'.strip() if sender else '???'

        # Маркер для целевого чата
        marker = ' [ЦЕЛЕВОЙ]' if chat_id == TARGET_CHAT_ID else ''

        print(f"\n{'='*60}")
        print(f"Новое сообщение{marker}")
        print(f"  Чат: {chat_id}")
        print(f"  От: {sender_name} (id={message.sender_id})")
        print(f"  Текст: {text[:500]}")

        # Вложения
        if message.attaches:
            print(f"  Вложения ({len(message.attaches)}):")
            for i, attach in enumerate(message.attaches):
                print(f"    [{i}] type={type(attach).__name__}")
                if isinstance(attach, dict):
                    print(f"        keys: {list(attach.keys())}")
                    print(f"        data: {json.dumps(attach, ensure_ascii=False, indent=8)[:1000]}")
                else:
                    print(f"        attrs: {vars(attach) if hasattr(attach, '__dict__') else attach}")

        # Полные raw_data для отладки
        if message.raw_data:
            rd = json.dumps(message.raw_data, ensure_ascii=False, indent=2)
            if len(rd) > 3000:
                rd = rd[:3000] + '...'
            print(f"  raw_data: {rd}")

        print(f"{'='*60}")

    print("Запускаем клиент...")
    print("(При первом запуске будет отправлен SMS-код)\n")
    await client.start()


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nОстановлено пользователем.")
    except Exception as e:
        print(f"\nОшибка: {e}")
        import traceback
        traceback.print_exc()
