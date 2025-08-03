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

LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
os.makedirs(LOG_DIR, exist_ok=True)


class LevelFilter(logging.Filter):
    def __init__(self, level: int) -> None:
        super().__init__()
        self.level = level

    def filter(self, record: logging.LogRecord) -> bool:  # pragma: no cover - simple filter
        return record.levelno == self.level


formatter = logging.Formatter("%(asctime)s %(levelname)s:%(name)s:%(message)s")
root_logger = logging.getLogger()
root_logger.setLevel(logging.DEBUG)

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

openai.api_key = OPENAI_API_KEY
logger.debug('OpenAI API key loaded')

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)
logger.debug('Discord client initialized')

app = Flask(__name__)
app.secret_key = FLASK_SECRET_KEY

def load_prompt_data(path="prompt_data.json"):
    logger.debug("Loading prompt data from %s", path)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def save_prompt_data(data: dict, path="prompt_data.json"):
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

current_weather = "Undetermined"
weather_roll_date = None

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
        username = request.form.get("username")
        password = request.form.get("password")
        if username == WEB_USERNAME and password == WEB_PASSWORD:
            session["logged_in"] = True
            logger.info("User %s logged in", username)
            return redirect(url_for("dashboard"))
        logger.warning("Failed login attempt for user %s", username)
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.pop("logged_in", None)
    logger.info("User logged out")
    return redirect(url_for("login"))

