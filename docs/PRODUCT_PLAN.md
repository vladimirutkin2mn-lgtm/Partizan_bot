# Partizan Bot — Product Vision & Action Plan

## 1. Идея

**Partizan Bot** — автономный AI Growth Operator для internet-native продуктов.

Пользователь не просит систему «придумать пост» или «настроить рекламу». Он описывает продукт своими словами и задаёт бизнес-цель:

> У меня AI relationship oracle. Он помогает пользователю разбирать отношения и получать персонализированные интерактивные readings. Подписка — $9.99. Бюджет — $1,000. Найди первые 200 платящих пользователей с CAC не выше $5.

Если описания недостаточно, Partizan Bot сам задаёт несколько уточняющих вопросов, формирует структурированный `ProductProfile`, показывает пользователю своё понимание продукта и после этого запускает growth loop.

Дальше система должна сама пройти полный цикл:

1. зафиксировать продукт, его ценность и ограничения;
2. выделить и приоритизировать целевые аудитории;
3. найти конкретные места, где эти аудитории уже находятся;
4. придумать набор acquisition-гипотез;
5. подготовить необходимые маркетинговые материалы;
6. запустить разрешённые действия через подключённые каналы;
7. собрать данные по результатам;
8. остановить слабые гипотезы и масштабировать сильные;
9. генерировать следующую волну экспериментов.

Главная продуктовая идея:

> **Не AI для создания маркетинга, а AI, которому можно поставить задачу “найди мне клиентов”.**

---

## 2. Проблема

У небольших SaaS, Telegram-ботов, AI-приложений, mobile apps и других digital-продуктов часто нет полноценной growth-команды.

Основатель может самостоятельно создать продукт, но дальше возникает длинная цепочка ручной работы:

- понять, кому продавать;
- исследовать конкурентов;
- найти сообщества и площадки;
- искать блогеров и партнёров;
- писать им;
- делать креативы;
- создавать лендинги;
- размечать ссылки;
- запускать эксперименты;
- собирать аналитику;
- сравнивать CAC;
- придумывать следующую гипотезу.

Большинство AI-маркетинговых продуктов автоматизируют отдельные части этого процесса. Partizan Bot должен автоматизировать **цикл принятия решений целиком**.

---

## 3. Первый целевой клиент

На старте не пытаемся обслуживать любой бизнес.

### ICP v1

Небольшие internet-native продукты:

- Telegram-боты;
- AI apps;
- SaaS;
- mobile apps;
- browser extensions;
- digital subscriptions;
- небольшие consumer apps;
- creator / info products.

### Почему этот сегмент

У них обычно есть:

- понятный digital product;
- простая регистрация;
- измеримая конверсия;
- понятная цена или monetization model;
- короткий путь до покупки;
- возможность быстро получить обратную связь по эксперименту.

Это позволяет Partizan Bot учиться на фактических данных, а не только выдавать советы.

---

## 4. Что пользователь передаёт системе

### Принцип onboarding

**На MVP Partizan Bot не должен пытаться понять продукт по URL.**

Источник истины о самом продукте — его создатель. Пользователь описывает продукт своими словами, а система превращает это описание в структурированный `ProductProfile`.

Если каких-то данных не хватает или система видит противоречие, она задаёт уточняющие вопросы.

URL, bot link, App Store link или сайт могут быть сохранены как **опциональные reference links**, но не являются обязательным источником для понимания продукта в MVP.

### Минимальный product brief

Пользователь сообщает:

- название / тип продукта;
- что делает продукт;
- какую проблему или желание пользователя он закрывает;
- ключевые use cases;
- УТП / почему пользователь должен выбрать именно его;
- цену и модель монетизации;
- географию;
- язык;
- бизнес-цель;
- бюджет;
- максимальный CAC / CPA, если он известен;
- доступные каналы и аккаунты;
- ограничения на действия.

Опционально:

- предполагаемая текущая аудитория;
- известные конкуренты;
- прошлые маркетинговые эксперименты;
- existing traction;
- reference links.

### Пример initial brief

