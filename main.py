import os
import sys
import warnings
import asyncio
from discord.ext import tasks, commands
import discord
import requests

warnings.filterwarnings("ignore", category=UserWarning, module="discord")

# -------------------------------
# Load environment variables
# -------------------------------
def load_local_env(path: str = ".env") -> None:
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as env_file:
        for line in env_file:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())

load_local_env()

def normalize_secret(value: str | None) -> str:
    if value is None:
        return ""
    cleaned = value.strip()
    if len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in ("'", '"'):
        cleaned = cleaned[1:-1].strip()
    return cleaned

def get_token_candidates() -> list[tuple[str, str]]:
    candidates: list[tuple[str, str]] = []

    primary = normalize_secret(os.getenv("DISCORD_USER_TOKEN"))
    if primary:
        candidates.append(("DISCORD_USER_TOKEN", primary))

    for key, raw_value in os.environ.items():
        if key == "DISCORD_USER_TOKEN" or not key.endswith("_DISCORD_USER_TOKEN"):
            continue
        candidate = normalize_secret(raw_value)
        if candidate and all(candidate != existing for _, existing in candidates):
            candidates.append((key, candidate))

    return candidates

# -------------------------------
# Configuration
# -------------------------------
token_candidates = get_token_candidates()
if not token_candidates:
    raise RuntimeError("Missing DISCORD_USER_TOKEN (or *_DISCORD_USER_TOKEN) in environment or .env")

TOKEN = ""

channel_id_raw = os.getenv("TARGET_CHANNEL_ID")
if not channel_id_raw:
    raise RuntimeError("Missing TARGET_CHANNEL_ID in environment or .env")
try:
    CHANNEL_ID = int(channel_id_raw.strip())
except ValueError:
    raise RuntimeError("TARGET_CHANNEL_ID must be an integer")

temp_voice_creator_raw = os.getenv("TEMP_VOICE_CREATOR_CHANNEL_ID", "1479600922727547043")
try:
    TEMP_VOICE_CREATOR_CHANNEL_ID = int(temp_voice_creator_raw.strip())
except ValueError:
    raise RuntimeError("TEMP_VOICE_CREATOR_CHANNEL_ID must be an integer")

AUTO_MESSAGE = os.getenv("AUTO_MESSAGE", "Automated test message every 3 hours!")
TARGET_BOT_ID = os.getenv("TARGET_BOT_ID")
AUTO_MESSAGE_DELAY_SECONDS = float(os.getenv("AUTO_MESSAGE_DELAY_SECONDS", "2.0"))
BUY_MONITOR_SECONDS = float(os.getenv("BUY_MONITOR_SECONDS", "20"))
VOICE_MOVE_WAIT_SECONDS = float(os.getenv("VOICE_MOVE_WAIT_SECONDS", "5"))
VOICE_MOVE_TIMEOUT_SECONDS = float(os.getenv("VOICE_MOVE_TIMEOUT_SECONDS", "45"))
print(TARGET_BOT_ID)
# -------------------------------
# Optional token validation
# -------------------------------
print("Validating token...")

selected_source = None
for source_name, candidate in token_candidates:
    try:
        r = requests.get(
            "https://discord.com/api/v9/users/@me",
            headers={"Authorization": candidate},
            timeout=10,
        )
    except Exception as e:
        print(f"Could not validate token from {source_name}: {e}")
        continue

    if r.status_code == 200:
        user_data = r.json()
        TOKEN = candidate
        selected_source = source_name
        print(
            f"Token valid from {source_name}! "
            f"Logged in as {user_data['username']}#{user_data['discriminator']}"
        )
        break

    print(f"Token in {source_name} failed (status {r.status_code}). Response: {r.text[:200]}")

if not TOKEN:
    print("No valid token found in configured token environment variables.")
    sys.exit(1)

# -------------------------------
# Bot setup
# -------------------------------
intents = None
if hasattr(discord, "Intents"):
    intents = discord.Intents.default()
    if hasattr(intents, "message_content"):
        intents.message_content = True
