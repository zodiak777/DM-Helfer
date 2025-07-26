import os
import random
import logging
from datetime import datetime
from dotenv import load_dotenv
import openai
import discord
from discord.ext import tasks
from discord import app_commands

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s:%(name)s:%(message)s",
    handlers=[
        logging.FileHandler("bot.log"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)

load_dotenv()
DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
CHANNEL_ID = os.getenv('CHANNEL_ID')

logger.debug("Env vars geladen: CHANNEL_ID=%s", CHANNEL_ID)

if DISCORD_TOKEN is None:
    logger.error('DISCORD_TOKEN environment variable nicht gesetzt')
    raise RuntimeError('DISCORD_TOKEN environment variable nicht gesetzt')

if OPENAI_API_KEY is None:
    logger.error('OPENAI_API_KEY environment variable nicht gesetzt')
    raise RuntimeError('OPENAI_API_KEY environment variable nicht gesetzt')
    
if CHANNEL_ID is None:
    logger.error('CHANNEL_ID environment variable nicht gesetzt')
    raise RuntimeError('CHANNEL_ID environment variable nicht gesetzt')
    
CHANNEL_ID = int(CHANNEL_ID)

openai.api_key = OPENAI_API_KEY
logger.debug('OpenAI API key geladen')

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)
logger.debug('Discord client initialisiert')

def load_pre_prompt(path="prompt_parts"):
    logger.debug("Lade pre prompt aus Ordner %s", path)

    def read_file(filename):
        full_path = os.path.join(path, filename)
        logger.debug("Lese %s", full_path)
        with open(full_path, "r", encoding="utf-8") as f:
            return f.read().strip()

    parts = []
    parts.append(read_file("core.txt"))
    parts.append("Spielercharaktere:\n" + read_file("spieler.txt"))
    parts.append("Nicht-Spielercharaktere:\n" + read_file("npcs.txt"))
    parts.append("Tiere:\n" + read_file("tiere.txt"))
    section_title = "Gegebene Weltinformationen (fest, nicht erweitern!):"
    parts.append(section_title + "\n" + read_file("welt.txt"))

    return "\n\n".join(parts)

PRE_PROMPT = load_pre_prompt()
logger.debug('Pre prompt geladen')

current_weather = "Unbestimmt"
weather_roll_date = None

WEATHER_TABLE = {
    1: "glühende Hitze",
    2: "sehr heiße Sonne",
    3: "heiße Temperaturen",
    4: "warm und sonnig",
    5: "angenehm warm",
    6: "milder Sonnenschein",
    7: "warmer Sonnenschein",
    8: "leicht Bewölkt",
    9: "bewölkt",
    10: "wolkig",
    11: "leichter Regen",
    12: "nieselnder Regen",
    13: "mäßiger Regen",
    14: "anhaltender Regen",
    15: "starker Regen",
    16: "Regen mit starkem Wind",
    17: "Gewitter",
    18: "heftiges Gewitter",
    19: "Platzregen",
    20: "extremer Platzregen",
}

NPC_LIST = [
    "Agatha",
    "Bwayes",
    "Brumir",
    "Nithra",
    "Faelwyn",
    "Vaelion",
]
logger.debug('Discord client initialisiert')

user_list = {
    "zodiak6610": "Spielleiter",
    "delailajana": "Bella",
    "epimetheus.": "Epizard",
    "dewarr1": "Rashar",
    "fritzifitzgerald.": "Fritzi",
    "pinkdevli692": "Joanne",
    "itsamereiki": "Reiki",
    "flohoehoe": "Casmir",
    "spielhorst": "Horst",
    "tibolonius": "Vex",
    ".wolfgrimm": "Katazur",
    "DM-Helfer#7090": "DM-Helfer"
}

def get_random_npc():
    return random.choice(NPC_LIST)

def roll_weather():
    roll = random.randint(1, 20)
    desc = WEATHER_TABLE.get(roll, "Unbekannt")
    logger.info("Wetter roll %s => %s", roll, desc)
    return desc

@client.event
async def on_ready():
    await tree.sync()
    hourly_post.start()

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
    for npc in NPC_LIST:
        if npc.lower() in content_lower:
            await reply_as_npc(npc, message)
            break

