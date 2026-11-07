---
title: API guide and error codes
category: technical
updated: 2026-10-01
---

# API guide and error codes

## Getting started

The AcmeCloud REST API lets you create projects and tasks, read reports and trigger automations from your own code.

- Base URL: https://api.acmecloud.example/v1
- Format: JSON requests and responses.
- Authentication: send your API key in the header `Authorization: Bearer <your-api-key>`.

## API keys

Create keys in Settings > Developer > API keys. A key is shown only once when it is created, so copy it straight away. If you lose it, create a new key and delete the old one.

Each key can have scopes: read, write and admin. A key without the right scope gets a 403 error.

Keep keys secret. Never put them in front-end code or public repositories. If a key leaks, delete it immediately; deleted keys stop working within 1 minute.

## Rate limits

| Plan | Requests per day | Burst limit |
|---|---|---|
| Free | 1,000 | 2 requests per second |
| Pro | 50,000 | 10 requests per second |
| Enterprise | 500,000 | 50 requests per second |

Daily limits reset at 00:00 UTC. Every response includes the headers `X-RateLimit-Remaining` and `X-RateLimit-Reset`.

## Error codes

### 400 Bad Request

The request is malformed, for example invalid JSON or a missing required field. The response body explains which field is wrong. Fix the request and send it again.

### 401 Unauthorized

The API key is missing, mistyped or deleted. Check that:

1. The header is exactly `Authorization: Bearer <key>`, with a space after "Bearer".
2. The key has no extra spaces or line breaks.
3. The key still exists in Settings > Developer > API keys.

If it still fails, create a new key and use that.

### 403 Forbidden

The key is valid but not allowed to do this. Either the key lacks the needed scope (for example a read-only key trying to create a task), or the feature is not included in your plan (for example audit log endpoints on Pro). Create a key with the right scope or upgrade the plan.

### 404 Not Found

The resource does not exist or your key cannot see it. Check the ID in the URL and that the project belongs to the workspace the key was created in.

### 409 Conflict

The request clashes with the current state, for example creating a project with a name that already exists. Change the request and retry.

### 422 Unprocessable Entity

The JSON is valid but a value is not allowed, for example a due date in the wrong format. Dates must use ISO 8601, for example 2026-10-01.

### 429 Too Many Requests

You hit the burst limit or the daily limit. Wait for the number of seconds in the `Retry-After` header before retrying. Use exponential backoff and spread requests out. If you regularly hit the daily limit, consider upgrading your plan.

### 500 Internal Server Error

Something failed on AcmeCloud's side. Retry with exponential backoff (for example after 1, 2, then 4 seconds). If it keeps happening, open a Technical ticket and include the `X-Request-ID` header value from the response.

### 503 Service Unavailable

The API is temporarily unavailable, usually during maintenance. Check status.acmecloud.example and retry later.

## Webhooks

Webhooks send an HTTP POST to your URL when events happen, for example task.created or automation.failed. Set them up in Settings > Developer > Webhooks. Your endpoint must reply with a 2xx status within 10 seconds; failed deliveries are retried 5 times over 1 hour. Every webhook is signed with the header `X-Acme-Signature` so you can verify it came from AcmeCloud.
