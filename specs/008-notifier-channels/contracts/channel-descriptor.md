# Contract: ChannelDescriptor

The catalog entry every channel provides. Lives in `channels/<id>/descriptor.py`, registered in `channels/catalog.py`. Import-light: declaring a descriptor MUST NOT import FastAPI, uvicorn, or the channel's delivery SDK (those load lazily, in-container, via `transport_factory`).

## Required attributes

```text
ChannelDescriptor(
    id: str,                    # stable, lowercase, unique; == [transport].type == image suffix
    label: str,                 # human-facing, shown in picker / `remo notifier channels`
    image_name: str,            # e.g. "remo-notifier-telegram"; tagged :<version> at deploy
    required_env: list[RequiredEnv],   # checked by deploy preflight (FR-012)
    transport_factory: str,     # "pkg.module:callable"; lazy-imported only in the container
    render_transport_toml: Callable[[dict[str, str]], str],  # owns the [transport.<id>] TOML
)

RequiredEnv(
    name: str,        # REMO_NOTIFIER_<CHANNEL>_<NAME>  (FR-012a)
    secret: bool,     # True -> on-host secret file (0400), never in TOML/logs
    purpose: str,     # shown in listings and preflight errors
)
```

## Behavioral contract

- **Uniqueness**: `id` is unique across the catalog; collisions are a build-time error.
- **Env convention**: every `required_env.name` MUST start with `REMO_NOTIFIER_<CHANNEL>_` where `<CHANNEL>` is the uppercased `id`.
- **Secret handling**: at most one `required_env` is `secret=True`; the deploy writes it to the secret file the transport reads at startup. Non-secret vars render into the TOML fragment.
- **TOML ownership**: `render_transport_toml(values)` returns a fragment beginning with `[transport]\ntype = "<id>"` followed by `[transport.<id>]` keys built from the non-secret `values`. The Ansible role inserts this fragment verbatim; the role contains no channel-specific TOML.
- **Lazy factory**: `transport_factory` resolves to `build(config) -> NotificationTransport` and is imported only inside the service container. The laptop CLI never imports it.

## Telegram reference instance

```text
ChannelDescriptor(
  id="telegram",
  label="Telegram",
  image_name="remo-notifier-telegram",
  required_env=[
    RequiredEnv("REMO_NOTIFIER_TELEGRAM_BOT_TOKEN", secret=True,  purpose="Bot API token from @BotFather"),
    RequiredEnv("REMO_NOTIFIER_TELEGRAM_CHAT_ID",   secret=False, purpose="Authorized chat id that may approve"),
  ],
  transport_factory="remo_cli.notifier.channels.telegram.transport:build",
  render_transport_toml=<renders [transport] type="telegram" + [transport.telegram] bot_token_file/authorized_chat_id/message_parse_mode>,
)
```

The rendered Telegram TOML is byte-identical to spec 007's `notifier.toml`.