@tree.command(name="force", description="Sofort eine Nachricht posten")
async def force_command(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    npc = get_random_npc()
    await generate_and_send(f'Schreibe eine kurze Szene mit dem NPC {npc}.', npc)
    await interaction.followup.send("Nachricht gepostet.", ephemeral=True)

def load_npc_extension(npc_name: str, path: str = os.path.join("prompt_parts", "npcs")) -> str:
    """Lade erweiterte Informationen zu einem NPC, falls vorhanden."""
    base = npc_name.split()[0].lower()
    candidates = [f"{base}_erweitert.txt", f"{base}_erweitert"]
    for fname in candidates:
        full_path = os.path.join(path, fname)
        if os.path.exists(full_path):
            logger.debug("Lese NPC-Erweiterung %s", full_path)
            with open(full_path, "r", encoding="utf-8") as f:
                return f.read().strip()
    logger.debug("Keine NPC-Erweiterung für %s gefunden", npc_name)
    return ""

async def generate_and_send(input, npc_name: str | None = None):
    current_time = datetime.now().strftime('%H:%M')
    parts = [PRE_PROMPT]
    if npc_name:
        extra = load_npc_extension(npc_name)
        if extra:
            parts.append(extra)
    parts.append(f"Es ist aktuell {current_time} Uhr. Das Wetter heute: {current_weather}.")
    prompt = "\n\n".join(parts)
    logger.debug('Prompt an OpenAI gesendet: %s', prompt)
    channel = client.get_channel(CHANNEL_ID)

    try:
        response = openai.chat.completions.create(  # DO NOT CHANGE THIS LINE
            model='gpt-4.1',
            messages=[
                {'role': 'system', 'content': prompt},
                {
                    'role': 'user',
                    'content': input
                },
            ],
            max_tokens=1024,
        )
        message = response.choices[0].message.content.strip()
        logger.debug('OpenAI response: %s', message)
        await channel.send(message)
        logger.info('Message an Channel %s gesendet.', channel.id)
    except Exception:
        logger.error('Fehler beim Nachricht senden: ', exc_info=True)

async def get_recent_messages(channel: discord.TextChannel, limit: int = 10, before: discord.Message | None = None):
    messages = []
    async for msg in channel.history(limit=limit, before=before, oldest_first=False):
        messages.append(f"{user_list[str(msg.author)]}: {msg.content}")
    messages.reverse()
    return "\n".join(messages)

async def reply_as_npc(npc_name: str, trigger_message: discord.Message):
    logger.info('Generiere Antwort als %s', npc_name)
    channel = client.get_channel(CHANNEL_ID)
    context = await get_recent_messages(channel, limit=10, before=trigger_message)
    input_text = (
        f"Kontext der letzten Nachrichten:\n{context}\n\n"
        f"Antworte als {npc_name} auf folgende Nachricht. Halte dich an die Stilrichtlinien.\n"
        f"Nachricht von {user_list[str(trigger_message.author)]}: {trigger_message.content}"
    )
    await generate_and_send(input_text, npc_name)

@tasks.loop(hours=1)
async def hourly_post():
    logger.debug('stündlicher post')
    now = datetime.now()
    global current_weather, weather_roll_date

    if now.hour == 8 and (weather_roll_date != now.date()):
        current_weather = roll_weather()
        weather_roll_date = now.date()
        await generate_and_send('Beschreibe das aktuelle Wetter. Verwende dabei KEINE NPCs')
        logger.info('Tägliches wetter Bestimmt: %s', current_weather)

    if 1 <= now.hour <= 8:
        logger.debug('Stille Stunde')
        return

    if random.random() > 0.05:
        logger.debug('Kein post')
        return

    try:
        last_message = None
        channel = client.get_channel(CHANNEL_ID)
        async for message in channel.history(limit=1):
            last_message = message
            break
        if last_message is not None:
            age = discord.utils.utcnow() - last_message.created_at
            if age.total_seconds() < 3600:
                logger.debug('Letzte nachricht erst %s sekunden alt; übersprungen', age.total_seconds())
                return
    except Exception:
        logger.error('Error fetching channel history', exc_info=True)
        return

    npc = get_random_npc()
    await generate_and_send(f'Schreibe eine kurze Szene mit dem NPC {npc}.', npc)

if __name__ == '__main__':
    logger.info('Starting Discord bot')
    client.run(DISCORD_TOKEN)
