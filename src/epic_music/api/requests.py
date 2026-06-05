import asyncio
from datetime import datetime
import json
from typing import Any, Dict, List, Literal, Sequence, Tuple
from os import environ
from urllib.parse import urlparse, parse_qs
from time import time

from httpx import AsyncClient, Auth, DigestAuth, ReadTimeout
from loguru import logger
from anthropic import AsyncAnthropic

from epic_music.api.models import FeedEntry, ListFeedRequest, EntryReaction, TrackArtist, TrackGenre
from epic_music.database.client import DatabaseClient

_MUSICBRANZ_BASE_URL = "https://musicbrainz.org/ws/2"
_YOUTUBE_SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"
_YOUTUBE_LIST_URL = "https://www.googleapis.com/youtube/v3/videos"
_DISCOGS_SEARCH_URL = "https://api.discogs.com/database/search"

_SupportedSites = Literal["youtube", "musicbrainz", "discogs"]

class RateLimitAPIClient:
    def __init__(self):
        self.youtube_token = environ["YOUTUBE_TOKEN"]
        self.discogs_token = environ["DISCOGS_TOKEN"]
        self.musicbrainz_username = environ["MUSICBRAINZ_USERNAME"]
        self.musicbrainz_password = environ["MUSICBRAINZ_PASSWORD"]
        self.user_agent = "epic-music/0.1.0 ( https://github.com/mhso/epic-music-api )"

        self._max_requests_per_min = {
            "youtube": 60,
            "musicbrainz": 40,
            "discogs": 40,
        }
        self._recent_requests = {
            "youtube": [],
            "musicbrainz": [],
            "discogs": [],
        }

    def _clean_up_old_requests(self, site_name: _SupportedSites):
        timestamps = []
        for timestamp in self._recent_requests[site_name]:
            if time() - timestamp <= 60:
                timestamps.append(timestamp)

        self._recent_requests[site_name] = timestamps

    def _get_wait_time(self, site_name: _SupportedSites):
        requests_made = len(self._recent_requests[site_name])
        if requests_made == 0:
            return 0

        requests_remaining = self._max_requests_per_min[site_name] - requests_made
        time_since_first = time() - self._recent_requests[site_name][0]

        return time_since_first / requests_remaining

    async def _make_request(
        self,
        url: str,
        site_name: _SupportedSites,
        params: Dict[str, Any] | None = None,
        headers: Dict[str, Any] | None = None,
        auth: Auth | None = None,
    ):
        self._clean_up_old_requests(site_name)

        # Get the amount of time we should sleep to avoid rate-limitting
        wait_time = self._get_wait_time(site_name)
        if wait_time > 0:
            print(f"Waiting {wait_time:.2f} secs...")
            await asyncio.sleep(wait_time)

        if headers is None:
            headers = {}

        headers["User-Agent"] = self.user_agent

        for attempt in range(1, 4):
            response = None
            try:
                async with AsyncClient() as client:
                    response = await client.get(url, params=params, headers=headers, auth=auth)

            except ReadTimeout:
                pass

            self._recent_requests[site_name].append(time())

            if not response or response.status_code >= 500:
                await asyncio.sleep(attempt * 2)

            else:
                break

        logger.info(f"Sent request to {url} | Params: {params} | Response: {response.status_code if response else None}")

        if not response or response.is_error:
            desc = f"Request error to '{url}'"
            if response:
                desc += str(response.status_code)

            logger.exception(desc)
            return None

        return response

    async def make_youtube_search_request(self, title: str, artist: str):
        response = await self._make_request(
            _YOUTUBE_SEARCH_URL,
            "youtube",
            params={
                "part": "snippet",
                "type": "video",
                "q": f"{artist} - {title}",
                "key": self.youtube_token,
            }
        )

        if response is None:
            return None

        response_data = response.json()
        results = response_data["items"]

        if results == []:
            return None

        first_result = results[0]
        video_id = first_result["id"]["videoId"]

        return f"https://youtube.com/watch?v={video_id}"

    async def make_youtube_list_request(self, video_id: str):
        response = await self._make_request(
            _YOUTUBE_LIST_URL,
            "youtube",
            params={
                "part": "snippet",
                "id": video_id,
                "key": self.youtube_token,
            }
        )

        if response is None:
            return None, None

        response_data = response.json()
        results = response_data["items"]

        if results == []:
            return None, None

        first_result = results[0]
        title = first_result["snippet"]["title"]
        channel = first_result["snippet"]["channelTitle"]

        if channel.endswith(" - Topic"):
            channel = channel.split(" - ")[0]

        return title, channel

    async def make_spotify_api_request(self, track_id: str):
        """
        Spotify API access requires Premium subscription, which I no longer have...
        """

    async def make_discogs_api_request(self, params: Dict[str, str]):
        response = await self._make_request(
            _DISCOGS_SEARCH_URL,
            "discogs",
            params={
                "token": self.discogs_token,
                **params,
            },
        )
        if response is None:
            return {}

        response_data = response.json()["results"]

        if not response_data:
            return {}

        for entry in response_data:
            if entry["type"] != "release":
                continue

            album = entry.get("title")
            genres = entry.get("genre")

            if album is not None and genres:
                return {
                    "album": album,
                    "genres": genres,
                }

        return {}

    async def make_musicbrainz_api_request(
        self,
        track: str | None,
        artist: str | None,
        album: str | None,
    ) -> Dict[str, str | List[str] | None]:
        params = {}

        if track is not None: # Search for track
            category = "recording"
            if artist is not None: # Include artist in search
                params["artist"] = artist

            params["recording"] = track
        else: # Search for album
            category = "release"
            if artist is not None: # Include artist in search
                params["artist"] = artist

            params["release"] = album

        terms = " AND ".join([f"{k}:{v}" for k, v in params.items()])
        query = f"query={terms}"

        response = await self._make_request(
            f"{_MUSICBRANZ_BASE_URL}/{category}",
            "musicbrainz",
            params={
                "limit": 10,
                "query": query,
            },
            headers={"Accept": "application/json"},
            auth=DigestAuth(self.musicbrainz_username, self.musicbrainz_password),
        )

        if response is None:
            return {}

        response_data = response.json()[f"{category}s"]

        if response_data == []:
            return {}

        def _parse_date(date: str):
            try:
                return datetime.strptime(date, "%Y-%m-%d").timestamp()
            except (TypeError, ValueError):
                try:
                    return datetime(int(date), 12, 31).timestamp()
                except ValueError:
                    return time()

        response_data.sort(key=lambda d: _parse_date(d.get("first-release-date", time())))

        # Try to get specific info for the top 10 search results
        data = {}
        for result in response_data:
            result_title = result.get("title")
            artists = [artist["name"] for artist in result["artist-credit"]]

            if category == "release":
                release_group = result["release-group"]
            else:
                releases = result.get("releases", [])
                release_group = None if not releases else releases[0]["release-group"]

            if release_group and "Live" in release_group.get("secondary-types", []):
                continue

            if result_title is not None and artists != []:
                if category == "recording":
                    data["title"] = result_title
                    releases = result.get("releases", [])
                    if releases:
                        data["album"] = releases[0]["title"]

                else:
                    data["album"] = result_title

                data["artists"] = artists
                break

        if (album := params.get("release")):
            params["release_title"] = album
            del params["release"]

        if (artists := data.get("artists")):
            params["artist"] = artists[0]

        # Make a reqest to Discogs to get genre and album if it wasn't found on MusicBrainz
        discogs_data = await self.make_discogs_api_request(params)

        data["album"] = data.get("album", discogs_data.get("album"))
        data["genres"] = discogs_data.get("genres")

        return data

