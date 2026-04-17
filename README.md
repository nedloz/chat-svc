# chat-svc

WebSocket gateway for chat. Nginx validates the token before the request reaches this service and forwards the resolved user id in a header.

## Current flow
- Nginx validates JWT and injects `X-User-Id`
- chat-svc reads `user_id` from the forwarded header
- chat-svc fetches the user profile from `auth-svc` using `AUTH_PROFILE_URL`
- chat-svc caches the profile in Redis
- chat-svc calls `rag-svc` for retrieval only
- chat-svc calls `llm-svc` for streaming only
- chat-svc stores sessions, messages, feedback, and rag runs in Postgres

Frontend websocket connection should pass access token in URL query (for nginx validation), for example:

`/ws/chat?token=<access_token>`

`AUTH_PROFILE_URL` must contain `{user_id}` placeholder, for example:

```
AUTH_PROFILE_URL=http://auth-svc:8000/api/auth/users/{user_id}/profile
```
