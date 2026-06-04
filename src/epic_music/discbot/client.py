from datetime import datetime

from discord import Client, Guild, Reaction, TextChannel, Message, Forbidden, Intents

from epic_music.api.requests import RateLimitAPIClient, on_messages_synced, extract_url_info
from epic_music.api.models import FeedEntry
from epic_music.database.client import DatabaseClient

GUILD_ID = 418753222560186371
CHANNEL_ID = 483937471135088640
DISCORD_IDS = {
    267401734513491969: "Mokle", # Mikkel
    140425901673414656: "Frode", # Frederik
    164760621840072705: "Alex", # Alexander
    226045137657135104: "Mathias", # Mathias
    230973018707329026: "Fronk", # Frank
    252802093654474752: "Mogens" # Magnus
}

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

        self.latest_msg_timestamp = datetime(2026, 2, 20)#latest_msg_timestamp
        self.database_client = database_client
        self.api_client = api_client
        self.guild: Guild = None
        self.channel: TextChannel = None

    async def on_ready(self):
        self.guild = self.get_guild(GUILD_ID)
        self.channel = self.guild.get_channel(CHANNEL_ID)

        await self.sync_messages()

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
                    "message_id": message.id,
                    "reactions": reactions,
                }

                print("Raw data:", raw_data)
                track_data.append(raw_data)

        return track_data

    async def sync_messages(self):
        try:
            track_data = []
            async for message in self.channel.history(limit=None, after=self.latest_msg_timestamp, oldest_first=True):
                track_data.extend(await self._handle_message(message))

            await on_messages_synced(track_data, self.api_client, self.database_client)

        except Forbidden as exc:
            print("Insufficient permissions to read messages:", exc)

    async def on_message(self, message: Message):
        track_data = await self._handle_message(message)
        if track_data == []:
            return

        await on_messages_synced(track_data, self.api_client, self.database_client)

    async def on_reaction_add(self, reaction: Reaction, user):
        with self.database_client as cursor:
            cursor.update_reaction_count(reaction.message.id, str(reaction.emoji), reaction.count)

    async def on_reaction_remove(self, reaction: Reaction, user):
        with self.database_client as cursor:
            cursor.update_reaction_count(reaction.message.id, str(reaction.emoji), reaction.count)
