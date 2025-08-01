import os
import random
import logging
import json
from datetime import datetime
from functools import wraps
from threading import Thread
from dotenv import load_dotenv
import openai
import discord
from discord.ext import tasks
from discord import app_commands
from flask import Flask, request, session, redirect, url_for, render_template

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
    logger.error('WEB_USERNAME oder WEB_PASSWORD nicht gesetzt')
    raise RuntimeError('WEB_USERNAME oder WEB_PASSWORD nicht gesetzt')

CHANNEL_ID = int(CHANNEL_ID)

openai.api_key = OPENAI_API_KEY
logger.debug('OpenAI API key geladen')

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)
logger.debug('Discord client initialisiert')

app = Flask(__name__)
app.secret_key = FLASK_SECRET_KEY

def load_prompt_data(path="prompt_data.json"):
    logger.debug("Lade Prompt-Daten aus %s", path)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def save_prompt_data(data: dict, path="prompt_data.json"):
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

def refresh_data():
    global PROMPT_DATA, PRE_PROMPT, NPC_LIST
    PROMPT_DATA = load_prompt_data()
    PRE_PROMPT = build_pre_prompt(PROMPT_DATA)
    NPC_LIST = sorted({n["name"].split()[0] for n in PROMPT_DATA.get("npc", [])})

logger.debug('Pre prompt geladen')

current_weather = "Unbestimmt"
weather_roll_date = None

NPC_LIST: list[str] = []

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

