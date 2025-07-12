import os
import random
import logging
from datetime import datetime

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s:%(name)s:%(message)s",
    handlers=[
        logging.FileHandler("bot.log"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)

from dotenv import load_dotenv

load_dotenv()

import openai
import discord
from discord.ext import tasks

DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
CHANNEL_ID = os.getenv('CHANNEL_ID')

logger.debug("Env vars loaded: CHANNEL_ID=%s", CHANNEL_ID)

if CHANNEL_ID is None:
    logger.error('CHANNEL_ID environment variable not set')
    raise RuntimeError('CHANNEL_ID environment variable not set')
CHANNEL_ID = int(CHANNEL_ID)

openai.api_key = OPENAI_API_KEY
logger.debug('OpenAI API key loaded')

intents = discord.Intents.default()
client = discord.Client(intents=intents)
logger.debug('Discord client initialized')

def load_pre_prompt(path="pre_prompt.txt"):
    logger.debug('Loading pre prompt from %s', path)
    with open(path, "r", encoding="utf-8") as f:
        return f.read().strip()

PRE_PROMPT = load_pre_prompt()
logger.debug('Pre prompt loaded')

@client.event
async def on_ready():
    logger.info('Logged in as %s', client.user)
    hourly_post.start()

@tasks.loop(hours=1)
async def hourly_post():
    logger.debug('hourly_post triggered')
    current_hour = datetime.now().hour
    if 1 <= current_hour <= 8:
        logger.debug('Posting disabled during quiet hours')
        return

    if random.random() > 0.05:
        logger.debug('Skipped posting this hour')
        return

    channel = client.get_channel(CHANNEL_ID)
    if channel is None:
        logger.error('Channel not found')
        return

    try:
        last_message = None
        async for message in channel.history(limit=1):
            last_message = message
            break
        if last_message is not None:
            age = discord.utils.utcnow() - last_message.created_at
            if age.total_seconds() < 3600:
                logger.debug('Last message only %s seconds old; skipping', age.total_seconds())
                return
    except Exception:
        logger.error('Error fetching channel history', exc_info=True)
        return

    current_time = datetime.now().strftime('%H:%M')
    prompt = f"{PRE_PROMPT} Es ist aktuell {current_time} Uhr."
    logger.debug('Prompt sent to OpenAI: %s', prompt)

    try:
        response = openai.chat.completions.create(
            model='gpt-4.1',
            messages=[
                {'role': 'system', 'content': prompt},
                {
                    'role': 'user',
                    'content': 'Write a short message for the Discord channel.'
                },
            ],
            max_tokens=1024,
        )
        message = response.choices[0].message.content.strip()
        logger.debug('OpenAI response: %s', message)
        await channel.send(message)
        logger.info('Message sent to channel %s', CHANNEL_ID)
    except Exception as e:
        logger.error('Error sending message', exc_info=True)

if __name__ == '__main__':
    logger.info('Starting Discord bot')
    client.run(DISCORD_TOKEN)
