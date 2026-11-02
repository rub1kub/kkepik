# Журнал действий

Бот и мини-апп пишут в `/var/lib/kkepik-activity/events.sqlite3`. Записи хранятся 30 дней. Тела сообщений, `initData` и IP-адреса в журнал не входят.

При установке на новом сервере дайте боту и Apache доступ к одной базе:

```bash
sudo install -d -m 2770 -o root -g www-data /var/lib/kkepik-activity
sudo touch /var/lib/kkepik-activity/events.sqlite3
sudo chown root:www-data /var/lib/kkepik-activity/events.sqlite3
sudo chmod 0660 /var/lib/kkepik-activity/events.sqlite3
sudo install -m 0644 deploy/systemd/kkepik-activity-prune.* /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now kkepik-activity-prune.timer
```

В unit-файле указан путь `/root/kkepik_bot`. Если бот установлен в другом каталоге, измените `WorkingDirectory` и `ExecStart`. Путь к журналу можно задать через `KKEPIK_ACTIVITY_DB`; у бота и веб-приложения он должен совпадать.

Отчёты доступны локально из каталога бота:

```bash
./venv/bin/python scripts/activity_report.py --user TELEGRAM_ID --since YYYY-MM-DD
./venv/bin/python scripts/activity_report.py --downloads --since YYYY-MM-DD
./venv/bin/python scripts/activity_report.py --downloads --anonymous
```

`origin=client` означает событие интерфейса. `schedule.fetch` — запрос данных, `schedule.view` — сообщение клиента об отображении расписания. Для скачиваний отдельно записываются запрос, выдача, ошибка и обрыв. `HEAD` помечается как проверка файла и не считается завершённым скачиванием.

В ссылках на PDF используется обычный `user_id`. Такие записи имеют `attribution=url_id`: это метка из ссылки, она не подтверждает личность скачавшего. Подписанные Telegram-заголовки имеют приоритет. При отсутствии ID скачивание записывается анонимно (`actor_id=0`).

Исключения из календаря задаются в `bot/college_calendar.py`. После изменения обновите копии для сайта:

```bash
python bot/scripts/build_college_calendar.py web
```
