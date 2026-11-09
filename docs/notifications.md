# Notifications: in-app, push, email and the installable app

When something needs someone's attention, such as results waiting for approval, results returned
for correction, or a registration to approve, the system tells them in three ways:

| Channel | Who | Cost |
|---|---|---|
| **In-app** – bell in the top bar, *Notifications* page | Everyone | Free, unlimited |
| **Push** – appears on the phone or computer, with an app-icon badge | Everyone who turns it on (per device) | Free, unlimited |
| **Email** | Staff only (alerts); anyone (password reset, account set-up) | Counts against the daily email allowance |

Students never get alert emails. A single results publication can reach hundreds of students, which
would use up a free email plan in one action. They get in-app and push notifications instead. For the
same reason, staff get no email for registrations waiting for approval (one per student during
registration week) or for blocked sign-offs (shown on the administrator's dashboard).

## Events

| Event | Who is told |
|---|---|
| Result sheet submitted | Staff who can approve results |
| Result sheet returned | The course's lecturers and the submitter |
| Result sheet approved | Staff who can publish results, plus the lecturers |
| Result sheet published | The lecturers; registered students (in-app and push) |
| Amendment requested | Staff who can approve amendments (not the requester) |
| Amendment approved or rejected | The requester |
| Resit opened | The lecturers; eligible students (in-app and push) |
| Resit submitted, returned, approved or published | As for result sheets; students when their resit result is published |
| Registration needs approval | Registrars (administrators if there is no registrar), in-app and push only |
| Registration approved or rejected | The student |
| Lecturer assigned to a course | That lecturer |
| Progression review applied, or student status changed | The student (in-app and push) |
| Sign-off blocked: only the submitter could approve or publish | Administrators, in-app and push only (also shown on the dashboard) |

The person who performed the action is never notified about it. Messages contain course codes and
what happened, **never marks, grades or personal details**, because notifications appear on lock
screens and in mailboxes. The link opens the signed-in page.

## Email: Brevo and the daily allowance

Production sends email through Brevo's HTTP API (`api.brevo.com` is on PythonAnywhere's allowlist for
free accounts; SMTP is not). The free plan allows **300 emails a day, shared with campaigns**.

* `EMAIL_DAILY_LIMIT` (default 300). Lower it if the same Brevo account also sends campaigns.
* `EMAIL_SECURITY_RESERVE` (default 60) is kept for password resets and account set-up links. Alerts
  may use only `limit − reserve` (240 by default).
* The day runs from midnight to midnight UTC, which is also Ghana time.
* Mail that does not fit today waits in the queue. The daily task merges each person's waiting alerts
  into **one digest email**.
* Alerts still waiting after 7 days are dropped. The in-app notification remains.
* Password-reset and set-up links are generated when the email is actually sent, so a queued link is
  still valid. No link or token is ever stored.
* Each person can switch alert emails off under *Profile → Notifications*. Security emails are
  always sent.
* Importing many staff at once queues their set-up emails. Any that exceed today's allowance go out on
  the following days, and the import screen says how many are waiting.

Administrators see emails sent today, emails waiting and failures on the dashboard (*Notification delivery*).

## Delivery without background workers

The target host has no background workers and allows one scheduled task a day, so the outbox is sent:

1. **Straight away**: up to 15 messages, after the action that created them has been saved. Staff are
   sent first.
2. **While people use the app**: every open app checks its unread count every minute. Each check also
   sends up to 10 waiting messages, at most once every 10 seconds per server process. A publication to
   150 students therefore reaches their devices within a minute or two during working hours.
3. **Daily**: `python manage.py send_pending_messages` sends everything left. It also builds the email
   digests, retries failures (up to 5 attempts), and prunes old data: read notifications after 180 days,
   outbox rows after 90 days, and devices that keep failing.

A failed email or push never breaks the action that caused it. The failure is logged and retried.

## Push notifications

