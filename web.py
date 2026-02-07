import os
import json
from functools import wraps
from flask import Flask, request, session, redirect, url_for, render_template

app = Flask(__name__)

CONFIG = {}
GET_PROMPT_DATA = None
SAVE_PROMPT_DATA = None
REFRESH_DATA = None
LOG_DIR = ""
WEB_USERNAME = ""
WEB_PASSWORD = ""
BASE_DIR = ""
logger = None

def init_web(config, get_prompt_data, save_prompt_data, refresh_data,
             log_dir, username, password, base_dir, secret_key, log):
    global CONFIG, GET_PROMPT_DATA, SAVE_PROMPT_DATA, REFRESH_DATA
    global LOG_DIR, WEB_USERNAME, WEB_PASSWORD, BASE_DIR, logger
    CONFIG = config
    GET_PROMPT_DATA = get_prompt_data
    SAVE_PROMPT_DATA = save_prompt_data
    REFRESH_DATA = refresh_data
    LOG_DIR = log_dir
    WEB_USERNAME = username
    WEB_PASSWORD = password
    BASE_DIR = base_dir
    logger = log
    app.secret_key = secret_key

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
            return redirect(url_for("index"))
        logger.warning("Failed login attempt for user %s", username)
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.pop("logged_in", None)
    logger.info("User logged out")
    return redirect(url_for("login"))

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/prompt_data")
@login_required
def prompt_data():
    data = GET_PROMPT_DATA()
    npc_count = len(data.get("npc", []))
    player_count = len(data.get("spieler", []))
    animal_count = len(data.get("tiere", []))
    event_count = len(data.get("events", []))
    user_count = len(data.get("user_list", {}))
    core_text = "vorhanden" if data.get("core") else "nicht gesetzt"
    world_text = "vorhanden" if data.get("welt") else "nicht gesetzt"
    return render_template(
        "prompt_data.html",
        npc_count=npc_count,
        player_count=player_count,
        animal_count=animal_count,
        event_count=event_count,
        user_count=user_count,
        core_text=core_text,
        world_text=world_text,
    )

@app.route("/npcs")
@login_required
def npc_list():
    data = GET_PROMPT_DATA()
    all_npcs = sorted(n["name"].split()[0] for n in data.get("npc", []))
    return render_template("npc_list.html", npcs=all_npcs)

@app.route("/add", methods=["GET", "POST"])
@login_required
def add_npc():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        short = request.form.get("short", "").strip()
        long = request.form.get("long", "").strip()
        if name and short:
            data = GET_PROMPT_DATA()
            npc_list = data.setdefault("npc", [])
            npc_list.append({"name": name, "short": short, "long": long})
            SAVE_PROMPT_DATA(data)
            REFRESH_DATA()
            logger.info("Added NPC %s", name)
            return redirect(url_for("npc_list"))
    return render_template("add_npc.html")

@app.route("/edit/<name>", methods=["GET", "POST"])
@login_required
def edit_npc(name):
    data = GET_PROMPT_DATA()
    npc_list = data.get("npc", [])
    npc = next((n for n in npc_list if n["name"].split()[0] == name), None)
    if npc is None:
        logger.warning("NPC %s not found", name)
        return "NPC not found", 404
    if request.method == "POST":
        npc["short"] = request.form.get("short", "").strip()
        npc["long"] = request.form.get("long", "").strip()
        SAVE_PROMPT_DATA(data)
        REFRESH_DATA()
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
    data = GET_PROMPT_DATA()
    npc_list = data.get("npc", [])
    data["npc"] = [n for n in npc_list if n["name"].split()[0] != name]
    SAVE_PROMPT_DATA(data)
    REFRESH_DATA()
    logger.info("Deleted NPC %s", name)
    return redirect(url_for("npc_list"))

@app.route("/players")
@login_required
def player_list():
    data = GET_PROMPT_DATA()
    players = sorted(p["name"] for p in data.get("spieler", []))
    return render_template("player_list.html", players=players)