```text
Product: AI relationship oracle
Description: интерактивный AI-провидец для вопросов об отношениях и будущем отношений
Value proposition: персонализированные readings, которые учитывают историю пользователя
USP: не статический гороскоп, а продолжающаяся персональная история
Market: US
Language: English
Price: $9.99/month
Budget: $500
Goal: 100 paid users
Max CAC: $5
Allowed: creator outreach, partnerships, content, communities, SEO
```

### Уточняющий диалог

Partizan Bot не должен заставлять пользователя заполнять огромную анкету заранее.

Flow:

```text
Свободное описание продукта
        ↓
Draft ProductProfile
        ↓
Gap / contradiction detection
        ↓
1–3 самых важных уточняющих вопроса
        ↓
Updated ProductProfile
        ↓
Если информации достаточно → подтверждение понимания
        ↓
ICP discovery
```

Примеры полезных вопросов:

- «За что конкретно пользователь платит $9.99: подписку на неограниченные readings или пакет?»
- «Какой основной повод заставляет человека открыть продукт впервые?»
- «Что пользователь получает у вас такого, чего нет в обычном ChatGPT?»
- «Есть ли аудитории, которым продукт точно не предназначен?»

Важно: система спрашивает **только то, что существенно изменит marketing strategy**.

---

## 5. Основной продуктовый pipeline

### Stage 1 — Product Intake & Clarification

Product Analyst получает свободное описание от пользователя и:

- выделяет value proposition;
- формулирует основные use cases;
- выделяет user pain / desire;
- фиксирует USP / differentiation;
- нормализует pricing / monetization;
- фиксирует market / language;
- фиксирует бизнес-цель, budget и CAC guardrail;
- отмечает assumptions;
- обнаруживает пробелы и противоречия;
- задаёт минимально необходимое число уточняющих вопросов.

Результат: подтверждённый пользователем структурированный `ProductProfile`.

**В MVP здесь нет crawling лендинга и автоматического извлечения product facts из URL.**

После формирования `ProductProfile` Partizan Bot уже может использовать web/search для внешнего исследования рынка, конкурентов, аудитории и каналов.

### Stage 2 — Generate ICPs

Создаётся 10–30 потенциальных сегментов.

Для каждого сегмента фиксируются:

- кто это;
- какая у него проблема / желание;
- какой trigger заставляет искать решение;
- насколько проблема срочная;
- willingness to pay;
- альтернативы;
- предполагаемый message / hook.

### Stage 3 — Score ICPs

Каждый сегмент получает оценку по нескольким измерениям:

- pain intensity;
- purchase intent;
- willingness to pay;
- ease of targeting;
- market size;
- competition;
- expected CAC;
- speed of validation.

Результат — ranked list сегментов.

### Stage 4 — Channel Discovery

Partizan Bot ищет не абстрактные каналы вроде «TikTok» или «Reddit», а конкретные точки дистрибуции:

- конкретные creators;
- Telegram-каналы;
- Reddit communities;
- Discord servers;
- newsletters;
- directories;
- niche sites;
- podcasts;
- блогеров;
- affiliate-партнёров;
- complementary products;
- SEO query clusters;
- публичные discussion threads;
- тематические медиа.

Каждый найденный источник становится объектом `ChannelOpportunity`.

### Stage 5 — Generate Growth Plays

Система превращает аудиторию + канал в конкретную executable-гипотезу.

Примеры:

#### Micro Creator Seeding

Найти creators → оценить engagement и соответствие ICP → выбрать лучших → подготовить персонализированный outreach → выдать referral links → сравнить CAC.

#### Affiliate Hunting

Найти продукты и creators с совпадающей аудиторией → предложить revshare / CPA → подключить tracking → масштабировать лучших партнёров.

#### Community Discovery

Найти сообщества, где уже обсуждается проблема → сформировать полезный контент и список разрешённых способов участия → измерить переходы и регистрации.

#### Programmatic SEO

Найти long-tail query clusters → создать landing templates → публиковать страницы → измерять impressions / signup / paid conversion.

#### Newsletter Seeding

