import os
import random
import logging
import json
from datetime import datetime
from threading import Thread
from dotenv import load_dotenv
from openai import OpenAI
import discord
from discord.ext import tasks
from discord import app_commands
import web

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(BASE_DIR, "config.json"), "r", encoding="utf-8") as f:
    CONFIG = json.load(f)

LOG_DIR = os.path.join(BASE_DIR, CONFIG["logging"]["log_dir"])
os.makedirs(LOG_DIR, exist_ok=True)
LOG_LEVEL = getattr(logging, CONFIG["logging"]["log_level"].upper(), logging.INFO)

PROMPT_DATA_PATH = os.path.join(BASE_DIR, CONFIG["data_paths"]["prompt_data"])
OPENAI_MODEL = CONFIG["openai"]["model"]
OPENAI_MAX_TOKENS = CONFIG["openai"]["max_tokens"]

TASK_INTERVAL_HOURS = CONFIG["discord"]["task_interval_hours"]
DAILY_WEATHER_HOUR = CONFIG["discord"]["daily_weather_hour"]
SILENT_HOURS_START, SILENT_HOURS_END = CONFIG["discord"]["silent_hours"]
POST_PROBABILITY = CONFIG["discord"]["post_probability_percent"] / 100.0
MIN_SECONDS_SINCE_USER_POST = CONFIG["discord"]["min_seconds_since_user_post"]
CONTEXT_MESSAGE_LIMIT = CONFIG["discord"]["context_message_limit"]

WEB_HOST = CONFIG["webserver"]["host"]
WEB_PORT = CONFIG["webserver"]["port"]

class LevelFilter(logging.Filter):
    def __init__(self, level: int) -> None:
        super().__init__()
        self.level = level

    def filter(self, record: logging.LogRecord) -> bool:
        return record.levelno == self.level

formatter = logging.Formatter("%(asctime)s %(levelname)s:%(name)s:%(message)s")
root_logger = logging.getLogger()
root_logger.setLevel(LOG_LEVEL)

stream_handler = logging.StreamHandler()
stream_handler.setFormatter(formatter)
root_logger.addHandler(stream_handler)

for name, level in [
    ("debug", logging.DEBUG),
    ("info", logging.INFO),
    ("warning", logging.WARNING),
    ("error", logging.ERROR),
]:
    file_handler = logging.FileHandler(os.path.join(LOG_DIR, f"{name}.log"))
    file_handler.setLevel(level)
    file_handler.addFilter(LevelFilter(level))
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)

logger = logging.getLogger(__name__)

load_dotenv()
DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
CHANNEL_ID = os.getenv('CHANNEL_ID')
WEB_USERNAME = os.getenv('WEB_USERNAME')
WEB_PASSWORD = os.getenv('WEB_PASSWORD')
FLASK_SECRET_KEY = os.getenv('FLASK_SECRET_KEY', 'secret')

logger.debug("Loaded environment variables: CHANNEL_ID=%s", CHANNEL_ID)

if DISCORD_TOKEN is None:
    raise RuntimeError('DISCORD_TOKEN environment variable is not set')

if OPENAI_API_KEY is None:
    raise RuntimeError('OPENAI_API_KEY environment variable is not set')

if CHANNEL_ID is None:
    raise RuntimeError('CHANNEL_ID environment variable is not set')

if WEB_USERNAME is None or WEB_PASSWORD is None:
    raise RuntimeError('WEB_USERNAME or WEB_PASSWORD is not set')

CHANNEL_ID = int(CHANNEL_ID)

openai_client = OpenAI(api_key=OPENAI_API_KEY)
logger.debug('OpenAI client initialized')

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)
logger.debug('Discord client initialized')

def load_prompt_data(path=PROMPT_DATA_PATH):
    logger.debug("Loading prompt data from %s", path)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def save_prompt_data(data: dict, path=PROMPT_DATA_PATH):
    logger.debug("Saving prompt data to %s", path)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def build_pre_prompt(data: dict) -> str:
    def join_section(items, fmt):
        return "\n".join(fmt(i) for i in items)

    spieler_txt = join_section(
        data.get("spieler", []), lambda p: f"{p['name']} – {p['info']}"
    )
    npc_txt = join_section(
        data.get("npc", []), lambda n: f"{n['name']}: {n['short']}"
    )
    tiere_txt = join_section(
        data.get("tiere", []), lambda t: f"{t['name']}: {t['info']}"
    )
    parts = [
        data.get("core", ""),
        "Spielercharaktere:\n" + spieler_txt,
        "Nicht-Spielercharaktere:\n" + npc_txt,
        "Tiere:\n" + tiere_txt,
    ]
    section_title = "Gegebene Weltinformationen (fest, nicht erweitern!):"
    parts.append(section_title + "\n" + data.get("welt", ""))
    return "\n\n".join(parts)