@app.route("/players/add", methods=["GET", "POST"])
@login_required
def add_player():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        info = request.form.get("info", "").strip()
        if name and info:
            data = GET_PROMPT_DATA()
            lst = data.setdefault("spieler", [])
            lst.append({"name": name, "info": info})
            SAVE_PROMPT_DATA(data)
            REFRESH_DATA()
            logger.info("Added player %s", name)
            return redirect(url_for("player_list"))
    return render_template("add_player.html")

@app.route("/players/edit/<name>", methods=["GET", "POST"])
@login_required
def edit_player(name):
    data = GET_PROMPT_DATA()
    players = data.get("spieler", [])
    pl = next((p for p in players if p["name"] == name), None)
    if pl is None:
        logger.warning("Player %s not found", name)
        return "Player not found", 404
    if request.method == "POST":
        pl["info"] = request.form.get("info", "").strip()
        SAVE_PROMPT_DATA(data)
        REFRESH_DATA()
        logger.info("Edited player %s", name)
        return redirect(url_for("player_list"))
    return render_template("edit_player.html", name=name, info=pl.get("info", ""))

@app.route("/players/delete/<name>")
@login_required
def delete_player(name):
    data = GET_PROMPT_DATA()
    players = data.get("spieler", [])
    data["spieler"] = [p for p in players if p["name"] != name]
    SAVE_PROMPT_DATA(data)
    REFRESH_DATA()
    logger.info("Deleted player %s", name)
    return redirect(url_for("player_list"))

@app.route("/animals")
@login_required
def animal_list():
    data = GET_PROMPT_DATA()
    animals = sorted(t["name"] for t in data.get("tiere", []))
    return render_template("animal_list.html", animals=animals)

@app.route("/animals/add", methods=["GET", "POST"])
@login_required
def add_animal():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        info = request.form.get("info", "").strip()
        if name and info:
            data = GET_PROMPT_DATA()
            lst = data.setdefault("tiere", [])
            lst.append({"name": name, "info": info})
            SAVE_PROMPT_DATA(data)
            REFRESH_DATA()
            logger.info("Added animal %s", name)
            return redirect(url_for("animal_list"))
    return render_template("add_animal.html")

@app.route("/animals/edit/<name>", methods=["GET", "POST"])
@login_required
def edit_animal(name):
    data = GET_PROMPT_DATA()
    animals = data.get("tiere", [])
    an = next((a for a in animals if a["name"] == name), None)
    if an is None:
        logger.warning("Animal %s not found", name)
        return "Animal not found", 404
    if request.method == "POST":
        an["info"] = request.form.get("info", "").strip()
        SAVE_PROMPT_DATA(data)
        REFRESH_DATA()
        logger.info("Edited animal %s", name)
        return redirect(url_for("animal_list"))
    return render_template("edit_animal.html", name=name, info=an.get("info", ""))

@app.route("/animals/delete/<name>")
@login_required
def delete_animal(name):
    data = GET_PROMPT_DATA()
    animals = data.get("tiere", [])
    data["tiere"] = [a for a in animals if a["name"] != name]
    SAVE_PROMPT_DATA(data)
    REFRESH_DATA()
    logger.info("Deleted animal %s", name)
    return redirect(url_for("animal_list"))

@app.route("/events")
@login_required
def event_list():
    data = GET_PROMPT_DATA()
    events = data.get("events", [])
    return render_template("event_list.html", events=events)

@app.route("/events/add", methods=["GET", "POST"])
@login_required
def add_event():
    if request.method == "POST":
        npc = request.form.get("npc", "").strip()
        info = request.form.get("info", "").strip()
        if npc and info:
            data = GET_PROMPT_DATA()
            lst = data.setdefault("events", [])
            lst.append({"npc": npc, "info": info})
            SAVE_PROMPT_DATA(data)
            REFRESH_DATA()
            logger.info("Added event for NPC %s", npc)
            return redirect(url_for("event_list"))
    return render_template("add_event.html")

@app.route("/events/delete/<int:index>")
@login_required
def delete_event(index):
    data = GET_PROMPT_DATA()
    events = data.get("events", [])
    if 0 <= index < len(events):
        removed = events.pop(index)
        SAVE_PROMPT_DATA(data)
        REFRESH_DATA()
        logger.info("Deleted event for NPC %s", removed.get("npc"))
    return redirect(url_for("event_list"))