* Generate keys once with `python manage.py generate_vapid_keys` and put them in `.env`
  (`VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY`, `VAPID_SUBJECT=mailto:...`). Without keys, push is off and
  everything else still works.
  Changing the keys disconnects every device, and people then have to turn push on again.
* **Push is on by default.** Browsers only allow notifications after the person agrees once on each
  device, so:
  * if notifications are already allowed on the device, it is signed up for the account automatically
    at every sign-in, with no question;
  * otherwise a small *Turn on notifications* prompt appears after sign-in; one tap shows the browser's
    *Allow* question. *Not now* hides it for 3 days;
  * on an iPhone or iPad in Safari, the prompt explains that the app must be added to the Home Screen
    first (Apple only allows push for installed apps);
  * if the person turns push off under *Profile → Notifications*, that is remembered on the device and
    it is never switched back on by itself. If they blocked notifications in the browser, the app cannot
    ask again: they have to allow them in the browser's site settings.
* **What a notification looks like.** Like a message in a chat app: the institution's logo as the
  picture, the title in bold and a one-line preview of the message (every notification has one), with
  the app's name and the time. On Android and desktop Chrome/Edge there are *Open* and *Mark as read*
  buttons; *Mark as read* works without opening the app where the browser allows it, and otherwise
  opens the item. Android's small status-bar icon is the institution's initials, because Android shows
  only the shape of that icon. Previews never contain marks, grades or other personal details.
* Signing out removes the device's subscription, so a shared computer does not receive the previous
  person's notifications.
* Push and the service worker need HTTPS. Vercel and PythonAnywhere provide it, and `localhost` works
  for development.

### What works where

| | Push | App-icon badge (count) | In-app sound | Install |
|---|---|---|---|---|
| Chrome / Edge on Windows, macOS, ChromeOS | Yes | Yes (installed app) | Yes | *Install app* in the account menu |
| Chrome on Android | Yes | Dot only (launcher decides) | Yes | *Install app* |
| Safari on iPhone / iPad (iOS 16.4+) | Only after *Add to Home Screen* | Yes (installed app) | Yes | Share → *Add to Home Screen* |
| Safari on macOS | Yes | Yes (Dock, installed app) | Yes | File → *Add to Dock* |
| Firefox | Yes | No | Yes | Not supported on desktop |

**Sound.** When the portal is open, a new notification plays a short chime (people can switch it off).
When the portal is closed, the device's own notification sound is used. No browser lets a website set
a custom push sound.

The chime is original. It was synthesised by the frontend's `scripts/make-notification-sound.py` and is
stored as `public/sounds/notification.wav`. To use another sound, replace that file with a short
WAV/MP3 that your institution has the rights to use. Sounds from other apps, for example Snapchat's,
are copyrighted.

## The installable app (PWA)

* The app is branded automatically: its name, theme colour and icons come from *Institution
  settings*. Icons are drawn from the logo, or from the initials when there is no logo, at
  `/api/v1/institution/app-icon/`. The logo's empty margins are trimmed and it is scaled to fill the
  icon, so a small or padded upload still looks right. Use a square logo of at least 512 × 512 pixels
  for the sharpest result.
* The browser-tab icon (favicon) is the logo too, and so is the opening screen: Android builds it from
  the app icon and name, and on iPhone and iPad the opening screen shows the logo and the institution's
  name (`/api/v1/institution/app-splash/`).
* After changing the logo, phones that already installed the app update its icon on their own, usually
  within a day; removing and reinstalling the app shows it at once.
* Opening the app starts on the dashboard. Shortcuts (long-press or right-click the icon) open
  Notifications, My results, and Results to review.
* **Offline**: the app shell loads without a connection and shows a clear offline page.
  **Records, results and uploads are never stored on the device** (`/api/*` and `/media/*` are not
  cached), to protect personal data on shared or lost devices. Signing out also clears cached files
  and the badge.
* Updates: installed apps check for a new version every hour and offer *Reload* when one is ready.