else:
    print("[startup] discord.Intents is unavailable in this discord package; continuing without intents.")

bot_kwargs = {
    "command_prefix": "!",
    "self_bot": True,
    "help_command": None,
}
if intents is not None:
    bot_kwargs["intents"] = intents

bot = commands.Bot(**bot_kwargs)
console_task: asyncio.Task | None = None

def summarize_message_for_log(message: discord.Message, max_len: int = 280) -> str:
    parts: list[str] = []

    if message.content and message.content.strip():
        parts.append(message.content.strip())

    for embed in message.embeds:
        if embed.title:
            parts.append(f"embed-title: {embed.title}")
        if embed.description:
            parts.append(f"embed-desc: {embed.description}")
        for field in embed.fields[:6]:
            field_name = (field.name or "").strip()
            field_value = (field.value or "").strip()
            if field_name or field_value:
                parts.append(f"{field_name}: {field_value}".strip(": "))

    if message.components:
        component_labels: list[str] = []
        for row in message.components:
            for child in row.children:
                label = getattr(child, "label", None)
                placeholder = getattr(child, "placeholder", None)
                custom_id = getattr(child, "custom_id", None)
                if label:
                    component_labels.append(str(label))
                elif placeholder:
                    component_labels.append(f"select:{placeholder}")
                elif custom_id:
                    component_labels.append(f"id:{custom_id}")
                else:
                    component_labels.append(type(child).__name__)
        if component_labels:
            parts.append("components: " + ", ".join(component_labels[:8]))

    if message.attachments:
        filenames = [attachment.filename for attachment in message.attachments[:3]]
        parts.append("attachments: " + ", ".join(filenames))

    text = " | ".join(part.replace("\n", " ").strip() for part in parts if part.strip())
    if not text:
        return "<no text/embed/component content>"
    if len(text) > max_len:
        return text[:max_len] + "..."
    return text

def extract_invite_code(invite: str) -> str:
    if "discord.gg/" in invite or "discord.com/invite/" in invite:
        return invite.split("/")[-1].split("?")[0].strip()
    return invite.strip()

async def do_join(invite: str, source: discord.Message | None):
    """Join a server and send result to the source channel."""
    source_channel = source.channel.id if source else "console"
    print(f"[do_join] called with invite='{invite}', source channel={source_channel}")
    code = extract_invite_code(invite)
    url = f"https://discord.com/api/v9/invites/{code}"
    async def send_feedback(text: str):
        if source:
            await source.channel.send(text)
        else:
            print(f"[console] {text}")
    try:
        async with bot.http._session.post(url, headers={"Authorization": TOKEN}, json={}) as resp:
            if resp.status in (200, 204):
                await send_feedback(f"Joined via invite `{code}`.")
            elif resp.status == 429:
                await send_feedback("Rate limited. Wait and try again.")
            else:
                text = await resp.text()
                await send_feedback(f"Failed ({resp.status}): {text[:200]}")
                print(text)
    except Exception as exc:
        await send_feedback(f"Error: {exc}")

async def do_msg(content: str, source: discord.Message | None):
    """Send a message to the fixed target channel and report back to source."""
    source_channel = source.channel.id if source else "console"
    print(f"[do_msg] called with content='{content}', source channel={source_channel}")
    async def send_feedback(text: str):
        if source:
            await source.channel.send(text)
        else:
            print(f"[console] {text}")
    if not content.strip():
        await send_feedback("Usage: !msg your message here")
        return

    # Try to get the target channel (cached or fetch)
    target_channel = bot.get_channel(CHANNEL_ID)
    print(f"[do_msg] bot.get_channel({CHANNEL_ID}) returned: {target_channel}")
    if target_channel is None:
        try:
            target_channel = await bot.fetch_channel(CHANNEL_ID)
            print(f"[do_msg] fetch_channel succeeded: {target_channel}")
        except Exception as e:
            print(f"[do_msg] fetch_channel failed: {e}")
            await send_feedback(
                f"Channel {CHANNEL_ID} not found. Confirm the account has access. Error: {e}"
            )
            return

    try:
        print(f"[do_msg] Attempting to send '{content}' to {target_channel.id}")
        await target_channel.send(content)
        await send_feedback(f"Sent to <#{CHANNEL_ID}>: `{content}`")
        print("[do_msg] Message sent successfully")
    except discord.Forbidden:
        print("[do_msg] Forbidden error")
        await send_feedback("No permission to send in that channel.")
    except Exception as exc:
        print(f"[do_msg] Exception: {exc}")
        await send_feedback(f"Error sending: {exc}")