@app.route("/world", methods=["GET", "POST"])
@login_required
def edit_world():
    data = GET_PROMPT_DATA()
    if request.method == "POST":
        data["welt"] = request.form.get("welt", "").strip()
        SAVE_PROMPT_DATA(data)
        REFRESH_DATA()
        logger.info("World description updated")
        return redirect(url_for("npc_list"))
    return render_template(
        "edit_world.html",
        welt=data.get("welt", ""),
    )

@app.route("/core", methods=["GET", "POST"])
@login_required
def edit_core():
    data = GET_PROMPT_DATA()
    if request.method == "POST":
        data["core"] = request.form.get("core", "").strip()
        SAVE_PROMPT_DATA(data)
        REFRESH_DATA()
        logger.info("Core description updated")
        return redirect(url_for("npc_list"))
    return render_template(
        "edit_core.html",
        core=data.get("core", ""),
    )

@app.route("/weather", methods=["GET", "POST"])
@login_required
def edit_weather():
    data = GET_PROMPT_DATA()
    if request.method == "POST":
        wt = {str(i): request.form.get(str(i), "").strip() for i in range(1, 21)}
        data["weather_table"] = wt
        SAVE_PROMPT_DATA(data)
        REFRESH_DATA()
        logger.info("Weather table updated")
        return redirect(url_for("prompt_data"))
    weather = {int(k): v for k, v in data.get("weather_table", {}).items()}
    return render_template("edit_weather.html", weather=weather)

@app.route("/users")
@login_required
def user_list():
    data = GET_PROMPT_DATA()
    users = data.get("user_list", {})
    return render_template("user_list.html", users=users)

@app.route("/users/add", methods=["GET", "POST"])
@login_required
def add_user():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        character = request.form.get("character", "").strip()
        if username and character:
            data = GET_PROMPT_DATA()
            data.setdefault("user_list", {})[username] = character
            SAVE_PROMPT_DATA(data)
            REFRESH_DATA()
            logger.info("Added user %s with character %s", username, character)
            return redirect(url_for("user_list"))
    return render_template("add_user.html")

@app.route("/users/edit/<username>", methods=["GET", "POST"])
@login_required
def edit_user(username):
    data = GET_PROMPT_DATA()
    users = data.get("user_list", {})
    if username not in users:
        logger.warning("User %s not found", username)
        return "User not found", 404
    if request.method == "POST":
        users[username] = request.form.get("character", "").strip()
        SAVE_PROMPT_DATA(data)
        REFRESH_DATA()
        logger.info("Edited user %s", username)
        return redirect(url_for("user_list"))
    return render_template("edit_user.html", username=username, character=users[username])

@app.route("/users/delete/<username>")
@login_required
def delete_user(username):
    data = GET_PROMPT_DATA()
    users = data.get("user_list", {})
    users.pop(username, None)
    SAVE_PROMPT_DATA(data)
    REFRESH_DATA()
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

@app.route("/settings", methods=["GET", "POST"])
@login_required
def settings():
    config_path = os.path.join(BASE_DIR, "config.json")
    error = None
    if request.method == "POST":
        raw = request.form.get("config", "")
        weather_description_enabled = bool(request.form.get("daily_weather_description_enabled"))
        try:
            new_config = json.loads(raw)
            with open(config_path, "r", encoding="utf-8") as f:
                current = json.load(f)
            new_config["webserver"] = current.get("webserver", {})
            new_config.setdefault("discord", {})
            new_config["discord"]["daily_weather_description_enabled"] = weather_description_enabled

            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(new_config, f, ensure_ascii=False, indent=2)
            global CONFIG
            CONFIG = new_config
            REFRESH_DATA()
            return redirect(url_for("settings"))
        except json.JSONDecodeError:
            error = "Ungültiges JSON."
    with open(config_path, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    cfg.pop("webserver", None)
    daily_weather_description_enabled = cfg.get("discord", {}).get("daily_weather_description_enabled", True)
    cfg_json = json.dumps(cfg, ensure_ascii=False, indent=2)
    return render_template(
        "settings.html",
        config=cfg_json,
        error=error,
        daily_weather_description_enabled=daily_weather_description_enabled,
    )