Найти небольшие newsletters с подходящей аудиторией → собрать контакты → подготовить предложения о sponsored / affiliate / cross-promo размещении.

#### Partnership Hunting

Найти неконкурирующие продукты с тем же ICP → подготовить cross-promo / bundle / referral предложение.

### Stage 6 — Build Assets

Система создаёт необходимые материалы:

- outreach messages;
- email sequences;
- social copy;
- landing copy;
- creator briefs;
- referral links;
- UTM links;
- affiliate offers;
- SEO briefs;
- experiment descriptions;
- креативы через подключённый image/video generation stack.

### Stage 7 — Execute

Система выполняет действия через разрешённые API и подключённые аккаунты.

На MVP часть действий может требовать подтверждения пользователя (`approve / reject`).

### Stage 8 — Measure

Для каждого эксперимента собираем:

- spend;
- impressions;
- clicks;
- visits;
- signups;
- activated users;
- paid users;
- revenue;
- CAC / CPA;
- conversion rate;
- payback / ROAS, когда доступны данные.

### Stage 9 — Decide

Growth Manager Agent принимает одно из решений:

- `SCALE`;
- `CONTINUE`;
- `MODIFY`;
- `STOP`.

### Stage 10 — Learn

Система сохраняет результаты экспериментов и использует их при следующем планировании.

Цель — постепенно построить собственный data moat:

> какие аудитории × каналы × hooks × offers работают для конкретных типов продуктов.

---

## 6. Агентная архитектура

Не строим одного огромного универсального агента.

### 1. Product Analyst Agent

Ведёт guided intake: превращает свободное пользовательское описание в `ProductProfile`, обнаруживает gaps / contradictions и задаёт уточняющие вопросы.

### 2. ICP Agent

Генерирует и оценивает сегменты аудитории.

### 3. Channel Hunter Agent

Ищет конкретные точки дистрибуции в интернете.

### 4. Growth Hacker Agent

Создаёт Growth Plays для конкретной пары `ICP × Channel`.

### 5. Creative Agent

Создаёт тексты, hooks, briefs и креативы.

### 6. Outreach Agent

Готовит и позже выполняет персонализированную коммуникацию.

### 7. Experiment Runner

Запускает разрешённые действия и управляет состояниями эксперимента.

### 8. Analytics Agent

Собирает метрики и нормализует attribution.

### 9. Growth Manager Agent

Выбирает, что масштабировать, изменять и останавливать.

---

## 7. Killer feature

Главный интерфейс продукта:

# Find me customers

Пользователь видит не список рекомендаций, а процесс выполнения цели.

Пример dashboard:

| Experiment | Spend | Paid users | CAC | Decision |
|---|---:|---:|---:|---|
| TikTok creators | $420 | 103 | $4.08 | SCALE |
| Reddit Ads | $300 | 21 | $14.29 | STOP |
| SEO cluster A | $90 | 17 | $5.29 | CONTINUE |
| Affiliates | $210 | 79 | $2.66 | SCALE |
| Newsletters | $160 | 44 | $3.64 | SCALE |

Growth Manager сообщает пользователю:

```text
Affiliate partnerships outperform paid social by 4.9x.
I stopped the weakest experiment.
I found a new creator segment and prepared the next test.
```

---

## 8. Что понимаем под «партизанским маркетингом»

Partizan Bot должен искать дешёвые, недооценённые и нестандартные точки дистрибуции.

Приоритетные механики:

- micro-influencer discovery;
- affiliate seeding;
- partnerships;
- cross-promo;
- directory distribution;
- community discovery;
- contextual participation в сообществах, когда это разрешено правилами площадки;
- public conversation discovery;
- newsletter outreach;
- PR / niche media discovery;
- SEO / programmatic SEO;
- content opportunities вокруг реальных трендов и информационных поводов.

Не строим систему вокруг:

- массового спама;
- накрутки;
- фейковых отзывов;
- fake engagement;
- скрытой выдачи AI за независимого реального пользователя;
- обхода банов и ограничений площадок.

Это не только вопрос правил: такие механики плохо масштабируются и быстро уничтожают аккаунты, домены и репутацию продукта.