async def do_switch(raw_target_id: str, source: discord.Message | None):
    """Update AUTO_MESSAGE to target a new braquage ID."""
    async def send_feedback(text: str):
        if source:
            await source.channel.send(text)
        else:
            print(f"[console] {text}")

    target_id = raw_target_id.strip()
    if not target_id:
        await send_feedback("Usage: switch target_user_id")
        return

    try:
        int(target_id)
    except ValueError:
        await send_feedback(f"Invalid ID '{target_id}'. Expected a numeric Discord ID.")
        return

    global AUTO_MESSAGE
    AUTO_MESSAGE = f"&braquage {target_id}"
    await send_feedback(f"AUTO_MESSAGE updated to: {AUTO_MESSAGE}")

async def do_msgid(raw_channel_id: str, content: str, source: discord.Message | None):
    """Send a message to a specific channel ID."""
    async def send_feedback(text: str):
        if source:
            await source.channel.send(text)
        else:
            print(f"[console] {text}")

    channel_id_text = raw_channel_id.strip()
    if not channel_id_text or not content.strip():
        await send_feedback("Usage: msgid channel_id your message here")
        return

    try:
        target_channel_id = int(channel_id_text)
    except ValueError:
        await send_feedback(f"Invalid channel ID '{channel_id_text}'. Expected a numeric Discord ID.")
        return

    target_channel = bot.get_channel(target_channel_id)
    if target_channel is None:
        try:
            target_channel = await bot.fetch_channel(target_channel_id)
        except Exception as e:
            await send_feedback(f"Channel {target_channel_id} not found or inaccessible. Error: {e}")
            return

    if not hasattr(target_channel, "send"):
        await send_feedback(
            f"Channel {target_channel_id} is not messageable (type={type(target_channel).__name__})."
        )
        return

    try:
        await target_channel.send(content)
        await send_feedback(f"Sent to <#{target_channel_id}>: `{content}`")
    except discord.Forbidden:
        await send_feedback(f"No permission to send in <#{target_channel_id}>.")
    except Exception as exc:
        await send_feedback(f"Error sending to {target_channel_id}: {exc}")

async def do_room(raw_room_id: str, source: discord.Message | None):
    """Join a voice room by channel ID and make it the temp voice creator target."""
    async def send_feedback(text: str):
        if source:
            await source.channel.send(text)
        else:
            print(f"[console] {text}")

    room_id_text = raw_room_id.strip()
    if not room_id_text:
        await send_feedback("Usage: room voice_channel_id")
        return

    try:
        room_id = int(room_id_text)
    except ValueError:
        await send_feedback(f"Invalid room ID '{room_id_text}'. Expected a numeric Discord ID.")
        return

    room_channel = bot.get_channel(room_id)
    if room_channel is None:
        try:
            room_channel = await bot.fetch_channel(room_id)
        except Exception as e:
            await send_feedback(f"Room {room_id} not found or inaccessible. Error: {e}")
            return

    guild = getattr(room_channel, "guild", None)
    if guild is None:
        await send_feedback(f"Room {room_id} is not a guild voice channel.")
        return

    if bot.voice_clients:
        for existing_vc in list(bot.voice_clients):
            try:
                await existing_vc.disconnect(force=True)
            except Exception as disconnect_error:
                print(f"[room] Failed disconnecting existing voice client: {disconnect_error}")

    try:
        await guild.change_voice_state(channel=room_channel, self_deaf=True, self_mute=False)
    except Exception as connect_error:
        await send_feedback(f"Failed to join room {room_id}: {connect_error}")
        return

    global TEMP_VOICE_CREATOR_CHANNEL_ID
    TEMP_VOICE_CREATOR_CHANNEL_ID = room_id
    await send_feedback(
        f"Joined room {room_id} and set TEMP_VOICE_CREATOR_CHANNEL_ID={TEMP_VOICE_CREATOR_CHANNEL_ID}"
    )

