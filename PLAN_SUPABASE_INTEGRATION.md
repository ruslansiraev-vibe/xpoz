# Промпт: Интеграция данных TikTok + Udemy + Xpoz в Supabase

> Этот документ — готовый промпт-план для реализации. Скопируй его в чат с AI-ассистентом и попроси выполнить нужную фазу.

---

## Контекст проекта

Три изолированных проекта, данные которых нужно объединить в единую Supabase базу:

### 1. base_tiktok (`/root/projects/base_tiktok/`)
- **SQLite** `data.db`, таблица `csv_data` — ~50 колонок TikTok-профилей
- Колонки (из `migrate_to_supabase.py` → `TIKTOK_COLS`): `keyword_source`, `uniqueId`, `secUid`, `id`, `nickname`, `signature`, `email`, `bioLink`, `bioLink_title`, `language`, `region`, `verified`, `privateAccount`, `followers`, `following`, `likes`, `videos`, `diggCount`, `engagement_rate`, `avg_likes_per_video`, `follower_ratio`, `avatar`, `sample_aweme_id`, `sample_desc`, `sample_hashtags`, `sample_mentions`, `sample_playCount`, `sample_shareCount`, `sample_commentCount`, `sample_diggCount`, `sample_createTime`, `sample_duration`, `sample_music_title`, `sample_music_author`, `account_createTime`, `source`, `enrich_error`, `mailing_list`, `phone`, `business`, `cat`, `post_date`, `privacy`, `ads`, `likes_coefficient`, `comment_coefficient`, `login`, `fol_cnt`, `sub_cnt`, `post_cnt`, `name`, `desc`, `link`, `city`, `country`, `reg_date`, `lang`
- Уже есть `migrate_to_supabase.py` — простой APPEND в таблицу `tiktok_creators` через psycopg2 (без upsert, без дедупликации)
- Есть `merge_update_base.py` — мержит Instagram-данные в SQLite по `uniqueId`
- Уникальный ключ: `uniqueId`

### 2. udemy (`/root/projects/udemy/`)
- **SQLite** `data.db`, таблица `leads` — 40+ колонок инструкторов Udemy
- Колонки (из `udemy_leads/db.py` → `LEADS_COLUMNS`): `course_id`, `course_title`, `course_description`, `price_current`, `price_original`, `course_url`, `instructor`, `raw_instructor`, `scraped_at`, `search_url`, `course_img`, `ribbon`, `rating`, `rating_count`, `total_hours`, `lectures_count`, `level`, `UdemyProfile1-3`, `profile_links_status`, `GoogleLink1-15`, `email`, `email2`, `email3`, `facebook`, `instagram`, `linkedin`, `phone`, `pinterest`, `tiktok`, `twitter`, `website`, `youtube`, `pipeline_stage`, `apify_run_id_*`, `email_sent_at`, `email_opened`, `email_replied`, `email_bounced`
- Upsert по `course_url`, контакты мержатся (не перезаписываются)
- Стадии пайплайна: `udemy` → `google` / `google_empty` → `done`
- Уникальный ключ: `course_url`
- **Важные поля для линковки**: `instagram`, `tiktok`, `youtube`, `twitter`, `email`

### 3. xpoz (`/root/projects/xpoz/deploy/`)
- Анализ Instagram-аккаунтов через **Xpoz MCP API** (`https://mcp.xpoz.ai/mcp`)
- Supabase таблицы: `accounts` (входные данные, колонки: `login`, `email`, `fol_cnt`), `analysis_results` (результаты анализа)
- `analysis_results` колонки: `username`, `analyzed_at`, `follower_count`, `posts_analyzed`, `reels_performance`, `reels_90d_count`, `reels_above_150pct`, `low_performing_reels`, `bottom10_avg_views`, `post_engagement`, `engagement_rate_pct`, `total_interactions`, `monetization`, `monetization_signals`, `monetization_reason`, `youtube_url`, `twitter_url`, `twitter_followers`, `other_socials` (JSONB), `error`, `llm_cost_usd`, `xpoz_results_used`
- TikTok — только ссылки из bio Instagram (в `other_socials.tiktok`)
- Supabase URL: задаётся через `SUPABASE_URL` / `SUPABASE_KEY`
- `analysis_results` — независимая аналитическая сущность Xpoz. Она не должна иметь прямой FK/parent-child связи с `udemy_leads` или `leads`