---

# 9. MVP

## Главная задача MVP

Доказать следующий цикл:

> **Product Brief → ProductProfile → ICP → concrete channels → Growth Plays → experiment → metrics → decision.**

Не нужно сразу автоматически запускать 20 рекламных кабинетов.

### MVP v0 — Research Engine

Пользователь описывает продукт и задаёт growth goal.

Partizan Bot:

1. формирует draft `ProductProfile`;
2. при необходимости задаёт уточняющие вопросы;
3. получает подтверждённый `ProductProfile`;
4. создаёт 10–20 ICP;
5. выполняет scoring ICP;
6. находит список конкретных каналов;
7. генерирует 20–50 Growth Plays;
8. приоритизирует эксперименты.

### MVP v1 — Assisted Execution

Добавляем execution с подтверждением пользователя:

- outreach lists;
- personalized messages;
- creator / partner contact queue;
- UTM generation;
- referral codes;
- simple landing variants;
- experiment state machine;
- ручной `Approve & Run`.

### MVP v2 — Closed Learning Loop

Добавляем:

- event tracking;
- attribution;
- experiment comparison;
- CAC calculation;
- Growth Manager decisions;
- automatic next-experiment generation.

### MVP v3 — Autonomous Growth

Добавляем ограниченную автономность:

- budget allocation;
- auto-stop rules;
- auto-scale rules;
- scheduled discovery;
- autonomous outreach в разрешённых каналах;
- multi-channel execution.

---

# 10. Что НЕ делаем в первом MVP

Чтобы не утонуть в интеграциях, на старте не делаем:

- автоматический разбор продукта по URL;
- crawling лендинга для извлечения product facts;
- универсальную CRM;
- полный Meta Ads manager;
- полный Google Ads manager;
- TikTok Ads replacement;
- поддержку offline-бизнесов;
- enterprise marketing automation;
- сложную multi-touch attribution;
- собственный email provider;
- собственную систему генерации видео;
- автоматизацию всех соцсетей одновременно.

---

# 11. Предлагаемый technical shape

На старте — простой modular monolith.

```text
Frontend / Telegram UI
        |
        v
     API layer
        |
        v
Growth Orchestrator
        |
        +--> Product Analyst / Clarification
        +--> ICP Agent
        +--> Channel Hunter
        +--> Growth Hacker
        +--> Creative Agent
        +--> Outreach Agent
        +--> Analytics Agent
        +--> Growth Manager
        |
        v
PostgreSQL
        |
        +--> Products
        +--> ICPs
        +--> Channels
        +--> Growth Plays
        +--> Experiments
        +--> Events
        +--> Decisions
```

Отдельно:

- background jobs / queue;
- browser/search connectors для внешнего market/channel research;
- LLM provider abstraction;
- integrations layer;
- analytics/event ingestion.

Не начинаем с microservices.

---

# 12. Базовые сущности данных

### Product

- id
- name
- category
- description
- problem_or_desire
- value_proposition
- usp
- use_cases
- pricing_model
- price
- target_geographies
- languages
- known_audience
- known_competitors
- assumptions
- reference_links
- goal
- budget
- max_cac
- allowed_channels
- restrictions
- profile_status

`profile_status` на старте:

- `DRAFT`;
- `NEEDS_CLARIFICATION`;
- `CONFIRMED`.

### ClarificationQuestion

- id
- product_id
- question
- reason
- priority
- answer
- status

### ICP

- id
- product_id
- title
- description
- pain
- trigger
- willingness_to_pay
- score

### ChannelOpportunity

- id
- icp_id
- platform
- url
- type
- audience_size
- relevance_score
- contact_data
- acquisition_method

### GrowthPlay

- id
- icp_id
- channel_id
- hypothesis
- execution_plan
- expected_cost
- expected_result
- priority

### Experiment

- id
- growth_play_id
- status
- budget
- started_at
- ended_at
- metrics

### Decision

- experiment_id
- action
- reason
- created_at

---

# 13. Главные продуктовые метрики

## North Star на этапе разработки

