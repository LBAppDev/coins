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

# -------------------------------
# Configuration
# -------------------------------
TOKEN = os.getenv("DISCORD_USER_TOKEN")
if TOKEN:
    TOKEN = TOKEN.strip()
else:
    raise RuntimeError("Missing DISCORD_USER_TOKEN in environment or .env")

channel_id_raw = os.getenv("TARGET_CHANNEL_ID")
if not channel_id_raw:
    raise RuntimeError("Missing TARGET_CHANNEL_ID in environment or .env")
try:
    CHANNEL_ID = int(channel_id_raw.strip())
except ValueError:
    raise RuntimeError("TARGET_CHANNEL_ID must be an integer")

AUTO_MESSAGE = os.getenv("AUTO_MESSAGE", "Automated test message every 3 hours!")
TARGET_BOT_ID = os.getenv("TARGET_BOT_ID")
AUTO_MESSAGE_DELAY_SECONDS = float(os.getenv("AUTO_MESSAGE_DELAY_SECONDS", "2.0"))
BUY_MONITOR_SECONDS = float(os.getenv("BUY_MONITOR_SECONDS", "20"))
print(TARGET_BOT_ID)
# -------------------------------
# Optional token validation
# -------------------------------
print("Validating token...")
headers = {"Authorization": TOKEN}
try:
    r = requests.get("https://discord.com/api/v9/users/@me", headers=headers, timeout=10)
    if r.status_code == 200:
        user_data = r.json()
        print(f"Token valid! Logged in as {user_data['username']}#{user_data['discriminator']}")
    else:
        print(f"Token validation failed (status {r.status_code}).")
        print(f"Response: {r.text[:200]}")
        sys.exit(1)
except Exception as e:
    print(f"Could not validate token: {e}")
    sys.exit(1)

# -------------------------------
# Bot setup
# -------------------------------
intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(
    command_prefix="!",
    self_bot=True,
    intents=intents,
    help_command=None,
)
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

async def console_command_loop():
    await bot.wait_until_ready()
    print("Console commands ready: msg <message> | join <invite>")
    while not bot.is_closed():
        line = await asyncio.to_thread(sys.stdin.readline)
        if not line:
            await asyncio.sleep(0.1)
            continue
        line = line.strip()
        if not line:
            continue

        lower = line.lower()
        if lower.startswith("msg "):
            body = line[4:].strip()
            await do_msg(body, None)
            continue

        if lower.startswith("join "):
            invite = line[5:].strip()
            if not invite:
                print("[console] Usage: join invite_link_or_code")
                continue
            await do_join(invite, None)
            continue

        print("[console] Unknown command. Use: msg <message> | join <invite>")

@tasks.loop(hours=1.5)
async def send_message_loop():
    print("[loop] send_message_loop triggered")
    channel = bot.get_channel(CHANNEL_ID)
    if not channel:
        print(f"[loop] Auto message failed: channel {CHANNEL_ID} not found")
        return
    # --- STEP 1: Send &buy, monitor messages/edits, and select anti-rob when available ---
    await channel.send(AUTO_MESSAGE)
    await asyncio.sleep(AUTO_MESSAGE_DELAY_SECONDS)
    await channel.send("&dep all")
    await asyncio.sleep(AUTO_MESSAGE_DELAY_SECONDS)
    await channel.send("&with 1000")
    await asyncio.sleep(AUTO_MESSAGE_DELAY_SECONDS)
    
    try:
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

            def check(msg: discord.Message) -> bool:
                if msg.channel.id != CHANNEL_ID:
                    return False
                if msg.id == buy_message.id:
                    return False
                if msg.created_at < buy_message.created_at:
                    return False
                if target_bot_id_int is not None and msg.author.id != target_bot_id_int:
                    return False
                return True

            def check_edit(before: discord.Message, after: discord.Message) -> bool:
                return check(after)

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

                select_menu = get_select_menu(response)
                if select_menu is None:
                    continue

                anti_rob_option = next(
                    (
                        opt
                        for opt in select_menu.options
                        if "anti-rob" in opt.label.lower() or "anti rob" in opt.label.lower()
                    ),
                    None,
                )

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
