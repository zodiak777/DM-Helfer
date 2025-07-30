import os
import random
import logging
import json
from threading import Thread
from datetime import datetime
from dotenv import load_dotenv
import openai
import discord
from discord.ext import tasks
from discord import app_commands
from flask import Flask, request, session, redirect, url_for, render_template_string

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
WEB_USERNAME = os.getenv('WEB_USERNAME')
WEB_PASSWORD = os.getenv('WEB_PASSWORD')
FLASK_SECRET_KEY = os.getenv('FLASK_SECRET_KEY', 'secret')

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

if WEB_USERNAME is None or WEB_PASSWORD is None:
    logger.error('WEB_USERNAME und WEB_PASSWORD müssen gesetzt sein')
    raise RuntimeError('WEB_USERNAME oder WEB_PASSWORD nicht gesetzt')
    
CHANNEL_ID = int(CHANNEL_ID)

openai.api_key = OPENAI_API_KEY
logger.debug('OpenAI API key geladen')

app = Flask(__name__)
app.secret_key = FLASK_SECRET_KEY

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)
logger.debug('Discord client initialisiert')

def load_prompt_data(path="prompt_data.json"):
    logger.debug("Lade Prompt-Daten aus %s", path)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def build_pre_prompt(data: dict) -> str:
    parts = [
        data.get("core", ""),
        "Spielercharaktere:\n" + data.get("spieler", ""),
        "Nicht-Spielercharaktere:\n" + data.get("npcs", ""),
        "Tiere:\n" + data.get("tiere", ""),
    ]
    section_title = "Gegebene Weltinformationen (fest, nicht erweitern!):"
    parts.append(section_title + "\n" + data.get("welt", ""))
    return "\n\n".join(parts)

PROMPT_DATA = load_prompt_data()
PRE_PROMPT = build_pre_prompt(PROMPT_DATA)
logger.debug('Pre prompt geladen')

def save_prompt_data(data: dict, path: str = "prompt_data.json") -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def reload_prompt() -> None:
    global PROMPT_DATA, PRE_PROMPT
    PROMPT_DATA = load_prompt_data()
    PRE_PROMPT = build_pre_prompt(PROMPT_DATA)


def parse_npc_short(text: str) -> dict:
    result = {}
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if ":" in line:
            name, desc = line.split(":", 1)
            result[name.strip()] = desc.strip()
    return result


def build_npc_short(data: dict) -> str:
    return "\n".join(f"{name}: {desc}" for name, desc in data.items())

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

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        if request.form.get('username') == WEB_USERNAME and request.form.get('password') == WEB_PASSWORD:
            session['logged_in'] = True
            return redirect(url_for('edit'))
        return 'Login fehlgeschlagen', 401
    return render_template_string('''<form method="post">
        <input name="username" placeholder="Username">
        <input type="password" name="password" placeholder="Password">
        <button type="submit">Login</button>
    </form>''')


@app.route('/edit', methods=['GET', 'POST'])
def edit():
    if not session.get('logged_in'):
        return redirect(url_for('login'))

    short_desc = parse_npc_short(PROMPT_DATA.get('npcs', ''))

    if request.method == 'POST':
        for name in NPC_LIST:
            s_key = f'short_{name}'
            l_key = f'long_{name}'
            if s_key in request.form:
                short_desc[name] = request.form[s_key]
            if l_key in request.form:
                PROMPT_DATA.setdefault('npc_details', {})[name] = request.form[l_key]
        PROMPT_DATA['npcs'] = build_npc_short(short_desc)
        save_prompt_data(PROMPT_DATA)
        reload_prompt()

    rows = []
    for name in NPC_LIST:
        s_val = short_desc.get(name, '')
        l_val = PROMPT_DATA.get('npc_details', {}).get(name, '')
        rows.append(f"<h3>{name}</h3>Kurzbeschreibung:<br><textarea name='short_{name}' rows='2' cols='80'>{s_val}</textarea><br>Erweiterte Beschreibung:<br><textarea name='long_{name}' rows='6' cols='80'>{l_val}</textarea>")
    body = "".join(rows)
    return render_template_string('<form method="post">' + body + '<br><button type="submit">Speichern</button></form>')

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

def load_npc_extension(npc_name: str, data: dict = PROMPT_DATA) -> str:
    base = npc_name.split()[0]
    details = data.get("npc_details", {})
    extra = details.get(base)
    if extra:
        logger.debug("NPC-Erweiterung für %s gefunden", npc_name)
        return extra
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
    logger.info('Starting Discord bot and web interface')

    def run_web():
        app.run(host='0.0.0.0', port=5000)

    Thread(target=run_web, daemon=True).start()
    client.run(DISCORD_TOKEN)
