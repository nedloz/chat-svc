# Chat Service — API и функциональность

## 1. Назначение сервиса

`chat-svc` — микросервис realtime-чата, предоставляющий WebSocket API для взаимодействия пользователя с AI-системой.

Сервис является промежуточным слоем между frontend и остальными backend-компонентами AI-платформы. Он отвечает за жизненный цикл чат-сессии, получение информации о пользователе, хранение сообщений, передачу пользовательского вопроса в RAG, получение streaming-ответа от LLM и доставку этого ответа клиенту.

В текущей архитектуре `chat-svc` не выполняет самостоятельную валидацию JWT. Токен передаётся в WebSocket URL, например:

```text
/ws/chat?token=<access_token>
```

Nginx проверяет JWT и после успешной проверки передаёт в `chat-svc` идентификатор пользователя через header:

```http
X-User-Id: <user_uuid>
```

`chat-svc` затем использует этот UUID для работы с профилем, сессией и сообщениями пользователя.

---

# 2. Что представляет собой chat-svc

Сервис можно рассматривать как **оркестратор пользовательского чата**.

Он связывает следующие компоненты:

```text
                    Frontend
                       |
                       | WebSocket
                       v
                     Nginx
                       |
                 JWT validation
                       |
                  X-User-Id
                       |
                       v
                  chat-svc
                 /    |    \
                /     |     \
               v      v      v
          auth-svc  Redis  PostgreSQL
                        |
                        |
                        v
                     RAG
                      |
                      v
                     LLM
                      |
              streaming events
                      |
                      v
                  chat-svc
                      |
                      v
                   Frontend
```

В частности:

* `auth-svc` предоставляет профиль пользователя;
* Redis используется для кеша и временного состояния realtime-операций;
* PostgreSQL хранит постоянные данные;
* `rag-svc` отвечает за retrieval;
* `llm-svc` генерирует ответ;
* `chat-svc` связывает весь pipeline и отдаёт результат клиенту.

---

# 3. Полный функционал

`chat-svc` выполняет следующие задачи:

* устанавливает WebSocket-соединение с пользователем;
* получает идентификатор пользователя от gateway;
* получает профиль пользователя из `auth-svc`;
* кеширует профиль пользователя в Redis;
* получает существующую chat session или создаёт её;
* сохраняет пользовательские сообщения;
* создаёт связанные assistant messages;
* отправляет вопросы в RAG;
* поддерживает streaming-ответы от LLM;
* пересылает streaming chunks frontend;
* сохраняет итоговый ответ;
* хранит историю сообщений;
* поддерживает pagination истории;
* кеширует последние сообщения;
* принимает feedback на ответы;
* хранит RAG metadata и retrieval statistics;
* поддерживает periodic session history sync;
* поддерживает несколько WebSocket-подключений одной session;
* хранит временное состояние незавершённых LLM requests в Redis;
* предоставляет внутренний WebSocket для событий от LLM;
* предоставляет health check.

Основной пользовательский WebSocket и типы входящих сообщений определены в `app/ws/handler.py` и `app/ws/schemas.py`.

---

# 4. API сервиса

Основной API является WebSocket API.

HTTP API минимален и используется преимущественно для health check.

## HTTP endpoint

### `GET /healthz`

Проверяет доступность сервиса.

Пример:

```http
GET /healthz
```

Ожидаемый ответ:

```json
{
  "status": "ok"
}
```

---

# 5. Пользовательский WebSocket API

## `WS /ws/chat`

Основной endpoint для frontend.

Пример:

```text
/ws/chat?token=<access_token>
```

Через query parameter `token` JWT доходит до Nginx. Сам `chat-svc` не декодирует этот JWT; он использует `X-User-Id`, добавленный после проверки токена. В коде `token` явно игнорируется, а UUID пользователя извлекается из `settings.user_id_header_name`.

---

# 6. Установка WebSocket-соединения

После подключения `chat-svc`:

1. извлекает `user_id`;
2. проверяет его формат UUID;
3. ищет профиль пользователя в Redis;
4. при отсутствии профиля обращается к `auth-svc`;
5. сохраняет профиль в Redis;
6. получает или создаёт chat session;
7. регистрирует WebSocket connection;
8. возвращает клиенту `connected`.