async def do_msg_create_room(content: str, source: discord.Message | None):
    """Create/join moved room flow, then send a one-off message there."""
    async def send_feedback(text: str):
        if source:
            await source.channel.send(text)
        else:
            print(f"[console] {text}")

    if not content.strip():
        await send_feedback("Usage: msg your message here")
        return

    channel: discord.abc.Messageable | None = None
    voice_client: discord.VoiceClient | None = None
    voice_guild: discord.Guild | None = None

    try:
        channel, voice_client, voice_guild = await connect_and_get_moved_voice_channel()
        if channel is None:
            await send_feedback("Could not resolve moved private voice target channel.")
            return

        await channel.send(content)
        await send_feedback(f"Created room flow and sent to <#{channel.id}>: `{content}`")
    except Exception as exc:
        await send_feedback(f"Error during room-create message flow: {exc}")
    finally:
        if voice_client and voice_client.is_connected():
            try:
                await voice_client.disconnect(force=True)
            except Exception as disconnect_error:
                print(f"[msg-room] Error while disconnecting voice client: {disconnect_error}")
        elif voice_guild is not None:
            try:
                await voice_guild.change_voice_state(channel=None, self_deaf=False, self_mute=False)
            except Exception as voice_state_clear_error:
                print(f"[msg-room] Error while clearing voice state: {voice_state_clear_error}")

async def handle_runtime_command(line: str, source: discord.Message | None, *, dm_mode: bool) -> bool:
    lower = line.lower()

    if lower.startswith("msgid "):
        parts = line.split(maxsplit=2)
        if len(parts) < 3:
            if source:
                await source.channel.send("Usage: msgid channel_id your message here")
            else:
                print("[console] Usage: msgid channel_id your message here")
            return True
        await do_msgid(parts[1], parts[2], source)
        return True

    if lower.startswith("room "):
        room_id = line[5:].strip()
        await do_room(room_id, source)
        return True

    if lower.startswith("switch "):
        raw_target_id = line[7:].strip()
        await do_switch(raw_target_id, source)
        return True

    if lower.startswith("msg "):
        body = line[4:].strip()
        if dm_mode:
            await do_msg_create_room(body, source)
        else:
            await do_msg(body, source)
        return True

    if lower.startswith("join "):
        invite = line[5:].strip()
        if not invite:
            if source:
                await source.channel.send("Usage: join invite_link_or_code")
            else:
                print("[console] Usage: join invite_link_or_code")
            return True
        await do_join(invite, source)
        return True

    return False

async def console_command_loop():
    await bot.wait_until_ready()
    print("Console commands ready: msg <message> | msgid <id> <message> | room <id> | join <invite> | switch <id>")
    while not bot.is_closed():
        line = await asyncio.to_thread(sys.stdin.readline)
        if not line:
            await asyncio.sleep(0.1)
            continue
        line = line.strip()
        if not line:
            continue

        handled = await handle_runtime_command(line, None, dm_mode=False)
        if handled:
            continue

        print("[console] Unknown command. Use: msg <message> | msgid <id> <message> | room <id> | join <invite> | switch <id>")