async def _extract_track_info(video_title: str, channel_title: str | None):
    """
    Uses Claude to extract the names of any referenced artists, track, and/or album
    from the given YouTube video and channel name.
    """
    client = AsyncAnthropic(api_key=environ["CLAUDE_API_KEY"])

    channel_info = f"Channel name: {channel_title}" if channel_title else ""

    prompt = f"""Given the following data:
YouTube title: {video_title}
{channel_info}

Try to extract as many of the following fields as possible:
- track
- artist
- album"""

    message = await client.messages.create(
        max_tokens=1024,
        messages=[
            {
                "role": "user",
                "content": prompt,
            }
        ],
        model="claude-opus-4-7",
        output_config={
            "format": {
                "type": "json_schema",
                "schema": {
                    "type": "object",
                    "properties": {
                        "artist": {"type": "string"},
                        "track": {"type": "string"},
                        "album": {"type": "string"},
                    },
                    "required": [],
                    "additionalProperties": False,
                },
            }
        },
    )

    return message.content

async def _evaluate_lookup_result(raw_data: Dict[str, str], processed_data: Dict[str, str]):
    """
    Uses Claude to evaluate whether the categorization
    of track, album, and artist seem correct.
    """
    client = AsyncAnthropic(api_key=environ["CLAUDE_API_KEY"])

    processed_copy = dict(processed_data)
    del processed_copy["genres"]

    if "title" in processed_copy:
        processed_copy["track"] = processed_copy["title"]
        del processed_copy["title"]

    raw_json = json.dumps(raw_data)
    processed_json = json.dumps(processed_copy)

    prompt = f"""Given the following data:
Input: {raw_json}
Output: {processed_json}

Evaluate whether the output has captured the correct track/album and artist from the input.
Put emphasis on the keys 'artist' and 'track', if they are present.

Give a score between 0 and 1 where 0 means nothing matches and 1 is a perfect match."""

    message = await client.messages.create(
        max_tokens=1024,
        messages=[
            {
                "role": "user",
                "content": prompt,
            }
        ],
        model="claude-opus-4-7",
        output_config={
            "format": {
                "type": "json_schema",
                "schema": {
                    "type": "object",
                    "properties": {
                        "score": {"type": "number"},
                    },
                    "required": ["score"],
                    "additionalProperties": False,
                },
            }
        },
    )

    return message.content