Соединение получает уникальный `connection_id`, а сам пользователь связывается с конкретной `session_id`.

Пример ответа:

```json
{
  "type": "connected",
  "user_id": "6e9d...",
  "session_id": "a8f1..."
}
```

---

# 7. Формат клиентских сообщений

После установки WebSocket-соединения клиент отправляет JSON-сообщения.

Текущая реализация поддерживает четыре типа:

```text
ping
history_get
feedback_set
question
```

Они определены непосредственно в WebSocket schemas.

---

# 8. Ping

## Сообщение `ping`

Используется для проверки работоспособности соединения.

Request:

```json
{
  "type": "ping"
}
```

Response:

```json
{
  "type": "pong"
}
```

Обработка выполняется непосредственно в WebSocket handler.

---

# 9. Получение истории

## Сообщение `history_get`

Позволяет получить историю текущей chat session.

Request:

```json
{
  "type": "history_get",
  "limit": 20,
  "before_message_id": null
}
```

Поля:

| Поле                | Тип       | Описание                                     |
| ------------------- | --------- | -------------------------------------------- |
| `type`              | string    | `history_get`                                |
| `limit`             | integer   | Количество сообщений                         |
| `before_message_id` | UUID/null | Cursor для загрузки предыдущей части истории |

Schema по умолчанию устанавливает `limit = 20`.

После запроса сервис обращается к history layer, используя Redis и PostgreSQL.

Ответ:

```json
{
  "type": "history_page",
  "session_id": "a8f1...",
  "items": [],
  "has_more": true,
  "next_before_message_id": "uuid",
  "source": "redis"
}
```

Возможные значения `source`:

```text
redis
postgres
```

То есть сервис может обслужить первую часть истории из Redis, а при необходимости перейти к PostgreSQL.

---

# 10. Cursor-based pagination

История поддерживает пагинацию через:

```json
{
  "before_message_id": "uuid"
}
```

Таким образом, клиент может загружать историю частями:

```text
Запрос 1
before_message_id = null
        |
        v
messages 1..20
        |
        v
next_before_message_id
        |
        v
Запрос 2
before_message_id = previous cursor
        |
        v
messages 21..40
```

Это позволяет не загружать всю историю сессии одним запросом.

---

# 11. Отправка вопроса

## Сообщение `question`

Это главный пользовательский запрос к AI.

Пример:

```json
{
  "type": "question",
  "request_id": "b7d8...",
  "content": "Объясни второй закон Ньютона"
}
```

Schema:

```text
type
request_id
content
```

`request_id` является необязательным. Если клиент его не передал, `chat-svc` создаёт UUID самостоятельно. `content` содержит текст вопроса.

---

# 12. Обработка пользовательского вопроса

После получения `question` сервис выполняет следующий pipeline.

### Шаг 1. Получение профиля

Профиль берётся из Redis.

Если profile cache отсутствует, `chat-svc` вызывает `auth-svc` и затем кеширует результат.

---

### Шаг 2. Periodic history sync

Перед обработкой нового вопроса сервис проверяет, нужно ли выполнить periodic отправку истории сессии в RAG.

Если periodic gate разрешает выполнение, сервис:

* получает историю;
* получает соответствующие RAG runs;
* создаёт специальное assistant message;
* создаёт pending request;
* формирует periodic payload;
* отправляет его в RAG.

В payload используется маркер:

```text
[periodic-session-history-sync]
```

а также передаются сообщения, RAG runs, профиль пользователя и параметры интервала.

---

### Шаг 3. Создание user message

Пользовательский вопрос сохраняется в PostgreSQL как message с:

```text
role = user
```

---

### Шаг 4. Создание assistant message

Одновременно создаётся сообщение ассистента:

```text
role = assistant
content = ""
```

Оно связывается с сообщением пользователя через `reply_to_message_id`.

---

### Шаг 5. Уведомление frontend

Frontend получает:

```json
{
  "type": "message_started",
  "request_id": "b7d8...",
  "session_id": "a8f1...",
  "user_message_id": "uuid",
  "assistant_message_id": "uuid"
}
```

