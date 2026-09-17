# TBGG daily challenge results bot

Posts the TBGG GeoGuessr club daily-challenge **team score** to Discord once a day.

The team score is the sum over rounds of the single highest round score any club member got:
what the club would have scored if the best player on each round had played it alone.

```
🌍 TBGG Daily Challenge — 2026-09-16

24,995 / 25,000 · 99.98%
Best solo 24,920 (Charlie) · team gain +75

🇿🇦 🇹🇷 🇨🇴 🇮🇱 🇨🇾
R  Cty  Score   Distance   Time  Player
1  ZA   5,000        2 m   113s  Charlie
2  TR   5,000        4 m    34s  Charlie
                     4 m   154s  Yung Jefe
                     6 m   165s  alech
3  CO   5,000        8 m   155s  Charlie
                     4 m   174s  Yung Jefe
4  IL   4,995     1.6 km    21s  claravoyant
5  CY   5,000        7 m   139s  Yung Jefe
```

## How it runs

An EventBridge rule fires a Python 3.13 Lambda at **00:05 UTC** (`cron(5 0 * * ? *)`). The
GeoGuessr daily challenge closes at 23:59:59 UTC, so the run reports on the day that just
ended and nobody's late round is missed.

A second schedule runs at **20:00 Europe/Berlin** (DST-aware, via EventBridge Scheduler —
EventBridge *Rules* are UTC-only) and checks whether the GeoGuessr cookie still authenticates.
If it has expired you get a **DM** with refresh instructions, hours before the nightly post
would have failed.

### Authentication

The `_ncfa` session cookie is the only credential. GeoGuessr signs users in with emailed
one-time codes rather than passwords, so there is no credential pair a bot can post to obtain
a session — the cookie is copied from a browser and replaced by hand when it expires. That is
what the evening health check exists to warn about.

Consequently **nothing in this project writes to SSM**; the function's access is read-only.

| Parameter | Contents |
| --- | --- |
| `/tbgg-bot/geoguessr/ncfa` | the `_ncfa` cookie |
| `/tbgg-bot/discord/token` | the Discord bot token |

### Where messages go

| | Destination |
| --- | --- |
| The daily result | every channel in `discordChannelIds` |
| Failures and cookie expiry | a DM to `discordAlertUserId` |

The club never sees a stack trace, and a broken run reaches someone who can fix it.

`discordChannelIds` is a comma-separated list, and delivery is **per channel**: one channel
refusing the post — the bot not invited to that server yet, or missing **View Channel** /
**Send Messages** / **Embed Links** — never costs the others their message. A partial
delivery is reported by DM naming the channel and Discord's own reason; the run only fails
if *every* channel refuses.

### Refreshing the cookie

Open geoguessr.com signed in → DevTools → Application → Cookies → copy `_ncfa`, then:

```bash
aws ssm put-parameter --type SecureString \
  --name /tbgg-bot/geoguessr/ncfa --value '<cookie>' --overwrite
```

Check it worked with `uv run python -m tbgg_bot --check`.

Discord posting uses pycord's REST client (login, send, close) instead of opening a gateway
websocket: a Lambda invocation is too short-lived to justify the connect handshake.

## Layout

| Path | What it is |
| --- | --- |
| `src/tbgg_bot/geoguessr.py` | Sign-in and leaderboard fetch |
| `src/tbgg_bot/session.py` | Cookie-refresh-on-rejection logic |
| `src/tbgg_bot/scoring.py` | Best-of-rounds team score |
| `src/tbgg_bot/discord_post.py` | Embed rendering and pycord posting |
| `src/tbgg_bot/handler.py` | Lambda entry point |
| `infra/` | TypeScript CDK app |
| `requirements-lambda.txt` | Pinned runtime deps, exported from `uv.lock` |

## Local development

```bash
uv sync
uv run pytest
uv run ruff check . && uv run ruff format --check .
uv run mypy
```

Run it against real data without posting anything:

```bash
cp .env.example .env   # fill in GEOGUESSR_NCFA or email/password
set -a && source .env && set +a
uv run python -m tbgg_bot --dry-run            # yesterday
uv run python -m tbgg_bot 2026-09-16 --dry-run # a specific UTC day
```

Drop `--dry-run` to actually post (needs `DISCORD_TOKEN` and `DISCORD_CHANNEL_IDS`).

## Deploying

### 1. Create the Discord bot

1. <https://discord.com/developers/applications> → **New Application**
2. **Bot** → **Reset Token**, copy it
3. **OAuth2 → URL Generator**: scope `bot`, permissions **Send Messages** + **Embed Links**
4. Open the generated URL and invite the bot to your server
5. In Discord, enable Developer Mode, then right-click the target channel → **Copy Channel ID**

No privileged intents are needed — the bot never connects to the gateway.

### 2. Create the parameters

CloudFormation cannot create SecureString parameters, so these are written once with the CLI
and only referenced by the stack. Nothing secret ever enters the template, and no deploy can
overwrite them.

```bash
export AWS_PROFILE=AdministratorAccess-420151438082
aws sso login

aws ssm put-parameter --type SecureString --name /tbgg-bot/geoguessr/ncfa --value '<cookie>'
aws ssm put-parameter --type SecureString --name /tbgg-bot/discord/token --value '<bot token>'
```

Add `--overwrite` when changing a value later. These are Standard parameters encrypted with
the AWS-managed `aws/ssm` key, so they cost nothing.

### 3. Deploy the stack

```bash
cd infra
npm install
npx cdk bootstrap                                   # first time in this account/region only
npx cdk deploy -c discordChannelIds=<id>,<id> -c discordAlertUserId=<your user id>
```

Put both in the `context` block of `infra/cdk.json` to avoid passing them each time. Your own
user ID comes from right-clicking your name in Discord with Developer Mode on.

### 4. Check it works

```bash
aws lambda invoke --function-name <FunctionName from stack outputs> \
  --payload '{"date":"2026-09-16"}' --cli-binary-format raw-in-base64-out /dev/stdout
```

The optional `date` overrides the target day; omit the payload to use yesterday.

## Notes

- `requirements-lambda.txt` is generated — after changing dependencies run `npm run lockdeps`
  in `infra/` (or `uv export --frozen --no-dev --no-emit-project --format requirements-txt
  -o requirements-lambda.txt`).
- Bundling uses `uv --python-platform aarch64-manylinux2014` so the compiled wheels aiohttp
  needs are built for Lambda's Linux/arm64, not macOS. No container required. If uv is
  missing, CDK falls back to the Lambda build image — set `CDK_DOCKER=finch` to use Finch,
  and start the VM first with `finch vm start`.
- The GeoGuessr API is undocumented and unofficial; endpoints can change without notice.

## License

[CC0 1.0 Universal](LICENSE) — dedicated to the public domain. Do whatever you like with it,
no attribution required.