NPC_LIST: list[str] = []
WEATHER_TABLE: dict[int, str] = {}
USER_LIST: dict[str, str] = {}

def refresh_data():
    global PROMPT_DATA, PRE_PROMPT, NPC_LIST, WEATHER_TABLE, USER_LIST
    PROMPT_DATA = load_prompt_data()
    PRE_PROMPT = build_pre_prompt(PROMPT_DATA)
    NPC_LIST = sorted({n["name"].split()[0] for n in PROMPT_DATA.get("npc", [])})
    WEATHER_TABLE = {int(k): v for k, v in PROMPT_DATA.get("weather_table", {}).items()}
    USER_LIST = PROMPT_DATA.get("user_list", {})
    logger.debug(
        "Prompt data refreshed: %d NPCs, %d players, %d animals, %d users",
        len(NPC_LIST),
        len(PROMPT_DATA.get("spieler", [])),
        len(PROMPT_DATA.get("tiere", [])),
        len(USER_LIST),
    )

refresh_data()

web.init_web(
    CONFIG,
    lambda: PROMPT_DATA,
    save_prompt_data,
    refresh_data,
    LOG_DIR,
    WEB_USERNAME,
    WEB_PASSWORD,
    BASE_DIR,
    FLASK_SECRET_KEY,
    logger,
)

current_weather = "Undetermined"
weather_roll_date = None
event_probability = 0.01

def get_random_npc():
    return random.choice(NPC_LIST)

def roll_weather():
    roll = random.randint(1, 20)
    desc = WEATHER_TABLE.get(roll, "Unknown")
    logger.info("Weather roll %s => %s", roll, desc)
    return desc

@client.event
async def on_ready():
    await tree.sync()
    hourly_post.start()
    refresh_data()
    logger.info("Logged in as %s", client.user)

@client.event
async def on_message(message: discord.Message):
    if message.author == client.user:
        return
    if message.author.bot:
        return
    if hasattr(message.author, "roles") and any(role.name == "Weltenschmied" for role in message.author.roles):
        return
    if message.channel.id != CHANNEL_ID:
        return
    content_lower = message.content.lower()
    npcs_in_message = sorted({npc for npc in NPC_LIST if npc.lower() in content_lower})
    if len(npcs_in_message) == 1:
        await reply_as_npc(npcs_in_message[0], message)
    elif len(npcs_in_message) > 1:
        await reply_as_npcs(npcs_in_message, message)

@tree.command(name="force", description="Sofort eine Nachricht posten")
async def force_command(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    npc = get_random_npc()
    logger.info("Force command triggered by %s using NPC %s", interaction.user, npc)
    await generate_and_send(f'Schreibe eine kurze Szene mit dem NPC {npc}.', npc)
    await interaction.followup.send("Nachricht gepostet.", ephemeral=True)

@tree.command(name="regie", description="Regieanweisungen geben")
@app_commands.describe(anweisung="Was soll geschehen?")
async def regie_command(interaction: discord.Interaction, anweisung: str):
    if not any(role.name == "Weltenschmied" for role in getattr(interaction.user, "roles", [])):
        await interaction.response.send_message(
            "Nur Weltenschmiede können diese Funktion nutzen.", ephemeral=True
        )
        return
    await interaction.response.defer(ephemeral=True)
    logger.info("Regie command triggered by %s: %s", interaction.user, anweisung)
    npcs_in_text = sorted({npc for npc in NPC_LIST if npc.lower() in anweisung.lower()})
    if npcs_in_text:
        await generate_and_send(anweisung, npcs_in_text)
    else:
        await generate_and_send(anweisung)
    await interaction.followup.send("Regieanweisung ausgeführt.", ephemeral=True)

def load_npc_extension(npc_name: str) -> str:
    base = npc_name.split()[0]
    for npc in PROMPT_DATA.get("npc", []):
        if npc["name"].split()[0] == base:
            extra = npc.get("long", "")
            if extra:
                logger.debug("Found NPC extension for %s", npc_name)
                return extra
    logger.debug("No NPC extension found for %s", npc_name)
    return ""

async def generate_and_send(input, npc_names: list[str] | str | None = None):
    current_time = datetime.now().strftime('%H:%M')
    parts = [PRE_PROMPT]
    if npc_names:
        if isinstance(npc_names, str):
            npc_names = [npc_names]
        for npc_name in npc_names:
            extra = load_npc_extension(npc_name)
            if extra:
                parts.append(extra)
    parts.append(f"Es ist aktuell {current_time} Uhr. Das Wetter heute: {current_weather}.")
    prompt = "\n\n".join(parts)
    logger.debug('Prompt sent to OpenAI: %s', prompt)
    channel = client.get_channel(CHANNEL_ID)

    try:
        response = openai_client.responses.create(
            model=OPENAI_MODEL,
            input=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": input},
            ],
            reasoning={"effort": "low"},
            max_output_tokens=OPENAI_MAX_TOKENS,
        )
        message = response.output_text.strip()
        logger.debug('OpenAI response: %s', message)
        if not ("[none]" or "none") in message:
            await channel.send(message)
            print(message)
        logger.info('Message sent to channel %s', channel.id)
    except Exception:
        logger.error('Error while sending message', exc_info=True)