async def connect_and_get_moved_voice_channel() -> tuple[discord.abc.Messageable | None, discord.VoiceClient | None, discord.Guild | None]:
    temp_channel = bot.get_channel(TEMP_VOICE_CREATOR_CHANNEL_ID)
    if temp_channel is None:
        try:
            temp_channel = await bot.fetch_channel(TEMP_VOICE_CREATOR_CHANNEL_ID)
        except Exception as e:
            print(f"[loop] Could not fetch temp voice creator channel {TEMP_VOICE_CREATOR_CHANNEL_ID}: {e}")
            return None, None, None

    if not hasattr(temp_channel, "guild") or not hasattr(temp_channel, "id"):
        print(
            f"[loop] Channel {TEMP_VOICE_CREATOR_CHANNEL_ID} is not a guild voice-like channel "
            f"(type={type(temp_channel).__name__})"
        )
        return None, None, None

    guild = temp_channel.guild
    if guild is None or bot.user is None:
        print("[loop] Missing guild/user context for voice move flow")
        return None, None, None

    if bot.voice_clients:
        for existing_vc in list(bot.voice_clients):
            try:
                await existing_vc.disconnect(force=True)
            except Exception as disconnect_error:
                print(f"[loop] Failed disconnecting existing voice client: {disconnect_error}")

    try:
        # Use voice state updates instead of opening a voice websocket handshake (DAVE/E2EE can break connect()).
        await guild.change_voice_state(channel=temp_channel, self_deaf=True, self_mute=False)
    except Exception as connect_error:
        print(f"[loop] Failed setting voice state to temp voice creator channel: {connect_error}")
        return None, None, guild

    print(f"[loop] Requested voice join to temp voice creator channel {temp_channel.id}")

    moved_channel = None

    def moved_check(member: discord.Member, before: discord.VoiceState, after: discord.VoiceState) -> bool:
        if bot.user is None or member.id != bot.user.id:
            return False
        if after.channel is None:
            return False
        return after.channel.id != temp_channel.id

    try:
        _, _, after_state = await bot.wait_for(
            "voice_state_update",
            timeout=VOICE_MOVE_TIMEOUT_SECONDS,
            check=moved_check,
        )
        moved_channel = after_state.channel
    except asyncio.TimeoutError:
        # Fallback: check cached voice state map in case event was missed.
        voice_state = guild.voice_states.get(bot.user.id)
        if voice_state and voice_state.channel and voice_state.channel.id != temp_channel.id:
            moved_channel = voice_state.channel
    except Exception as move_wait_error:
        print(f"[loop] Error while waiting for voice move event: {move_wait_error}")

    if moved_channel is None:
        print("[loop] Timed out waiting to be moved to a private voice channel")
        return None, None, guild

    print(f"[loop] Moved to private voice channel {moved_channel.id}")
    await asyncio.sleep(VOICE_MOVE_WAIT_SECONDS)
    if hasattr(moved_channel, "send"):
        return moved_channel, None, guild

    print(
        f"[loop] Moved channel {moved_channel.id} is not messageable "
        f"(type={type(moved_channel).__name__})"
    )
    return None, None, guild