**Количество экспериментальных циклов, которые Partizan Bot может провести от гипотезы до измеримого результата.**

## Качество Product Intake

- % product briefs, после которых достаточно ≤3 уточняющих вопросов;
- median number of clarification questions;
- доля ProductProfile, подтверждённых пользователем без существенных правок;
- time from first description to confirmed ProductProfile.

## Качество discovery

- % найденных каналов, признанных релевантными;
- число новых actionable opportunities на продукт;
- доля дублирующихся возможностей;
- стоимость discovery одного качественного канала.

## Качество Growth Plays

- доля гипотез, которые пользователь готов запустить;
- experiment launch rate;
- median time from product input to first experiment.

## Экономика

- CAC;
- CAC improvement per iteration;
- conversion to paid;
- revenue generated;
- marketing spend under management.

## Автономность

- % этапов без ручного вмешательства;
- approvals required per experiment;
- доля автоматически принятых правильных `scale / stop` решений.

---

# 14. План разработки

## Milestone 0 — Foundation

Цель: создать каркас приложения и основные контракты данных.

- [ ] определить стек;
- [ ] создать application skeleton;
- [ ] PostgreSQL + migrations;
- [ ] модели Product / ClarificationQuestion / ICP / Channel / GrowthPlay / Experiment;
- [ ] базовая LLM abstraction;
- [ ] job queue;
- [ ] structured logging;
- [ ] минимальные тесты и CI.

**Definition of Done:** можно создать Product, пройти mock clarification flow и запустить mock growth workflow.

---

## Milestone 1 — Product Brief & Clarification

- [ ] free-text product intake;
- [ ] `ProductProfile` schema;
- [ ] extraction of structured product facts from user input;
- [ ] completeness rules;
- [ ] contradiction / ambiguity detection;
- [ ] clarification question generator;
- [ ] answer ingestion;
- [ ] profile confirmation state;
- [ ] assumption tracking.

**DoD:** по свободному описанию пользователя система формирует `ProductProfile`, задаёт только необходимые уточняющие вопросы и доводит профиль до состояния `CONFIRMED` без чтения сайта продукта.

---

## Milestone 2 — ICP Engine

- [ ] ICP generation;
- [ ] ICP scoring rubric;
- [ ] ranking;
- [ ] pain / trigger / WTP hypotheses;
- [ ] duplicate clustering;
- [ ] explainability для score.

**DoD:** система выдаёт ranked список минимум из 10 осмысленных ICP.

---

## Milestone 3 — Channel Hunter

Начать только с 2–3 типов источников.

Предлагаемый первый набор:

1. Reddit / public communities;
2. creators;
3. newsletters / niche websites.

Задачи:

- [ ] source adapters;
- [ ] search query generation;
- [ ] crawling / retrieval;
- [ ] normalization;
- [ ] deduplication;
- [ ] relevance scoring;
- [ ] evidence storage.

**DoD:** для top ICP система находит минимум 30 конкретных `ChannelOpportunity` с URL и rationale.

---

## Milestone 4 — Growth Play Generator

- [ ] GrowthPlay schema;
- [ ] play templates;
- [ ] hypothesis generation;
- [ ] estimated effort / cost;
- [ ] expected impact;
- [ ] experiment prioritization;
- [ ] user approval state.

**DoD:** система превращает найденные каналы минимум в 20 конкретных executable experiments.

---

## Milestone 5 — Execution Assistant

Первый execution channel лучше сделать не рекламным кабинетом, а outreach / partnership flow.

- [ ] contact extraction;
- [ ] personalized message generation;
- [ ] outreach queue;
- [ ] approve / edit / reject;
- [ ] UTM generator;
- [ ] referral link support;
- [ ] experiment status tracking.

**DoD:** пользователь может выбрать Growth Play и реально запустить его из Partizan Bot.

---

## Milestone 6 — Analytics Loop

- [ ] event ingestion API;
- [ ] UTM attribution;
- [ ] signup / paid conversion;
- [ ] spend tracking;
- [ ] CAC calculation;
- [ ] experiment dashboard;
- [ ] normalized metrics.