def login_required(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login"))
        return func(*args, **kwargs)
    return wrapper

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        if request.form.get("username") == WEB_USERNAME and request.form.get("password") == WEB_PASSWORD:
            session["logged_in"] = True
            return redirect(url_for("dashboard"))
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.pop("logged_in", None)
    return redirect(url_for("login"))

@app.route("/")
@login_required
def dashboard():
    npc_count = len(PROMPT_DATA.get("npc", []))
    player_count = len(PROMPT_DATA.get("spieler", []))
    animal_count = len(PROMPT_DATA.get("tiere", []))
    core_text = "vorhanden" if PROMPT_DATA.get("core") else "nicht gesetzt"
    world_text = "vorhanden" if PROMPT_DATA.get("welt") else "nicht gesetzt"
    return render_template(
        "dashboard.html",
        npc_count=npc_count,
        player_count=player_count,
        animal_count=animal_count,
        core_text=core_text,
        world_text=world_text,
    )


@app.route("/npcs")
@login_required
def npc_list():
    all_npcs = sorted(n["name"].split()[0] for n in PROMPT_DATA.get("npc", []))
    return render_template("npc_list.html", npcs=all_npcs)

@app.route("/add", methods=["GET", "POST"])
@login_required
def add_npc():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        short = request.form.get("short", "").strip()
        long = request.form.get("long", "").strip()
        if name and short:
            npc_list = PROMPT_DATA.setdefault("npc", [])
            npc_list.append({"name": name, "short": short, "long": long})
            save_prompt_data(PROMPT_DATA)
            refresh_data()
            return redirect(url_for("npc_list"))
    return render_template("add_npc.html")

@app.route("/edit/<name>", methods=["GET", "POST"])
@login_required
def edit_npc(name):
    npc_list = PROMPT_DATA.get("npc", [])
    npc = next((n for n in npc_list if n["name"].split()[0] == name), None)
    if npc is None:
        return "NPC not found", 404
    if request.method == "POST":
        npc["short"] = request.form.get("short", "").strip()
        npc["long"] = request.form.get("long", "").strip()
        save_prompt_data(PROMPT_DATA)
        refresh_data()
        return redirect(url_for("npc_list"))
    short_text = npc.get("short", "")
    long_text = npc.get("long", "")
    return render_template(
        "edit_npc.html",
        name=name,
        short=short_text,
        long=long_text,
    )

@app.route("/delete/<name>")
@login_required
def delete_npc(name):
    npc_list = PROMPT_DATA.get("npc", [])
    PROMPT_DATA["npc"] = [n for n in npc_list if n["name"].split()[0] != name]
    save_prompt_data(PROMPT_DATA)
    refresh_data()
    return redirect(url_for("npc_list"))

@app.route("/players")
@login_required
def player_list():
    players = sorted(p["name"] for p in PROMPT_DATA.get("spieler", []))
    return render_template("player_list.html", players=players)

@app.route("/players/add", methods=["GET", "POST"])
@login_required
def add_player():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        info = request.form.get("info", "").strip()
        if name and info:
            lst = PROMPT_DATA.setdefault("spieler", [])
            lst.append({"name": name, "info": info})
            save_prompt_data(PROMPT_DATA)
            refresh_data()
            return redirect(url_for("player_list"))
    return render_template("add_player.html")

@app.route("/players/edit/<name>", methods=["GET", "POST"])
@login_required
def edit_player(name):
    players = PROMPT_DATA.get("spieler", [])
    pl = next((p for p in players if p["name"] == name), None)
    if pl is None:
        return "Spieler not found", 404
    if request.method == "POST":
        pl["info"] = request.form.get("info", "").strip()
        save_prompt_data(PROMPT_DATA)
        refresh_data()
        return redirect(url_for("player_list"))
    return render_template("edit_player.html", name=name, info=pl.get("info", ""))

@app.route("/players/delete/<name>")
@login_required
def delete_player(name):
    players = PROMPT_DATA.get("spieler", [])
    PROMPT_DATA["spieler"] = [p for p in players if p["name"] != name]
    save_prompt_data(PROMPT_DATA)
    refresh_data()
    return redirect(url_for("player_list"))

@app.route("/animals")
@login_required
def animal_list():
    animals = sorted(t["name"] for t in PROMPT_DATA.get("tiere", []))
    return render_template("animal_list.html", animals=animals)

@app.route("/animals/add", methods=["GET", "POST"])
@login_required
def add_animal():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        info = request.form.get("info", "").strip()
        if name and info:
            lst = PROMPT_DATA.setdefault("tiere", [])
            lst.append({"name": name, "info": info})
            save_prompt_data(PROMPT_DATA)
            refresh_data()
            return redirect(url_for("animal_list"))
    return render_template("add_animal.html")

@app.route("/animals/edit/<name>", methods=["GET", "POST"])
@login_required
def edit_animal(name):
    animals = PROMPT_DATA.get("tiere", [])
    an = next((a for a in animals if a["name"] == name), None)
    if an is None:
        return "Tier not found", 404
    if request.method == "POST":
        an["info"] = request.form.get("info", "").strip()
        save_prompt_data(PROMPT_DATA)
        refresh_data()
        return redirect(url_for("animal_list"))
    return render_template("edit_animal.html", name=name, info=an.get("info", ""))

@app.route("/animals/delete/<name>")
@login_required
def delete_animal(name):
    animals = PROMPT_DATA.get("tiere", [])
    PROMPT_DATA["tiere"] = [a for a in animals if a["name"] != name]
    save_prompt_data(PROMPT_DATA)
    refresh_data()
    return redirect(url_for("animal_list"))

@app.route("/world", methods=["GET", "POST"])
@login_required
def edit_world():
    if request.method == "POST":
        PROMPT_DATA["welt"] = request.form.get("welt", "").strip()
        save_prompt_data(PROMPT_DATA)
        refresh_data()
        return redirect(url_for("npc_list"))
    return render_template(
        "edit_world.html",
        welt=PROMPT_DATA.get("welt", ""),
    )

@app.route("/core", methods=["GET", "POST"])
@login_required
def edit_core():
    if request.method == "POST":
        PROMPT_DATA["core"] = request.form.get("core", "").strip()
        save_prompt_data(PROMPT_DATA)
        refresh_data()
        return redirect(url_for("npc_list"))
    return render_template(
        "edit_core.html",
        core=PROMPT_DATA.get("core", ""),
    )

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
    refresh_data()

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

def load_npc_extension(npc_name: str) -> str:
    base = npc_name.split()[0]
    for npc in PROMPT_DATA.get("npc", []):
        if npc["name"].split()[0] == base:
            extra = npc.get("long", "")
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

def run_flask():
    app.run(host='0.0.0.0', port=5000)

if __name__ == '__main__':
    Thread(target=run_flask, daemon=True).start()
    logger.info('Starting Discord bot')
    client.run(DISCORD_TOKEN)
