from argparse import ArgumentParser
import asyncio
import json
import os
import re

from dotenv import load_dotenv

from epic_music.api.requests import RateLimitAPIClient, _extract_track_info, _evaluate_lookup_result
from epic_music.api.models import FeedEntry, ResponseFeedEntry, TrackGenre
from epic_music.discbot.client import DISCORD_IDS
from epic_music.database.client import DatabaseClient

load_dotenv()

class ScriptRunner:
    def __init__(self) -> None:
        os.environ["RESOURCES_PATH"] = "../resources"
        self.api_client = RateLimitAPIClient()

    async def extract_info(self):
        video_title = "Summer Mix 2026🍓May Top Playlist🍓Alan Walker, Dua Lipa, Coldplay Style🍓Best Popular Songs 2025"
        youtube_id = "WispzhKm6hQ"

        video_title, channel_title = await self.api_client.make_youtube_list_request(youtube_id)
        if video_title is None:
            video_title = title

        extracted_data = await _extract_track_info(video_title, channel_title)

        print("Extracted JSON by Claude:", extracted_data[0].text)
        try:
            extracted_data_json = json.loads(extracted_data[0].text)
        except json.JSONDecodeError:
            extracted_data_json = {}

        track_data = await self.api_client.make_musicbrainz_api_request(
            extracted_data_json.get("track"),
            extracted_data_json.get("artist"),
            extracted_data_json.get("album"),
        )

        print(track_data)

        # score_content = await _evaluate_lookup_result(extracted_data_json, track_data)
        # try:
        #     score_data = json.loads(score_content[0].text)
        # except json.JSONDecodeError:
        #     score_data = {}

        # print("Score evaluated by Claude:", score_data)

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
                page=0,
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
        from sqlmodel import select

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