Это позволяет клиенту сразу создать в интерфейсе состояние сообщения ассистента, ещё до получения ответа LLM.

---

### Шаг 6. Создание pending request

Информация о текущей генерации сохраняется в Redis.

В неё входят:

```text
request_id
session_id
assistant_message_id
user_id
message_id
question
started_at
last_flush_at
last_flush_len
full_text
```

Это позволяет сохранить состояние незавершённого streaming request независимо от WebSocket-соединения.

---

### Шаг 7. Передача вопроса в RAG

После подготовки данных сервис создаёт RAG payload:

```json
{
  "request_id": "...",
  "message_id": "...",
  "assistant_message_id": "...",
  "session_id": "...",
  "user_id": "...",
  "question": "...",
  "profile": {}
}
```

и отправляет его в `rag-svc`.

Таким образом, `chat-svc` передаёт RAG не только текст вопроса, но и контекст пользователя и identifiers текущего chat request.

---

# 13. Подтверждение принятия вопроса

После ответа RAG frontend получает:

```json
{
  "type": "question_accepted",
  "request_id": "...",
  "assistant_message_id": "...",
  "status": "accepted"
}
```

Это означает, что вопрос принят pipeline и дальнейшая генерация будет поступать через streaming events.

---

# 14. Streaming от LLM

Текущая архитектура использует отдельный внутренний канал для LLM events.

`chat-svc` принимает WebSocket connection от LLM-сервиса на:

```text
/ws/internal/llm
```

LLM передаёт туда события генерации.

В текущем репозитории поддерживаются события:

```text
generation_started
stream_chunk
message_completed
message_error
```

`InternalLlmEventService` является обработчиком этих событий.

---

# 15. Internal LLM WebSocket

## `WS /ws/internal/llm`

Этот endpoint предназначен не для frontend, а для внутренних сервисных соединений.

При подключении должна пройти internal authentication.

Используются service headers:

```http
X-Service-Name: llm-svc
X-Service-Token: <service-token>
```

После успешной авторизации внутренний WebSocket может отправлять LLM events.

Таким образом:

```text
LLM service
     |
     | WebSocket
     v
chat-svc /ws/internal/llm
     |
     | processing
     v
frontend /ws/chat
```

---

# 16. `generation_started`

Событие сообщает о начале генерации.

Пример:

```json
{
  "type": "generation_started",
  "request_id": "...",
  "session_id": "...",
  "assistant_message_id": "..."
}
```

В текущем `InternalLlmEventService` специальная дополнительная обработка этого события отсутствует — оно принимается и завершается без дальнейших действий.

---

# 17. `stream_chunk`

Основное streaming-событие.

Пример:

```json
{
  "type": "stream_chunk",
  "request_id": "...",
  "session_id": "...",
  "assistant_message_id": "...",
  "delta": "Текст ответа..."
}
```

`delta` содержит очередной фрагмент ответа.

`chat-svc`:

1. находит pending request;
2. добавляет `delta` к накопленному тексту;
3. обновляет состояние в Redis;
4. сохраняет накопленный текст;
5. отправляет streaming event пользователю.

В `InternalLlmEventService` обработка `stream_chunk` начинается именно с поиска соответствующего pending request.

---

# 18. Streaming response для frontend

Frontend получает streaming fragments по своему `/ws/chat`.

Таким образом, полный ответ не ожидается целиком:

```text
LLM
 |
 | "Ньютон"
 v
chat-svc
 |
 v
Frontend

LLM
 |
 | " сформулировал..."
 v
chat-svc
 |
 v
Frontend

LLM
 |
 | " второй закон..."
 v
chat-svc
 |
 v
Frontend
```

Это позволяет отображать ответ пользователю постепенно.

---

# 19. `message_completed`

Когда LLM заканчивает генерацию, передаётся:

```json
{
  "type": "message_completed",
  "request_id": "...",
  "session_id": "...",
  "assistant_message_id": "..."
}
```

Событие означает, что streaming ответа завершён.

На этапе завершения сервис сохраняет финальное состояние ответа и завершает pending request.

---

# 20. `message_error`

Если LLM завершил генерацию с ошибкой, используется:

```json
{
  "type": "message_error",
  "request_id": "...",
  "session_id": "...",
  "assistant_message_id": "...",
  "message": "..."
}
```

Ошибка передаётся клиенту как WebSocket error event.

Это позволяет frontend отличать нормальное завершение генерации от ошибки pipeline.

---

# 21. Feedback API

## `feedback_set`

Пользователь может отправить оценку сообщения.

Request:

```json
{
  "type": "feedback_set",
  "message_id": "uuid",
  "helpful": 1
}
```

Schema содержит:

```text
type
message_id
helpful
```

`message_id` — идентификатор сообщения, которое оценивается. `helpful` содержит оценку.

После сохранения frontend получает:

```json
{
  "type": "feedback_saved",
  "message_id": "uuid",
  "helpful": 1
}
```

---

# 22. Chat session

`chat-svc` использует сущность `chat session`.

При установлении пользовательского WebSocket соединения выполняется:

```text
get_or_create_session_id()
```

То есть сервис автоматически получает существующую session или создаёт новую.

Вся история сообщений относится к этой session.

Текущая модель предполагает основной chat context пользователя, а не создание произвольного количества независимых чатов через отдельный endpoint.

---

# 23. Несколько соединений одной session

Один `session_id` может иметь несколько активных WebSocket connections.

Например:

```text
Browser tab 1
       |
       +------+
              |
Browser tab 2 +----> session_id
              |
       +------+
       |
Browser tab 3
```

`ConnectionManager` управляет самими WebSocket-соединениями, а Redis используется для регистрации connection state.

Это позволяет отправлять события всем активным соединениям данной session.

---

# 24. Redis

Redis в `chat-svc` используется не только как обычный cache.

Он хранит временные данные, необходимые для работы realtime-системы:

* user profile cache;
* session cache;
* connection state;
* recent messages;
* pending LLM requests;
* periodic RAG gate;
* состояние streaming.

Таким образом, Redis является частью runtime архитектуры сервиса, а не необязательной оптимизацией.

---

# 25. PostgreSQL

PostgreSQL используется для постоянного хранения chat data.

В базе данных сервис хранит:

### Chat sessions

Сведения о сессии пользователя.

### Messages

Пользовательские и assistant messages.

### Feedback

Оценки сообщений.

### RAG runs

Данные о выполнении retrieval.

Это позволяет использовать `chat-svc` не только как транспорт WebSocket, но и как долговременное хранилище истории AI-взаимодействия.

---

# 26. RAG integration

Основной вопрос передаётся в `rag-svc`.

Фактический вызов выполняется из `_handle_question()`:

```text
chat-svc
   |
   | POST RAG_URL
   v
rag-svc
```

Payload содержит:

```text
request_id
message_id
assistant_message_id
session_id
user_id
question
profile
```

Это означает, что RAG получает не только вопрос, но и необходимый контекст конкретной сессии и пользователя.

---

# 27. Periodic RAG history sync

Помимо обычного retrieval для вопроса, `chat-svc` поддерживает periodic synchronization истории.

Перед обработкой вопроса сервис проверяет Redis gate.

Если период истёк, он:

1. получает историю;
2. получает существующие RAG runs;
3. формирует periodic payload;
4. отправляет его в RAG;
5. создаёт соответствующий pending assistant message.

Для этого используется:

```text
RAG_PERIODIC_URL
RAG_PERIODIC_INTERVAL_DAYS
RAG_PERIODIC_MESSAGES_LIMIT
```

Это позволяет передавать RAG дополнительный контекст накопленной истории сессии.

---

# 28. Взаимодействие с auth-svc

Для получения профиля пользователя сервис использует:

```text
AUTH_PROFILE_URL
```

Алгоритм:

```text
X-User-Id
    |
    v
chat-svc
    |
    | Redis lookup
    |
    +---- profile found ---> use cached profile
    |
    +---- cache miss -----> auth-svc
                              |
                              v
                           profile
                              |
                              v
                            Redis
```

Профиль используется при формировании RAG payload и в дальнейшем AI pipeline.

---

# 29. Service-to-service authentication

Для внутренних соединений предусмотрена отдельная схема аутентификации.

Используются:

```text
X-Service-Name
X-Service-Token
```