async def get_recent_messages(channel: discord.TextChannel, limit: int = CONTEXT_MESSAGE_LIMIT, before: discord.Message | None = None):
    messages = []
    async for msg in channel.history(limit=limit, before=before, oldest_first=False):
        messages.append(f"{USER_LIST[str(msg.author)]}: {msg.content}")
    messages.reverse()
    return "\n".join(messages)

async def reply_as_npc(npc_name: str, trigger_message: discord.Message):
    logger.info('Generating reply as %s', npc_name)
    channel = client.get_channel(CHANNEL_ID)
    context = await get_recent_messages(channel, limit=CONTEXT_MESSAGE_LIMIT, before=trigger_message)
    input_text = (
        f"Kontext der letzten Nachrichten:\n{context}\n\n"
        f"Antworte als {npc_name} auf folgende Nachricht. Halte dich an die Stilrichtlinien.\n"
        f"Wenn es keinen Sinn ergibt, dass {npc_name} darauf reagiert, antworte ausschließlich mit [none].\n"
        f"Nachricht von {USER_LIST[str(trigger_message.author)]}: {trigger_message.content}"
    )
    await generate_and_send(input_text, npc_name)

async def reply_as_npcs(npc_names: list[str], trigger_message: discord.Message):
    logger.info('Generating reply as %s', ", ".join(npc_names))
    channel = client.get_channel(CHANNEL_ID)
    context = await get_recent_messages(channel, limit=CONTEXT_MESSAGE_LIMIT, before=trigger_message)
    names_line = ", ".join(npc_names)
    input_text = (
        f"Kontext der letzten Nachrichten:\n{context}\n\n"
        f"Antworte auf folgende Nachricht. Übernehme dabei nacheinander die Rollen der folgenden Charaktere in einer einzigen Nachricht."
        f" Beginne jede Antwort mit dem jeweiligen Namen:\n{names_line}\n"
        f"Wenn es für einen der Charaktere keinen Sinn ergibt zu antworten, lass ihn aus.\n"
        f"Sollte es bei garkeinen Charakter Sinn ergeben, antworte ausschließlich mit [none]."
        f"Nachricht von {USER_LIST[str(trigger_message.author)]}: {trigger_message.content}"
    )
    await generate_and_send(input_text, npc_names)

@tasks.loop(hours=TASK_INTERVAL_HOURS)
async def hourly_post():
    logger.debug('Hourly post task triggered')
    now = datetime.now()
    global current_weather, weather_roll_date, event_probability

    if now.hour == DAILY_WEATHER_HOUR and (weather_roll_date != now.date()):
        current_weather = roll_weather()
        weather_roll_date = now.date()
        await generate_and_send('Beschreibe das aktuelle Wetter. Verwende dabei KEINE NPCs')
        logger.info('Daily weather determined: %s (event chance %.0f%%)', current_weather, event_probability * 100)

    if SILENT_HOURS_START <= now.hour <= SILENT_HOURS_END:
        logger.debug('Quiet hour')
        return

    if random.random() < event_probability:
        data = load_prompt_data()
        events = data.get("events", [])
        if events:
            event = random.choice(events)
            await generate_and_send(event.get("info", ""), event.get("npc"))
            events.remove(event)
            save_prompt_data(data)
            refresh_data()
            event_probability = 0.01
            logger.info('Special event executed for NPC %s. Event chance reset to 1%%', event.get('npc'))
            return
    
    if random.random() > POST_PROBABILITY:
        logger.debug('No post this hour')
        return

    try:
        last_message = None
        channel = client.get_channel(CHANNEL_ID)
        async for message in channel.history(limit=1):
            last_message = message
            break
        if last_message is not None:
            age = discord.utils.utcnow() - last_message.created_at
            if age.total_seconds() < MIN_SECONDS_SINCE_USER_POST:
                logger.debug('Last message only %s seconds old; skipped', age.total_seconds())
                return
    except Exception:
        logger.error('Error fetching channel history', exc_info=True)
        return

    npc = get_random_npc()
    await generate_and_send(f'Schreibe eine kurze Szene mit dem NPC {npc}.', npc)

def run_flask():
    web.app.run(host=WEB_HOST, port=WEB_PORT)

if __name__ == '__main__':
    Thread(target=run_flask, daemon=True).start()
    logger.info('Starting Discord bot')
    client.run(DISCORD_TOKEN)
