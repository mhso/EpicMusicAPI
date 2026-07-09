from argparse import ArgumentParser
import asyncio
import os
import re

from dotenv import load_dotenv
from sqlalchemy import delete
from sqlmodel import select

from epic_music.api.requests import RateLimitAPIClient, extract_track_data
from epic_music.api.models import FeedEntry, ResponseFeedEntry, TrackArtist, TrackGenre
from epic_music.discbot.client import DISCORD_IDS
from epic_music.database.client import DatabaseClient

load_dotenv()

class ScriptRunner:
    def __init__(self) -> None:
        os.environ["RESOURCES_PATH"] = "../resources"
        self.api_client = RateLimitAPIClient()

    async def extract_info(self):
        video_title = "My Neighbor Totoro Lyrics ( Tonari no Totoro ) || Sub English"
        youtube_id = "TrL9d4GrIGA"

        video_title, track_data = await extract_track_data(youtube_id, video_title, self.api_client)

        print(f"Data for {video_title}:")
        print(track_data)

    async def musicbrainz_request(self):
        result = await self.api_client.make_musicbrainz_search_request(
            None,
            "Linkin Park",
            "Hybrid Theory"
        )

        await self.api_client.make_musicbrainz_genres_request(result[0]["release_group"])

        print(result)

    async def discogs_request(self):
        params = {
            "artist": "Linkin Park",
            "release_title": "Hybrid Theory",
        }

        result = await self.api_client.make_discogs_api_request(params)
        print(result)

    async def youtube_request(self):
        youtube_title = "KEYGEN MUSIC ~ One hour mix"
        result = await self.api_client.make_youtube_search_request(youtube_title)
        print(result)

    async def list_entries(self):
        disc_id_reverse_lookup = {v: k for k, v in DISCORD_IDS.items()}
        filters = {}

        with DatabaseClient() as cursor:
            data = cursor.get_feed_entries(
                page_from=0,
                page_to=1,
                order_by="reactions",
                order_asc=False,
                filters=filters,
            )

            for entry in data["entries"]:
                extra = {
                    "posted_by": DISCORD_IDS.get(int(entry.posted_by), "Unknown"),
                    "avatar": "avatar123",
                }

                for reaction in entry.reactions:
                    if (search := re.match(r"\<a?\:(.+)\:\d+\>", reaction.emoji)):
                        print("YES!", search)
                        emoji_fmt = search.group(1)
                    else:
                        emoji_fmt = reaction.emoji

                reaction.emoji = emoji_fmt

                model = ResponseFeedEntry.model_validate(entry, update=extra)

                print(model.model_dump())

            print(data["total"])

    def strip_urls(self):
        import re

        pattern = re.compile(r"(http|https)\:\/\/\S+")

        with DatabaseClient() as cursor:
            entries = cursor.session.exec(select(FeedEntry)).all()

            for entry in entries:
                if not entry.message:
                    continue

                match = pattern.search(entry.message)
                if match is None:
                    continue

                entry.message = entry.message.replace(match.group(0), "")

            cursor.session.flush()
            cursor.session.commit()

    async def try_fix_missing_metadata(self):
        print("Are you sure? Exiting to make sure you're sure...")
        return

        with DatabaseClient() as cursor:
            cursor.session.exec(delete(TrackArtist))
            cursor.session.exec(delete(TrackGenre))
            cursor.session.commit()

        with DatabaseClient() as cursor:
            entries = list(cursor.session.exec(select(FeedEntry)).all())

            try:
                total = len(entries)
                curr_pct = 0
                for index, entry in enumerate(entries):
                    video_title, track_data = await extract_track_data(
                        entry.youtube_id, entry.youtube_title, self.api_client
                    )
                    entry.title = track_data.get("title")
                    entry.album = track_data.get("album")
                    entry.youtube_title = video_title

                    entry.artists = [
                        TrackArtist(artist=artist, rank=index)
                        for index, artist in enumerate(track_data.get("artists", []))
                    ]
                    entry.genres = [
                        TrackGenre(genre=genre, rank=index)
                        for index, genre in enumerate(track_data.get("genres", []))
                    ]

                    pct = int((index / total) * 100)
                    if pct != curr_pct:
                        curr_pct = pct
                        print(f"=+=+=+=+=+=+=+=+= Processed {index}/{total} ({pct}%) =+=+=+=+=+=+=+=+=")

                    if index and index % 10 == 0:
                        cursor.session.flush()

            finally:
                print("Saving changes to the database...")
                cursor.session.commit()

    async def add_more_genres(self):
        use_discogs = True

        # with DatabaseClient() as cursor:
        #     cursor.session.exec(delete(TrackGenre))
        #     cursor.session.commit()

        with DatabaseClient() as cursor:
            entries = list(cursor.session.exec(select(FeedEntry)).all())

            try:
                total = len(entries)
                curr_pct = 0
                for index, entry in enumerate(entries):
                    if not entry.title and not entry.album:
                        continue

                    if use_discogs:
                        params = {}
                        if entry.title:
                            params["track"] = entry.title
                        if entry.album:
                            params["release_title"] = entry.album
                        if entry.artists:
                            params["artists"] = [a.artist for a in entry.artists]

                        data = await self.api_client.make_discogs_api_request(params)

                        if not entry.album and (album := data.get("album")):
                            entry.album = album

                        genres = data.get("genres", [])
                    else:
                        for artist in entry.artists:
                            data = await self.api_client.make_musicbrainz_search_request(
                                entry.title, artist.artist, entry.album, True, True
                            )
                            if data == []:
                                continue

                            print("Data in:", entry.title, entry.album, artist.artist)
                            print("Data out:", data[0].get("title"), data[0].get("album"), data[0].get("artists"))

                            if (release_group := data[0].get("release_group")):
                                genres = await self.api_client.make_musicbrainz_genres_request(release_group)
                                break
                            else:
                                genres = []

                    existing_genres = set(g.genre for g in entry.genres)

                    genre_models = [
                        TrackGenre(feed_id=entry.id, genre=genre, rank=index)
                        for index, genre in enumerate(genres, start=len(existing_genres))
                        if genre not in existing_genres
                    ]

                    if genre_models:
                        print(f"Adding genres to {entry.youtube_title}: {genres}")
                        cursor.session.add_all(genre_models)

                    pct = int((index / total) * 100)
                    if pct != curr_pct:
                        curr_pct = pct
                        print(f"=+=+=+=+=+=+=+=+= Processed {index}/{total} ({pct}%) =+=+=+=+=+=+=+=+=")

                    if index and index % 10 == 0:
                        cursor.session.flush()

            finally:
                print("Saving changes to the database...")
                cursor.session.commit()

if __name__ == "__main__":
    PARSER = ArgumentParser()

    TEST_RUNNER = ScriptRunner()
    FUNCS = [
        func
        for func in TEST_RUNNER.__dir__()
        if not func.startswith("_") and callable(getattr(TEST_RUNNER, func))
    ]

    PARSER.add_argument("func", choices=FUNCS)
    PARSER.add_argument("args", nargs="*")

    ARGS = PARSER.parse_args()

    func = getattr(TEST_RUNNER, ARGS.func)

    if asyncio.iscoroutinefunction(func):
        asyncio.run(func(*ARGS.args))
    else:
        func(*ARGS.args)
