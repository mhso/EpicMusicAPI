from os import environ
from time import time
from typing import Any, Callable, Dict, Tuple
from re import compile, Pattern

from discord import Client, Guild, Member, Reaction, TextChannel, Message, Forbidden, Intents, User
from discord.client import Coro
from loguru import logger

from epic_music.api.requests import RateLimitAPIClient, on_messages_synced, extract_url_info
from epic_music.api.models import Environment
from epic_music.database.client import DatabaseClient

DISCORD_IDS = {
    267401734513491969: "Mokle", # Mikkel
    140425901673414656: "Frode", # Frederik
    164760621840072705: "Alex", # Alexander
    226045137657135104: "Mathias", # Mathias
    230973018707329026: "Fronk", # Frank
    252802093654474752: "Mogens" # Magnus
}

# Arbedsplads
GUILD_ID = 418753222560186371
CHANNEL_ID = 483937471135088640

# Test guild
TEST_GUILD_ID = 512363920044982272
TEST_CHANNEL_ID = 512363920044982274

class DiscordClient(Client):
    def __init__(
        self,
        database_client: DatabaseClient,
        api_client: RateLimitAPIClient,
    ):
        super().__init__(
            intents=Intents(
                members=True,
                guilds=True,
                emojis=True,
                reactions=True,
                guild_messages=True,
                message_content=True,
            )
        )

        with database_client as cursor:
            latest_msg_timestamp = cursor.get_latest_entry_timestamp()
            cursor.insert_users_if_missing(list(DISCORD_IDS.keys()))

        self.latest_msg_timestamp = latest_msg_timestamp
        self.database_client = database_client
        self.api_client = api_client
        self.guild: Guild = None
        self.channel: TextChannel = None
        self.environment = Environment(environ["ENVIRONMENT"])

        self._avatar_cache: Dict[int, Tuple[float, str]] = {}
        self._avatar_ttl = 6 * 60 * 60
        self._avatar_cache_size = 10

    async def on_ready(self):
        guild_id = TEST_GUILD_ID if self.environment is Environment.DEVELOPMENT else GUILD_ID
        channel_id = TEST_CHANNEL_ID if self.environment is Environment.DEVELOPMENT else CHANNEL_ID

        self.guild = self.get_guild(guild_id)
        self.channel = self.guild.get_channel(channel_id)

        try:
            await self.sync_messages()
        except Exception:
            logger.exception("Exception when syncing Discord messages!")

    async def get_avatar(self, disc_id: int):
        if self.guild is None:
            return None

        member = self.guild.get_member(disc_id)
        if member is None or member.avatar is None:
            return None

        if disc_id in self._avatar_cache:
            timestamp, path = self._avatar_cache[disc_id]

            if time() > timestamp + self._avatar_ttl or len(self._avatar_cache) > self._avatar_cache_size:
                del self._avatar_cache[disc_id]

            else:
                return path

        path = f"img/avatars/{disc_id}.png"
        local_path = f"{environ['STATIC_PATH']}/img/avatars/{disc_id}.png"
        static_path = f"static/img/avatars/{disc_id}.png"

        with open(local_path, "wb") as fp:
            await member.avatar.save(fp)

        self._avatar_cache[disc_id] = (time(), static_path)

        return static_path

    async def find_emoji(self, name: str):
        for emoji in self.guild.emojis:
            if emoji.name == name:
                return emoji

        return None

    async def send_authorization_url(self, user: User | Member):
        with self.database_client as cursor:
            user_token = cursor.get_user_token(user.id)

        if user_token is None:
            return False

        message = (
            "God aften kære kollega\n\n"
            "Her er dit super hemmelige link til #epic-music webzonen:\n"
            f"https://mhooge.com/epic-music?token={user_token}"
        )

        await user.send(message)

        return True

    def _strip_urls_from_message(self, content: str, pattern: Pattern):
        if not content:
            return None

        match = pattern.search(content)
        if match is None:
            return content

        return content.replace(match.group(0), "")

    async def _handle_message(self, message: Message):
        if message.channel.id != self.channel.id or message.author.id not in DISCORD_IDS:
            return []

        reactions = [
            {
                "emoji": str(reaction.emoji),
                "count": reaction.count
            }
            for reaction in message.reactions
        ]

        url_pattern = compile(r"(http|https)\:\/\/\S+")

        track_data = []
        for embed in message.embeds:
            video_url = embed.video.url if embed.video else None
            site_name, youtube_id = extract_url_info(embed.url, video_url, embed.provider.name)

            if site_name and youtube_id and embed.title:
                raw_data = {
                    "title": embed.title,
                    "site_name": site_name,
                    "original_url": embed.url or video_url,
                    "youtube_id": youtube_id,
                    "posted_by": message.author.id,
                    "date_posted": message.created_at,
                    "message": self._strip_urls_from_message(message.content, url_pattern),
                    "message_id": message.id,
                    "reactions": reactions,
                }

                print(f"Raw data for msg from {message.created_at}:", raw_data)
                track_data.append(raw_data)

        return track_data

    async def _add_message_data(self):
        from sqlalchemy import update
        from epic_music.api.models import FeedEntry

        with self.database_client as cursor:
            async for message in self.channel.history(limit=None, oldest_first=True):
                cursor.session.exec(update(FeedEntry).where(FeedEntry.message_id == message.id).values(message=message.content))

            cursor.session.commit()

        print("DONE")

    async def sync_messages(self):
        try:
            track_data = []
            async for message in self.channel.history(limit=None, after=self.latest_msg_timestamp, oldest_first=True):
                track_data.extend(await self._handle_message(message))

            if track_data == []:
                return

            await on_messages_synced(track_data, self.api_client, self.database_client)

        except Forbidden:
            logger.exception("Insufficient permissions to read Discord messages!")

    async def on_message(self, message: Message):
        if message.channel.guild != self.guild:
            return

        if message.content.strip() == "!epic-music":
            try:
                if not await self.send_authorization_url(message.author):
                    response = f"Du har desværre ikke adgang til #epic-music webzonen {self.find_emoji('frank')}"
                    await message.channel.send(response)
            except Exception:
                pass

        if message.channel != self.channel:
            return

        track_data = await self._handle_message(message)
        if track_data != []:
            await on_messages_synced(track_data, self.api_client, self.database_client)

    async def on_reaction_add(self, reaction: Reaction, user):
        with self.database_client as cursor:
            cursor.update_reaction_count(reaction.message.id, str(reaction.emoji), reaction.count)

    async def on_reaction_remove(self, reaction: Reaction, user):
        with self.database_client as cursor:
            cursor.update_reaction_count(reaction.message.id, str(reaction.emoji), reaction.count)
