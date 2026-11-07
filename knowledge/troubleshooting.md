---
title: Troubleshooting common problems
category: technical
updated: 2026-10-01
---

# Troubleshooting common problems

Before anything else, check status.acmecloud.example. If there is an ongoing incident, the team is already working on it and the status page shows updates.

## Supported browsers and apps

AcmeCloud supports the latest two versions of Chrome, Firefox, Edge and Safari. Internet Explorer is not supported. The mobile apps need iOS 16 or later, or Android 10 or later.

## Cannot log in

1. Make sure you are using the right email address. If your company uses SSO, use the "Log in with SSO" button instead of a password.
2. Reset your password with "Forgot password".
3. If you see "Account locked", wait 15 minutes after 5 failed attempts, then try again.
4. If 2FA codes are rejected, check that your phone's time is set automatically. Authenticator codes fail when the clock is off by more than a minute.
5. Try a private browser window to rule out an old session or a browser extension.

## App is slow or pages do not load

1. Refresh the page and clear the browser cache (Ctrl+Shift+R on Windows, Cmd+Shift+R on Mac).
2. Disable browser extensions such as ad blockers for app.acmecloud.example.
3. Try another network. Some office firewalls block the WebSocket connection the app uses for live updates.
4. Very large boards (more than 5,000 tasks) load slower. Archive finished tasks or split the project.

If the problem continues, open a Technical ticket with your browser name and version, the page URL and a screenshot.

## Changes are not syncing

Live updates need a WebSocket connection. If changes from teammates only appear after a refresh, your network may be blocking WebSockets. Ask your IT team to allow wss://app.acmecloud.example. On mobile, check that background data is allowed for the AcmeCloud app.

## Automations are not running

1. Check the automation is switched on and its trigger matches what happened.
2. Check your monthly automation run limit in Settings > Usage: Free has 100 runs, Pro has 5,000. When the limit is reached, automations pause until the next month or until you buy extra runs.
3. Open the automation's run history to see the error for failed runs.

## File uploads fail

The maximum size for one file is 250 MB on Free and Pro, and 2 GB on Enterprise. Uploads also fail when the workspace storage is full; check Settings > Usage.

## Email notifications are missing

Check Settings > Notifications, then your spam folder. Add notifications@acmecloud.example to your contacts.

## Scheduled maintenance

Planned maintenance happens on Sundays between 02:00 and 04:00 Pakistan Standard Time (UTC+5). It is announced on the status page at least 3 days ahead. The app may be read-only or briefly unavailable during this window.

## When to open a Technical ticket

Open a ticket if the steps above did not help, if you see data missing, or if an error repeats. Include what you did, what you expected, what happened, and any error message or request ID.