**Связь между проектами отсутствует.** Нет линковки профилей, нет общей таблицы, нет синхронизации.

---

## Целевая архитектура

```
Источники данных                    Supabase PostgreSQL
─────────────────                   ────────────────────
base_tiktok SQLite ──sync_tiktok.py──► tiktok_creators (upsert по uniqueId)
                                              │
udemy SQLite ────────sync_udemy.py───► udemy_leads (upsert по course_url)
                                              │
Xpoz MCP API ───────batch_analyze.py─► analysis_results (уже работает)
                                              │
                                    link_profiles.py
                                              │
                                              ▼
                              profile_links / identity_links
                         (нейтральная таблица соответствий)
                                              │
                                              ▼
                         unified_creators VIEW / analyst views
```

---

## Фаза 1: Схема Supabase — новые таблицы

### Задача
Создать DDL и выполнить в Supabase SQL Editor.

### 1.1. Доработать `tiktok_creators`
- Добавить `UNIQUE` constraint на `uniqueId`
- Добавить индексы на `email`, `login`, `followers`, `region`, `source`

### 1.2. Создать таблицу `udemy_leads`
- Все колонки из `LEADS_COLUMNS` (тип TEXT, как в SQLite)
- UNIQUE по `course_url`
- Индексы на `instructor`, `email`, `instagram`, `tiktok`, `pipeline_stage`

### 1.3. Создать таблицу `profile_links`
```sql
CREATE TABLE profile_links (
    id BIGSERIAL PRIMARY KEY,
    left_system TEXT NOT NULL,         -- 'tiktok', 'udemy', 'xpoz'
    left_entity_type TEXT NOT NULL,    -- 'creator_profile', 'lead', 'analysis_result'
    left_entity_key TEXT NOT NULL,     -- uniqueId / course_url / username
    right_system TEXT NOT NULL,
    right_entity_type TEXT NOT NULL,
    right_entity_key TEXT NOT NULL,
    match_method TEXT,                 -- 'email', 'username', 'bio_link', 'manual'
    confidence FLOAT DEFAULT 1.0,
    evidence JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(
        left_system,
        left_entity_type,
        left_entity_key,
        right_system,
        right_entity_type,
        right_entity_key
    )
);
```

Назначение таблицы:
- Хранить только соответствия между сущностями разных проектов
- Не делать прямой зависимости `udemy_leads -> analysis_results` или `analysis_results -> udemy_leads`
- Разрешать many-to-many связи и неоднозначные матчи с `confidence` и `evidence`

### 1.4. Создать VIEW `unified_creators`
- VIEW строится через `profile_links`, но сами таблицы `tiktok_creators`, `udemy_leads`, `analysis_results` остаются независимыми
- `analysis_results` подключается как аналитический snapshot по профилю, а не как дочерняя запись `lead`
- Если одна строка получается слишком широкой, лучше сделать 2 VIEW:
  - `unified_profiles` для identity-слоя
  - `unified_analysis` для аналитики Xpoz поверх найденных связей

---

## Фаза 2: Миграция данных

### 2.1. Доработать `migrate_to_supabase.py` → `sync_tiktok.py`
Файл: `/root/projects/base_tiktok/migrate_to_supabase.py`

Что изменить:
- Заменить простой INSERT на **upsert** (`INSERT ... ON CONFLICT(uniqueId) DO UPDATE`)
- Добавить инкрементальную синхронизацию: запоминать последний `_rowid`, синхронизировать только новые
- Поддержка повторного запуска (идемпотентность)
- Добавить `--incremental` флаг

