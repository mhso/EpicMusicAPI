from argparse import ArgumentParser
import asyncio
import json

from dotenv import load_dotenv

from epic_music.api.requests import RateLimitAPIClient, _extract_track_info, _evaluate_lookup_result
from epic_music.api.models import ResponseFeedEntry
from epic_music.discbot.client import DISCORD_IDS
from epic_music.database.client import DatabaseClient

load_dotenv()

class ScriptRunner:
    def __init__(self) -> None:
        self.api_client = RateLimitAPIClient()

    async def extract_info(self):
        video_title = "Linkin Park - Hybrid Theory [Full Album]"
        youtube_id = "A2Ojfv3ziXg"

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

        score_content = await _evaluate_lookup_result(extracted_data_json, track_data)
        try:
            score_data = json.loads(score_content[0].text)
        except json.JSONDecodeError:
            score_data = {}

        print("Score evaluated by Claude:", score_data)

    async def discogs_request(self):
        params = {
            "token": self.api_client.discogs_token,
            "artist": "Linkin Park",
            "release": "Hybrid Theory",
        }

        result = await self.api_client.make_discogs_api_request(params)
        print(result)

    async def list_entries(self):
        with DatabaseClient() as cursor:
            entries, total = cursor.get_feed_entries()

            print(total)

            for entry in entries:
                extra = {
                    "posted_by": DISCORD_IDS.get(entry.posted_by, "Unknown"),
                    "avatar": "avata123",
                }
                model = ResponseFeedEntry.model_validate(entry, update=extra)
                print(model.model_dump_json())

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