и конфигурация доверенных сервисов.

Frontend не должен использовать этот механизм напрямую.

Он предназначен для:

* `chat-svc → auth-svc`;
* `chat-svc → rag-svc`;
* `llm-svc → chat-svc`.

---

# 30. Ошибки протокола

Если клиент отправляет неизвестный тип сообщения:

```json
{
  "type": "something_else"
}
```

сервис возвращает:

```json
{
  "type": "error",
  "message": "unsupported message type: something_else"
}
```

Это поведение непосредственно реализовано в WebSocket handler.

Некорректные данные для поддерживаемых типов проходят Pydantic validation и приводят к ошибке обработки соответствующей schema.

---

# 31. Жизненный цикл одного вопроса

Полный request flow выглядит следующим образом:

```text
1. Frontend
      |
      | question
      v
2. chat-svc
      |
      | save user message
      | create assistant message
      |
      | message_started
      v
3. chat-svc
      |
      | RAG request
      v
4. rag-svc
      |
      v
5. LLM
      |
      | generation_started
      | stream_chunk
      | stream_chunk
      | stream_chunk
      | message_completed
      v
6. chat-svc
      |
      | streaming events
      v
7. Frontend
```

Параллельно сервис сохраняет состояние в Redis и PostgreSQL.

---

# 32. Что сервис хранит

`chat-svc` отвечает за постоянные данные, связанные с процессом общения.

К ним относятся:

```text
session
message
assistant response
feedback
RAG run
retrieval metadata
token usage
latency
```

Таким образом, историю общения можно использовать не только для отображения пользователю, но и для анализа качества AI/RAG pipeline.

---

# 33. Что chat-svc НЕ делает

Для правильного понимания архитектуры важно определить границы ответственности.

`chat-svc` не является:

* сервисом регистрации пользователей;
* сервисом выдачи JWT;
* основной базой пользователей;
* vector database;
* embedding service;
* LLM;
* RAG engine.

Его задача — организовать chat workflow между этими компонентами.

Упрощённо:

```text
auth-svc      → Кто пользователь?
rag-svc       → Какой контекст найти?
llm-svc       → Как сгенерировать ответ?
chat-svc      → Как связать всё это в пользовательский чат?
```

Текущий README прямо описывает `chat-svc` как WebSocket gateway for chat.

---

# 34. Ограничения и особенности текущей реализации

### JWT проверяется не самим chat-svc

Без gateway, который устанавливает `X-User-Id`, WebSocket flow не сможет корректно определить пользователя. Код прямо требует forwarded user ID.

### Основной API — WebSocket

CRUD REST API для chat sessions или messages в текущем сервисе нет. Операции выполняются через WebSocket protocol.

### RAG и LLM разделены

`chat-svc` отправляет retrieval request в RAG, а streaming events получает от LLM через internal event channel.

### `generation_started` не содержит отдельной логики

Событие принимается, но текущий `InternalLlmEventService` сразу завершает его обработку.

### History API не является отдельным HTTP endpoint

История получается через WebSocket message `history_get`.

### Feedback также WebSocket-команда

Оценка ответа не является REST endpoint.

---

# 35. Итог

`chat-svc` — это центральный realtime-компонент AI-чата.

Его основная задача — превратить набор независимых микросервисов в единый пользовательский диалог.

Он:

```text
принимает пользователя
        ↓
получает его профиль
        ↓
находит/создаёт session
        ↓
принимает вопрос
        ↓
сохраняет сообщение
        ↓
отправляет вопрос в RAG
        ↓
получает события от LLM
        ↓
передаёт streaming frontend
        ↓
сохраняет полный ответ
        ↓
хранит историю и metadata
        ↓
принимает feedback
```

За счёт этого frontend взаимодействует прежде всего с одним chat endpoint:

```text
/ws/chat
```

а `chat-svc` скрывает внутреннюю сложность взаимодействия с `auth-svc`, `rag-svc`, `llm-svc`, Redis и PostgreSQL.

Таким образом, в общей архитектуре системы `chat-svc` является **оркестратором пользовательского AI-диалога и владельцем chat runtime/history**, тогда как специализированные сервисы отвечают за identity, retrieval и генерацию.
