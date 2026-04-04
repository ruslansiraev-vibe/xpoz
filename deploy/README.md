# Instagram Batch Analyzer — Инструкция по деплою на сервер

## Что делает скрипт

Анализирует 7,533 Instagram аккаунтов (те, у кого есть email и >3000 подписчиков)
по 5 критериям:

| # | Критерий | Метод |
|---|----------|-------|
| C1 | Reels performance — ≥5 reels с views ≥ 150% подписчиков | Python |
| C2 | Low-performing reels — avg нижних 10 из 15 > 15% подписчиков | Python |
| C3 | Post engagement — avg (likes+comments+reshares) / followers ≥ 1.5% | Python |
| C4 | Monetization signals — платные/бесплатные продукты, сообщества, вебинары | Claude Haiku |
| C5 | Other socials — YouTube, Twitter/X | Xpoz + bio parsing |

Входные аккаунты для batch-режима читаются из CSV-файла, синхронизируются в локальную SQLite таблицу `accounts`, а результаты пишутся в локальную SQLite таблицу `analysis_results` в реальном времени.

**Ожидаемое время:** ~1.5–2 часа (20 параллельных воркеров)
**Стоимость LLM:** ~$4.30 (Claude Haiku с кешированием)

---

## Требования к серверу

- Ubuntu 20.04+ / любой Linux
- Python 3.10+
- ~500 MB RAM
- Интернет (для Xpoz API и Anthropic)
- **Не нужно:** внешний PostgreSQL/Supabase, Nginx, Docker

Минимальный VPS: **$3–5/мес** (DigitalOcean, Hetzner, Contabo).
Подойдёт даже бесплатный tier Oracle Cloud (Always Free).

---

## Шаг 1 — Локальная база создаётся автоматически

При первом запуске `batch_analyze.py` сам создаст локальный файл SQLite и нужные таблицы:

- `accounts`
- `analysis_results`

---

## Шаг 2 — Залить файлы на сервер

```bash
# Вариант A: scp (если есть SSH)
scp -r deploy/ user@your-server-ip:~/instagram-analyzer/

# Вариант B: через git (создай приватный репо)
git init && git add . && git commit -m "init"
git remote add origin https://github.com/yourname/analyzer.git
git push
# На сервере: git clone https://github.com/yourname/analyzer.git

# Вариант C: copy-paste файлов через панель управления сервером
```

---

## Шаг 2.1 — Подготовить CSV с аккаунтами

Положи рядом с `batch_analyze.py` файл `accounts.csv` или передай путь через переменную окружения `XPOZ_ACCOUNTS_CSV`.

Минимально важные колонки:

- `login` — Instagram username
- `email` — для пресета `email-qualified`
- `fol_cnt` — число подписчиков для сортировки и фильтра `> 3000`

Пример:

```csv
login,email,fol_cnt
example_creator,creator@example.com,125000
another_creator,0,8700
```

Если колонка называется не `login`, скрипт также попробует найти username в `username`, `user`, `handle`, `instagram`, `instagram_username`.

### Список username из TXT (без CSV)

Файл: по одному Instagram username на строку; пустые строки и строки, начинающиеся с `#`, игнорируются. Шаблон: `usernames.template.txt`.

```bash
python3 batch_analyze.py --usernames-file ./my_list.txt --limit 200 --workers 20
```

В Ops Console тот же режим: блок **«Список из TXT»** на странице запусков.

---

## Шаг 3 — Установить зависимости

```bash
cd ~/instagram-analyzer
chmod +x setup.sh run.sh
./setup.sh
```

---

## Шаг 4 — Установить Anthropic API ключ

```bash
export ANTHROPIC_API_KEY=sk-ant-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

Чтобы ключ сохранился после перезапуска — добавь в `~/.bashrc`:

```bash
echo 'export ANTHROPIC_API_KEY=sk-ant-xxx...' >> ~/.bashrc
source ~/.bashrc
```

Получить ключ: https://console.anthropic.com/settings/keys

---

## Шаг 5 — Запустить

```bash
./run.sh
```

Скрипт запускается в фоне через `nohup` и пишет лог в файл `batch_YYYYMMDD_HHMMSS.log`.
Можно закрыть SSH сессию — процесс продолжит работу.

**Мониторинг прогресса:**

```bash
# Следить за логом в реальном времени
tail -f batch_*.log

# Посмотреть статистику через скрипт
python3 batch_analyze.py --stats
```

Запуск с явным CSV:

```bash
XPOZ_ACCOUNTS_CSV=/path/to/accounts.csv ./run.sh
python3 batch_analyze.py --csv-file /path/to/accounts.csv --preset email-qualified --workers 20
```

**Результаты в SQLite:**
по умолчанию это файл `../data.db` относительно папки `deploy/`

---

## Полезные команды

```bash
# Проверить что процесс работает
ps aux | grep batch_analyze

# Остановить
kill $(cat run.pid 2>/dev/null || pgrep -f batch_analyze)

# Запустить снова (продолжит с необработанных)
./run.sh

# Статистика по результатам
python3 batch_analyze.py --stats

# Проанализировать один аккаунт для теста
python3 batch_analyze.py --username whop

# Запустить batch с CSV
python3 batch_analyze.py --csv-file /path/to/accounts.csv --limit 1000 --workers 20

# Перезапустить все (включая уже проанализированные)
python3 batch_analyze.py --csv-file /path/to/accounts.csv --preset email-qualified --workers 20 --reanalyze
```

---

## Структура файлов

```
deploy/
├── analyze_account.py      # Анализ одного аккаунта (5 критериев)
├── batch_analyze.py        # Batch запуск + SQLite интеграция
├── migrate_analysis_results_to_sqlite.py  # Разовый перенос старых analysis_results из Supabase
├── requirements.txt        # Python зависимости
├── setup.sh                # Установка зависимостей
├── run.sh                  # Запуск в фоне
└── README.md               # Этот файл
```

---

## Конфиг

| Параметр | Значение |
|----------|----------|
| Xpoz | `XPOZ_API_KEY` или `XPOZ_API_KEYS` (через запятую) в `ops_console.local.env` |
| SQLite DB | `../data.db` или `$XPOZ_DB_PATH` |
| Accounts CSV | `./accounts.csv` или `$XPOZ_ACCOUNTS_CSV` |
| Workers | 20 (параллельных потоков) |
| Posts per account | 20 |

Xpoz ключи чередуются автоматически при превышении лимита (HTTP 429).

---

## Просмотр результатов в SQLite

После запуска данные сразу видны в таблице `analysis_results` внутри файла SQLite.

Для быстрого просмотра:

```bash
python3 batch_analyze.py --stats
sqlite3 ../data.db "SELECT username, follower_count, engagement_rate_pct FROM analysis_results ORDER BY id DESC LIMIT 20;"
```