@tasks.loop(hours=1.5)
async def send_message_loop():
    print("[loop] send_message_loop triggered")
    channel: discord.abc.Messageable | None = None
    voice_client: discord.VoiceClient | None = None
    voice_guild: discord.Guild | None = None
    try:
        channel, voice_client, voice_guild = await connect_and_get_moved_voice_channel()
        if channel is None:
            print("[loop] Auto message failed: could not resolve moved private voice target channel")
            return

        print(f"[loop] Using moved voice channel {channel.id} as target for loop actions")

        # --- STEP 1: Do pre-buy commands in moved voice channel ---
        await channel.send(AUTO_MESSAGE)
        await asyncio.sleep(AUTO_MESSAGE_DELAY_SECONDS)
        await channel.send("&dep all")
        await asyncio.sleep(AUTO_MESSAGE_DELAY_SECONDS)
        await channel.send("&with 1000")
        await asyncio.sleep(AUTO_MESSAGE_DELAY_SECONDS)

        target_bot_id_int = None
        if TARGET_BOT_ID:
            try:
                target_bot_id_int = int(TARGET_BOT_ID.strip())
            except ValueError:
                print(f"[loop] Invalid TARGET_BOT_ID '{TARGET_BOT_ID}', continuing without author filter")

        async def wait_for_next_event(timeout: float, check, check_edit) -> tuple[str, discord.Message]:
            message_task = asyncio.create_task(
                bot.wait_for("message", timeout=timeout, check=check)
            )
            edit_task = asyncio.create_task(
                bot.wait_for("message_edit", timeout=timeout, check=check_edit)
            )
            done, pending = await asyncio.wait(
                {message_task, edit_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)

            first_error: Exception | None = None
            for finished in done:
                try:
                    result = finished.result()
                except Exception as exc:
                    if first_error is None:
                        first_error = exc
                    continue
                if isinstance(result, tuple):
                    return "message_edit", result[1]
                return "message", result

            if first_error is not None:
                raise first_error
            raise asyncio.TimeoutError()

        def get_select_menu(message: discord.Message):
            for row in message.components or []:
                for child in getattr(row, "children", []):
                    if isinstance(child, discord.SelectMenu):
                        return child
            return None

        def find_anti_rob_option(select_menu: discord.SelectMenu):
            for opt in select_menu.options:
                label_norm = str(opt.label).lower().replace(" ", "").replace("-", "")
                value_norm = str(opt.value).lower().replace(" ", "").replace("-", "")
                if "antirob" in label_norm or "antirob" in value_norm:
                    return opt
            return None

        async def get_select_menu_with_refresh(message: discord.Message):
            select_menu = get_select_menu(message)
            if select_menu is not None and getattr(select_menu, "options", None):
                return select_menu

            try:
                refreshed_message = await message.channel.fetch_message(message.id)
            except Exception as fetch_error:
                print(f"[loop] Could not refresh message {message.id} for components: {fetch_error}")
                return select_menu

            refreshed_select_menu = get_select_menu(refreshed_message)
            if refreshed_select_menu is not None:
                return refreshed_select_menu
            return select_menu

        async def select_dropdown_option(select_menu: discord.SelectMenu, option: discord.SelectOption):
            if hasattr(select_menu, "choose"):
                return await select_menu.choose(option)
            if hasattr(select_menu, "select"):
                return await select_menu.select(values=[option.value])
            raise RuntimeError("Select menu has no supported choose/select method")

        anti_rob_selected = False
        attempt = 0
        total_seen = 0

        while not anti_rob_selected:
            attempt += 1
            buy_message = await channel.send("&buy")
            print(
                f"[loop] Sent &buy command (attempt {attempt}), "
                f"monitoring channel messages for {BUY_MONITOR_SECONDS:.1f}s..."
            )
            observed_message_ids: set[int] = set()

            def check(msg: discord.Message) -> bool:
                if msg.channel.id != channel.id:
                    return False
                if msg.id == buy_message.id:
                    return False
                if msg.created_at < buy_message.created_at:
                    return False
                if target_bot_id_int is not None and msg.author.id != target_bot_id_int:
                    return False
                return True

            def check_edit(before: discord.Message, after: discord.Message) -> bool:
                if after.channel.id != channel.id:
                    return False
                if after.id == buy_message.id:
                    return False
                if target_bot_id_int is not None and after.author.id != target_bot_id_int:
                    return False

                # The bot may edit an existing message to attach components.
                reference_time = after.edited_at or after.created_at
                if reference_time < buy_message.created_at and after.id not in observed_message_ids:
                    return False
                return True

            loop = asyncio.get_running_loop()
            monitor_deadline = loop.time() + BUY_MONITOR_SECONDS
            seen_count = 0

            while True:
                remaining = monitor_deadline - loop.time()
                if remaining <= 0:
                    break

                try:
                    event_name, response = await wait_for_next_event(remaining, check, check_edit)
                except asyncio.TimeoutError:
                    break
                except Exception as wait_error:
                    print(f"[loop] Error while waiting for buy response events: {wait_error}")
                    break

                preview = summarize_message_for_log(response)
                author_type = "bot" if response.author.bot else "user"
                print(f"[loop][buy-monitor/{event_name}] {response.author} (ID: {response.author.id}, {author_type}): '{preview}'")
                seen_count += 1
                total_seen += 1
                observed_message_ids.add(response.id)

                select_menu = await get_select_menu_with_refresh(response)
                if select_menu is None:
                    continue

                anti_rob_option = find_anti_rob_option(select_menu)

                if anti_rob_option is None:
                    option_labels = [opt.label for opt in select_menu.options]
                    print(f"[loop] Dropdown found but anti-rob option missing. Options: {option_labels}")
                    continue

                try:
                    await select_dropdown_option(select_menu, anti_rob_option)
                    print(
                        f"[loop] Selected anti-rob (attempt {attempt}, "
                        f"label='{anti_rob_option.label}', value='{anti_rob_option.value}')"
                    )
                    anti_rob_selected = True
                    break
                except Exception as select_error:
                    print(f"[loop] Failed to select anti-rob from dropdown: {select_error}")

            print(f"[loop] Buy monitor attempt {attempt} finished. Messages observed: {seen_count}")
            if anti_rob_selected:
                break

            print("[loop] anti-rob not selected yet, retrying &buy...")
            await asyncio.sleep(AUTO_MESSAGE_DELAY_SECONDS)

        print(f"[loop] anti-rob selection succeeded after {attempt} attempt(s); total observed messages: {total_seen}")

    except Exception as e:
        print(f"[loop] Error while running buy/select retry loop: {e}")
    finally:
        if voice_client and voice_client.is_connected():
            try:
                await voice_client.disconnect(force=True)
                print("[loop] Disconnected from voice channel")
            except Exception as disconnect_error:
                print(f"[loop] Error while disconnecting voice client: {disconnect_error}")
        elif voice_guild is not None:
            try:
                await voice_guild.change_voice_state(channel=None, self_deaf=False, self_mute=False)
                print("[loop] Cleared voice state (left voice channel)")
            except Exception as voice_state_clear_error:
                print(f"[loop] Error while clearing voice state: {voice_state_clear_error}")

    # --- STEP 2: Send deposit messages (your original functionality) ---

    print("[loop] Auto message sent\n")

@send_message_loop.before_loop
async def before_send_loop():
    await bot.wait_until_ready()
    print(f"Ready as {bot.user}. Commands: !join and !msg")

@bot.event
async def on_ready():
    global console_task
    print(f"Logged in as {bot.user} (self-bot mode)")
    if not send_message_loop.is_running():
        send_message_loop.start()
    if console_task is None or console_task.done():
        console_task = asyncio.create_task(console_command_loop())

@bot.event
async def on_message(message):
    # Only respond to our own messages
    if bot.user is None or message.author.id != bot.user.id:
        return

    print(f"[on_message] Received: '{message.content}' in channel {message.channel.id}")

    content = message.content.strip()

    # Plain-text DM commands (no prefix): switch/msg/msgid/room/join
    if message.guild is None:
        handled_dm = await handle_runtime_command(content, message, dm_mode=True)
        if handled_dm:
            return

    if content.startswith("!msg"):
        print("[on_message] Matched !msg")
        body = content[4:].strip()
        await do_msg(body, message)
        return

    if content.startswith("!join"):
        print("[on_message] Matched !join")
        invite = content[5:].strip()
        if not invite:
            await message.channel.send("Usage: !join invite_link_or_code")
            return
        await do_join(invite, message)
        return

    # For any other commands (if you add more), let the framework handle them
    await bot.process_commands(message)

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        return
    try:
        await ctx.send(f"Command error: {type(error).__name__}: {error}")
    except Exception:
        print(f"Command error: {type(error).__name__}: {error}")

# -------------------------------
# Run the bot
# -------------------------------
if __name__ == "__main__":
    bot.run(TOKEN)