### 2.2. Создать `sync_udemy.py`
Расположение: `/root/projects/udemy/sync_udemy.py`

- Читать из Udemy SQLite (`data.db`, таблица `leads`)
- Upsert в Supabase `udemy_leads` по `course_url`
- **Переносить только строки со стадией `done`** (проверенные данные)
- Батчевая загрузка через psycopg2 (`execute_batch`)
- `--dry-run` для проверки

---

## Фаза 3: Линковка профилей

### 3.1. Создать `link_profiles.py`
Расположение: `/root/projects/xpoz/deploy/link_profiles.py` (или отдельный проект)

Три стратегии матчинга (в порядке приоритета):

1. **По email** — если `email` из `tiktok_creators` совпадает с `email`/`email2`/`email3` из `udemy_leads` или email из `accounts`
2. **По username/handle** — если `uniqueId` из TikTok совпадает с `instagram` из Udemy или `username` из `analysis_results`
3. **По ссылке в bio** — если `other_socials->>'tiktok'` из `analysis_results` содержит `uniqueId` из `tiktok_creators`

Результат матчинга:
- Создаётся запись в `profile_links`
- Исходные таблицы не обновляются прямыми FK друг на друга
- `analysis_results` остаётся самостоятельной таблицей результатов анализа профиля

Логика вставки нового TikTok профиля:
- **Сценарий A**: профиля нет нигде в базе → просто INSERT в `tiktok_creators`
- **Сценарий B**: профиль уже матчится с Udemy или Xpoz (по email/username/bio) → INSERT в `tiktok_creators` + создать запись в `profile_links`

### 3.2. Автозапуск линковки
После каждого `sync_tiktok.py` / `sync_udemy.py` автоматически запускать `link_profiles.py` для новых записей.

---

## Фаза 4: Расширение сбора данных в Xpoz

### 4.1. Дополнительные данные в `analyze_account.py`
Файл: `/root/projects/xpoz/deploy/analyze_account.py`

Сейчас собирается: Instagram профиль (10 полей), 20 постов (9 полей каждый), Twitter по username, ссылки из bio.

Добавить параллельно:
- **TikTok через Xpoz** (если API поддерживает) — метрики TikTok-аккаунта, если найден в bio
- **Расширенный парсинг bio** — Linktree, Beacons, Stan Store и другие агрегаторы ссылок
- **Дополнительные метрики постов** — хэштеги, упоминания брендов, типы контента
- **Сохранение raw-данных** — полный JSON профиля в JSONB-колонку для будущего анализа

### 4.2. Обновление схемы `analysis_results`
- ALTER TABLE: добавить колонки для новых данных
- Обновить `save_result` в `batch_analyze.py`

---

## Фаза 5: Оркестрация

### 5.1. Создать `sync_all.py`
```
sync_all.py
  1. sync_tiktok.py  (SQLite → Supabase tiktok_creators)
  2. sync_udemy.py   (SQLite → Supabase udemy_leads)
  3. link_profiles.py (автоматическая линковка новых записей)
  4. Отчёт: новые записи, новые линки, ошибки
```

Запуск по cron или вручную на любом из серверов.

### 5.2. Использование серверов
- **Сервер 1** (где Xpoz): `batch_analyze.py` + `sync_tiktok.py`
- **Сервер 2** (где base_tiktok + udemy SQLite): `sync_udemy.py` + `sync_tiktok.py` + `link_profiles.py`
- Оба сервера пишут в **один Supabase проект**

---

## Порядок реализации

1. Фаза 1 (схема) → 2. Фаза 2 (миграция) → 3. Фаза 3 (линковка) → 4. Фаза 4 (расширение Xpoz) → 5. Фаза 5 (оркестрация)

Каждую фазу можно реализовать отдельно. Фазы 1-3 — минимальный MVP.