def _extract_site_name(base_url: str | None, embed_url: str | None) -> str | None:
    if base_url:
        if "youtube.com" in base_url:
            return "youtube"

        if "spotify.com" in base_url:
            return "spotify"
        
        if "tidal.com" in base_url:
            return "tidal"

    elif embed_url and "youtube.com" in embed_url:
            return "youtube"

    return None

def _handle_youtube_link(base_url: str | None, embed_url: str | None):
    video_id = None
    if base_url:
        parsed_url = urlparse(base_url)
        params = parse_qs(parsed_url.query)
        if len(id_val := params.get("v", [])) == 1:
            video_id = id_val[0]

    if not video_id and embed_url:
        parsed_url = urlparse(embed_url)
        split = parsed_url.path.split("/")

        # Handle trailing slash
        if split[-1] == "":
            offset = 2
        else:
            offset = 1

        if len(split) < offset + 1:
            return None

        if split[-offset - 1] != "embed":
            return None

        video_id = split[-offset]

    return video_id

def _handle_spotify_link(base_url: str | None) -> str | None:
    """
    1. Query Spotify API by track ID
    2. Query YouTube API for video of the song title
    3. Return the YouTube ID of found video, if any
    """
    return None

def extract_url_info(
    base_url: str | None,
    embed_url: str | None,
    site_name: str | None
) -> Tuple[str | None, str | None]:
    if site_name is None:
        site_name = _extract_site_name(base_url, embed_url)
    else:
        site_name = site_name.lower()

    if site_name is None:
        return None, None

    video_id = None
    if site_name == "youtube":
        video_id = _handle_youtube_link(base_url, embed_url)
    elif site_name == "spotify":
        video_id = _handle_spotify_link(base_url)

    return site_name, video_id

async def handle_list_entries(request: ListFeedRequest, database_client: DatabaseClient) -> List[FeedEntry]:
    """
    Load entries from the database, optionally filtered or sorted
    based on given parameters and with pagination support_make_request
    """
    with database_client as cursor:
        return list(
            cursor.get_feed_entries(
                request.page,
                request.sort_by,
                request.sort_order == "asc",
                request.filters,
            )
        )

async def on_messages_synced(
    feed_entries: List[Dict[str, Any]],
    api_client: RateLimitAPIClient,
    database_client: DatabaseClient
):
    """
    Called when the Discord client has finished a sync of messages in epic-music.
    Recieves the raw entries, processes them, and saves them to the database
    """
    data_models = []
    for raw_data in feed_entries:
        youtube_id = raw_data.pop("youtube_id")
        embed_title = raw_data.pop("title")

        video_title, channel_title = await api_client.make_youtube_list_request(youtube_id)
        if video_title is None:
            video_title = embed_title

        print("Video title:", video_title)
        print("Channel title:", channel_title)

        extracted_content = await _extract_track_info(video_title, channel_title)

        print("Extracted JSON by Claude:", extracted_content[0].text)
        try:
            extracted_data = json.loads(extracted_content[0].text)
        except json.JSONDecodeError:
            extracted_data = {}

        try:
            track_data = await api_client.make_musicbrainz_api_request(
                extracted_data.get("track"),
                extracted_data.get("artist"),
                extracted_data.get("album"),
            )
        except Exception:
            logger.exception("Error during musicbrainz API request!")
            track_data = {}

        if extracted_data and track_data:
            score_content = await _evaluate_lookup_result(extracted_data, track_data)
            try:
                score_data = json.loads(score_content[0].text)
            except json.JSONDecodeError:
                score_data = {}

            print("Score evaluated by Claude:", score_data)

            if score_data["score"] < 0.6:
                track_data = {}

        reactions = [EntryReaction(**reaction_data) for reaction_data in raw_data.pop("reactions")]
        artists = [TrackArtist(artist=artist) for artist in track_data.get("artists", [])]
        genres = [TrackGenre(genre=artist) for artist in track_data.get("genres", [])]

        model = FeedEntry(
            title=track_data.get("title"),
            album=track_data.get("album"),
            youtube_title=video_title,
            youtube_url=f"https://youtube.com/watch?v={youtube_id}",
            **raw_data,
            reactions=reactions,
            artists=artists,
            genres=genres,
        )
        print("Extracted data:", model)

        data_models.append(model)

    logger.info(f"Saving {len(data_models)} entries to the database...")

    try:
        with database_client as cursor:
            cursor.save_feed_entries(data_models)
    except Exception:
        logger.exception("Error when saving data to database!")