@app.route("/")
@login_required
def dashboard():
    npc_count = len(PROMPT_DATA.get("npc", []))
    player_count = len(PROMPT_DATA.get("spieler", []))
    animal_count = len(PROMPT_DATA.get("tiere", []))
    user_count = len(PROMPT_DATA.get("user_list", {}))
    core_text = "vorhanden" if PROMPT_DATA.get("core") else "nicht gesetzt"
    world_text = "vorhanden" if PROMPT_DATA.get("welt") else "nicht gesetzt"
    return render_template(
        "dashboard.html",
        npc_count=npc_count,
        player_count=player_count,
        animal_count=animal_count,
        user_count=user_count,
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
            logger.info("Added NPC %s", name)
            return redirect(url_for("npc_list"))
    return render_template("add_npc.html")

@app.route("/edit/<name>", methods=["GET", "POST"])
@login_required
def edit_npc(name):
    npc_list = PROMPT_DATA.get("npc", [])
    npc = next((n for n in npc_list if n["name"].split()[0] == name), None)
    if npc is None:
        logger.warning("NPC %s not found", name)
        return "NPC not found", 404
    if request.method == "POST":
        npc["short"] = request.form.get("short", "").strip()
        npc["long"] = request.form.get("long", "").strip()
        save_prompt_data(PROMPT_DATA)
        refresh_data()
        logger.info("Edited NPC %s", name)
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
    logger.info("Deleted NPC %s", name)
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
            logger.info("Added player %s", name)
            return redirect(url_for("player_list"))
    return render_template("add_player.html")

@app.route("/players/edit/<name>", methods=["GET", "POST"])
@login_required
def edit_player(name):
    players = PROMPT_DATA.get("spieler", [])
    pl = next((p for p in players if p["name"] == name), None)
    if pl is None:
        logger.warning("Player %s not found", name)
        return "Player not found", 404
    if request.method == "POST":
        pl["info"] = request.form.get("info", "").strip()
        save_prompt_data(PROMPT_DATA)
        refresh_data()
        logger.info("Edited player %s", name)
        return redirect(url_for("player_list"))
    return render_template("edit_player.html", name=name, info=pl.get("info", ""))

@app.route("/players/delete/<name>")
@login_required
def delete_player(name):
    players = PROMPT_DATA.get("spieler", [])
    PROMPT_DATA["spieler"] = [p for p in players if p["name"] != name]
    save_prompt_data(PROMPT_DATA)
    refresh_data()
    logger.info("Deleted player %s", name)
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
            logger.info("Added animal %s", name)
            return redirect(url_for("animal_list"))
    return render_template("add_animal.html")

@app.route("/animals/edit/<name>", methods=["GET", "POST"])
@login_required
def edit_animal(name):
    animals = PROMPT_DATA.get("tiere", [])
    an = next((a for a in animals if a["name"] == name), None)
    if an is None:
        logger.warning("Animal %s not found", name)
        return "Animal not found", 404
    if request.method == "POST":
        an["info"] = request.form.get("info", "").strip()
        save_prompt_data(PROMPT_DATA)
        refresh_data()
        logger.info("Edited animal %s", name)
        return redirect(url_for("animal_list"))
    return render_template("edit_animal.html", name=name, info=an.get("info", ""))

@app.route("/animals/delete/<name>")
@login_required
def delete_animal(name):
    animals = PROMPT_DATA.get("tiere", [])
    PROMPT_DATA["tiere"] = [a for a in animals if a["name"] != name]
    save_prompt_data(PROMPT_DATA)
    refresh_data()
    logger.info("Deleted animal %s", name)
    return redirect(url_for("animal_list"))

@app.route("/world", methods=["GET", "POST"])
@login_required
def edit_world():
    if request.method == "POST":
        PROMPT_DATA["welt"] = request.form.get("welt", "").strip()
        save_prompt_data(PROMPT_DATA)
        refresh_data()
        logger.info("World description updated")
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
        logger.info("Core description updated")
        return redirect(url_for("npc_list"))
    return render_template(
        "edit_core.html",
        core=PROMPT_DATA.get("core", ""),
    )

@app.route("/weather", methods=["GET", "POST"])
@login_required
def edit_weather():
    if request.method == "POST":
        wt = {str(i): request.form.get(str(i), "").strip() for i in range(1, 21)}
        PROMPT_DATA["weather_table"] = wt
        save_prompt_data(PROMPT_DATA)
        refresh_data()
        logger.info("Weather table updated")
        return redirect(url_for("dashboard"))
    weather = {int(k): v for k, v in PROMPT_DATA.get("weather_table", {}).items()}
    return render_template("edit_weather.html", weather=weather)

@app.route("/users")
@login_required
def user_list():
    users = PROMPT_DATA.get("user_list", {})
    return render_template("user_list.html", users=users)

@app.route("/users/add", methods=["GET", "POST"])
@login_required
def add_user():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        character = request.form.get("character", "").strip()
        if username and character:
            PROMPT_DATA.setdefault("user_list", {})[username] = character
            save_prompt_data(PROMPT_DATA)
            refresh_data()
            logger.info("Added user %s with character %s", username, character)
            return redirect(url_for("user_list"))
    return render_template("add_user.html")

@app.route("/users/edit/<username>", methods=["GET", "POST"])
@login_required
def edit_user(username):
    users = PROMPT_DATA.get("user_list", {})
    if username not in users:
        logger.warning("User %s not found", username)
        return "User not found", 404
    if request.method == "POST":
        users[username] = request.form.get("character", "").strip()
        save_prompt_data(PROMPT_DATA)
        refresh_data()
        logger.info("Edited user %s", username)
        return redirect(url_for("user_list"))
    return render_template("edit_user.html", username=username, character=users[username])

@app.route("/users/delete/<username>")
@login_required
def delete_user(username):
    users = PROMPT_DATA.get("user_list", {})
    users.pop(username, None)
    save_prompt_data(PROMPT_DATA)
    refresh_data()
    return redirect(url_for("user_list"))

@app.route("/logs")
@login_required
def view_logs():
    log_files = [f for f in os.listdir(LOG_DIR) if f.endswith(".log")]
    selected_log = request.args.get("log")
    content = ""
    if selected_log in log_files:
        with open(os.path.join(LOG_DIR, selected_log), "r", encoding="utf-8") as f:
            content = f.read()
    return render_template(
        "logs.html",
        log_files=log_files,
        selected_log=selected_log,
        log_content=content,
    )

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
    for npc in NPC_LIST:
        if npc.lower() in content_lower:
            await reply_as_npc(npc, message)
            break

@tree.command(name="force", description="Sofort eine Nachricht posten")
async def force_command(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    npc = get_random_npc()
    logger.info("Force command triggered by %s using NPC %s", interaction.user, npc)
    await generate_and_send(f'Schreibe eine kurze Szene mit dem NPC {npc}.', npc)
    await interaction.followup.send("Nachricht gepostet.", ephemeral=True)

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

async def generate_and_send(input, npc_name: str | None = None):
    current_time = datetime.now().strftime('%H:%M')
    parts = [PRE_PROMPT]
    if npc_name:
        extra = load_npc_extension(npc_name)
        if extra:
            parts.append(extra)
    parts.append(f"Es ist aktuell {current_time} Uhr. Das Wetter heute: {current_weather}.")
    prompt = "\n\n".join(parts)
    logger.debug('Prompt sent to OpenAI: %s', prompt)
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
        logger.info('Message sent to channel %s', channel.id)
    except Exception:
        logger.error('Error while sending message', exc_info=True)

async def get_recent_messages(channel: discord.TextChannel, limit: int = 10, before: discord.Message | None = None):
    messages = []
    async for msg in channel.history(limit=limit, before=before, oldest_first=False):
        messages.append(f"{USER_LIST[str(msg.author)]}: {msg.content}")
    messages.reverse()
    return "\n".join(messages)

async def reply_as_npc(npc_name: str, trigger_message: discord.Message):
    logger.info('Generating reply as %s', npc_name)
    channel = client.get_channel(CHANNEL_ID)
    context = await get_recent_messages(channel, limit=10, before=trigger_message)
    input_text = (
        f"Kontext der letzten Nachrichten:\n{context}\n\n"
        f"Antworte als {npc_name} auf folgende Nachricht. Halte dich an die Stilrichtlinien.\n"
        f"Nachricht von {USER_LIST[str(trigger_message.author)]}: {trigger_message.content}"
    )
    await generate_and_send(input_text, npc_name)

@tasks.loop(hours=1)
async def hourly_post():
    logger.debug('Hourly post task triggered')
    now = datetime.now()
    global current_weather, weather_roll_date

    if now.hour == 8 and (weather_roll_date != now.date()):
        current_weather = roll_weather()
        weather_roll_date = now.date()
        await generate_and_send('Beschreibe das aktuelle Wetter. Verwende dabei KEINE NPCs')
        logger.info('Daily weather determined: %s', current_weather)

    if 1 <= now.hour <= 8:
        logger.debug('Quiet hour')
        return

    if random.random() > 0.05:
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
            if age.total_seconds() < 3600:
                logger.debug('Last message only %s seconds old; skipped', age.total_seconds())
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