**DoD:** система видит результат эксперимента и рассчитывает CAC.

---

## Milestone 7 — Growth Manager

- [ ] `SCALE / CONTINUE / MODIFY / STOP` policy;
- [ ] decision rationale;
- [ ] budget guardrails;
- [ ] automatic next hypothesis;
- [ ] learning memory;
- [ ] experiment history retrieval.

**DoD:** после получения результатов Partizan Bot самостоятельно предлагает следующий лучший action.

---

## Milestone 8 — Dogfood

Первый настоящий полигон — наши собственные digital-продукты.

Для каждого тестового продукта:

1. пользователь описывает продукт и цель;
2. Partizan Bot формирует и уточняет `ProductProfile`;
3. ставится конкретная acquisition goal;
4. ограничивается бюджет;
5. запускается discovery;
6. выбираются top Growth Plays;
7. проводятся реальные эксперименты;
8. записываются CAC и conversion;
9. прогноз сравнивается с фактом;
10. scoring улучшается на фактических данных.

**DoD:** минимум один продукт получает первых реальных пользователей через pipeline Partizan Bot.

---

# 15. Первый end-to-end сценарий

Первый сценарий должен быть максимально узким:

```text
Свободное описание продукта + УТП + цель
    ↓
Product Analyst
    ↓
Draft ProductProfile
    ↓
Уточняющие вопросы, только если нужны
    ↓
Confirmed ProductProfile
    ↓
10 ICP
    ↓
Top 3 ICP
    ↓
Channel Hunter
    ↓
30–100 concrete opportunities
    ↓
Growth Hacker
    ↓
20 Growth Plays
    ↓
Top 5 experiments
    ↓
User approves one outreach experiment
    ↓
Messages + UTM/referral links
    ↓
Execution
    ↓
Signup / paid events
    ↓
CAC
    ↓
Scale / Modify / Stop
```

Пока этот сценарий не работает end-to-end, не расширяем число каналов.

---

# 16. Ключевые принципы разработки

1. **User is the source of truth for product facts.** На MVP не пытаемся угадывать продукт по сайту.
2. **Ask only high-value questions.** Уточняющий вопрос должен существенно менять маркетинговое решение.
3. **Execution over reports.** Каждая рекомендация должна стремиться стать действием.
4. **Evidence over hallucination.** Рыночные выводы, аудитории и каналы должны ссылаться на реальные внешние источники, когда это возможно.
5. **Concrete channels over generic advice.** Не «используйте Reddit», а конкретное сообщество / creator / newsletter.
6. **Experiments over strategy decks.** Каждая идея превращается в измеримый тест.
7. **Economics over vanity metrics.** Главные результаты — paid users, revenue, CAC.
8. **Closed loop.** Результат эксперимента обязан влиять на следующую итерацию.
9. **Human approval first, autonomy later.** Сначала доказываем качество решений, потом увеличиваем автономность.
10. **Dogfood aggressively.** Наши собственные боты и приложения — основной тестовый полигон.

---

# 17. Ближайший порядок действий

Начинаем не с интерфейса, scraping продукта и не с рекламных интеграций.

### Следующие шаги

1. создать технический skeleton;
2. зафиксировать Pydantic / DB schemas `Product`, `ClarificationQuestion` и остальных основных сущностей;
3. реализовать guided Product Analyst;
4. реализовать completeness / contradiction detection;
5. реализовать clarification loop;
6. реализовать ICP Agent + scoring;
7. выбрать три первых класса источников для Channel Hunter;
8. довести discovery до реальных URL и evidence;
9. реализовать GrowthPlay generation;
10. собрать простой experiment runner;
11. подключить event tracking;
12. проверить полный цикл на одном из наших существующих ботов.

Первый большой критерий успеха проекта:

> **Partizan Bot получает свободное описание нового digital-продукта, при необходимости задаёт несколько точных уточняющих вопросов и без заранее заданной аудитории самостоятельно находит минимум 3 правдоподобных ICP, 30 конкретных точек дистрибуции и 10 измеримых Growth Plays, из которых хотя бы один реально запускается и получает измеримый результат.**
